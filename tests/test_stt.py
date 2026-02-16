from __future__ import annotations

import asyncio
import unittest
from datetime import datetime
from logging import getLogger
from os import getenv
from typing import Any, List, Type

from dotenv import load_dotenv

from config import AUDIO_SAMPLE_RATE, CHUNK_MS, TEST_REALTIME_FACTOR, FINAL_SILENCE_S, OUT_PATH, ASSETS_DIR
from lib.helper_load_assets import get_test_files
from lib.helper_diff import write_diff_report
from lib.stt import transcript_ingest_loop, init_stt_once_provider
from lib.stt_provider_cartesia import CartesiaInkProvider, CartesiaSttConfig
from lib.stt_provider_deepgram import DeepgramRealtimeProvider, DeepgramSttConfig
from lib.stt_provider_speechmatics import SpeechmaticsRealtimeProvider, SpeechmaticsSttConfig
from lib.stt_provider_elevenlabs import ElevenLabsRealtimeProvider, ElevenLabsSttConfig
from lib.stt_provider_google import GoogleRealtimeProvider, GoogleSttConfig
from lib.utils import setup_logging
from lib.helper_stream_wav import stream_wav_file


setup_logging()
logger = getLogger(__name__)
load_dotenv()


class TestStt(unittest.IsolatedAsyncioTestCase):
    async def _runner(self, provider_cls: Type[Any], config: Any) -> None:
        logger.info("Starting test runner for %s.", provider_cls.__name__)

        ts = datetime.now().strftime('%Y%m%d_%H%M%S')  # make sure all reports from run has same timestamp
        ts += "_" + provider_cls.__name__

        pairs = list(get_test_files(ASSETS_DIR))
        if not pairs:
            assert False, f"Found no files to test. Requires at least one wav/txt pair in {ASSETS_DIR}."

        for pair in pairs:
            with self.subTest(msg=pair.wav.name):
                logger.info("Processing file %s.", pair.wav.name)

                # Instantiate a fresh provider for each file
                provider = provider_cls(config)

                # read the expected file and normalize it.
                expected_raw = pair.txt.read_text(encoding="utf-8")

                # prepare streaming machinery
                audio_queue: asyncio.Queue = asyncio.Queue(maxsize=40)  # no need for long queue, we should stream near realtime
                transcript_queue: asyncio.Queue = asyncio.Queue(maxsize=200)
                transcript_segments: List[str] = []
                running = asyncio.Event()
                running.set()

                stt_task = asyncio.create_task(init_stt_once_provider(provider, audio_queue, transcript_queue, running))
                ingest_task = asyncio.create_task(transcript_ingest_loop(running, transcript_queue, transcript_segments))

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

                # Send a stop also to the ingest loop and collect drained transcripts.
                await ingest_task

                # get results
                got_raw = " ".join(transcript_segments)
                logger.info("Final transcript raw: %r", got_raw)

                # stop everything
                running.clear()

                # calculated diff and write report file
                fname = f"{ts}_{pair.wav.stem}.diff.html"
                report_path = OUT_PATH / fname
                report = write_diff_report(
                    expected=expected_raw,
                    got=got_raw,
                    out_path=report_path,
                    title=f"{pair.wav.name}: {provider.__class__.__name__}",
                    detail=f"Provider: {provider.__class__.__name__}\nSound: {pair.wav.name}\nExpected: {pair.txt.name}\nReport: {fname}",
                )
                logger.info(f"{pair.wav.name} error rate: {report.character_error_rate:.1f}%")

                # Goal of the test is to check for realtime STT to work.
                # So as long as we receive similar lengths (tolerance 14%) string back, we are happy.
                # We do not verify whether what we got is relevant as part of the test result here.
                self.assertAlmostEqual(len(expected_raw), len(got_raw), delta=len(expected_raw) / 7.0)

    async def test_cartesia(self) -> None:
        config = CartesiaSttConfig(api_key=getenv("CARTESIA_API_KEY"))
        await self._runner(CartesiaInkProvider, config)

    async def test_deepgram(self) -> None:
        config = DeepgramSttConfig(api_key=getenv("DEEPGRAM_API_KEY"))
        await self._runner(DeepgramRealtimeProvider, config)

    async def test_eleven_labs(self) -> None:
        config = ElevenLabsSttConfig(api_key=getenv("ELEVENLABS_API_KEY"))
        await self._runner(ElevenLabsRealtimeProvider, config)

    async def test_google(self) -> None:
        # Google uses Application Default Credentials (ADC), not an API key.
        # Set GOOGLE_APPLICATION_CREDENTIALS env var to your service account JSON.
        config = GoogleSttConfig()
        await self._runner(GoogleRealtimeProvider, config)

    async def test_speechmatics(self) -> None:
        config = SpeechmaticsSttConfig(api_key=getenv("SPEECHMATICS_API_KEY"))
        await self._runner(SpeechmaticsRealtimeProvider, config)
