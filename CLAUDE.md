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

## Test Output

- **HTML diffs** in `out/` — visual comparison of expected vs actual transcripts
- **Logs** in `log/` — DEBUG for project code (`lib.*`), INFO for third-party libraries
- Tests assert transcript length is within 14% of expected (CER-based tolerance)

## Configuration

`config.py` defines audio parameters (16kHz, mono, PCM16LE), VAD settings, and streaming parameters (200ms chunks). Key values referenced across providers:
- Language: `cs` (ISO 639-1) / `cs-CZ` (BCP-47, used by Google)
- Audio: 16kHz sample rate, mono, 16-bit PCM (`pcm_s16le`)
- Streaming: 200ms chunks, 1.0x realtime factor, 2s final silence padding

## Design Principles

- **Avoid provider SDKs** — providers are accessed directly via WebSocket (except Google which requires its SDK). This keeps dependencies light at the cost of more work if APIs change.
- **Config architecture** — universal STT params live in `config.py` (language, format, VAD). Provider-specific settings (model, URL, param name translations) live in each provider's frozen dataclass. API keys are only injected at instantiation time.
- **Queue-based IPC** — audio and transcript queues decouple streaming from processing. The test creates `audio_queue` (maxsize=40) and `transcript_queue` (maxsize=200).

## Adding a New Provider

1. Create `lib/stt_provider_<name>.py` with:
   - A frozen `@dataclass` config class (API key + provider-specific settings)
   - A class implementing the `RealtimeSttProvider` protocol from `lib/stt_provider.py`
   - The protocol requires: `async __aenter__`/`__aexit__`, `send_audio(bytes)`, `end_audio()`, `events() -> AsyncIterator[TranscriptEvent]`
2. Follow existing providers — they all use WebSocket with an internal `asyncio.Queue` for events
3. Add a test method in `tests/test_stt.py` following the pattern of existing tests (instantiate config, call `self._runner()`)
4. Add the API key env var to `.env`