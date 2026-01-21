from __future__ import annotations

import asyncio
import os
import unittest
from pathlib import Path
from typing import List
from logging import getLogger

from config import AUDIO_SAMPLE_RATE
from lib.assets import get_test_files
from lib.stt import init_stt_once
from lib.wav_stream import iter_wav_pcm_chunks, stream_pcm_to_queue_realtime
from lib.utils import setup_logging


# Real-time-ish streaming parameters
CHUNK_MS = 200  # 200ms chunks is common
REALTIME_FACTOR = 1.0   # 1.0 = realtime, 0.0 = as fast as possible
POST_ROLL_SILENCE_S = 2.0 # allow VAD to commit final segment
MAX_COLLECT_IDLE_S = 2.0  # idle window after stop



setup_logging()
logger = getLogger(__name__)


def _normalize_text(s: str) -> str:
    # Conservative normalizer; adjust in one place if needed.
    return " ".join(s.strip().split())


async def _collect_committed_transcripts(
        transcript_queue: asyncio.Queue,
        running: asyncio.Event,
        *,
        idle_after_stop_s: float = 2.0,
) -> List[str]:
    """
    Collect committed transcript segments from transcript_queue.

    Behavior:
      - while running: wait for items
      - after running cleared: stop once queue remains idle for idle_after_stop_s
    """
    out: List[str] = []
    loop = asyncio.get_running_loop()
    idle_deadline = None

    while True:
        try:
            item = await asyncio.wait_for(transcript_queue.get(), timeout=0.25)
            if item is None:
                continue
            out.append(str(item))
            idle_deadline = None
        except asyncio.TimeoutError:
            if running.is_set():
                continue

            if idle_deadline is None:
                idle_deadline = loop.time() + idle_after_stop_s

            if loop.time() >= idle_deadline:
                break

    return out


class TestSttAssets(unittest.IsolatedAsyncioTestCase):
    """
    One test method, multiple subtests (one per asset).
    Plays nicely in IntelliJ/IDEA.
    """

    async def test_assets_wav_streaming_matches_expected_txt(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        assets_dir = repo_root / "assets"

        chunk_ms = int(os.getenv("STT_TEST_CHUNK_MS", "20"))
        realtime_factor = float(os.getenv("STT_TEST_REALTIME_FACTOR", "1.0"))
        post_roll_silence_s = float(os.getenv("STT_TEST_POST_ROLL_SILENCE_SILENCE_S", "2.0"))
        idle_after_stop_s = float(os.getenv("STT_TEST_IDLE_AFTER_STOP_S", "2.0"))

        pairs = list(get_test_files(assets_dir))
        if not pairs:
            assert False, "Found no files to test. Requires wav/txt pair in assets/."

        for pair in pairs:
            with self.subTest(asset=pair.wav.name, msg=pair.wav.name):
                expected = _normalize_text(pair.txt.read_text(encoding="utf-8"))

                audio_queue: asyncio.Queue = asyncio.Queue(maxsize=200)
                transcript_queue: asyncio.Queue = asyncio.Queue(maxsize=200)
                running = asyncio.Event()
                running.set()

                stt_task = asyncio.create_task(init_stt_once(audio_queue, transcript_queue, running))
                collector_task = asyncio.create_task(
                    _collect_committed_transcripts(transcript_queue, running, idle_after_stop_s=idle_after_stop_s)
                )

                try:
                    pcm_chunks = iter_wav_pcm_chunks(
                        pair.wav,
                        chunk_ms=chunk_ms,
                        expected_sample_rate=AUDIO_SAMPLE_RATE,
                        expected_channels=1,
                        expected_sample_width_bytes=2,
                    )
                    await stream_pcm_to_queue_realtime(
                        pcm_chunks,
                        audio_queue,
                        chunk_ms=chunk_ms,
                        realtime_factor=realtime_factor,
                        post_roll_silence_s=post_roll_silence_s,
                        running=running,
                    )
                finally:
                    running.clear()

                # ensure STT session ends and we collected tail commits
                await stt_task
                segments = await collector_task

                got = _normalize_text(" ".join(segments))

                self.assertEqual(
                    got,
                    expected,
                    msg=(
                        f"Transcript mismatch for {pair.wav.name}\n\n"
                        f"EXPECTED:\n{expected}\n\n"
                        f"GOT:\n{got}\n\n"
                        f"SEGMENTS:\n{segments}\n"
                    ),
                )
