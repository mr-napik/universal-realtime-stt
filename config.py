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

# ElevenLabs voice config
# Latency: https://elevenlabs.io/docs/developers/best-practices/latency-optimization#use-flash-models
# Flash models deliver ~75ms inference speeds.
#
# Current voice: Anet 2.0
ELEVENLABS_TTS_VOICE_ID = "MpbYQvoTmXjHkaxtLiSh"
ELEVENLABS_TTS_MODEL = "eleven_turbo_v2_5"

# Text to speech parameters
# How similar are different renderings.
# Too low stability tends to lead to some strange artifacts in the voice.
# for v3 model it seems it must be one of: [0.0, 0.5, 1.0] (0.0 = Creative, 0.5 = Natural, 1.0 = Robust)
ELEVENLABS_TTS_STABILITY = 0.5
ELEVENLABS_TTS_SPEED = 0.9

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
TEMP_AUDIO_PATH = TMP_PATH / "user_input.wav"
