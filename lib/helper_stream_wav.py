from __future__ import annotations

import asyncio
import wave
from dataclasses import dataclass
from logging import getLogger
from pathlib import Path
from typing import Iterator, Optional

logger = getLogger(__name__)


def make_silence_chunk(duration_s: float, sample_rate: int, sample_width_bytes: int) -> bytes:
    """Create a silence audio chunk of given duration."""
    return b"\x00" * sample_width_bytes * int(sample_rate * duration_s)


async def stream_silence(duration_s: float, audio_queue: asyncio.Queue, chunk_ms: int, *,
                         realtime_factor: float = 1.0, sample_rate: int = 16000, sample_width_bytes: int = 2) -> int:
    logger.debug(f"[WAV]: streaming leading silence chunks for {duration_s:.1f} seconds.")
    if duration_s <= 0.0:
        return 0

    total_s = 0.0
    chunk_s = chunk_ms / 1000.0
    chunks = 0
    while total_s < duration_s:
        chunk = make_silence_chunk(chunk_s, sample_rate, sample_width_bytes)
        await audio_queue.put(chunk)
        await asyncio.sleep(chunk_s * realtime_factor)
        total_s += chunk_s
        chunks += 1

    return chunks


@dataclass(frozen=True)
class WavFormat:
    """
    Description of wave format:
    - channels: Number of audio channels (1=mono, 2=stereo).
    - sample_width_bytes: Bytes per sample (1=8-bit, 2=16-bit, 4=32-bit).
    - sample_rate: Samples per second in Hz (e.g., 16000, 44100).
    - n_frames: Total number of audio frames in the file.
    - comptype: Compression type code ('NONE' for uncompressed PCM).
    - compname: Human-readable compression name ('not compressed' for PCM).
    """
    channels: int
    sample_width_bytes: int
    sample_rate: int
    n_frames: int
    comptype: str
    compname: str


def inspect_wav(path: Path) -> WavFormat:
    """
    Extract and return audio format metadata from a WAV file.

    Args:
        path: Path to the WAV file to inspect.

    Returns:
        WavFormat object: se object doc for explanation.
    """
    logger.debug("[WAV] analyzing file: %s", str(path))
    path = path.resolve()
    with wave.open(str(path), "rb") as wf:
        return WavFormat(channels=wf.getnchannels(), sample_width_bytes=wf.getsampwidth(),
                         sample_rate=wf.getframerate(), n_frames=wf.getnframes(), comptype=wf.getcomptype(),
                         compname=wf.getcompname(), )


def iter_wav_pcm_chunks(path: Path, *, chunk_ms: int, expected_sample_rate: int, expected_channels: int = 1,
                        expected_sample_width_bytes: int = 2, ) -> Iterator[bytes]:
    """
    Returns iterator: Yield raw PCM frames from a WAV file in fixed chunk sizes.

    Assumptions/enforced:
      - uncompressed PCM WAV (comptype == 'NONE')
      - expected sample rate / channels / sample width
      - output bytes are exactly what wave.readframes returns (interleaved if channels>1)

    If you want to support more formats later, extend here (not in tests).
    """
    fmt = inspect_wav(path)
    logger.debug("[WAV] file: %s; format: %r", str(path), fmt)

    if fmt.comptype != "NONE":
        raise ValueError(f"{path.name}: compressed WAV not supported (comptype={fmt.comptype} {fmt.compname})")
    if fmt.sample_rate != expected_sample_rate:
        raise ValueError(f"{path.name}: sample_rate={fmt.sample_rate} expected={expected_sample_rate}")
    if fmt.channels != expected_channels:
        raise ValueError(f"{path.name}: channels={fmt.channels} expected={expected_channels}")
    if fmt.sample_width_bytes != expected_sample_width_bytes:
        raise ValueError(
            f"{path.name}: sample_width_bytes={fmt.sample_width_bytes} expected={expected_sample_width_bytes}")

    frames_per_chunk = int(expected_sample_rate * (chunk_ms / 1000.0))
    logger.debug("[WAV] frames_per_chunk: %.0f (sample rate %d; chunk_ms %.0f)", frames_per_chunk, expected_sample_rate,
                 chunk_ms)
    if frames_per_chunk <= 0:
        raise ValueError("chunk_ms too small")

    with wave.open(str(path), "rb") as wf:
        logger.debug("Starting streaming...")
        while True:
            data = wf.readframes(frames_per_chunk)
            if not data:
                break
            yield data


async def stream_pcm_to_queue_realtime(pcm_chunks: Iterator[bytes], audio_queue: asyncio.Queue, chunk_ms: int, *,
                                       realtime_factor: float = 1.0, silence_s: float = 2.0,
                                       expected_sample_rate: int = 16000, expected_sample_width_bytes: int = 2,
                                       running: Optional[asyncio.Event] = None) -> int:
    """
    Stream PCM audio chunks to an async queue with real-time pacing.

    Feeds pre-chunked PCM data into a queue at a configurable rate, with silence
    padding at the start and end to help VAD detect speech boundaries. Pushes
    None as a sentinel when streaming completes.

    Args:
        pcm_chunks: Iterator yielding raw PCM audio data as bytes.
        audio_queue: Async queue to receive audio chunks. A None sentinel is
            pushed when streaming completes.
        chunk_ms: Duration of each chunk in milliseconds, used for pacing timing.
        realtime_factor: Playback speed multiplier:
            - 1.0 = real-time (default)
            - 0.5 = 2x faster
            - 0.0 = no delay between chunks (as fast as possible)
        silence_s: Duration of silence (in seconds) to add before and after
            the audio. Helps VAD properly detect speech start/end.
        expected_sample_rate: Sample rate in Hz, passed to silence generation.
        expected_sample_width_bytes: Bytes per sample (e.g., 2 for 16-bit),
            used for silence chunk generation.
        running: Optional asyncio.Event for cancellation. If provided and cleared,
            streaming stops early.

    Returns:
        Total number of chunks streamed (including silence chunks).
    """
    # Stream a moment of silence at the beginning, as I often see the first word cut off if we start immediately.
    total_chunks_streamed = await stream_silence(silence_s, audio_queue, chunk_ms,
        realtime_factor=realtime_factor, sample_rate=expected_sample_rate, sample_width_bytes=expected_sample_width_bytes)

    cnt = 0
    for chunk in pcm_chunks:
        if running is not None and not running.is_set():
            break
        await audio_queue.put(chunk)
        cnt += 1

        if cnt % 20 == 0:
            logger.debug(f"[WAV]: sent chunk {cnt}.")

        if realtime_factor > 0:
            await asyncio.sleep((chunk_ms / 1000.0) * realtime_factor)

    total_chunks_streamed += cnt

    # allow provider/VAD to finalize the "committed" transcript by sending silence at the end...
    total_chunks_streamed += await stream_silence(silence_s, audio_queue, chunk_ms,
        realtime_factor=realtime_factor, sample_rate=expected_sample_rate, sample_width_bytes=expected_sample_width_bytes)

    # cleanly close - this is important
    logger.info(f"Wav streaming: done, sent {cnt} chunks. Pushing None to the audio queue.")
    await audio_queue.put(None)

    # done
    return total_chunks_streamed


async def stream_wav_file(file: Path, audio_queue: asyncio.Queue, chunk_ms: int, expected_sample_rate: int, *,
                          realtime_factor: float = 1.0, silence: float = 2.0, expected_channels: int = 1,
                          expected_sample_width_bytes: int = 2, running: Optional[asyncio.Event] = None):
    """
    Stream a WAV file to an async queue with real-time pacing, suitable for STT providers.

    This is the main entry point for streaming audio. It validates the WAV format,
    chunks the audio into fixed-size PCM frames, and feeds them to the queue with
    configurable timing. Silence is added before and after the audio to help VAD
    (Voice Activity Detection) properly detect speech boundaries.

    Args:
        file: Path to the WAV file to stream.
        audio_queue: Async queue where PCM chunks will be placed. Consumers (e.g., STT
            providers) should read from this queue. A None sentinel is pushed when done.
        chunk_ms: Duration of each audio chunk in milliseconds (e.g., 200ms).
        expected_sample_rate: Required sample rate in Hz (e.g., 16000). Raises if mismatch.
        realtime_factor: Playback speed multiplier. 1.0 = real-time, 0.5 = 2x faster,
            0.0 = no delay between chunks.
        silence: Duration of silence (in seconds) to prepend and append to the audio.
            Helps prevent first/last word cutoff by giving VAD time to stabilize.
        expected_channels: Required channel count (default 1 for mono). Raises if mismatch.
        expected_sample_width_bytes: Required bytes per sample (default 2 for 16-bit).
            Raises if mismatch.
        running: Optional asyncio.Event for runtime cancellation. If set and cleared, streaming
            stops early.

    Returns:
        Total number of chunks streamed (including silence chunks).

    Raises:
        ValueError: If the WAV file format doesn't match expected parameters.
    """
    logger.debug("[WAV]: stream_wav_file %s", file)

    # Validate inputs
    if not file.exists():
        raise FileNotFoundError(f"WAV file not found: {file}")
    if not file.is_file():
        raise ValueError(f"Path is not a file: {file}")
    if chunk_ms < 10 or chunk_ms > 5000:
        raise ValueError(f"chunk_ms must be positive, more than 10 ms and less than 5000 (5 seconds), got {chunk_ms}")
    if expected_sample_rate <= 0:
        raise ValueError(f"expected_sample_rate must be positive, got {expected_sample_rate}")

    pcm_chunks_iterator = iter_wav_pcm_chunks(file, chunk_ms=chunk_ms, expected_sample_rate=expected_sample_rate,
                                              expected_channels=expected_channels,
                                              expected_sample_width_bytes=expected_sample_width_bytes, )

    # This actually streams chunks to the queue and blocks until it is done.
    return await stream_pcm_to_queue_realtime(pcm_chunks_iterator, audio_queue, chunk_ms=chunk_ms,
                                              realtime_factor=realtime_factor, silence_s=silence,
                                              expected_sample_rate=expected_sample_rate,
                                              expected_sample_width_bytes=expected_sample_width_bytes,
                                              running=running, )
