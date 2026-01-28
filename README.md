# Universal Realtime STT Lib

Tool to test various STT providers.

1) A unit test suite that looks into the assets directory for any mp3 or wav file it finds there.
2) It will stream them to a realtime STT API and capture the resulting committed transcripts. 
3) It will compare them to a txt file of the same name as the audio file (source of ground truth) and calculate diff.

**Note:** While we are running the test statically on files, it closely mimics behavior when the audio is streamed in real time, and we receive committed transcripts in real time. 

Goal is to build a robust testing pipeline which we can use to test multiple providers.

## .venv config in IntelliJ IDEA (on Mac)

```
# Create venv
python3.13 -m venv .venv

# Test
source .venv/bin/activate
python --version
```

Then in IntelliJ 2025.3 (Mac):
1) Right click on project
2) Open module setting (also option + down)
3) Select platform settings
4) Select Add SDK
5) New SDK
6) Select existing SDK, type Python
7) Fins the <project>/.venv/bin/python

Then restart Idea and check that the terminal automatically opens to (.venv). This confirms that Idea understands everything correctly.

Then install dependencies from requirements.


## Config Architecture

In an ideal case, universal STT configuration should be taken from config.py (which should contain universal stt parametrization that is provider independent like language, format, silences, VAD etc. - even though that each provider might use slightly different naming and notation). 

The provider specific object should contain provider specific settings like model name and url. 

Finally, the api key should only be taken at the moment of provider object instantiation, as that is a secret, and we do not want to handle it inside the library. Secret should really be something that the user provides at top level, so that various methods can be conveniently used without diving deep into the lib.


## Potential Test Asset Sources
- https://www.pametnaroda.cz/cs/archive
- https://youtu.be/Der9UHsGinI
- https://youtu.be/DA6mbcmEZPc
- https://ceskepodcasty.cz/podcast/senior-life-podcast

The sample file is taken from here: https://www.pametnaroda.cz/cs/vodicka-adolf-1913

### Converting Test Assets to WAV (on Mac)

Most sources are in MP3 or other compressed format. Most STT for example Eleven Labs expect PCM16000 mono. Following commands can convert assets to acceptable format to run the test suite.

```
afconvert input.mp3 output.wav -f WAVE -d LEI16@16000 -c 1 -v
```
or
```
ffmpeg -i input.mp3 -ac 1 -ar 16000 -c:a pcm_s16le output.wav
```
