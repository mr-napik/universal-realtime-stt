from __future__ import annotations

import asyncio
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional
from lib.utils import _make_silence_chunk
from config import AUDIO_SAMPLE_RATE

@dataclass(frozen=True)
class WavFormat:
    channels: int
    sample_width_bytes: int
    sample_rate: int
    n_frames: int
    comptype: str
    compname: str


def inspect_wav(path: Path) -> WavFormat:
    path = path.resolve()
    with wave.open(str(path), "rb") as wf:
        return WavFormat(
            channels=wf.getnchannels(),
            sample_width_bytes=wf.getsampwidth(),
            sample_rate=wf.getframerate(),
            n_frames=wf.getnframes(),
            comptype=wf.getcomptype(),
            compname=wf.getcompname(),
        )


def iter_wav_pcm_chunks(
        path: Path,
        *,
        chunk_ms: int,
        expected_sample_rate: int,
        expected_channels: int = 1,
        expected_sample_width_bytes: int = 2,
) -> Iterator[bytes]:
    """
    Yield raw PCM frames from a WAV file in fixed chunk sizes.

    Assumptions/enforced:
      - uncompressed PCM WAV (comptype == 'NONE')
      - expected sample rate / channels / sample width
      - output bytes are exactly what wave.readframes returns (interleaved if channels>1)

    If you want to support more formats later, extend here (not in tests).
    """
    fmt = inspect_wav(path)

    if fmt.comptype != "NONE":
        raise ValueError(f"{path.name}: compressed WAV not supported (comptype={fmt.comptype} {fmt.compname})")
    if fmt.sample_rate != expected_sample_rate:
        raise ValueError(f"{path.name}: sample_rate={fmt.sample_rate} expected={expected_sample_rate}")
    if fmt.channels != expected_channels:
        raise ValueError(f"{path.name}: channels={fmt.channels} expected={expected_channels}")
    if fmt.sample_width_bytes != expected_sample_width_bytes:
        raise ValueError(
            f"{path.name}: sample_width_bytes={fmt.sample_width_bytes} expected={expected_sample_width_bytes}"
        )

    frames_per_chunk = int(expected_sample_rate * (chunk_ms / 1000.0))
    if frames_per_chunk <= 0:
        raise ValueError("chunk_ms too small")

    with wave.open(str(path), "rb") as wf:
        while True:
            data = wf.readframes(frames_per_chunk)
            if not data:
                break
            yield data


async def stream_pcm_to_queue_realtime(
        pcm_chunks: Iterator[bytes],
        audio_queue: asyncio.Queue,
        *,
        chunk_ms: int,
        realtime_factor: float = 1.0,
        post_roll_silence_s: float = 2.0,
        running: Optional[asyncio.Event] = None,
) -> None:
    """
    Put PCM chunks into audio_queue with real-time-ish pacing.

    realtime_factor:
      - 1.0 = realtime
      - 0.5 = 2x faster
      - 0.0 = no pacing sleep (still chunked)
    """

    cnt = 0
    for chunk in pcm_chunks:
        if running is not None and not running.is_set():
            break
        await audio_queue.put(chunk)
        cnt += 1

        if cnt % 20 == 0:
            print(f"Wav stream: sent chunk {cnt}...", flush=True)

        if realtime_factor > 0:
            await asyncio.sleep((chunk_ms / 1000.0) * realtime_factor)

    # allow provider/VAD to finalize "committed" transcript by sending silence...
    tot = 0.0
    while tot < post_roll_silence_s:
        chunk = _make_silence_chunk(AUDIO_SAMPLE_RATE, chunk_ms/1000.0)
        await audio_queue.put(chunk)
        await asyncio.sleep((chunk_ms / 1000.0) * realtime_factor)
        tot += chunk_ms/1000.0

    # cleanly close - this is important
    await audio_queue.put(None)
