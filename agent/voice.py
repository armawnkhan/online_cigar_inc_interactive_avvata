"""Ears and mouth.

Deepgram Nova-3 streaming in, ElevenLabs Flash streaming out. Both sockets stay
open for the whole session - reconnecting mid-conversation costs a full second
and the customer hears it.

Output is PCM16 mono 16kHz, which is what the Unreal bridge and Audio2Face-3D
both want. Do not change the sample rate without changing PROTOCOL.md.
"""
import os
import json
import base64
import asyncio
import logging
from typing import Callable, Awaitable, AsyncIterator

import websockets

log = logging.getLogger("voice")

SAMPLE_RATE = 16000

DEEPGRAM_URL = (
    "wss://api.deepgram.com/v1/listen"
    f"?model=nova-3&language=en-US&encoding=linear16&sample_rate={SAMPLE_RATE}"
    "&channels=1&interim_results=true&smart_format=true"
    "&endpointing=400&vad_events=true&utterance_end_ms=1000"
)

ELEVEN_URL = (
    "wss://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream-input"
    f"?model_id=eleven_flash_v2_5&output_format=pcm_{SAMPLE_RATE}"
    "&auto_mode=true"
)


class Ears:
    """Streaming STT. Calls on_final(text) at end of utterance,
    on_speech_start() the instant the customer opens their mouth."""

    def __init__(
        self,
        on_final: Callable[[str], Awaitable[None]],
        on_speech_start: Callable[[], Awaitable[None]] | None = None,
    ):
        self.on_final = on_final
        self.on_speech_start = on_speech_start
        self.ws = None
        self._task = None
        self._buf = ""

    async def start(self):
        self.ws = await websockets.connect(
            DEEPGRAM_URL,
            additional_headers={"Authorization": f"Token {os.environ['DEEPGRAM_API_KEY']}"},
            ping_interval=5,
        )
        self._task = asyncio.create_task(self._listen())
        log.info("Deepgram connected")

    async def send_audio(self, pcm: bytes):
        if self.ws:
            try:
                await self.ws.send(pcm)
            except websockets.ConnectionClosed:
                log.warning("Deepgram dropped - reconnecting")
                await self.start()

    async def _listen(self):
        try:
            async for raw in self.ws:
                msg = json.loads(raw)
                t = msg.get("type")

                if t == "SpeechStarted" and self.on_speech_start:
                    await self.on_speech_start()

                elif t == "Results":
                    alt = msg["channel"]["alternatives"][0]
                    text = alt.get("transcript", "").strip()
                    if text and msg.get("is_final"):
                        self._buf = f"{self._buf} {text}".strip()
                    # speech_final fires at the endpointing threshold (400ms of
                    # silence) - roughly 600ms sooner than UtteranceEnd, which
                    # stays below only as a backstop for missed endpoints.
                    if msg.get("speech_final") and self._buf:
                        final, self._buf = self._buf, ""
                        await self.on_final(final)

                elif t == "UtteranceEnd":
                    if self._buf:
                        final, self._buf = self._buf, ""
                        await self.on_final(final)
        except websockets.ConnectionClosed:
            log.info("Deepgram closed")
        except Exception:
            log.exception("Deepgram listener died")

    async def close(self):
        if self._task:
            self._task.cancel()
        if self.ws:
            await self.ws.close()


class Mouth:
    """Streaming TTS. Text in, PCM chunks out via the on_audio callback.

    Uses ElevenLabs' multi-context streaming: text is flushed per sentence so
    audio starts before the sentence is even finished generating.
    """

    def __init__(self, on_audio: Callable[[bytes], Awaitable[None]]):
        self.on_audio = on_audio
        self.voice_id = os.environ["ELEVENLABS_VOICE_ID"]
        self.ws = None
        self._reader = None
        self._keepalive = None
        self._generation = 0   # bumped on interrupt; stale audio is dropped

    async def start(self):
        self.ws = await websockets.connect(
            ELEVEN_URL.format(voice_id=self.voice_id),
            additional_headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"]},
            ping_interval=10,
        )
        await self.ws.send(json.dumps({
            "text": " ",
            "voice_settings": {
                "stability": 0.45,       # some variation - she is not a recording
                "similarity_boost": 0.8,
                "speed": 0.96,           # a hair slow reads as composed, not sluggish
            },
            "generation_config": {"chunk_length_schedule": [50, 120, 200, 280]},
        }))
        self._reader = asyncio.create_task(self._read())
        if self._keepalive:
            self._keepalive.cancel()
        self._keepalive = asyncio.create_task(self._ping())
        log.info("ElevenLabs connected (voice %s)", self.voice_id)

    async def _ping(self):
        """ElevenLabs drops the socket after ~20s of silence, and the next
        sentence then pays a visible reconnect. A single space every 15s
        keeps the line warm without generating any audio."""
        try:
            while True:
                await asyncio.sleep(15)
                if self.ws:
                    await self.ws.send(json.dumps({"text": " "}))
        except (asyncio.CancelledError, websockets.ConnectionClosed):
            pass

    async def say(self, text: str):
        if not self.ws:
            await self.start()
        try:
            await self.ws.send(json.dumps({"text": text + " ", "try_trigger_generation": True}))
        except websockets.ConnectionClosed:
            log.warning("ElevenLabs dropped - reconnecting")
            await self.start()
            await self.ws.send(json.dumps({"text": text + " "}))

    async def flush(self):
        if self.ws:
            await self.ws.send(json.dumps({"text": ""}))

    async def interrupt(self):
        """Barge-in. Tear the socket down and rebuild - it is the only reliable
        way to guarantee no queued audio leaks out after she's been cut off."""
        self._generation += 1
        if self._reader:
            self._reader.cancel()
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass
        self.ws = None
        await self.start()

    async def _read(self):
        gen = self._generation
        try:
            async for raw in self.ws:
                if gen != self._generation:
                    return
                msg = json.loads(raw)
                if msg.get("audio"):
                    await self.on_audio(base64.b64decode(msg["audio"]))
                elif msg.get("isFinal"):
                    pass
        except (websockets.ConnectionClosed, asyncio.CancelledError):
            pass
        except Exception:
            log.exception("ElevenLabs reader died")

    async def close(self):
        if self._reader:
            self._reader.cancel()
        if self._keepalive:
            self._keepalive.cancel()
        if self.ws:
            await self.ws.close()


async def mic_stream(device: int | None = None) -> AsyncIterator[bytes]:
    """PCM16 @16k from the beamforming array. Customer is 3-6ft away, so the
    array's own DSP is doing the heavy lifting - do not add gain here.

    MIC_GAIN (env, default 1.0) exists for laptop testing with quiet desk
    mics only. Leave it unset in production with the array."""
    import sounddevice as sd

    gain = float(os.getenv("MIC_GAIN", "1.0"))
    q: asyncio.Queue[bytes] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def cb(indata, frames, time_info, status):
        if status:
            log.debug("mic status: %s", status)
        pcm = bytes(indata)
        if gain != 1.0:
            import numpy as np
            x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) * gain
            pcm = np.clip(x, -32768, 32767).astype(np.int16).tobytes()
        loop.call_soon_threadsafe(q.put_nowait, pcm)

    with sd.RawInputStream(
        samplerate=SAMPLE_RATE, blocksize=1600, device=device,
        dtype="int16", channels=1, callback=cb,
    ):
        while True:
            yield await q.get()
