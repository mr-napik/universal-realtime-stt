from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Awaitable, Callable, Optional

from helpers.diff_report import CustomMetricResult, DiffReport
from helpers.stream_wav import stream_wav_file, logger
from helpers.transcript_ingest import transcript_ingest_task
from lib.stt import stt_session_task
from lib.stt_provider import RealtimeSttProvider


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
    running = asyncio.Event()
    running.set()

    stt_task = asyncio.create_task(stt_session_task(provider, input_audio_queue, output_transcript_queue, running))
    ingest_task = asyncio.create_task(transcript_ingest_task(running, output_transcript_queue))

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
    # Wait till STT session ends (task completes).
    await stt_task

    # Similarly wait for the ingest loop to exit and collect drained transcripts.
    segments = await ingest_task
    running.clear()

    return " ".join(segments)


async def transcribe_and_diff(
        provider: RealtimeSttProvider,
        wav_path: Path,
        txt_path: Path,
        out_path: Path,
        *,
        chunk_ms: int = 200,
        sample_rate: int = 16_000,
        realtime_factor: float = 1.0,
        silence_s: float = 2.0,
        custom_metric_fn: Optional[Callable[[str, str], Awaitable[CustomMetricResult]]] = None,
) -> DiffReport:
    """
    Transcribe a WAV file and compare against ground-truth text.

    Runs the full pipeline: stream audio to the provider, collect the
    transcript, read the expected text, generate an HTML diff report,
    and return the DiffReport with accuracy metrics.

    Args:
        provider: An already-instantiated (but not yet entered) RealtimeSttProvider.
        wav_path: Path to the WAV file (must be PCM 16kHz mono 16-bit).
        txt_path: Path to the ground-truth transcript text file.
        out_path: Path where the HTML diff report will be written.
        chunk_ms: Audio chunk duration in milliseconds.
        sample_rate: Expected sample rate in Hz.
        realtime_factor: Playback speed (1.0 = real-time, 0.0 = no delay).
        silence_s: Silence padding (seconds) added before and after audio for VAD.
        custom_metric_fn: Optional async callable (expected, got) -> CustomMetricResult.
            When supplied, the result is embedded in the DiffReport and shown in the
            HTML report and TSV export. See helpers/semantic_understanding.py for an example.

    Returns:
        DiffReport with accuracy metrics and paths.
    """
    provider_name = provider.__class__.__name__

    transcript_raw = await transcribe_wav_realtime(
        provider,
        wav_path,
        chunk_ms=chunk_ms,
        sample_rate=sample_rate,
        realtime_factor=realtime_factor,
        silence_s=silence_s,
    )
    logger.info("Final transcript raw: %r", transcript_raw)

    # read ground truth and compute diff
    expected_raw = txt_path.read_text(encoding="utf-8")

    custom_metric = await custom_metric_fn(expected_raw, transcript_raw) if custom_metric_fn else None
    report = DiffReport(expected_raw, transcript_raw, custom_metric=custom_metric)
    report.write_html(
        out_path,
        title=f"{wav_path.name}: {provider_name}",
        detail=f"Provider: {provider_name}\nSound: {wav_path.name}\nExpected: {txt_path.name}\nReport: {out_path.name}",
    )
    return report
