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

    logger.debug("[STT] Initializing STT once...")

    async def _sender() -> None:
        try:
            while conversation_running.is_set():
                chunk = await audio_queue.get()
                if chunk is None:
                    break
                await provider.send_audio(chunk)
        finally:
            logger.debug("[STT] _sender() reached finally.")
            await provider.end_audio()

    async def _receiver() -> None:
        async for ev in provider.events():
            logger.info("[STT] _receiver(): received event: %r", ev)
            if not conversation_running.is_set():
                logger.warning("[STT] _receiver(): conversation_running is not set, breaking")
                break
            if ev.is_final and ev.text.strip():
                logger.debug("[STT] _receiver(): putting in transcript_queue: %s", ev.text.strip()[:50])
                await transcript_queue.put(ev.text.strip())
                logger.debug("[STT] _receiver(): put completed, queue size now: %d", transcript_queue.qsize())

        logger.debug("[STT] _receiver() reached finally. Putting stop token to the transcript_queue.")
        await transcript_queue.put(None)

    async with provider:
        logger.debug("[STT] Provider context entered, creating sender/receiver tasks...")
        sender = asyncio.create_task(_sender())
        receiver = asyncio.create_task(_receiver())
        logger.info("[STT] All tasks created, init successful, awaiting sender...")

        try:
            await sender  # finishes sending + end_audio() signals provider to close
            await receiver  # finishes when provider's events() yields None
        finally:
            logger.debug("[STT] init_stt_once_provider(): reached finally.")
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
        logger.debug("[INGEST] drain_transcript_queue: waiting for first item...")
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


async def transcript_ingest_task(
        app_running: asyncio.Event,
        transcript_queue: asyncio.Queue[Optional[str]],
) -> List[str]:
    """Ingest STT transcripts and return the collected segments."""
    result: List[str] = []
    try:
        while app_running.is_set():
            text = await drain_transcript_queue(transcript_queue, app_running)

            if text is None:
                logger.info("[INGEST] Received stop signal.")
                break

            if text:
                logger.debug("[INGEST] Received: %s", text)
                result.append(text)
    except asyncio.CancelledError:
        logger.info("Cancelled.")
        raise
    except Exception as e:
        logger.exception("Crashed: %r", e)
    return result
