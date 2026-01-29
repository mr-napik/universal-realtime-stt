from __future__ import annotations

import asyncio
import traceback
from dataclasses import dataclass
from logging import getLogger
from typing import AsyncIterator, Optional

from google.cloud import speech

from config import AUDIO_SAMPLE_RATE, STT_LANGUAGE_BCP_47
from lib.stt_provider import RealtimeSttProvider, TranscriptEvent

logger = getLogger(__name__)


@dataclass(frozen=True)
class GoogleSttConfig:
    """
    Configuration for Google Cloud Speech-to-Text realtime provider.

    Note: Google uses Application Default Credentials (ADC) for authentication,
    not an API key. Set GOOGLE_APPLICATION_CREDENTIALS environment variable
    to point to your service account JSON file.

    Provider-specific settings have defaults appropriate for Google.
    Universal STT settings are imported from config.py but can be overridden.
    """
    # Provider-specific settings
    encoding: speech.RecognitionConfig.AudioEncoding = speech.RecognitionConfig.AudioEncoding.LINEAR16
    interim_results: bool = True  # Whether to return interim (non-final) results

    # Universal STT settings (defaults from config.py, can be overridden)
    # Note: Google expects BCP-47 language code (e.g., "cs-CZ")
    language: str = STT_LANGUAGE_BCP_47
    sample_rate: int = AUDIO_SAMPLE_RATE


class GoogleRealtimeProvider(RealtimeSttProvider):
    """
    Google Cloud Speech-to-Text v1 streaming adapter.

    Library: google-cloud-speech
    Uses: SpeechClient.streaming_recognize(streaming_config, requests)

    Authentication: Uses Application Default Credentials (ADC).
    Set GOOGLE_APPLICATION_CREDENTIALS env var to your service account JSON.
    """

    def __init__(self, cfg: Optional[GoogleSttConfig] = None) -> None:
        self._cfg = cfg or GoogleSttConfig()
        self._audio_q: asyncio.Queue[Optional[bytes]] = asyncio.Queue(maxsize=400)
        self._events_q: asyncio.Queue[Optional[TranscriptEvent]] = asyncio.Queue(maxsize=200)
        self._closed = asyncio.Event()
        self._error: Optional[Exception] = None
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
        """Send audio chunk to Google Speech-to-Text."""
        if self._error:
            raise self._error
        if self._closed.is_set():
            logger.warning("[STT] Google: cannot send audio, connection closed")
            return
        await self._audio_q.put(pcm_chunk)

    async def end_audio(self) -> None:
        """Signal end of audio stream."""
        await self._audio_q.put(None)

    def events(self) -> AsyncIterator[TranscriptEvent | None]:
        """Async iterator yielding transcript events."""
        async def _aiter() -> AsyncIterator[TranscriptEvent]:
            while True:
                ev = await self._events_q.get()
                if ev is None:
                    if self._error:
                        raise self._error
                    break
                yield ev
        return _aiter()

    @property
    def error(self) -> Optional[Exception]:
        """Return the error that caused the connection to close, if any."""
        return self._error

    async def _run_stream_in_thread(self) -> None:
        await asyncio.to_thread(self._blocking_stream_loop)

    def _blocking_stream_loop(self) -> None:
        loop = self._loop
        if loop is None:
            raise RuntimeError("GoogleRealtimeProvider: event loop not set")

        client = speech.SpeechClient()

        # IntelliJ sometimes warns these constructors want dict; that's just stub noise.
        config = speech.RecognitionConfig(
            encoding=self._cfg.encoding,  # type: ignore[arg-type]
            sample_rate_hertz=self._cfg.sample_rate,  # type: ignore[arg-type]
            language_code=self._cfg.language,  # type: ignore[arg-type]
        )
        streaming_config = speech.StreamingRecognitionConfig(
            config=config,  # type: ignore[arg-type]
            interim_results=self._cfg.interim_results,  # type: ignore[arg-type]
        )

        def request_iter():
            while True:
                chunk = asyncio.run_coroutine_threadsafe(self._audio_q.get(), loop).result()
                if chunk is None:
                    break
                yield speech.StreamingRecognizeRequest(audio_content=chunk)  # type: ignore[arg-type]

        try:
            # This is iterator that feeds responses
            responses = client.streaming_recognize(streaming_config, request_iter())  # type: ignore[arg-type]

            for resp in responses:
                # Note: Google is very verbose sending partial response after every
                # submitted chunk. So we do not display them by default as it creates a LOT of debug.
                # logger.debug("[STT] Google response:\n%r", resp)

                # resp.results is a repeated field; iterate it.
                for result in getattr(resp, "results", ()):
                    text = (result.alternatives[0].transcript or "").strip()
                    if not text:
                        continue

                    # Treat is_final as "committed"
                    if bool(getattr(result, "is_final", False)):
                        logger.debug("[STT] Google: final transcript: %s", text[:50])
                        asyncio.run_coroutine_threadsafe(
                            self._events_q.put(TranscriptEvent(text=text, is_final=True)),
                            loop,
                        ).result()

        except Exception as e:
            logger.exception("[STT] Google streaming crashed: %r", e)
            traceback.print_exc()
            self._error = e
        finally:
            # Stop the async iterator and mark closed.
            asyncio.run_coroutine_threadsafe(self._events_q.put(None), loop).result()
            self._closed.set()
