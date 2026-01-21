import asyncio
import base64
from json import loads, dumps
from typing import Optional
from logging import getLogger
from time import time

from fastapi import WebSocket, WebSocketDisconnect
from websockets import connect, ConnectionClosedOK

from config import AUDIO_SAMPLE_RATE, ELEVENLABS_STT_VAD_SILENCE_THRESHOLD_S, ELEVENLABS_STT_MIN_SILENCE_DURATION_MS, \
    ELEVENLABS_STT_REALTIME_URL, ELEVENLABS_API_KEY, ELEVENLABS_STT_MIN_SPEECH_DURATION_MS, ELEVENLABS_STT_VAD_THRESHOLD


logger = getLogger(__name__)


# ---------------------------------------------------------------------------
# STT constants
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# STT helpers
# ---------------------------------------------------------------------------

def _build_stt_url() -> tuple[str, dict[str, str]]:
    """
    Build ElevenLabs STT WebSocket URL and headers.
    https://elevenlabs.io/docs/api-reference/speech-to-text/v-1-speech-to-text-realtime

    Available parameters and VAD:
    https://elevenlabs.io/docs/developers/guides/cookbooks/speech-to-text/realtime/transcripts-and-commit-strategies#voice-activity-detection-vad
        audio_format=AudioFormat.PCM_16000,
        commit_strategy=CommitStrategy.VAD,
        vad_silence_threshold_secs=1.5,
        vad_threshold=0.4,
        min_speech_duration_ms=100,
        min_silence_duration_ms=100,
    """
    audio_format = f"pcm_{AUDIO_SAMPLE_RATE}"
    query_params = [
        "model_id=scribe_v2_realtime",
        f"audio_format={audio_format}",
        "commit_strategy=vad",
        "language_code=cs",
        f"vad_silence_threshold_secs={ELEVENLABS_STT_VAD_SILENCE_THRESHOLD_S}",
        f"vad_threshold={ELEVENLABS_STT_VAD_THRESHOLD}",
        f"min_silence_duration_ms={ELEVENLABS_STT_MIN_SILENCE_DURATION_MS}",
        f"min_speech_duration_ms={ELEVENLABS_STT_MIN_SPEECH_DURATION_MS}",
        # @TODO: we should use this to derive length of user speech (and also see if we get speakers).
        # "include_timestamps=True",
    ]
    ws_url = ELEVENLABS_STT_REALTIME_URL + "?" + "&".join(query_params)
    headers = {"xi-api-key": ELEVENLABS_API_KEY}
    return ws_url, headers


def _make_silence_chunk(sample_rate: int, duration_s: float = 0.1) -> bytes:
    """Create a silence audio chunk of given duration."""
    return b"\x00\x00" * int(sample_rate * duration_s)


async def _stt_send_audio_task(
    stt_ws,
    audio_queue: asyncio.Queue[Optional[bytes]],
    conversation_running: asyncio.Event,
    sample_rate: int,
) -> None:
    """Send audio chunks from queue to STT WebSocket."""
    silence_chunk = _make_silence_chunk(sample_rate)

    try:
        while conversation_running.is_set():
            try:
                chunk = await asyncio.wait_for(audio_queue.get(), timeout=0.2)
            except asyncio.TimeoutError:
                chunk = silence_chunk

            if chunk is None:
                break

            payload = {
                "message_type": "input_audio_chunk",
                "audio_base_64": base64.b64encode(chunk).decode("ascii"),
                "sample_rate": sample_rate,
            }
            await stt_ws.send(dumps(payload))
    except Exception as e:
        # @TODO: catch known correct termination exceptions and log as info, rest as exception
        # @TODO: catch known bad exceptions (out of quota and propagate them/stop convo - there is no point in retrying.
        logger.warning("[STT] _stt_send_audio stopped: %r", e)
    finally:
        logger.info("[STT] _stt_send_audio finished.")


async def _stt_receive_transcripts_task(
    stt_ws,
    transcript_queue: asyncio.Queue[Optional[str]],
    conversation_running: asyncio.Event,
) -> None:
    """Receive transcripts from STT WebSocket and forward committed ones to queue."""
    try:
        while conversation_running.is_set():
            reply = await stt_ws.recv()
            data = loads(reply)  # process json
            msg_type = data.get("message_type")

            if msg_type == STT_MSG_PARTIAL_TRANSCRIPT:
                logger.debug("[STT] _stt_receive_transcripts: partial: %s", data.get("text", "").strip())
                continue

            if msg_type in (STT_MSG_COMMITTED_TRANSCRIPT, STT_MSG_COMMITTED_TRANSCRIPT_TS):
                text = data.get("text", "").strip()
                if text:
                    logger.debug("[STT] _stt_receive_transcripts: adding %d chars to queue.", len(text))
                    await transcript_queue.put(text)
                continue

            if msg_type in STT_ERROR_TYPES:
                raise RuntimeError(f"STT error: {data}")

    except ConnectionClosedOK:
        logger.debug("[STT] _stt_receive_transcripts: session closed cleanly.")
    except Exception as e:
        # @TODO: catch known correct termination exceptions and log as info, rest as exception
        # @TODO: catch known bad exceptions (out of quota and propagate them/stop convo - there is no point in retrying.
        logger.warning("[STT] _stt_receive_transcripts: crashed with exception: %r", e)
    finally:
        logger.info("[STT] _stt_receive_transcripts: finished.")


async def _verify_stt_handshake(stt_ws) -> None:
    """Verify STT session started correctly."""
    first_msg = await stt_ws.recv()
    data = loads(first_msg)
    if data.get("message_type") != STT_MSG_SESSION_STARTED:
        raise RuntimeError("STT session did not start correctly")
    logger.info("[STT] Session started.")


# ---------------------------------------------------------------------------
# STT session
# ---------------------------------------------------------------------------

async def init_stt_once(
        audio_queue: asyncio.Queue[Optional[bytes]],
        transcript_queue: asyncio.Queue[Optional[str]],
        conversation_running: asyncio.Event,
) -> None:
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

    logger.debug("[STT] init_stt_once(): opening STT session.")
    async with connect(
            ws_url,
            additional_headers=headers,
            ping_interval=10,
            ping_timeout=10,
            close_timeout=5,
            max_queue=32,
    ) as stt_ws:
        await _verify_stt_handshake(stt_ws)

        sender = asyncio.create_task(
            _stt_send_audio_task(stt_ws, audio_queue, conversation_running, AUDIO_SAMPLE_RATE)
        )
        receiver = asyncio.create_task(
            _stt_receive_transcripts_task(stt_ws, transcript_queue, conversation_running)
        )

        # Run both tasks; if one completes/crashes, cancel the other
        _, pending = await asyncio.wait(
            {sender, receiver},
            return_when=asyncio.FIRST_COMPLETED,
        )

        for t in pending:
            logger.debug("[STT] init_stt_once(): cancelling task: %s", t)
            t.cancel()

        # @TODO: check for bad exceptions (when there is no point to retry) and pass them upwards or quit convo


    # all done
    logger.debug("[STT] init_stt_once(): STT session closed cleanly.")


async def stt_session_task(
        audio_queue: asyncio.Queue[Optional[bytes]],
        transcript_queue: asyncio.Queue[Optional[str]],
        app_running: asyncio.Event,
):
    """
    Runner that keeps opening the STT session in case it crashes/ is shutdown.
    """

    retry_delay = 1.0
    failed_retries = 0

    while app_running.is_set():
        start_ts = time()
        try:
            logger.info("[STT] Starting STT session.")
            await init_stt_once(audio_queue, transcript_queue, app_running)
            logger.info("[STT] STT session ended.")
        except Exception as e:
            # this should be a rare occurrence, since exceptions should be caught locally
            logger.exception("[STT] STT crashed: %r", e, exc_info=e)

        if not app_running.is_set():
            break  # application quit

        # check if situation is bad and kill everything if we cannot init conversation
        if time() - start_ts < 10:
            # if we crashed within 10 s of starting something is bad
            failed_retries += 1

        if failed_retries > 3:
            raise RuntimeError("3 failed STT startup attempts. System shutdown.")

        logger.info("[STT] Reconnecting in %.1fs...", retry_delay)
        await asyncio.sleep(min(failed_retries * retry_delay, 0.5))

    logger.info("[STT] STT finished.")


# ---------------------------------------------------------------------------
# Audio receive
# ---------------------------------------------------------------------------

async def receive_audio_from_client(ws: WebSocket, audio_queue: asyncio.Queue[Optional[bytes]],
                                    app_running: asyncio.Event, line_open: asyncio.Event):
    """
    Reads from the WebSocket (mic and more) and pushes audio frames into audio_queue
    only when line is open (LLM is ready to listen). When not set, audio is discarded.

    Note: the same socket is used for all messages from client, both mic and other
    (other is not currently used).
    """

    try:
        logger.info("[WS] Client connected in receive_audio_from_client().")
        while app_running.is_set():
            msg = await ws.receive()

            # mic
            if "bytes" in msg and msg["bytes"] is not None:
                # if line is not open, we just drop the packet
                if line_open.is_set():
                    await audio_queue.put(msg["bytes"])

            # text (might be later used for controls)
            elif "text" in msg and msg["text"] is not None:
                data = msg["text"]
                logger.info("[WS] Text from client: %s", data)

    except WebSocketDisconnect:
        logger.info("[WS] Client disconnected in receive_audio_from_client().")
        app_running.clear()

    except RuntimeError as e:
        if 'Cannot call "receive" once a disconnect message has been received.' in str(e):
            logger.info("[WS] receive(): client disconnected; shutting down.")
        else:
            logger.exception("[WS] RuntimeError in receive_audio_from_client: %r", e, exc_info=e)
        app_running.clear()

    except Exception as e:
        logger.exception("[WS] Error in receive_audio_from_client: %r", e, exc_info=e)
        app_running.clear()

    finally:
        # this should be correct ending
        await audio_queue.put(None)
        logger.info("[WS] receive_audio_from_client(): finished.")
