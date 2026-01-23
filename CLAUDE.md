# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Speech-to-Text testing framework that validates STT provider accuracy by streaming WAV audio files to providers via WebSocket and comparing transcribed output against ground-truth transcripts. Currently implements ElevenLabs as the STT provider. Test audio is in Czech.

## Commands

```bash
# Setup
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Run tests (requires ELEVENLABS_API_KEY in .env)
pytest tests/test_stt_assets.py
pytest tests/test_stt_assets.py -v
```

## Architecture

The system uses async/await throughout with queue-based communication between components.

**Provider abstraction** (`lib/stt_provider.py`): Defines `RealtimeSttProvider` protocol — an async context manager that accepts audio bytes and yields `TranscriptEvent` objects. New providers implement this protocol.

**ElevenLabs provider** (`lib/stt_provider_elevenlabs.py`): Connects via WebSocket to `wss://api.elevenlabs.io/v1/speech-to-text/realtime`, sends base64-encoded audio chunks as JSON, receives committed transcript events.

**Session orchestration** (`lib/stt.py`): Runs two concurrent async tasks:
- Audio streamer: sends PCM chunks from a queue to the provider
- Transcript ingestor: collects committed transcripts into a result list

**WAV streaming** (`lib/wav_stream.py`): Reads WAV files, yields PCM chunks with realistic timing pacing, and appends post-roll silence to ensure VAD commits the final utterance.

**Validation** (`lib/diff_report.py`): Generates HTML diff reports and calculates Levenshtein distance. Tests pass if character error rate (CER) is ≤ 5%.

**Test assets** (`assets/`): WAV/TXT file pairs where the TXT contains the expected transcript. Audio must be PCM16000, mono, uncompressed. Convert with:
```bash
ffmpeg -i input.mp3 -ac 1 -ar 16000 -c:a pcm_s16le output.wav
```

## Configuration

`config.py` defines audio parameters (16kHz, mono, PCM16LE), VAD settings, and streaming parameters (200ms chunks). API key is loaded from `.env` via python-dotenv.