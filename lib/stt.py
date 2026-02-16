import asyncio
from logging import getLogger
from pathlib import Path
from typing import Optional, List

from lib.helper_stream_wav import stream_wav_file
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
            await sender       # finishes sending + end_audio() signals provider to close
            await receiver     # finishes when provider's events() yields None
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
        # logger.debug("[INGEST] drain_transcript_queue: got first item: %r", first[:50] if first else first)
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

    logger.info("[INGEST] Received: %s", text)
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


# ---------------------------------------------------------------------------
# High-level: transcribe a WAV file (this is more useful for testing)
# ---------------------------------------------------------------------------


async def transcribe_wav_realtime(
        provider: RealtimeSttProvider,
        wav_path: Path,
        *,
        chunk_ms: int = 200,
        sample_rate: int = 16_000,
        realtime_factor: float = 1.0,
        silence_s: float = 2.0,
) -> str:
    """
    Transcribe a WAV file using the given STT provider.
    This is more for tests, since it reads file from the disk
    and then streams it in realtime pace.

    Sets up the streaming pipeline (queues, sender/receiver tasks),
    streams audio with real-time pacing, collects committed transcripts,
    and returns the joined result.

    Args:
        provider: An already-instantiated (but not yet entered) RealtimeSttProvider.
        wav_path: Path to the WAV file (must be PCM 16kHz mono 16-bit).
        chunk_ms: Audio chunk duration in milliseconds.
        sample_rate: Expected sample rate in Hz.
        realtime_factor: Playback speed (1.0 = real-time, 0.0 = no delay).
        silence_s: Silence padding (seconds) added before and after audio for VAD.

    Returns:
        The full transcript as a single string (segments joined by space).
    """
    input_audio_queue: asyncio.Queue = asyncio.Queue(maxsize=40)
    output_transcript_queue: asyncio.Queue = asyncio.Queue(maxsize=200)
    resulting_transcript_segments: List[str] = []
    running = asyncio.Event()
    running.set()

    stt_task = asyncio.create_task(init_stt_once_provider(provider, input_audio_queue, output_transcript_queue, running))
    ingest_task = asyncio.create_task(transcript_ingest_loop(running, output_transcript_queue, resulting_transcript_segments))

    await stream_wav_file(
        wav_path,
        input_audio_queue,
        chunk_ms,
        sample_rate,
        realtime_factor=realtime_factor,
        silence=silence_s,
        running=running,
    )

    # At this point, streaming is completed and all chunks sent.
    # Ensure STT session ends (and task completes).
    await stt_task

    # Similarly wait for the ingest loop and collect drained transcripts.
    await ingest_task
    running.clear()

    return " ".join(resulting_transcript_segments)
