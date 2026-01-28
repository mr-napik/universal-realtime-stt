from __future__ import annotations

import asyncio
import unittest
from datetime import datetime
from logging import getLogger
from os import getenv
from typing import List

from dotenv import load_dotenv

from config import AUDIO_SAMPLE_RATE, CHUNK_MS, TEST_REALTIME_FACTOR, FINAL_SILENCE_S, TMP_PATH, ASSETS_DIR
from lib.helper_load_assets import get_test_files
from lib.helper_diff import write_diff_report
from lib.stt import transcript_ingest_loop, init_stt_once_provider
from lib.stt_provider import RealtimeSttProvider
from lib.stt_provider_cartesia import CartesiaInkProvider, CartesiaSttConfig
from lib.stt_provider_elevenlabs import ElevenLabsRealtimeProvider
from lib.stt_provider_google import GoogleRealtimeProvider
from lib.utils import setup_logging
from lib.helper_stream_wav import stream_wav_file


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

                # This actually starts streaming chunks to the queue and blocks until it is done
                # Other tasks are already waiting to process the queue.
                await stream_wav_file(
                    pair.wav,
                    audio_queue,
                    CHUNK_MS,
                    AUDIO_SAMPLE_RATE,
                    realtime_factor=TEST_REALTIME_FACTOR,
                    silence=FINAL_SILENCE_S,
                    running=running,
                )

                # At this point, streaming is completed and all chunks sent.
                # Ensure STT session ends (and task completes).
                await stt_task

                # Give some time to STT and transcript collection to wrap up (in addition to the silence).
                # This is time after the streaming ends we wait for last transcript to arrive.
                logger.debug("Waiting for SST task to complete for %.1f s", FINAL_SILENCE_S * 2)
                await asyncio.sleep(FINAL_SILENCE_S * 2)

                # Send a stop also to the ingest loop and collect drained transcripts.
                await transcript_queue.put(None)
                segments = await ingest_task

                # get results
                got_raw = " ".join(segments)
                logger.info("Final transcript raw: %r", got_raw)

                # stop everything
                running.clear()

                # calculated diff and write report
                report_path = TMP_PATH / f"{ts}_{pair.wav.stem}.diff.html"
                report = write_diff_report(
                    expected=expected_raw,
                    got=got_raw,
                    out_path=report_path,
                    title=f"{pair.wav.name}",
                    sound_file=f"Asset: {pair.wav}\nExpected: {pair.txt}\n",
                )
                logger.info(f"{pair.wav.name} error rate: {report.character_error_rate:.1f}%")

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
