"""
STT Session — the core bridge between audio input and transcript output.

This module runs a single real-time STT session against any provider that
implements the RealtimeSttProvider protocol (see lib/stt_provider.py).
Communication is fully queue-based:

    audio_queue  (bytes | None)  →  provider  →  transcript_queue  (str | None)

Internally two concurrent tasks handle the plumbing:

  _sender   — pulls PCM chunks from audio_queue, forwards them to the
              provider via send_audio(). A None chunk signals end-of-audio
              and triggers provider.end_audio().

  _receiver — iterates the provider's event stream. Committed (is_final)
              transcript segments are pushed into transcript_queue. When the
              provider closes the stream, a None sentinel is pushed to signal
              end-of-transcripts.

Typical usage::

    provider = SomeProvider(config)
    audio_q:      asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=40)
    transcript_q: asyncio.Queue[str   | None] = asyncio.Queue(maxsize=200)
    running = asyncio.Event()
    running.set()

    task = asyncio.create_task(stt_task(provider, audio_q, transcript_q, running))

    # Feed audio into audio_q (e.g. from a microphone or WAV file),
    # then push None to signal end-of-audio.
    # Read committed transcripts from transcript_q until you receive None.

    await task
"""
import asyncio
from logging import getLogger
from typing import Optional

from lib.stt_provider import RealtimeSttProvider

logger = getLogger(__name__)


async def stt_session_task(
        provider: RealtimeSttProvider,
        audio_queue: asyncio.Queue[Optional[bytes]],
        transcript_queue: asyncio.Queue[Optional[str]],
        conversation_running: asyncio.Event,
) -> None:
    """
    Run a provider-agnostic real-time STT session.

    Enters the provider's async context, then concurrently:
      - forwards PCM audio from *audio_queue* to the provider,
      - collects committed transcripts into *transcript_queue*.

    The function returns once all audio has been sent and the provider
    has finished emitting events. On early cancellation or error, both
    internal tasks are cancelled and the provider context is exited
    cleanly.

    Args:
        provider: An instantiated (but not yet entered) RealtimeSttProvider.
        audio_queue: Feed raw PCM bytes here. Push None to signal
            end-of-audio.
        transcript_queue: Committed transcript strings appear here.
            A None sentinel is pushed when the provider stream ends.
        conversation_running: Event flag; clear it to request an early
            stop of both sender and receiver.
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
            logger.debug("[STT] _receiver(): received event: %r", ev)
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
