import asyncio
import base64
from json import loads, dumps
from typing import Optional, List
from logging import getLogger
from time import time

from fastapi import WebSocket, WebSocketDisconnect
from websockets import connect, ConnectionClosedOK

from lib.stt_provider import RealtimeSttProvider, TranscriptEvent
from lib.stt_provider_elevenlabs import ElevenLabsRealtimeProvider


logger = getLogger(__name__)



# ---------------------------------------------------------------------------
# STT helpers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# STT session
# ---------------------------------------------------------------------------


async def init_stt_once_provider(
        provider: RealtimeSttProvider,
        audio_queue: asyncio.Queue[Optional[bytes]],
        transcript_queue: asyncio.Queue[Optional[str]],
        conversation_running: asyncio.Event,
) -> None:
    """
    Provider-agnostic STT session:
      - reads audio chunks from audio_queue
      - sends to provider
      - receives provider events
      - pushes committed transcripts into transcript_queue
    """

    async def _sender() -> None:
        try:
            while conversation_running.is_set():
                chunk = await audio_queue.get()
                if chunk is None:
                    break
                await provider.send_audio(chunk)
        finally:
            await provider.end_audio()

    async def _receiver() -> None:
        async for ev in provider.events():
            if not conversation_running.is_set():
                break
            if ev.is_final and ev.text.strip():
                await transcript_queue.put(ev.text.strip())

    async with provider:
        sender = asyncio.create_task(_sender())
        receiver = asyncio.create_task(_receiver())

        _, pending = await asyncio.wait(
            {sender, receiver},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()


async def init_stt_once(
        audio_queue: asyncio.Queue[Optional[bytes]],
        transcript_queue: asyncio.Queue[Optional[str]],
        conversation_running: asyncio.Event,
) -> None:
    # default provider stays ElevenLabs (backportable)
    provider = ElevenLabsRealtimeProvider()
    await init_stt_once_provider(provider, audio_queue, transcript_queue, conversation_running)



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


# ---------------------------------------------------------------------------
# Transcript Ingest Loop
# ---------------------------------------------------------------------------
#
async def drain_transcript_queue(
        queue: asyncio.Queue[Optional[str]],
        app_running: asyncio.Event,
) -> Optional[str]:
    """
    Drain all available transcripts from queue.

    Returns:
        - None: Stop signal received
        - "": Queue was empty or only whitespace
        - str: Combined transcript text
    """
    texts = []
    try:
        first = await queue.get()
        if first is None:
            return None  # Stop signal

        texts.append(first)
        while app_running.is_set():
            item = queue.get_nowait()
            if item is None:
                return None  # Stop signal mid-stream
            texts.append(item)
    except asyncio.QueueEmpty:
        pass  # Drained all available

    result = "\n".join(texts).strip()
    if texts:
        logger.debug("[INGEST] Drained %d items: %r", len(texts), result[:100])
    return result


async def transcript_ingest_step(
        app_running: asyncio.Event,
        transcript_queue: asyncio.Queue[Optional[str]],
        result: List[str],
) -> bool:
    """
    Process one batch of transcripts.

    Returns False if should stop, True to continue.
    """
    text = await drain_transcript_queue(transcript_queue, app_running)

    if text is None:
        logger.info("[INGEST] Received stop signal.")
        return False

    if not text:
        return True  # Empty batch, continue

    print("[INGEST] Received:", text)
    result.append(text)
    return True


async def transcript_ingest_loop(
        app_running: asyncio.Event,
        transcript_queue: asyncio.Queue[Optional[str]],
        result: List[str],
) -> None:
    """Ingest STT transcripts into ConversationLog."""
    try:
        while app_running.is_set():
            should_continue = await transcript_ingest_step(app_running, transcript_queue, result)
            if not should_continue:
                break
    except asyncio.CancelledError:
        logger.info("Cancelled.")
        raise
    except Exception as e:
        logger.exception("Crashed: %r", e)
