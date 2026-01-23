from __future__ import annotations

import asyncio
from dataclasses import dataclass
from logging import getLogger
from typing import AsyncIterator, Optional

from config import AUDIO_SAMPLE_RATE
from lib.stt_provider import RealtimeSttProvider, TranscriptEvent
from google.cloud import speech

logger = getLogger(__name__)


@dataclass(frozen=True)
class GoogleSttConfig:
    language_code: str = "cs-CZ"


class GoogleRealtimeProvider(RealtimeSttProvider):
    """
    Google Cloud Speech-to-Text v1 streaming adapter.

    Dependency:
      pip install google-cloud-speech

    Auth:
      export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service_account.json
    """

    def __init__(self, cfg: Optional[GoogleSttConfig] = None) -> None:
        self._cfg = cfg or GoogleSttConfig()
        self._audio_q: asyncio.Queue[Optional[bytes]] = asyncio.Queue(maxsize=400)
        self._events_q: asyncio.Queue[TranscriptEvent] = asyncio.Queue(maxsize=200)
        self._closed = asyncio.Event()
        self._thread_task: Optional[asyncio.Task] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    async def __aenter__(self) -> "GoogleRealtimeProvider":
        self._loop = asyncio.get_running_loop()  # NEW (main loop link)
        self._thread_task = asyncio.create_task(self._run_stream_in_thread())
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        try:
            await self.end_audio()
            self._closed.set()
            if self._thread_task:
                await self._thread_task
        finally:
            self._thread_task = None

    async def send_audio(self, pcm_chunk: bytes) -> None:
        await self._audio_q.put(pcm_chunk)

    async def end_audio(self) -> None:
        # Signal request generator to finish
        await self._audio_q.put(None)

    def events(self) -> AsyncIterator[TranscriptEvent]:
        async def _aiter() -> AsyncIterator[TranscriptEvent]:
            while not self._closed.is_set():
                ev = await self._events_q.get()
                yield ev
        return _aiter()

    async def _run_stream_in_thread(self) -> None:
        await asyncio.to_thread(self._blocking_stream_loop)

    def _blocking_stream_loop(self) -> None:
        client = speech.SpeechClient()

        config = speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
            sample_rate_hertz=AUDIO_SAMPLE_RATE,
            language_code=self._cfg.language_code,
        )
        streaming_config = speech.StreamingRecognitionConfig(
            config=config,
            interim_results=True,
        )

        loop = self._loop  # NEW

        def request_iter():
            # first request must contain streaming_config (per API contract) :contentReference[oaicite:4]{index=4}
            yield speech.StreamingRecognizeRequest(streaming_config=streaming_config)
            while True:
                chunk = asyncio.run_coroutine_threadsafe(self._audio_q.get(), loop).result()
                if chunk is None:
                    break
                yield speech.StreamingRecognizeRequest(audio_content=chunk)

        try:
            responses = client.streaming_recognize(requests=request_iter())
            for resp in responses:
                for result in resp.results:
                    # Google results can contain multiple alternatives; take the top.
                    if not result.alternatives:
                        continue
                    text = (result.alternatives[0].transcript or "").strip()
                    if not text:
                        continue

                    # Treat "is_final" as your "committed"
                    if result.is_final:
                        asyncio.run_coroutine_threadsafe(
                            self._events_q.put(TranscriptEvent(text=text, is_final=True)),
                            loop,
                        ).result()

        except Exception as e:
            logger.warning("[STT] Google streaming crashed: %r", e)
        finally:
            asyncio.run_coroutine_threadsafe(self._audio_q.put(None), loop)
            self._closed.set()
