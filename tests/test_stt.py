from __future__ import annotations

import asyncio
import unittest
from datetime import datetime
from logging import getLogger
from os import getenv
from typing import List

from dotenv import load_dotenv

from config import AUDIO_SAMPLE_RATE, CHUNK_MS, TEST_REALTIME_FACTOR, FINAL_SILENCE_S, TMP_PATH, ASSETS_DIR
from lib.assets import get_test_files
from lib.diff import write_diff_report
from lib.stt import transcript_ingest_loop, init_stt_once_provider
from lib.stt_provider import RealtimeSttProvider
from lib.stt_provider_cartesia import CartesiaInkProvider, CartesiaSttConfig
from lib.stt_provider_elevenlabs import ElevenLabsRealtimeProvider
from lib.stt_provider_google import GoogleRealtimeProvider
from lib.utils import setup_logging
from lib.wav_stream import iter_wav_pcm_chunks, stream_pcm_to_queue_realtime


setup_logging()
logger = getLogger(__name__)
load_dotenv()


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


class TestStt(unittest.IsolatedAsyncioTestCase):
    async def _runner(self, provider: RealtimeSttProvider) -> None:
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')  # make sure all reports from run has same timestamp
        ts += "_" + provider.__class__.__name__

        pairs = list(get_test_files(ASSETS_DIR))
        if not pairs:
            assert False, f"Found no files to test. Requires at least one wav/txt pair in {ASSETS_DIR}."

        for pair in pairs:
            with self.subTest(msg=pair.wav.name):
                # read the expected file and normalize it.
                expected_raw = pair.txt.read_text(encoding="utf-8")

                # prepare streaming machinery
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
                # at least for eleven labs, we need this to be longer, otherwise we cna miss quite a lot.
                await asyncio.sleep(FINAL_SILENCE_S * 2)

                # Stop ingest loop and collect drained transcripts.
                await transcript_queue.put(None)
                segments = await ingest_task

                # get results
                got_raw = " ".join(segments)
                print(got_raw)

                # stop everything
                running.clear()

                # write report
                report_path = TMP_PATH / f"{ts}_{pair.wav.stem}.diff.html"
                report = write_diff_report(
                    expected=expected_raw,
                    got=got_raw,
                    out_path=report_path,
                    title=f"{pair.wav.name}",
                    sound_file=f"Asset: {pair.wav}\nExpected: {pair.txt}\n",
                )
                print(f"{pair.wav.name} error rate: {report.character_error_rate:.1f}%")

                # goal of the text is for STT to work,
                # so as long as we receive similar lengths (tolerance 10%) string back, we are happy.
                self.assertAlmostEqual(len(expected_raw), len(got_raw), delta=len(expected_raw) / 10.0)

    async def test_eleven_labs(self) -> None:
        provider = ElevenLabsRealtimeProvider()
        await self._runner(provider)

    async def test_google(self) -> None:
        provider = GoogleRealtimeProvider()
        await self._runner(provider)

    async def test_cartesia(self) -> None:
        provider = CartesiaInkProvider(CartesiaSttConfig(api_key=getenv("CARTESIA_API_KEY")))
        await self._runner(provider)
