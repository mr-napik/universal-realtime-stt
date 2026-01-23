from __future__ import annotations

import asyncio
import traceback
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
        self._events_q: asyncio.Queue[Optional[TranscriptEvent]] = asyncio.Queue(maxsize=200)
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

    def events(self) -> AsyncIterator[TranscriptEvent | None]:
        async def _aiter() -> AsyncIterator[TranscriptEvent]:
            while True:
                ev = await self._events_q.get()
                if ev is None:
                    break
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
            while True:
                chunk = asyncio.run_coroutine_threadsafe(self._audio_q.get(), loop).result()
                if chunk is None:
                    break
                yield speech.StreamingRecognizeRequest(audio_content=chunk)

        try:
            responses = client.streaming_recognize(streaming_config, request_iter())
            for resp in responses:
                print(resp)
                result = getattr(resp, "results", None)
                if not result:
                    continue

                # Google results can contain multiple alternatives; take the top.
                # result is a StreamingRecognitionResult
                if not getattr(result, "alternatives", None):
                    continue

                alt0 = result.alternatives[0]
                text = (alt0.transcript or "").strip()

                if not text:
                    continue

                # Treat "is_final" as your "committed"
                if bool(getattr(result, "is_final", False)):
                    asyncio.run_coroutine_threadsafe(
                        self._events_q.put(TranscriptEvent(text=text, is_final=True)),
                        loop,
                    ).result()

        except Exception as e:
            logger.exception("[STT] Google streaming crashed: %r", e)
            traceback.print_exc()
        finally:
            # send task end sentinels
            asyncio.run_coroutine_threadsafe(self._audio_q.put(None), loop)
            asyncio.run_coroutine_threadsafe(self._events_q.put(None), loop)
            self._closed.set()
