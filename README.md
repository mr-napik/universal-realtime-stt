# Universal Realtime STT Library

A provider-agnostic library for realtime speech-to-text.

**Supports:**
- Cartesia https://cartesia.ai/
- Deepgram (nova-3) https://deepgram.com/
- ElevenLabs (scribe v2 realtime) https://elevenlabs.io/
- Google [Cloud Speech-to-Text API](https://console.cloud.google.com/apis/library/speech.googleapis.com)
- Speechmatics https://www.speechmatics.com/

Offers unified async interface — start one asyncio task, feed audio chunks to an input queue, and consume transcripts from an output queue without worrying about details.

Providers are accessed directly via WebSocket (no provider-specific SDKs, except Google). This keeps dependencies light.

## Installation

### Setup

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### IntelliJ IDEA Configuration (Mac)

After creating the venv:

1. Right-click on project → Open Module Settings (Option + Down)
2. Platform Settings → SDKs → Add SDK (+) → Add Python SDK from disk ...
3. Select existing SDK, type Python
4. Point to `<project>/.venv/bin/python`
5. Restart IDEA — the terminal should automatically activate `.venv`

Then install dependencies from `requirements.txt`.

### Environment Variables

Create a `.env` file with provider API keys:

```
ELEVENLABS_API_KEY=<key>
DEEPGRAM_API_KEY=<key>
SPEECHMATICS_API_KEY=<key>
CARTESIA_API_KEY=<key>
GOOGLE_APPLICATION_CREDENTIALS=<path-to-service-account.json>

# Optional — enables semantic understanding metric in benchmark and tests
GEMINI_API_KEY=<key>
```

## Integration and Use

The key modules have detailed docstrings explaining usage patterns and the queue-based architecture:

- **`lib/stt_provider.py`** — defines the `RealtimeSttProvider` protocol. Start here to understand the provider interface and how to implement a new one (includes a full code skeleton).
- **`lib/stt.py`** — the core `stt_session_task()` function that bridges audio input and transcript output. Docstring shows how to wire up the queues and run a session.
- **`lib/helper_transcript_ingest.py`** — a ready-made transcript consumer and a reference for building your own real-time consumer.

For end-to-end examples, see:

- **`lib/helper_stream_wav.py`** — `transcribe_wav_realtime()` ties everything together: queues, STT session, transcript ingestion, and WAV streaming.
- **`benchmark.py`** — runs all providers in parallel with result collation into a TSV report.

## Architecture

### Config Architecture

- **Universal config** (`config.py`): Provider-independent STT parameters — language, audio format, silence thresholds, VAD settings. Each provider may use slightly different naming and notation, but the source values come from here.
- **Provider config**: Each provider has a frozen dataclass with provider-specific settings (model name, URL, translation of universal parameter names to provider-specific ones).
- **API keys**: Injected only at provider instantiation time. Secrets are not handled inside the library — they are provided at the top level so that various methods can be used conveniently without diving deep into the lib.

### Dependencies

Provider-specific SDKs are avoided where possible. All providers except Google are accessed directly via WebSocket. This keeps the dependency footprint small but means more maintenance work if a provider changes their API.

`google-genai` is an optional dependency (commented out in `requirements.txt`) used only by the semantic understanding metric. Install it separately if needed — see [Semantic Understanding Metric](#semantic-understanding-metric).

## Testing

The test suite validates STT provider accuracy against ground-truth transcripts.

1. Scans the `assets/` directory for WAV/TXT file pairs
2. Streams each audio file to a realtime STT API and captures committed transcripts
3. Compares output against the corresponding TXT file (ground truth) and calculates a diff

**Note:** While the tests run on static files, they closely mimic realtime behavior — audio is streamed with realistic pacing and committed transcripts are received in real time.

### Running Tests

```bash
# All providers
pytest tests/test_stt.py -v

# Single provider
pytest tests/test_stt.py::TestStt::test_google -v
pytest tests/test_stt.py::TestStt::test_eleven_labs -v
pytest tests/test_stt.py::TestStt::test_deepgram -v
pytest tests/test_stt.py::TestStt::test_speechmatics -v
pytest tests/test_stt.py::TestStt::test_cartesia -v

# Speechmatics with LLM semantic understanding metric (requires GEMINI_API_KEY + google-genai)
pytest tests/test_stt.py::TestStt::test_speechmatics_semantics -v

# Diff report and LLM metric unit tests
pytest tests/test_diff.py -v
```

### Test Output

- HTML diff reports are generated in `out/`
- Logs are written to `log/`

### Audio Format

Test audio must be PCM 16kHz, mono, 16-bit. Convert with:

```bash
ffmpeg -i input.mp3 -ac 1 -ar 16000 -c:a pcm_s16le output.wav
```

## Semantic Understanding Metric

In addition to WER and CER, the benchmark and tests support an optional **Semantic Error Rate (SER)** metric. Instead of counting character or word differences, it uses a Gemini LLM to extract semantic facts (subject / predicate / object) from both the expected and STT transcripts and measures how many expected facts are missing from the STT output.

```
SER = facts_missing / (facts_both + facts_missing) * 100
```

Lower is better — same convention as WER and CER.

**To enable:**

1. Add `GEMINI_API_KEY=<key>` to `.env`
2. Install the optional dependency: `pip install google-genai`
   (or uncomment `google-genai` in `requirements.txt` and re-run `pip install -r requirements.txt`)

When active, `benchmark.py` adds a `custom_metric` (SER) column to the TSV report, and HTML diff reports gain a *Semantic Understanding* section with four stat cards (SER, Understanding, Missing%, Extra%) and a grouped fact list.

See [`doc/semantic_understanding_metric.md`](doc/semantic_understanding_metric.md) for full details, including how to write your own custom metric.

## TODO

### Code
- Verify configuration and retrieval of transcripts (as with Speechmatics, where we initially did not capture everything returned) — especially Google might suffer from a similar problem
- Optionally install all provider SDKs in a separate project and verify if there is something to improve (limit to providers we would consider using)

### More Providers to Consider
- OpenAI (check if they provide realtime STT)
- Soniox
- AWS
- Azure

### Publication
- Consider whether to publish on PyPI
- Consider licensing conditions
