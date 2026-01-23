from __future__ import annotations

import asyncio
import traceback
from dataclasses import dataclass
from logging import getLogger
from typing import AsyncIterator, Optional

from google.cloud import speech

from config import AUDIO_SAMPLE_RATE
from lib.stt_provider import RealtimeSttProvider, TranscriptEvent

logger = getLogger(__name__)


@dataclass(frozen=True)
class GoogleSttConfig:
    language_code: str = "cs-CZ"


class GoogleRealtimeProvider(RealtimeSttProvider):
    """
    Google Cloud Speech-to-Text v1 streaming adapter.

    Library: google-cloud-speech==2.36.0
    Uses: SpeechClient.streaming_recognize(streaming_config, requests)
    """

    def __init__(self, cfg: Optional[GoogleSttConfig] = None) -> None:
        self._cfg = cfg or GoogleSttConfig()
        self._audio_q: asyncio.Queue[Optional[bytes]] = asyncio.Queue(maxsize=400)
        self._events_q: asyncio.Queue[Optional[TranscriptEvent]] = asyncio.Queue(maxsize=200)
        self._closed = asyncio.Event()
        self._thread_task: Optional[asyncio.Task] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    async def __aenter__(self) -> "GoogleRealtimeProvider":
        self._loop = asyncio.get_running_loop()
        self._thread_task = asyncio.create_task(self._run_stream_in_thread())
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        try:
            await self.end_audio()
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
        loop = self._loop
        if loop is None:
            raise RuntimeError("GoogleRealtimeProvider: event loop not set")

        client = speech.SpeechClient()

        # IntelliJ sometimes warns these constructors want dict; that's just stub noise.
        config = speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,  # type: ignore[arg-type]
            sample_rate_hertz=AUDIO_SAMPLE_RATE,  # type: ignore[arg-type]
            language_code=self._cfg.language_code,  # type: ignore[arg-type]
        )
        streaming_config = speech.StreamingRecognitionConfig(
            config=config,    # type: ignore[arg-type]
            interim_results=True,    # type: ignore[arg-type]
        )

        def request_iter():
            # With google-cloud-speech 2.36.0, streaming_config is passed as the first
            # argument to streaming_recognize(), and requests should contain audio only.
            while True:
                chunk = asyncio.run_coroutine_threadsafe(self._audio_q.get(), loop).result()
                if chunk is None:
                    break
                yield speech.StreamingRecognizeRequest(audio_content=chunk)  # type: ignore[arg-type]

        try:
            # Signature in your env is (config, requests, ...) => pass both positionally.
            responses = client.streaming_recognize(streaming_config, request_iter())   # type: ignore[arg-type]

            for resp in responses:
                # logger.debug("Received google streaming response ...")

                # resp.results is a repeated field; iterate it.
                for result in getattr(resp, "results", ()):
                    # print("RESULT:", result)

                    text = (result.alternatives[0].transcript or "").strip()
                    if not text:
                        continue

                    # Treat is_final as "committed"
                    if bool(getattr(result, "is_final", False)):
                        logger.info("FINAL: %s", text)
                        asyncio.run_coroutine_threadsafe(
                            self._events_q.put(TranscriptEvent(text=text, is_final=True)),
                            loop,
                        ).result()

        except Exception as e:
            logger.exception("[STT] Google streaming crashed: %r", e)
            traceback.print_exc()
        finally:
            # Stop the async iterator and mark closed.
            asyncio.run_coroutine_threadsafe(self._events_q.put(None), loop).result()
            self._closed.set()
