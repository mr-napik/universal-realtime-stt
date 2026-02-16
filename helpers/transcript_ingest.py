"""
Transcript Ingest — consuming committed transcripts from the STT pipeline.

The STT session (lib/stt.py) pushes committed transcript strings into an
asyncio.Queue and signals completion with a None sentinel. This module
provides a ready-made consumer that collects those strings into a list.

Use it as a reference for building your own real-time consumer. The queue
contract is simple:

    str   → a committed transcript segment (one or more words)
    None  → end-of-stream, no more segments will arrive

A minimal custom consumer might look like::

    async for item in queue_iter(transcript_queue):
        print(item)          # display each segment as it arrives
        all_segments.append(item)

Or with the helper provided here::

    task = asyncio.create_task(
        transcript_ingest_task(running_event, transcript_queue)
    )
    # ... stream audio ...
    segments = await task     # returns List[str] of all committed segments
"""
import asyncio
from logging import getLogger

from typing import Optional, List

logger = getLogger(__name__)


async def transcript_ingest_task(
        app_running: asyncio.Event,
        transcript_queue: asyncio.Queue[Optional[str]],
) -> List[str]:
    """
    Collect committed transcript segments from the queue until end-of-stream.

    Reads items one at a time. Each non-None item is a committed transcript
    segment produced by the STT receiver. A None item signals that the
    provider has closed and no more segments will arrive.

    Args:
        app_running: Event flag; consumption continues while set. Clear it
            to request an early stop (e.g. on user cancellation).
        transcript_queue: The queue written to by the STT session. Yields
            str segments and a final None sentinel.

    Returns:
        List of transcript segments in the order they were received.
    """
    result: List[str] = []
    try:
        while app_running.is_set():
            item = await transcript_queue.get()
            if item is None:
                logger.info("[INGEST] Received stop signal.")
                break
            text = item.strip()
            if text:
                logger.debug("[INGEST] Received: %s", text[:100])
                result.append(text)
    except asyncio.CancelledError:
        logger.info("Cancelled.")
        raise
    except Exception as e:
        logger.exception("Crashed: %r", e)
    return result
