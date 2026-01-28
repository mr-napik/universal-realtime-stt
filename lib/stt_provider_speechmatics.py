from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from logging import getLogger
from typing import AsyncIterator, Optional

from websockets import connect, ConnectionClosed, ConnectionClosedOK

from config import (
    AUDIO_SAMPLE_RATE,
    STT_LANGUAGE_ISO_639_1,
    STT_VAD_SILENCE_THRESHOLD_S,
)
from lib.stt_provider import RealtimeSttProvider, TranscriptEvent

logger = getLogger(__name__)


@dataclass(frozen=True)
class SpeechmaticsSttConfig:
    api_key: str
    base_url: str = "wss://eu.rt.speechmatics.com/v2/"  # change region if needed

    # Language is typically ISO (e.g. "cs"). (BCP-47 locale is output formatting; keep simple.)
    language: str = STT_LANGUAGE_ISO_639_1

    # Speechmatics wants max_delay in [0.7, 4]. We'll clamp to that.
    # This influences how quickly "final" segments come back.
    max_delay_s: float = STT_VAD_SILENCE_THRESHOLD_S

    # Optional: ask server to detect end-of-utterance via silence
    end_of_utterance_silence_trigger_s: float = STT_VAD_SILENCE_THRESHOLD_S

    # We only need finals for your pipeline; keep partials off by default.
    enable_partials: bool = True

    # Raw audio format
    encoding: str = "pcm_s16le"
    sample_rate: int = AUDIO_SAMPLE_RATE


class SpeechmaticsRealtimeProvider(RealtimeSttProvider):
    """
    Speechmatics Realtime WebSocket provider.

    - StartRecognition (JSON)
    - AddAudio (binary PCM frames)
    - EndOfStream (JSON with last_seq_no)
    - Emits TranscriptEvent for AddTranscript (final)
    """

    def __init__(self, cfg: SpeechmaticsSttConfig) -> None:
        self._cfg = cfg
        self._ws = None
        self._events_q: asyncio.Queue[Optional[TranscriptEvent]] = asyncio.Queue(maxsize=200)

        self._rx_task: Optional[asyncio.Task] = None
        self._closed = asyncio.Event()
        self._ready = asyncio.Event()

        self._error: Optional[Exception] = None
        self._seq_no: int = 0  # increments per AddAudio frame sent

    async def __aenter__(self) -> "SpeechmaticsRealtimeProvider":
        if not self._cfg.api_key:
            raise ValueError("Speechmatics API key is required")

        headers = {"Authorization": f"Bearer {self._cfg.api_key}"}
        logger.info("[STT] Speechmatics: connecting to %s", self._cfg.base_url)
        logger.debug("[STT] Speechmatics: using language=%s, encoding=%s, sample_rate=%d",
                     self._cfg.language, self._cfg.encoding, self._cfg.sample_rate)

        try:
            self._ws = await asyncio.wait_for(
                connect(
                    self._cfg.base_url,
                    additional_headers=headers,
                    ping_interval=10,
                    ping_timeout=10,
                    close_timeout=5,
                    max_queue=32,
                ),
                timeout=30.0,
            )
            logger.info("[STT] Speechmatics: WebSocket connected successfully")
        except asyncio.TimeoutError:
            raise RuntimeError("Speechmatics WebSocket connection timed out after 30s") from None

        logger.debug("[STT] Speechmatics: starting receiver task...")
        self._rx_task = asyncio.create_task(self._recv_loop())

        # StartRecognition
        max_delay = float(self._cfg.max_delay_s)
        if max_delay < 0.7:
            max_delay = 0.7
        if max_delay > 4.0:
            max_delay = 4.0

        start_msg = {
            "message": "StartRecognition",
            "audio_format": {
                "type": "raw",
                "encoding": self._cfg.encoding,
                "sample_rate": int(self._cfg.sample_rate),
            },
            "transcription_config": {
                "language": self._cfg.language,
                "enable_partials": bool(self._cfg.enable_partials),
                "max_delay": max_delay,
                # End-of-utterance detection via silence (optional but useful for "commit-like" behavior)
                "conversation_config": {
                    "end_of_utterance_silence_trigger": float(self._cfg.end_of_utterance_silence_trigger_s),
                },
            },
        }

        logger.debug("[STT] Speechmatics: sending StartRecognition: %s", json.dumps(start_msg))
        await self._ws.send(json.dumps(start_msg))
        logger.debug("[STT] Speechmatics: StartRecognition sent, waiting for RecognitionStarted...")

        try:
            await asyncio.wait_for(self._ready.wait(), timeout=30.0)
        except asyncio.TimeoutError:
            raise RuntimeError("Speechmatics did not send RecognitionStarted within 30s") from None

        logger.info("[STT] Speechmatics: ready.")
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
        if self._error:
            raise self._error
        if self._closed.is_set() or self._ws is None:
            return

        self._seq_no += 1
        try:
            # AddAudio is binary: just send bytes
            await self._ws.send(pcm_chunk)
        except ConnectionClosed:
            self._closed.set()

    async def end_audio(self) -> None:
        """
        EndOfStream: tell Speechmatics we won't send more audio.
        last_seq_no is required and should be the last AddAudio sequence number.
        """
        if self._ws is None:
            return

        try:
            eos = {"message": "EndOfStream", "last_seq_no": int(self._seq_no)}
            await self._ws.send(json.dumps(eos))
            logger.debug("[STT] Speechmatics: EndOfStream sent (last_seq_no=%d)", self._seq_no)
        except Exception:
            pass

    def events(self) -> AsyncIterator[TranscriptEvent]:
        async def _aiter() -> AsyncIterator[TranscriptEvent]:
            while True:
                ev = await self._events_q.get()
                if ev is None:
                    if self._error:
                        raise self._error
                    break
                yield ev

        return _aiter()

    async def _recv_loop(self) -> None:
        """
        Reads JSON messages. Emits final transcript events on AddTranscript.
        """
        assert self._ws is not None
        logger.debug("[STT] Speechmatics: _recv_loop started, waiting for messages...")

        try:
            while not self._closed.is_set():
                msg = await self._ws.recv()

                # We only expect JSON text in recv loop (audio is client->server only).
                if isinstance(msg, (bytes, bytearray)):
                    logger.warning("[STT] Speechmatics: unexpected binary message from server")
                    continue

                data = json.loads(msg)
                typ = (data.get("message") or "").strip()
                # logger.debug("[STT] Speechmatics: message=%s, data=%s", typ, str(data)[:200])

                if typ == "RecognitionStarted":
                    self._ready.set()
                    continue

                if typ == "AddTranscript":
                    logger.debug("[STT] Speechmatics: AddTranscript %r", data)
                    try:
                        text = data['results'][0]['alternatives'][0]['content']
                        if text:
                            await self._events_q.put(TranscriptEvent(text=text, is_final=True))
                    except Exception as e:
                        logger.exception("[STT] Speechmatics: AddTranscript failed %r", e)
                    continue

                if typ == "AddPartialTranscript":
                    logger.debug("[STT] Speechmatics: AddPartialTranscript %r", str(data)[:300])
                    continue

                if typ == "EndOfTranscript":
                    logger.info("[STT] Speechmatics: EndOfTranscript received")
                    break

                if typ == "Warning":
                    # You may want to log these at info/warn level
                    logger.warning("[STT] Speechmatics warning: %s", data.get("reason", data))
                    continue

                if typ == "Error":
                    reason = data.get("reason", "")
                    etype = data.get("type", "")
                    self._error = RuntimeError(f"Speechmatics STT error ({etype}): {reason}")
                    logger.error("[STT] Speechmatics: %s", self._error)
                    raise self._error

                # Ignore other message types unless you need them.
                # AudioAdded / Info / EndOfUtterance etc.
        except ConnectionClosedOK:
            logger.debug("[STT] Speechmatics: session closed cleanly.")
        except ConnectionClosed as e:
            close_code = e.rcvd.code if e.rcvd else None
            close_reason = e.rcvd.reason if e.rcvd else None
            is_clean = close_code == 1000 or (e.rcvd is None and e.sent is not None)
            if is_clean:
                logger.debug("[STT] Speechmatics: session closed (code=%s, rcvd=%s).", close_code, e.rcvd)
            else:
                logger.warning("[STT] Speechmatics: connection closed unexpectedly (code=%s, reason=%s): %s",
                               close_code, close_reason, e)
                if not self._error:
                    self._error = RuntimeError(f"Speechmatics connection closed unexpectedly: {e}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("[STT] Speechmatics receiver crashed: %r", e)
            if not self._error:
                self._error = e
        finally:
            self._closed.set()
            self._ready.set()
            await self._events_q.put(None)
