import os
from dotenv import load_dotenv
from pathlib import Path

load_dotenv()

# credentials
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")

# logging config
LOG_LEVEL = os.getenv("LOG_LEVEL", "DEV").upper()

# file config
BASE_PATH = Path(__file__).parent
TMP_PATH = BASE_PATH / "tmp"
TMP_PATH.mkdir(exist_ok=True)
ASSETS_DIR = Path(BASE_PATH / "assets")
assert ASSETS_DIR.exists()
LOG_PATH = BASE_PATH / "log"
LOG_PATH.mkdir(exist_ok=True)

# Speech to text parameters
# https://elevenlabs.io/docs/models
ELEVENLABS_STT_REALTIME_URL = "wss://api.elevenlabs.io/v1/speech-to-text/realtime"
ELEVENLABS_STT_REALTIME_MODEL = "scribe_v2_realtime"
# VAD threshold detects how long silence is needed before chunk is committed
# (ElevenLabs default is fairly long at 1.5). Drawback is that only after this time, we
# get the transcript, so if it is too long it introduces significant delay before AI even starts.
ELEVENLABS_STT_VAD_SILENCE_THRESHOLD_S = 0.7  # seconds
# How big difference between silence and speech, default 0.4 (
ELEVENLABS_STT_VAD_THRESHOLD = 0.6
# The minimum length of silence (in milliseconds) required to consider
# the audio as non-speech or to trigger a pause in detection.
ELEVENLABS_STT_MIN_SILENCE_DURATION_MS = 300  # milliseconds
# Minimum speech duration in milliseconds:
# how long segment needs to be, to be considered speech and not noise.
ELEVENLABS_STT_MIN_SPEECH_DURATION_MS = 1000   # milliseconds

# audio
AUDIO_SAMPLE_RATE = 16000
AUDIO_CHANNELS = 1
AUDIO_DURATION_SECONDS = 4.0
