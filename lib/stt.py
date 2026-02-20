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
              provider closes the stream cleanly, a None sentinel is pushed
              to signal end-of-transcripts.

Close handling
--------------
The receiver is the natural completion signal: it only exits when the
provider actually closes the connection. The sender is a background task.

Normal close (all audio sent, provider finishes):
  1. Sender consumes all audio, calls end_audio(), sender task finishes.
  2. Provider commits remaining transcripts and closes the connection.
  3. Receiver drains the final events, puts None in transcript_queue, exits.
  4. stt_session_task returns normally.

Early close (provider drops the connection unexpectedly):
  1. Receiver detects the closed connection and raises the provider's error.
     It does NOT put None in transcript_queue — the queue stays open.
  2. stt_session_task's finally cancels the sender immediately, stopping
     further no-op send_audio() calls on the dead connection.
  3. stt_session_task exits with the provider's exception.

Respawn design
--------------
When a provider drops the connection mid-session the caller can detect the
failure (stt_session_task raises), then spawn a fresh stt_session_task
against a new provider instance using the SAME audio_queue and
transcript_queue:

  - audio_queue still contains the unconsumed chunks that the cancelled
    sender had not yet forwarded. The new session picks up from there.
  - transcript_queue was NOT closed (no None was sent), so the downstream
    consumer (e.g. transcript_ingest_task) keeps running and will
    seamlessly receive transcripts from the new session.

This makes session respawn transparent to both the audio producer and the
transcript consumer — only the session layer needs to be restarted.

Typical usage::

    provider = SomeProvider(config)
    audio_q:      asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=40)
    transcript_q: asyncio.Queue[str   | None] = asyncio.Queue(maxsize=200)
    running = asyncio.Event()
    running.set()

    task = asyncio.create_task(stt_session_task(provider, audio_q, transcript_q, running))

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

    Normal close:
        The function returns once the provider has closed the connection
        cleanly. In normal operation this happens after the sender has
        forwarded all audio and called end_audio(), prompting the provider
        to flush its final results and close. A None sentinel is placed in
        transcript_queue to signal end-of-stream to downstream consumers.

    Early / error close:
        If the provider closes the connection before all audio has been
        sent (network drop, server error, session limit, etc.) the receiver
        detects the closure and raises the provider's exception. The sender
        is cancelled immediately — stopping any further no-op send calls on
        the dead connection — and the exception propagates out of this
        function. In this case NO None is placed in transcript_queue, leaving
        it open for a potential session respawn (see module docstring).

    Args:
        provider: An instantiated (but not yet entered) RealtimeSttProvider.
        audio_queue: Feed raw PCM bytes here. Push None to signal
            end-of-audio. On early close, unconsumed chunks remain in the
            queue for a respawned session to consume.
        transcript_queue: Committed transcript strings appear here.
            A None sentinel is pushed only on clean close. On error the
            queue is left open so a new session can continue writing to it.
        conversation_running: Event flag; clear it to request an early
            stop of the sender loop.
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
        logger.info("[STT] All tasks created, init successful, awaiting receiver...")

        try:
            # Await receiver — it exits when the provider closes (cleanly or with error).
            # Sender runs concurrently as a background task; in normal operation it finishes
            # first (sends all audio + end_audio()), then the provider closes and receiver
            # exits. If the provider closes early (error), receiver exits first and we
            # cancel sender to stop it from spinning on a dead connection.
            await receiver
        finally:
            logger.debug("[STT] stt_session_task(): reached finally.")

            # done() is true in all cases of normal finish, cancel or exception.
            if not sender.done():
                logger.warning("[STT] stt_session_task(): Explicitly cancelling sender task.")
                sender.cancel()

                # in case of cancel, await correct close and propagate the error up, if it was not cancel. :)
                try:
                    await sender
                except (asyncio.CancelledError, Exception):
                    pass
