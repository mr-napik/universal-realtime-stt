import asyncio
import os
import shutil
import subprocess
from pathlib import Path
from typing import Iterable, List, Tuple

import pytest

from lib.stt import init_stt_once
from config import AUDIO_SAMPLE_RATE


ASSETS_DIR = Path(__file__).resolve().parents[1] / "assets"

# Real-time-ish streaming parameters
CHUNK_MS = int(os.getenv("STT_TEST_CHUNK_MS", "20"))  # 20ms chunks is common
REALTIME_FACTOR = float(os.getenv("STT_TEST_REALTIME_FACTOR", "1.0"))  # 1.0 = realtime, 0.0 = as fast as possible
POST_ROLL_SILENCE_S = float(os.getenv("STT_TEST_POST_ROLL_SILENCE_S", "2.0"))  # allow VAD to commit final segment
MAX_COLLECT_IDLE_S = float(os.getenv("STT_TEST_MAX_COLLECT_IDLE_S", "2.0"))  # idle window after stop


def _list_audio_assets() -> List[Path]:
    if not ASSETS_DIR.exists():
        return []
    files = []
    for ext in (".wav", ".mp3"):
        files.extend(sorted(ASSETS_DIR.rglob(f"*{ext}")))
    return files


def _expected_txt_for(audio_path: Path) -> Path:
    return audio_path.with_suffix(".txt")


def _normalize_text(s: str) -> str:
    # Keep this conservative; adjust if you want more forgiving matching.
    return " ".join(s.strip().split())


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _decode_to_pcm_s16le_mono_16k(audio_path: Path) -> bytes:
    """
    Convert arbitrary wav/mp3 to PCM s16le mono @ AUDIO_SAMPLE_RATE.
    Uses ffmpeg for consistent decoding/resampling across formats.

    Returns raw PCM bytes (little-endian int16 samples).
    """
    if not _ffmpeg_available():
        raise RuntimeError(
            "ffmpeg not found in PATH. Install ffmpeg or adjust the test to only use compatible WAV files."
        )

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(audio_path),
        "-f",
        "s16le",
        "-acodec",
        "pcm_s16le",
        "-ac",
        "1",
        "-ar",
        str(AUDIO_SAMPLE_RATE),
        "pipe:1",
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed for {audio_path.name}: {proc.stderr.decode('utf-8', errors='replace')}")
    return proc.stdout


async def _producer_stream_pcm(
        pcm: bytes,
        audio_queue: asyncio.Queue,
        running: asyncio.Event,
) -> None:
    """
    Feed PCM into the audio_queue in paced chunks to mimic realtime streaming.
    """
    bytes_per_sample = 2  # s16le
    samples_per_chunk = int(AUDIO_SAMPLE_RATE * (CHUNK_MS / 1000.0))
    chunk_size = samples_per_chunk * bytes_per_sample

    # stream audio
    for i in range(0, len(pcm), chunk_size):
        if not running.is_set():
            break
        await audio_queue.put(pcm[i : i + chunk_size])

        if REALTIME_FACTOR > 0:
            await asyncio.sleep((CHUNK_MS / 1000.0) * REALTIME_FACTOR)

    # post-roll silence time so VAD can commit final transcript
    if running.is_set() and POST_ROLL_SILENCE_S > 0:
        if REALTIME_FACTOR > 0:
            await asyncio.sleep(POST_ROLL_SILENCE_S * REALTIME_FACTOR)
        else:
            # still give the event loop a chance
            await asyncio.sleep(0.0)


async def _collector(
        transcript_queue: asyncio.Queue,
        running: asyncio.Event,
) -> List[str]:
    """
    Collect committed transcripts until 'running' is cleared and the queue stays idle for a bit.
    """
    out: List[str] = []
    idle_deadline = None

    while True:
        try:
            item = await asyncio.wait_for(transcript_queue.get(), timeout=0.2)
            if item is None:
                continue
            out.append(item)
            idle_deadline = None  # reset idle detection on activity
        except asyncio.TimeoutError:
            if running.is_set():
                continue

            # after stop: wait until the queue is idle for MAX_COLLECT_IDLE_S
            if idle_deadline is None:
                idle_deadline = asyncio.get_event_loop().time() + MAX_COLLECT_IDLE_S

            if asyncio.get_event_loop().time() >= idle_deadline:
                break

    return out


def _make_param_list() -> List[Tuple[Path, Path]]:
    assets = _list_audio_assets()
    params: List[Tuple[Path, Path]] = []
    for audio in assets:
        expected = _expected_txt_for(audio)
        if expected.exists():
            params.append((audio, expected))
    return params


PARAMS = _make_param_list()


@pytest.mark.parametrize(
    "audio_path, expected_txt",
    PARAMS,
    ids=lambda p: getattr(p, "name", str(p)),
)
@pytest.mark.asyncio
async def test_assets_stream_to_stt_and_match_expected(audio_path: Path, expected_txt: Path) -> None:
    """
    One test per asset file: stream audio -> collect committed transcripts -> compare to expected .txt
    """
    expected = _normalize_text(expected_txt.read_text(encoding="utf-8"))

    # decode to PCM the STT session expects
    pcm = _decode_to_pcm_s16le_mono_16k(audio_path)

    audio_queue: asyncio.Queue = asyncio.Queue(maxsize=200)
    transcript_queue: asyncio.Queue = asyncio.Queue(maxsize=200)
    running = asyncio.Event()
    running.set()

    stt_task = asyncio.create_task(init_stt_once(audio_queue, transcript_queue, running))
    collector_task = asyncio.create_task(_collector(transcript_queue, running))

    try:
        await _producer_stream_pcm(pcm, audio_queue, running)
    finally:
        # stop the STT session after streaming finishes
        running.clear()

    # wait for session and transcript ingest to settle
    await stt_task
    committed_segments = await collector_task

    got = _normalize_text(" ".join(committed_segments))

    assert got == expected, (
        f"Transcript mismatch for {audio_path.name}\n\n"
        f"EXPECTED:\n{expected}\n\n"
        f"GOT:\n{got}\n\n"
        f"SEGMENTS:\n{committed_segments}\n"
    )


def test_assets_present_or_skipped() -> None:
    """
    Guardrail: if there are audio files but no matching .txt files, fail loudly.
    """
    assets = _list_audio_assets()
    if not assets:
        pytest.skip("No assets/*.wav or assets/*.mp3 found.")

    matched = set(a for a, _ in PARAMS)
    missing = [a for a in assets if _expected_txt_for(a).exists() and a not in matched]
    # (currently PARAMS already includes expected exists; this is just future-proof)
    if missing:
        pytest.fail(f"Some assets had .txt but were not picked up: {[p.name for p in missing]}")

    if not PARAMS:
        pytest.fail(
            "Found audio assets, but none had matching .txt files (same basename). "
            "Example: assets/foo.wav + assets/foo.txt"
        )
