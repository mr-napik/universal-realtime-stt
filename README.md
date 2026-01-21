# Realtime STT Test Suite

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



## Asset Sources
- https://www.pametnaroda.cz/cs/archive
- https://youtu.be/Der9UHsGinI
- https://youtu.be/DA6mbcmEZPc
- https://ceskepodcasty.cz/podcast/senior-life-podcast

### Converting Assets to WAV (on Mac)

Most STT for example Eleven Labs expect PCM16000 mono:
```
afconvert files.mp3 output.wav -f WAVE -d LEI16 -r 16000 -c 1
```
or
```
ffmpeg -i input.mp3 -ac 1 -ar 16000 -c:a pcm_s16le output.wav
```
