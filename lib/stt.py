import asyncio
from logging import getLogger
from typing import Optional, List

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
            logger.info("UPSTREAM RECEIVER GOT: %r", ev)
            if not conversation_running.is_set():
                break
            if ev.is_final and ev.text.strip():
                await transcript_queue.put(ev.text.strip())

    async with provider:
        sender = asyncio.create_task(_sender())
        receiver = asyncio.create_task(_receiver())

        try:
            await sender       # finishes sending + end_audio() signals provider to close
            await receiver     # finishes when provider's events() yields None
        finally:
            if not sender.done():
                sender.cancel()
            if not receiver.done():
                receiver.cancel()


# ---------------------------------------------------------------------------
# Transcript Ingest Loop
# ---------------------------------------------------------------------------


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
