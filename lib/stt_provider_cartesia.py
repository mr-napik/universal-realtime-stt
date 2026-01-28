from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from logging import getLogger
from typing import AsyncIterator, Optional
from urllib.parse import urlencode

from websockets import connect, ConnectionClosedOK, ConnectionClosed

from config import AUDIO_SAMPLE_RATE, AUDIO_ENCODING, STT_LANGUAGE, STT_VAD_SILENCE_THRESHOLD_S, STT_VAD_THRESHOLD
from lib.stt_provider import RealtimeSttProvider, TranscriptEvent

logger = getLogger(__name__)


@dataclass(frozen=True)
class CartesiaSttConfig:
    """
    Configuration for Cartesia Ink-Whisper realtime STT provider.

    Provider-specific settings have defaults appropriate for Cartesia.
    Universal STT settings (sample_rate, language, VAD params) are imported
    from config.py but can be overridden here if needed.
    """
    api_key: str  # Required: passed at instantiation, not stored in config

    # Provider-specific settings
    model: str = "ink-whisper"
    base_url: str = "wss://api.cartesia.ai/stt/websocket"

    # Universal STT settings (defaults from config.py, can be overridden)
    language: str = STT_LANGUAGE  # ISO-639-1
    encoding: str = AUDIO_ENCODING
    sample_rate: int = AUDIO_SAMPLE_RATE
    min_volume: float = 0.15            # VAD threshold (0..1)
    # @TODO: this does not work, nothing is heard: min_volume: float = STT_VAD_THRESHOLD  # VAD threshold (0..1), maps to Cartesia's min_volume
    max_silence_duration_secs: float = STT_VAD_SILENCE_THRESHOLD_S  # endpointing / utterance boundary


class CartesiaInkProvider(RealtimeSttProvider):
    """
    Cartesia streaming STT over WebSocket (Ink-Whisper).

    Protocol (docs):
      - Connect to wss://api.cartesia.ai/stt/websocket with query params
      - Send binary websocket messages containing raw audio
      - Send text command 'done' to flush and close
      - Receive JSON messages: type=transcript/flush_done/done/error
    """
    def __init__(self, cfg: CartesiaSttConfig) -> None:
        self._cfg = cfg
        self._ws = None
        self._events_q: asyncio.Queue[Optional[TranscriptEvent]] = asyncio.Queue(maxsize=200)
        self._rx_task: Optional[asyncio.Task] = None
        self._closed = asyncio.Event()
        self._error: Optional[Exception] = None  # Store error for propagation

    async def __aenter__(self) -> "CartesiaInkProvider":
        # Query params required by Cartesia streaming STT endpoint. :contentReference[oaicite:2]{index=2}
        qs = urlencode({
            "model": self._cfg.model,
            "language": self._cfg.language,
            "encoding": self._cfg.encoding,
            "sample_rate": str(self._cfg.sample_rate),
            "min_volume": str(self._cfg.min_volume),
            "max_silence_duration_secs": str(self._cfg.max_silence_duration_secs),
        })
        url = f"{self._cfg.base_url}?{qs}"

        # Auth: Cartesia requires X-API-Key and Cartesia-Version headers.
        self._ws = await connect(
            url,
            additional_headers={
                "X-API-Key": self._cfg.api_key,
                "Cartesia-Version": "2025-04-16",
            },
            ping_interval=10,
            ping_timeout=10,
            close_timeout=5,
            max_queue=1024,  # larger queue to handle Cartesia's frequent interim transcripts
        )

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
            # Always terminate events() iterator
            try:
                await self._events_q.put(None)
            except Exception:
                pass
            self._ws = None
            self._rx_task = None

    async def send_audio(self, pcm_chunk: bytes) -> None:
        # Cartesia expects raw binary audio frames. :contentReference[oaicite:4]{index=4}
        if self._error:
            raise self._error
        if self._closed.is_set() or self._ws is None:
            logger.warning("[STT] Cartesia: cannot send audio, connection closed")
            return
        try:
            await self._ws.send(pcm_chunk)
        except ConnectionClosed:
            logger.warning("[STT] Cartesia: connection closed while sending audio")
            self._closed.set()

    async def end_audio(self) -> None:
        # 'done' flushes remaining audio and closes session. :contentReference[oaicite:5]{index=5}
        try:
            await self._ws.send("done")
        except Exception:
            pass

    def events(self) -> AsyncIterator[TranscriptEvent]:
        async def _aiter() -> AsyncIterator[TranscriptEvent]:
            while True:
                ev = await self._events_q.get()
                if ev is None:
                    # Check if we stopped due to an error
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
        try:
            while not self._closed.is_set():
                msg = await self._ws.recv()
                logger.debug("[STT] Cartesia: received message: %r", msg)

                # Cartesia sends JSON text messages for transcripts / acknowledgements. :contentReference[oaicite:6]{index=6}
                if isinstance(msg, bytes):
                    # Unexpected; ignore.
                    logger.warning("[STT] Cartesia: received unexpected message: %r", msg)
                    continue

                data = json.loads(msg)
                typ = data.get("type")

                if typ == "transcript":
                    text = (data.get("text") or "").strip()
                    is_final = bool(data.get("is_final", False))
                    if is_final and text:
                        await self._events_q.put(TranscriptEvent(text=text, is_final=True))
                    else:
                        logger.debug("[STT] Cartesia: type %s, text %s", typ, text)
                    continue

                if typ in ("flush_done",):
                    logger.info("[STT] Cartesia: received flush_done")
                    continue

                if typ == "done":
                    logger.info("[STT] Cartesia: received done")
                    break

                # Error format: { "type": "error", "message": "<string>", "code": <int> }
                if typ == "error":
                    error_msg = data.get("message", str(data))
                    error_code = int(data.get("code", 0))
                    self._error = RuntimeError(f"Cartesia STT error (code {error_code}): {error_msg}")
                    logger.error("[STT] Cartesia: %s", self._error)
                    raise self._error

        except ConnectionClosedOK:
            logger.debug("[STT] Cartesia: session closed cleanly.")
        except ConnectionClosed as e:
            logger.warning("[STT] Cartesia: connection closed unexpectedly: %s", e)
            if not self._error:
                self._error = RuntimeError(f"Cartesia connection closed unexpectedly: {e}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("[STT] Cartesia receiver crashed: %r", e)
            if not self._error:
                self._error = e
        finally:
            self._closed.set()
            await self._events_q.put(None)
