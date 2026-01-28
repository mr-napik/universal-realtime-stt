from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from logging import getLogger
from typing import AsyncIterator, Optional
from urllib.parse import urlencode

from websockets import connect, ConnectionClosedOK, ConnectionClosed

from config import AUDIO_SAMPLE_RATE, AUDIO_CHANNELS, STT_VAD_SILENCE_THRESHOLD_S, STT_LANGUAGE_ISO_639_1
from lib.stt_provider import RealtimeSttProvider, TranscriptEvent

logger = getLogger(__name__)


@dataclass(frozen=True)
class DeepgramSttConfig:
    """
    @TODO: Explore more features to enable:
    https://developers.deepgram.com/docs/stt-streaming-feature-overview
    """

    api_key: str
    # Deepgram Live Audio endpoint
    base_url: str = "wss://api.deepgram.com/v1/listen"

    # Query params (keep minimal; extend later if needed)
    model: str = "nova-3"                   # change if you use nova-3 / flux etc.
    language: str = STT_LANGUAGE_ISO_639_1
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
        self._error: Optional[Exception] = None

    async def __aenter__(self) -> "DeepgramRealtimeProvider":
        # Validate API key
        if not self._cfg.api_key:
            raise ValueError("Deepgram API key is required")

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
        logger.debug("[STT] Deepgram: connecting to %s", url)

        # Connect with timeout to avoid hanging indefinitely
        # Use additional_headers with lowercase 'token' per Deepgram examples
        # See: https://deepgram.com/learn/how-to-easily-debug-live-stream-requests
        try:
            self._ws = await asyncio.wait_for(
                connect(
                    url,
                    additional_headers={"Authorization": f"token {self._cfg.api_key}"},
                    open_timeout=10,
                    ping_interval=10,
                    ping_timeout=10,
                    close_timeout=5,
                    max_queue=32,
                ),
                timeout=15.0,
            )
        except asyncio.TimeoutError:
            raise RuntimeError("Deepgram WebSocket connection timed out after 15s")

        logger.info("[STT] Deepgram: WebSocket connected, starting receiver...")
        self._rx_task = asyncio.create_task(self._recv_loop())
        logger.info("[STT] Deepgram: ready.")
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
        """Send audio chunk to Deepgram (binary PCM frames)."""
        if self._error:
            raise self._error
        if self._closed.is_set() or self._ws is None:
            logger.warning("[STT] Deepgram: cannot send audio, connection closed")
            return
        try:
            await self._ws.send(pcm_chunk)
        except ConnectionClosed:
            logger.warning("[STT] Deepgram: connection closed while sending audio")
            self._closed.set()

    async def end_audio(self) -> None:
        """
        Flush the stream. Deepgram recommends sending a Finalize message. :contentReference[oaicite:4]{index=4}
        """
        try:
            await self._ws.send(json.dumps({"type": "Finalize"}))
            await asyncio.sleep(0.25)  # give Deepgram time to flush final results
        except Exception:
            pass

        # Optionally close the stream explicitly.
        try:
            await self._ws.send(json.dumps({"type": "CloseStream"}))
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
        """
        Receives Deepgram JSON messages.
        We only emit committed/final transcripts when message type is Results and is_final is true.
        """
        logger.debug("[STT] Deepgram: _recv_loop started, waiting for messages...")
        try:
            while not self._closed.is_set():
                msg = await self._ws.recv()
                # logger.debug("[STT] Deepgram: received message type: %s", type(msg).__name__)

                if isinstance(msg, (bytes, bytearray)):
                    # Deepgram sends JSON text; ignore unexpected bytes.
                    logger.warning("[STT] Deepgram: received unexpected binary message %r", msg)
                    continue

                data = json.loads(msg)
                typ = data.get("type")

                if typ == "Results":
                    if not data.get("is_final", False):
                        # Intermediate results - log but don't emit
                        # logger.debug("[STT] Deepgram intermediate transcript: %r", data)
                        continue

                    # Transcript lives here: channel.alternatives[0].transcript
                    channel = data.get("channel") or {}
                    alts = channel.get("alternatives") or []
                    if not alts:
                        continue

                    text = (alts[0].get("transcript") or "").strip()
                    if text:
                        logger.debug("[STT] Deepgram: final transcript: %s", text[:50])
                        await self._events_q.put(TranscriptEvent(text=text, is_final=True))
                    continue

                if typ in ("Metadata", "UtteranceEnd", "SpeechStarted"):
                    logger.debug("[STT] Deepgram: received %s", typ)
                    continue

                # Deepgram errors typically come as {"type":"Error", ...} or {"error": "..."}
                if typ == "Error" or "error" in data:
                    error_msg = data.get("message", str(data))
                    self._error = RuntimeError(f"Deepgram STT error: {error_msg}")
                    logger.error("[STT] Deepgram: %s", self._error)
                    raise self._error

        except ConnectionClosedOK:
            logger.debug("[STT] Deepgram: session closed cleanly.")
        except ConnectionClosed as e:
            # Close code 1000 is normal closure.
            is_clean = e.code == 1000 or (e.rcvd is None and e.sent is not None)
            if is_clean:
                logger.debug("[STT] Deepgram: session closed (code=%s, rcvd=%s).", e.code, e.rcvd)
            else:
                logger.warning("[STT] Deepgram: connection closed unexpectedly: %s", e)
                if not self._error:
                    self._error = RuntimeError(f"Deepgram connection closed unexpectedly: {e}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("[STT] Deepgram receiver crashed: %r", e)
            if not self._error:
                self._error = e
        finally:
            self._closed.set()
            await self._events_q.put(None)
