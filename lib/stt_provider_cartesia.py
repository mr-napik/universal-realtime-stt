from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from logging import getLogger
from typing import AsyncIterator, Optional
from urllib.parse import urlencode

from websockets import connect, ConnectionClosedOK

from config import AUDIO_SAMPLE_RATE, STT_VAD_SILENCE_THRESHOLD_S
from lib.stt_provider import RealtimeSttProvider, TranscriptEvent

logger = getLogger(__name__)


@dataclass(frozen=True)
class CartesiaSttConfig:
    api_key: str
    model: str = "ink-whisper"          # per Cartesia docs
    language: str = "cs"                # ISO-639-1 (Cartesia expects "cs", not "cs-CZ")
    encoding: str = "pcm_s16le"         # recommended by Cartesia docs
    sample_rate: int = AUDIO_SAMPLE_RATE
    min_volume: float = 0.15            # VAD threshold (0..1) @TODO: test STT_VAD_THRESHOLD
    max_silence_duration_secs: float = STT_VAD_SILENCE_THRESHOLD_S  # endpointing / utterance boundary
    base_url: str = "wss://api.cartesia.ai/stt/websocket"


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

        # Auth: Cartesia supports X-API-Key header. :contentReference[oaicite:3]{index=3}
        self._ws = await connect(
            url,
            additional_headers={"X-API-Key": self._cfg.api_key},
            ping_interval=10,
            ping_timeout=10,
            close_timeout=5,
            max_queue=32,
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
        await self._ws.send(pcm_chunk)

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
                    break
                yield ev
        return _aiter()

    async def _recv_loop(self) -> None:
        try:
            while not self._closed.is_set():
                msg = await self._ws.recv()

                # Cartesia sends JSON text messages for transcripts / acknowledgements. :contentReference[oaicite:6]{index=6}
                if isinstance(msg, bytes):
                    # Unexpected; ignore.
                    continue

                data = json.loads(msg)
                typ = data.get("type")

                if typ == "transcript":
                    text = (data.get("text") or "").strip()
                    is_final = bool(data.get("is_final", False))
                    if is_final and text:
                        await self._events_q.put(TranscriptEvent(text=text, is_final=True))
                    continue

                if typ in ("flush_done",):
                    continue

                if typ == "done":
                    break

                # Error format: { "type": "<string>", "error": "<string>", ... } :contentReference[oaicite:7]{index=7}
                if "error" in data:
                    raise RuntimeError(f"Cartesia STT error: {data}")

        except ConnectionClosedOK:
            pass
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("[STT] Cartesia receiver crashed: %r", e)
        finally:
            self._closed.set()
            await self._events_q.put(None)
