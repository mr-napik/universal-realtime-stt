from __future__ import annotations

import asyncio
import os
import unittest
from pathlib import Path
from typing import List
from logging import getLogger

from config import AUDIO_SAMPLE_RATE
from lib.assets import get_test_files
from lib.stt import init_stt_once, transcript_ingest_loop
from lib.wav_stream import iter_wav_pcm_chunks, stream_pcm_to_queue_realtime
from lib.utils import setup_logging


setup_logging()
logger = getLogger(__name__)


def _normalize_text(s: str) -> str:
    return " ".join(s.strip().split())


async def _ingest_transcripts_using_lib(
        running: asyncio.Event,
        transcript_queue: asyncio.Queue,
) -> List[str]:
    """
    Uses the same transcript ingest loop as the main project (lib/stt.py),
    so any logic changes there remain backportable.
    """
    result: List[str] = []
    await transcript_ingest_loop(running, transcript_queue, result)
    return result


class TestSttAssets(unittest.IsolatedAsyncioTestCase):
    async def test_assets_wav_streaming_matches_expected_txt(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        assets_dir = repo_root / "assets"

        chunk_ms = int(os.getenv("STT_TEST_CHUNK_MS", "20"))
        realtime_factor = float(os.getenv("STT_TEST_REALTIME_FACTOR", "1.0"))
        post_roll_silence_s = float(os.getenv("STT_TEST_POST_ROLL_SILENCE_S", "2.0"))

        pairs = list(get_test_files(assets_dir))
        if not pairs:
            assert False, "Found no files to test. Requires wav/txt pair in assets/."

        for pair in pairs:
            with self.subTest(msg=pair.wav.name):
                expected = _normalize_text(pair.txt.read_text(encoding="utf-8"))

                audio_queue: asyncio.Queue = asyncio.Queue(maxsize=200)
                transcript_queue: asyncio.Queue = asyncio.Queue(maxsize=200)
                running = asyncio.Event()
                running.set()

                stt_task = asyncio.create_task(init_stt_once(audio_queue, transcript_queue, running))
                ingest_task = asyncio.create_task(_ingest_transcripts_using_lib(running, transcript_queue))

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
                    # Stop STT sender cleanly.
                    await audio_queue.put(None)

                # Ensure STT session ends.
                await stt_task

                # Stop ingest loop and collect drained transcripts.
                await transcript_queue.put(None)
                segments = await ingest_task

                running.clear()

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
