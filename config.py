from pathlib import Path


# file config
BASE_PATH = Path(__file__).parent
TMP_PATH = BASE_PATH / "tmp"
TMP_PATH.mkdir(exist_ok=True)
ASSETS_DIR = Path(BASE_PATH / "assets")
assert ASSETS_DIR.exists()
LOG_PATH = BASE_PATH / "log"
LOG_PATH.mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# Universal STT Configuration (provider-independent)
# ---------------------------------------------------------------------------

# Language code for speech recognition (ISO 639-1)
STT_LANGUAGE = "cs"

# VAD threshold detects how long silence is needed before chunk is committed
# (ElevenLabs default is fairly long at 1.5). Drawback is that only after this time, we
# get the transcript, so if it is too long it introduces significant delay before AI even starts.
STT_VAD_SILENCE_THRESHOLD_S = 0.7  # seconds

# How big difference between silence and speech, default 0.4
STT_VAD_THRESHOLD = 0.6

# The minimum length of silence (in milliseconds) required to consider
# the audio as non-speech or to trigger a pause in detection.
STT_MIN_SILENCE_DURATION_MS = 300  # milliseconds

# Minimum speech duration in milliseconds:
# how long segment needs to be, to be considered speech and not noise.
STT_MIN_SPEECH_DURATION_MS = 1000   # milliseconds

# ---------------------------------------------------------------------------
# Audio Format Configuration
# ---------------------------------------------------------------------------

AUDIO_SAMPLE_RATE = 16000
AUDIO_CHANNELS = 1
AUDIO_SAMPLE_WIDTH_BYTES = 2  # 16-bit PCM
AUDIO_ENCODING = "pcm_s16le"  # PCM signed 16-bit little-endian
CHUNK_MS = 200

#Test config (0.0 = stream as fast as possible (no pacing), 1.0 = stream at natural pace).
TEST_REALTIME_FACTOR = 1.0
FINAL_SILENCE_S = 2.0
