from __future__ import annotations

import asyncio
import unittest
from datetime import datetime
from logging import getLogger
from typing import List

from config import AUDIO_SAMPLE_RATE, CHUNK_MS, TEST_REALTIME_FACTOR, FINAL_SILENCE_S, TMP_PATH, ASSETS_DIR
from lib.assets import get_test_files
from lib.diff_report import write_diff_html
from lib.stt import transcript_ingest_loop, init_stt_once_provider
from lib.stt_provider import RealtimeSttProvider
from lib.stt_provider_elevenlabs import ElevenLabsRealtimeProvider
from lib.utils import setup_logging
from lib.wav_stream import iter_wav_pcm_chunks, stream_pcm_to_queue_realtime

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
    async def _runner(self, provider: RealtimeSttProvider) -> None:
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')  # make sure all reports from run has same timestamp
        pairs = list(get_test_files(ASSETS_DIR))
        if not pairs:
            assert False, f"Found no files to test. Requires at least one wav/txt pair in {ASSETS_DIR}."

        for pair in pairs:
            with self.subTest(msg=pair.wav.name):
                expected = _normalize_text(pair.txt.read_text(encoding="utf-8"))

                audio_queue: asyncio.Queue = asyncio.Queue(maxsize=200)
                transcript_queue: asyncio.Queue = asyncio.Queue(maxsize=200)
                running = asyncio.Event()
                running.set()

                stt_task = asyncio.create_task(init_stt_once_provider(provider, audio_queue, transcript_queue, running))
                ingest_task = asyncio.create_task(_ingest_transcripts_using_lib(running, transcript_queue))

                pcm_chunks_iterator = iter_wav_pcm_chunks(
                    pair.wav,
                    chunk_ms=CHUNK_MS,
                    expected_sample_rate=AUDIO_SAMPLE_RATE,
                    expected_channels=1,
                    expected_sample_width_bytes=2,
                )

                # this actually starts streaming chunks to the queue and runs test (as other tasks are already waiting)
                await stream_pcm_to_queue_realtime(
                    pcm_chunks_iterator,
                    audio_queue,
                    chunk_ms=CHUNK_MS,
                    realtime_factor=TEST_REALTIME_FACTOR,
                    post_roll_silence_s=FINAL_SILENCE_S,
                    running=running,
                )

                # Ensure STT session ends.
                await stt_task

                # give some time to STT and transcript collection to wrap up (in addition to silence).
                await asyncio.sleep(FINAL_SILENCE_S)

                # Stop ingest loop and collect drained transcripts.
                await transcript_queue.put(None)
                segments = await ingest_task

                running.clear()

                got = _normalize_text(" ".join(segments))

                # evaluate result
                if got != expected:
                    report_path = TMP_PATH / f"{ts}_{pair.wav.stem}.diff.html"
                    report = write_diff_html(
                        expected=expected,
                        got=got,
                        out_path=report_path,
                        title=f"{pair.wav.name}",
                        context_hint=f"Asset: {pair.wav}\nExpected: {pair.txt}\n",
                    )

                    character_error_rate = round(float(report.levenshtein) / len(expected) * 100, 1)
                    print(f"{pair.wav.name} error rate: {character_error_rate:.1f}%")
                    if character_error_rate > 5:
                        self.fail(
                            f"{pair.wav.name} error rate: {character_error_rate:.1f}%\n"
                            f"Transcript mismatch for {pair.wav.name}\n"
                            f"Diff report written to: {report.html_path}\n"
                        )
                else:
                    print("Exact match! Wow!")

    async def test_eleven_labs(self) -> None:
        # default provider stays ElevenLabs (backportable)
        provider = ElevenLabsRealtimeProvider()
        await self._runner(provider)
