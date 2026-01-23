from __future__ import annotations

import asyncio
import base64
from json import loads, dumps
from logging import getLogger
from typing import AsyncIterator, Optional

from dotenv import load_dotenv
from os import getenv
from websockets import connect, ConnectionClosedOK

from config import (
    AUDIO_SAMPLE_RATE,
    STT_VAD_SILENCE_THRESHOLD_S,
    STT_MIN_SILENCE_DURATION_MS,
    STT_MIN_SPEECH_DURATION_MS,
    STT_VAD_THRESHOLD,
)
from lib.stt_provider import RealtimeSttProvider, TranscriptEvent


logger = getLogger(__name__)


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

# Speech to text parameters
# https://elevenlabs.io/docs/models
ELEVENLABS_STT_REALTIME_URL = "wss://api.elevenlabs.io/v1/speech-to-text/realtime"
ELEVENLABS_STT_REALTIME_MODEL = "scribe_v2_realtime"

# credentials
load_dotenv()
ELEVENLABS_API_KEY = getenv("ELEVENLABS_API_KEY")


def _build_stt_url() -> tuple[str, dict[str, str]]:
    audio_format = f"pcm_{AUDIO_SAMPLE_RATE}"
    query_params = [
        "model_id=scribe_v2_realtime",
        f"audio_format={audio_format}",
        "commit_strategy=vad",
        "language_code=cs",
        f"vad_silence_threshold_secs={STT_VAD_SILENCE_THRESHOLD_S}",
        f"vad_threshold={STT_VAD_THRESHOLD}",
        f"min_silence_duration_ms={STT_MIN_SILENCE_DURATION_MS}",
        f"min_speech_duration_ms={STT_MIN_SPEECH_DURATION_MS}",
    ]
    ws_url = ELEVENLABS_STT_REALTIME_URL + "?" + "&".join(query_params)
    headers = {"xi-api-key": ELEVENLABS_API_KEY}
    return ws_url, headers


async def _verify_stt_handshake(stt_ws) -> None:
    first_msg = await stt_ws.recv()
    data = loads(first_msg)
    if data.get("message_type") != STT_MSG_SESSION_STARTED:
        raise RuntimeError("STT session did not start correctly")
    logger.info("[STT] Session started.")


class ElevenLabsRealtimeProvider(RealtimeSttProvider):
    def __init__(self) -> None:
        self._stt_ws = None
        self._events_q: asyncio.Queue[TranscriptEvent] = asyncio.Queue(maxsize=200)
        self._receiver_task: Optional[asyncio.Task] = None
        self._closed = asyncio.Event()

    async def __aenter__(self) -> "ElevenLabsRealtimeProvider":
        """
        Run a single STT session: connect, send audio, receive transcripts.

        *Note to "random" socket close:*
        The WebSocket connection will automatically close after 20 seconds of inactivity.
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
        ws_url, headers = _build_stt_url()
        self._stt_ws = await connect(
            ws_url,
            additional_headers=headers,
            ping_interval=10,
            ping_timeout=10,
            close_timeout=5,
            max_queue=32,
        )
        await _verify_stt_handshake(self._stt_ws)
        self._receiver_task = asyncio.create_task(self._recv_loop())
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        try:
            self._closed.set()
            if self._receiver_task:
                self._receiver_task.cancel()
            if self._stt_ws:
                await self._stt_ws.close()
        finally:
            self._stt_ws = None

    async def send_audio(self, pcm_chunk: bytes) -> None:
        payload = {
            "message_type": "input_audio_chunk",
            "audio_base_64": base64.b64encode(pcm_chunk).decode("ascii"),
            "sample_rate": AUDIO_SAMPLE_RATE,
        }
        await self._stt_ws.send(dumps(payload))

    async def end_audio(self) -> None:
        # ElevenLabs doesn't require a special "end" message here for your current flow.
        # We just stop sending; session will close when the outer orchestration ends.
        return

    def events(self) -> AsyncIterator[TranscriptEvent]:
        async def _aiter() -> AsyncIterator[TranscriptEvent]:
            while not self._closed.is_set():
                ev = await self._events_q.get()
                yield ev

        return _aiter()

    async def _recv_loop(self) -> None:
        try:
            while not self._closed.is_set():
                reply = await self._stt_ws.recv()
                data = loads(reply)
                msg_type = data.get("message_type")

                if msg_type == STT_MSG_PARTIAL_TRANSCRIPT:
                    continue

                if msg_type in (STT_MSG_COMMITTED_TRANSCRIPT, STT_MSG_COMMITTED_TRANSCRIPT_TS):
                    text = data.get("text", "").strip()
                    if text:
                        await self._events_q.put(TranscriptEvent(text=text, is_final=True))
                    continue

                if msg_type in STT_ERROR_TYPES:
                    raise RuntimeError(f"STT error: {data}")

        except ConnectionClosedOK:
            logger.debug("[STT] ElevenLabs: session closed cleanly.")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("[STT] ElevenLabs receiver crashed: %r", e)
        finally:
            self._closed.set()
