import asyncio
from logging import getLogger
from typing import Optional, List

from fastapi import WebSocket, WebSocketDisconnect

from lib.stt_provider import RealtimeSttProvider

logger = getLogger(__name__)


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
