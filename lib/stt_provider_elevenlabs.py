from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from json import loads, dumps
from logging import getLogger
from typing import AsyncIterator, Optional
from urllib.parse import urlencode

from websockets import connect, ConnectionClosedOK, ConnectionClosed

from config import (
    AUDIO_SAMPLE_RATE,
    STT_LANGUAGE_ISO_639_1,
    STT_VAD_SILENCE_THRESHOLD_S,
    STT_MIN_SILENCE_DURATION_MS,
    STT_MIN_SPEECH_DURATION_MS,
    STT_VAD_THRESHOLD,
)
from lib.stt_provider import RealtimeSttProvider, TranscriptEvent


logger = getLogger(__name__)


# ElevenLabs message types
STT_MSG_SESSION_STARTED = "session_started"
STT_MSG_PARTIAL_TRANSCRIPT = "partial_transcript"
STT_MSG_COMMITTED_TRANSCRIPT = "committed_transcript"
STT_MSG_COMMITTED_TRANSCRIPT_TS = "committed_transcript_with_timestamps"
STT_ERROR_TYPES = frozenset({
    "scribeError",
    "scribeAuthError",
    "scribeQuotaExceededError",
    "queue_overflow",
})


@dataclass(frozen=True)
class ElevenLabsSttConfig:
    """
    Configuration for ElevenLabs realtime STT provider.

    Provider-specific settings have defaults appropriate for ElevenLabs.
    Universal STT settings (sample_rate, language, VAD params) are imported
    from config.py but can be overridden here if needed.
    """
    api_key: str  # Required: passed at instantiation, not stored in config

    # Provider-specific settings
    model: str = "scribe_v2_realtime"
    base_url: str = "wss://api.elevenlabs.io/v1/speech-to-text/realtime"
    commit_strategy: str = "vad"

    # Universal STT settings (defaults from config.py, can be overridden)
    sample_rate: int = AUDIO_SAMPLE_RATE
    language: str = STT_LANGUAGE_ISO_639_1
    vad_silence_threshold_s: float = STT_VAD_SILENCE_THRESHOLD_S
    vad_threshold: float = STT_VAD_THRESHOLD
    min_silence_duration_ms: int = STT_MIN_SILENCE_DURATION_MS
    min_speech_duration_ms: int = STT_MIN_SPEECH_DURATION_MS


class ElevenLabsRealtimeProvider(RealtimeSttProvider):
    """
    ElevenLabs streaming STT over WebSocket.

    Protocol:
      - Connect to wss://api.elevenlabs.io/v1/speech-to-text/realtime with query params
      - Send JSON messages with base64-encoded audio chunks
      - Receive JSON messages: session_started, partial_transcript, committed_transcript
    """

    def __init__(self, cfg: ElevenLabsSttConfig) -> None:
        self._cfg = cfg
        self._ws = None
        self._events_q: asyncio.Queue[Optional[TranscriptEvent]] = asyncio.Queue(maxsize=200)
        self._rx_task: Optional[asyncio.Task] = None
        self._closed = asyncio.Event()
        self._error: Optional[Exception] = None

    def _build_url(self) -> str:
        """Build WebSocket URL with query parameters."""
        params = {
            "model_id": self._cfg.model,
            "audio_format": f"pcm_{self._cfg.sample_rate}",
            "commit_strategy": self._cfg.commit_strategy,
            "language_code": self._cfg.language,
            "vad_silence_threshold_secs": str(self._cfg.vad_silence_threshold_s),
            "vad_threshold": str(self._cfg.vad_threshold),
            "min_silence_duration_ms": str(self._cfg.min_silence_duration_ms),
            "min_speech_duration_ms": str(self._cfg.min_speech_duration_ms),
        }
        return f"{self._cfg.base_url}?{urlencode(params)}"

    async def __aenter__(self) -> "ElevenLabsRealtimeProvider":
        """
        Connect and start STT session.

        Note: The WebSocket connection will automatically close after 20 seconds of inactivity.
        To keep the connection open, you can send a single space character " ".

        Please note that this string MUST INCLUDE A SPACE,
        as sending a fully empty string, "", will close the WebSocket.
        Elevenlabs doc: https://elevenlabs.io/docs/developers/websockets#tips

        @TODO: Send previous text as context
            Sending previous_text context is only possible when sending the first audio chunk via connection.send().
            Sending it in subsequent chunks will result in an error.
            Previous text works best when it’s under 50 characters long.
            https://elevenlabs.io/docs/developers/guides/cookbooks/speech-to-text/realtime/transcripts-and-commit-strategies#sending-previous-text-context
        """
        url = self._build_url()
        headers = {"xi-api-key": self._cfg.api_key}

        self._ws = await connect(
            url,
            additional_headers=headers,
            ping_interval=10,
            ping_timeout=10,
            close_timeout=5,
            max_queue=32,
        )

        # Verify handshake
        first_msg = await self._ws.recv()
        data = loads(first_msg)
        if data.get("message_type") != STT_MSG_SESSION_STARTED:
            raise RuntimeError(f"STT session did not start correctly: {data}")
        logger.info("[STT] ElevenLabs: session started.")

        self._rx_task = asyncio.create_task(self._recv_loop())
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        try:
            self._closed.set()
            if self._rx_task:
                self._rx_task.cancel()
            if self._ws:
                try:
                    await self._ws.close()
                except Exception:
                    pass
        finally:
            try:
                await self._events_q.put(None)
            except Exception:
                pass
            self._ws = None
            self._rx_task = None

    async def send_audio(self, pcm_chunk: bytes) -> None:
        """Send audio chunk to ElevenLabs (base64-encoded in JSON)."""
        if self._error:
            raise self._error
        if self._closed.is_set() or self._ws is None:
            logger.warning("[STT] ElevenLabs: cannot send audio, connection closed")
            return
        try:
            payload = {
                "message_type": "input_audio_chunk",
                "audio_base_64": base64.b64encode(pcm_chunk).decode("ascii"),
                "sample_rate": self._cfg.sample_rate,
            }
            await self._ws.send(dumps(payload))
        except ConnectionClosed:
            logger.warning("[STT] ElevenLabs: connection closed while sending audio")
            self._closed.set()

    async def end_audio(self) -> None:
        """Signal end of audio stream."""
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass

    def events(self) -> AsyncIterator[TranscriptEvent]:
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

    async def _recv_loop(self) -> None:
        """Background task receiving messages from WebSocket."""
        try:
            while not self._closed.is_set():
                reply = await self._ws.recv()
                data = loads(reply)
                msg_type = data.get("message_type")

                if msg_type == STT_MSG_PARTIAL_TRANSCRIPT:
                    continue

                if msg_type in (STT_MSG_COMMITTED_TRANSCRIPT, STT_MSG_COMMITTED_TRANSCRIPT_TS):
                    text = data.get("text", "").strip()
                    if text:
                        logger.debug("[STT] ElevenLabs: committed transcript: %s", text[:50])
                        await self._events_q.put(TranscriptEvent(text=text, is_final=True))
                    continue

                if msg_type in STT_ERROR_TYPES:
                    error_msg = data.get("message", str(data))
                    self._error = RuntimeError(f"ElevenLabs STT error ({msg_type}): {error_msg}")
                    logger.error("[STT] ElevenLabs: %s", self._error)
                    raise self._error

        except ConnectionClosedOK:
            logger.debug("[STT] ElevenLabs: session closed cleanly.")
        except ConnectionClosed as e:
            logger.warning("[STT] ElevenLabs: connection closed unexpectedly: %s", e)
            if not self._error:
                self._error = RuntimeError(f"ElevenLabs connection closed unexpectedly: {e}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("[STT] ElevenLabs receiver crashed: %r", e)
            if not self._error:
                self._error = e
        finally:
            self._closed.set()
            await self._events_q.put(None)