from __future__ import annotations

import unittest
from datetime import datetime
from logging import getLogger
from os import getenv
from typing import Any, Type

from dotenv import load_dotenv

from config import AUDIO_SAMPLE_RATE, CHUNK_MS, TEST_REALTIME_FACTOR, FINAL_SILENCE_S, OUT_PATH, ASSETS_DIR
from helpers.load_assets import get_test_files
from helpers.benchmark import transcribe_and_diff
from lib.stt_provider_cartesia import CartesiaInkProvider, CartesiaSttConfig
from lib.stt_provider_deepgram import DeepgramRealtimeProvider, DeepgramSttConfig
from lib.stt_provider_elevenlabs import ElevenLabsRealtimeProvider, ElevenLabsSttConfig
from lib.stt_provider_google import GoogleRealtimeProvider, GoogleSttConfig
from lib.stt_provider_speechmatics import SpeechmaticsRealtimeProvider, SpeechmaticsSttConfig
from lib.utils import setup_logging

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

                provider = provider_cls(config)
                report = await transcribe_and_diff(
                    provider,
                    pair.wav,
                    pair.txt,
                    OUT_PATH / f"{ts}_{pair.wav.stem}.diff.html",
                    chunk_ms=CHUNK_MS,
                    sample_rate=AUDIO_SAMPLE_RATE,
                    realtime_factor=TEST_REALTIME_FACTOR,
                    silence_s=FINAL_SILENCE_S,
                )
                logger.info(f"{pair.wav.name} error rate: {report.character_error_rate:.1f}%")

                # Goal of the test is to check for realtime STT to work.
                # So as long as we receive similar lengths (tolerance 14%) string back, we are happy.
                # We do not verify whether what we got is correct transcription as part of the test here.
                self.assertAlmostEqual(len(report.expected), len(report.got), delta=len(report.expected) / 7.0)

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
