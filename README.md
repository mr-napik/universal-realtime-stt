# Universal Realtime STT Library

A provider-agnostic library for realtime speech-to-text. Supports ElevenLabs, Google, Deepgram, Speechmatics, and Cartesia through a unified async interface — start one asyncio task, feed audio chunks to an input queue, and consume transcripts from an output queue.

Providers are accessed directly via WebSocket (no provider-specific SDKs, except Google which requires its own). This keeps dependencies light at the cost of additional work if a provider's API changes.

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
2. Platform Settings → Add SDK → New SDK
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
```

### Integration and Use

TODO — sample code showing how the lib should be used.

## Architecture

### Config Architecture

- **Universal config** (`config.py`): Provider-independent STT parameters — language, audio format, silence thresholds, VAD settings. Each provider may use slightly different naming and notation, but the source values come from here.
- **Provider config**: Each provider has a frozen dataclass with provider-specific settings (model name, URL, translation of universal parameter names to provider-specific ones).
- **API keys**: Injected only at provider instantiation time. Secrets are not handled inside the library — they are provided at the top level so that various methods can be used conveniently without diving deep into the lib.

### Dependencies

Provider-specific SDKs are avoided where possible. All providers except Google are accessed directly via WebSocket. This keeps the dependency footprint small but means more maintenance work if a provider changes their API.

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
```

### Test Output

- HTML diff reports are generated in `out/`
- Logs are written to `log/`

### Audio Format

Test audio must be PCM 16kHz, mono, 16-bit. Convert with:

```bash
ffmpeg -i input.mp3 -ac 1 -ar 16000 -c:a pcm_s16le output.wav
```

## TODO

### Data
- Get various audio files to cover approx 1 hour of data — ideally around 10 male and 10 female voices of various types
- Get the ground truth (use transcript from source, then check manually)

### Code
- Update the code to aggregate results for test cases into one file for a nice overview (this might only be needed for the larger test, not the unit test suite)
- Consider whether to make the large-scale test a standalone app rather than a test case — with lower logging and the ability to run different providers simultaneously to save time (ideally all providers in parallel)
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
