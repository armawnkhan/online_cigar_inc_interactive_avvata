"""Step 3 of the build order: the full voice loop with no Unreal.

Run:  python -m agent.local_voice

Talk to her through the laptop mic; she answers through the speakers.
Wear headphones - on open speakers she hears herself and barge-in loops.
No headphones? Set HALF_DUPLEX=1: her mic goes deaf while she speaks
(kills the echo, but also kills barge-in - lobby array needs neither).
Ctrl-C to quit.
"""
import os
import asyncio
import logging
import queue
import threading
import time

from dotenv import load_dotenv

load_dotenv()

import sounddevice as sd

from .brain import Brain
from .voice import Ears, Mouth, mic_stream, SAMPLE_RATE

logging.basicConfig(level=logging.WARNING)
log = logging.getLogger("local")


class Speaker:
    """Plays PCM chunks on a background thread so the event loop never blocks.
    flush() drops everything queued - the barge-in path."""

    def __init__(self):
        self._q: queue.Queue[bytes | None] = queue.Queue()
        self._gen = 0
        self._busy_until = 0.0
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def play(self, pcm: bytes):
        now = time.monotonic()
        self._busy_until = max(now, self._busy_until) + len(pcm) / 2 / SAMPLE_RATE
        self._q.put((self._gen, pcm))

    def flush(self):
        self._gen += 1
        self._busy_until = 0.0
        try:
            while True:
                self._q.get_nowait()
        except queue.Empty:
            pass

    @property
    def is_busy(self) -> bool:
        """True while queued audio is still coming out of the speaker,
        plus a short tail for room reverb."""
        return time.monotonic() < self._busy_until + 0.3

    def _run(self):
        with sd.RawOutputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16") as out:
            while True:
                gen, pcm = self._q.get()
                if gen == self._gen:
                    out.write(pcm)


class LocalSession:
    def __init__(self):
        self.half_duplex = os.getenv("HALF_DUPLEX") == "1"
        self.speaker = Speaker()
        self.mouth = Mouth(on_audio=self._on_audio)
        self.ears = Ears(on_final=self._on_final, on_speech_start=self._on_speech_start)
        self.brain = Brain(emit_ui=self._emit_ui, speak=self._speak)
        self._speaking = False
        self._t_heard = 0.0
        self._t_first_audio = None

    # ---------------- outputs

    async def _emit_ui(self, action, payload):
        print(f"  [SCENE] {action}: {payload.get('title', payload.get('handle', payload))}")

    async def _speak(self, text):
        print(f"  HER: {text}")
        self._speaking = True
        await self.mouth.say(text)

    async def _on_audio(self, pcm: bytes):
        if self._t_first_audio is None and self._t_heard:
            self._t_first_audio = time.perf_counter()
            print(f"  [latency] first sound {1000 * (self._t_first_audio - self._t_heard):.0f}ms "
                  f"after you stopped talking")
        self.speaker.play(pcm)

    # ---------------- inputs

    async def _on_speech_start(self):
        if self._speaking:
            print("  -- barge-in: she stops --")
            self.speaker.flush()
            await self.mouth.interrupt()
            self._speaking = False

    async def _on_final(self, text):
        print(f"\n  YOU: {text}")
        self._t_heard = time.perf_counter()
        self._t_first_audio = None
        await self.brain.respond(text)
        await self.mouth.flush()
        self._speaking = False

    # ---------------- lifecycle

    async def run(self):
        print("Cigar Inc. concierge - voice mode. Wear headphones. Ctrl-C to quit.\n")
        self.speaker.start()
        await asyncio.gather(self.mouth.start(), self.ears.start())
        await self.brain.greet()
        await self.mouth.flush()
        if self.half_duplex:
            print("(half-duplex: she can't hear you while she's talking)\n")
        async for chunk in mic_stream():
            if self.half_duplex and self.speaker.is_busy:
                continue
            await self.ears.send_audio(chunk)


if __name__ == "__main__":
    try:
        asyncio.run(LocalSession().run())
    except KeyboardInterrupt:
        print("\nbye")
