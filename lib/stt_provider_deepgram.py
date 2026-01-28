from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from logging import getLogger
from typing import AsyncIterator, Optional
from urllib.parse import urlencode

from websockets import connect, ConnectionClosedOK

from config import AUDIO_SAMPLE_RATE, AUDIO_CHANNELS, STT_VAD_SILENCE_THRESHOLD_S, STT_LANGUAGE_BCP_47
from lib.stt_provider import RealtimeSttProvider, TranscriptEvent

logger = getLogger(__name__)


@dataclass(frozen=True)
class DeepgramSttConfig:
    api_key: str
    # Deepgram Live Audio endpoint
    base_url: str = "wss://api.deepgram.com/v1/listen"

    # Query params (keep minimal; extend later if needed)
    model: str = "nova-2"                   # change if you use nova-3 / flux etc.
    language: str = STT_LANGUAGE_BCP_47     # Deepgram expects BCP-47; "cs" is fine for Czech
    punctuate: bool = True
    smart_format: bool = True
    interim_results: bool = True  # False if we only want final/committed for your pipeline

    # Raw PCM settings (since you send headerless PCM)
    encoding: str = "linear16"     # Deepgram docs: encoding required for raw packets :contentReference[oaicite:1]{index=1}
    sample_rate: int = AUDIO_SAMPLE_RATE
    channels: int = AUDIO_CHANNELS

    # Endpointing / silence handling (optional, but useful for “commits”)
    # endpointing is milliseconds as string in Deepgram query (or false)
    endpointing_ms: int = int(STT_VAD_SILENCE_THRESHOLD_S * 1000)


class DeepgramRealtimeProvider(RealtimeSttProvider):
    """
    Deepgram Live Audio WebSocket streaming provider.

    - Sends binary audio frames (raw PCM).
    - Sends {"type":"Finalize"} to flush.
    - Emits TranscriptEvent only when Deepgram marks result as final.
    """

    def __init__(self, cfg: DeepgramSttConfig) -> None:
        self._cfg = cfg
        self._ws = None
        self._events_q: asyncio.Queue[Optional[TranscriptEvent]] = asyncio.Queue(maxsize=200)
        self._rx_task: Optional[asyncio.Task] = None
        self._closed = asyncio.Event()

    async def __aenter__(self) -> "DeepgramRealtimeProvider":
        qs = urlencode(
            {
                "model": self._cfg.model,
                "language": self._cfg.language,
                "encoding": self._cfg.encoding,
                "sample_rate": str(self._cfg.sample_rate),
                "channels": str(self._cfg.channels),
                "punctuate": str(self._cfg.punctuate).lower(),
                "smart_format": str(self._cfg.smart_format).lower(),
                "interim_results": str(self._cfg.interim_results).lower(),
                "endpointing": str(self._cfg.endpointing_ms),
            }
        )
        url = f"{self._cfg.base_url}?{qs}"

        # Auth header per docs: "Authorization: Token <key>" :contentReference[oaicite:2]{index=2}
        self._ws = await connect(
            url,
            additional_headers={"Authorization": f"Token {self._cfg.api_key}"},
            ping_interval=10,
            ping_timeout=10,
            close_timeout=5,
            max_queue=32,
        )

        self._rx_task = asyncio.create_task(self._recv_loop())
        logger.info("[STT] Deepgram connected.")
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
            # Always terminate iterator
            try:
                await self._events_q.put(None)
            except Exception:
                pass
            self._ws = None
            self._rx_task = None

    async def send_audio(self, pcm_chunk: bytes) -> None:
        # Deepgram expects binary frames for audio. :contentReference[oaicite:3]{index=3}
        await self._ws.send(pcm_chunk)

    async def end_audio(self) -> None:
        """
        Flush the stream. Deepgram recommends sending a Finalize message. :contentReference[oaicite:4]{index=4}
        """
        try:
            await self._ws.send(json.dumps({"type": "Finalize"}))
        except Exception:
            pass

        # Optionally close the stream explicitly.
        try:
            await self._ws.send(json.dumps({"type": "CloseStream"}))
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
        """
        Receives Deepgram JSON messages.
        We only emit committed/final transcripts when message type is Results and is_final is true.
        """
        try:
            while not self._closed.is_set():
                msg = await self._ws.recv()

                if isinstance(msg, (bytes, bytearray)):
                    # Deepgram sends JSON text; ignore unexpected bytes.
                    continue

                data = json.loads(msg)
                typ = data.get("type")

                if typ == "Results":
                    if not data.get("is_final", False):
                        logger.debug("[STT] Deepgram intermediate transcript: %r", data)
                        continue

                    # Transcript lives here: channel.alternatives[0].transcript :contentReference[oaicite:5]{index=5}
                    channel = data.get("channel") or {}
                    alts = channel.get("alternatives") or []
                    if not alts:
                        continue

                    text = (alts[0].get("transcript") or "").strip()
                    if text:
                        await self._events_q.put(TranscriptEvent(text=text, is_final=True))
                    continue

                if typ in ("Metadata", "UtteranceEnd", "SpeechStarted"):
                    # Useful for debugging; ignore for now.
                    continue

                # Deepgram errors typically come as {"type":"Error", ...} or {"error": "..."}
                if typ == "Error" or "error" in data:
                    raise RuntimeError(f"Deepgram STT error: {data}")

        except ConnectionClosedOK:
            logger.info("[STT] Deepgram closed.")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("[STT] Deepgram receiver crashed: %r", e)
        finally:
            self._closed.set()
            await self._events_q.put(None)
