# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Speech-to-Text testing framework that validates STT provider accuracy by streaming WAV audio files to providers via WebSocket and comparing transcribed output against ground-truth transcripts. Test audio is in Czech.

## Commands

```bash
# Setup
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Run all provider tests
pytest tests/test_stt.py -v

# Run a single provider test
pytest tests/test_stt.py::TestStt::test_eleven_labs -v
pytest tests/test_stt.py::TestStt::test_google -v
pytest tests/test_stt.py::TestStt::test_deepgram -v
pytest tests/test_stt.py::TestStt::test_speechmatics -v
pytest tests/test_stt.py::TestStt::test_cartesia -v
```

## Environment Variables

Provider API keys in `.env`:
- `ELEVENLABS_API_KEY` - ElevenLabs
- `DEEPGRAM_API_KEY` - Deepgram
- `SPEECHMATICS_API_KEY` - Speechmatics
- `CARTESIA_API_KEY` - Cartesia
- `GOOGLE_APPLICATION_CREDENTIALS` - Path to Google service account JSON (uses ADC)

## Architecture

The system uses async/await throughout with queue-based communication between components.

**Provider abstraction** (`lib/stt_provider.py`): Defines `RealtimeSttProvider` protocol — an async context manager that accepts audio bytes and yields `TranscriptEvent` objects. New providers implement this protocol.

**Provider implementations** (`lib/stt_provider_*.py`): Each provider (ElevenLabs, Google, Deepgram, Speechmatics, Cartesia) has its own module implementing the protocol with provider-specific WebSocket handling and configuration dataclass.

**Session orchestration** (`lib/stt.py`): Runs two concurrent async tasks:
- Audio streamer: sends PCM chunks from a queue to the provider
- Transcript ingestor: collects committed transcripts into a result list

**WAV streaming** (`lib/helper_stream_wav.py`): Reads WAV files, yields PCM chunks with realistic timing pacing, and appends silence padding to ensure VAD commits the final utterance.

**Validation** (`lib/helper_diff.py`): Generates HTML diff reports in `out/` and calculates Levenshtein distance-based character error rate (CER).

**Test assets** (`assets/`): WAV/TXT file pairs where the TXT contains the expected transcript. Audio must be PCM 16kHz, mono, 16-bit. Convert with:
```bash
ffmpeg -i input.mp3 -ac 1 -ar 16000 -c:a pcm_s16le output.wav
```

## Configuration

`config.py` defines audio parameters (16kHz, mono, PCM16LE), VAD settings, and streaming parameters (200ms chunks).