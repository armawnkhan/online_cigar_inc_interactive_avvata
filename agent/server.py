"""The orchestrator. Owns the WebSocket to Unreal and the session state machine.

Run:  python -m agent.server
"""
import os
import json
import time
import asyncio
import logging
from typing import Optional

import websockets
from dotenv import load_dotenv

from .brain import Brain
from .voice import Ears, Mouth, mic_stream

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s")
log = logging.getLogger("server")

HOST, PORT = "127.0.0.1", 8765
SILENCE_TIMEOUT = 45          # seconds before farewell
MAX_SESSION = 600             # hard cap, seconds


class Session:
    def __init__(self, ue):
        self.ue = ue
        self.state = "attract"
        self.seq = 0
        self.started = 0.0
        self.last_activity = time.time()
        self.speaking = False
        self.active = False

        self.mouth = Mouth(on_audio=self._audio_out)
        self.ears = Ears(on_final=self._on_final, on_speech_start=self._on_speech_start)
        self.brain = Brain(emit_ui=self._emit_ui, speak=self._speak, set_state=self.set_state)

        self._mic_task: Optional[asyncio.Task] = None
        self._watchdog: Optional[asyncio.Task] = None

    # ---------------------------------------------------------- to Unreal

    async def _send(self, obj: dict):
        try:
            await self.ue.send(json.dumps(obj))
        except websockets.ConnectionClosed:
            pass

    async def set_state(self, value: str):
        if value != self.state:
            self.state = value
            await self._send({"type": "state", "value": value})

    async def _emit_ui(self, action: str, payload: dict):
        await self._send({"type": "ui", "action": action, "payload": payload})
        if action in ("show_product_frame", "show_media"):
            await self.set_state("presenting")

    async def _speak(self, text: str):
        """One sentence -> TTS. Brackets the audio with start/end markers."""
        if not self.speaking:
            self.seq += 1
            self.speaking = True
            await self._send({"type": "audio_start", "seq": self.seq,
                              "sample_rate": 16000, "channels": 1})
        log.info("HER: %s", text)
        await self.mouth.say(text)
        self.last_activity = time.time()

    async def _audio_out(self, pcm: bytes):
        try:
            await self.ue.send(pcm)          # binary frame
        except websockets.ConnectionClosed:
            pass

    async def _end_utterance(self):
        if self.speaking:
            await self.mouth.flush()
            await self._send({"type": "audio_end", "seq": self.seq})
            self.speaking = False

    # ---------------------------------------------------------- from ears

    async def _on_speech_start(self):
        """Barge-in. Fires the moment the customer opens their mouth."""
        if self.speaking:
            log.info("--- barge-in ---")
            await self._send({"type": "interrupt", "seq": self.seq})
            await self.mouth.interrupt()
            self.speaking = False
        await self.set_state("listening")
        self.last_activity = time.time()

    async def _on_final(self, text: str):
        log.info("THEM: %s", text)
        self.last_activity = time.time()
        await self.brain.respond(text)
        await self._end_utterance()
        await self.set_state("listening")

    # ---------------------------------------------------------- lifecycle

    async def wake(self, trigger: str = "touch"):
        if self.active:
            return
        log.info("waking (%s)", trigger)
        self.active = True
        self.started = time.time()
        self.last_activity = time.time()
        await self.set_state("waking")

        self.brain.reset()
        await asyncio.gather(self.mouth.start(), self.ears.start())

        self._mic_task = asyncio.create_task(self._pump_mic())
        self._watchdog = asyncio.create_task(self._watch())

        await self.brain.greet()
        await self._end_utterance()
        await self.set_state("listening")

    async def sleep(self, reason: str = "timeout", farewell: bool = True):
        if not self.active:
            return
        log.info("sleeping (%s)", reason)
        self.active = False
        await self.set_state("farewell")

        if farewell:
            await self._speak("I'll be right here whenever you're ready.")
            await self._end_utterance()
            await asyncio.sleep(2.5)

        for t in (self._mic_task, self._watchdog):
            if t:
                t.cancel()
        await asyncio.gather(self.ears.close(), self.mouth.close(), return_exceptions=True)
        await self._emit_ui("close_overlay", {})
        await self.set_state("attract")

    async def _pump_mic(self):
        try:
            async for chunk in mic_stream():
                await self.ears.send_audio(chunk)
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("mic pump died")

    async def _watch(self):
        """Silence timeout + hard session cap."""
        try:
            while True:
                await asyncio.sleep(2)
                if time.time() - self.last_activity > SILENCE_TIMEOUT:
                    return await self.sleep("silence")
                if time.time() - self.started > MAX_SESSION:
                    return await self.sleep("max_session")
        except asyncio.CancelledError:
            pass

    # ---------------------------------------------------------- from Unreal

    async def on_message(self, msg: dict):
        t = msg.get("type")
        self.last_activity = time.time()

        if t == "hello":
            log.info("Unreal connected: %s", msg.get("version"))
            await self.set_state("attract")

        elif t == "wake":
            await self.wake(msg.get("trigger", "touch"))

        elif t == "sleep":
            await self.sleep(msg.get("reason", "manual"))

        elif t == "speech_detected":
            await self._on_speech_start()

        elif t == "touch":
            if not self.active:
                await self.wake("touch")
            # Touches enter the conversation as context so she reacts OUT LOUD.
            # A silent page-open is the failure mode we are avoiding.
            target, tid = msg.get("target"), msg.get("id") or msg.get("handle")
            close_prompt = (
                "[The customer closed the sign-up page. Pick the conversation back up "
                "warmly where you left off - don't quiz them on whether they signed up.]"
                if tid == "signup" else
                "[The customer closed the panel. Acknowledge briefly and offer the next thing.]"
            )
            prompt = {
                "menu":          f"[The customer tapped the '{tid}' menu. React and guide them.]",
                "product_grid":  f"[The customer tapped product '{tid}'. Show its frame and say something about it.]",
                "product_frame": f"[The customer tapped the frame for '{tid}'. Open its detail and elaborate.]",
                "close":         close_prompt,
                "mic":           "[The customer tapped the mic. Invite them to speak.]",
            }.get(target)
            if prompt:
                await self.brain.respond(prompt)
                await self._end_utterance()
                await self.set_state("listening")


async def handler(ue):
    log.info("Unreal client connected")
    session = Session(ue)
    try:
        async for raw in ue:
            if isinstance(raw, bytes):
                continue                     # Unreal sends no binary upstream
            try:
                await session.on_message(json.loads(raw))
            except json.JSONDecodeError:
                log.warning("bad JSON from Unreal: %r", raw[:200])
            except Exception:
                log.exception("error handling message")
    except websockets.ConnectionClosed:
        pass
    finally:
        log.info("Unreal client disconnected")
        await session.sleep("disconnect", farewell=False)


async def main():
    for var in ("ANTHROPIC_API_KEY", "ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID", "DEEPGRAM_API_KEY"):
        if not os.getenv(var):
            raise SystemExit(f"Missing {var} - copy .env.example to .env and fill it in")

    async with websockets.serve(handler, HOST, PORT, max_size=None, ping_interval=10):
        log.info("Agent listening on ws://%s:%d - waiting for Unreal", HOST, PORT)
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
