# Universal Realtime STT Library

This library allows you to run realtime STT (speech to text) with ElevenLabs, Google, Deepgram, Speechmatics and Cartesia without worrying about any provider specific details and without installing their dependencies. All you need to do is to start one asyncio task, feed sound chunks to the input sound queue and consume transcripts in string format from the output queue (LinkedIn article?).

## TODO

### Test Case
- Read license conditions at pametnaroda.cz and consider whether to add more files into repo. Though in principle, since we would like to test on hours of audio, storing all that in repo would not be ideal since this would make the lib unnecessarily large. Still we might include more than one file as a sample if the license allows that.
- Get various audio files to cover approx 1 hour of data. Ideally around 5 male and 5 female voices of various types.
- Get the ground truth (which means using eleven labs offline and then checking manually).
- Consider how to publish potential results.

### Code
- Update the test case to run on multiple files. Which in general would primarily mean instantiating the provider for each.
- Update the code to aggregate results for test cases into one file to have a nice overview. But this might not be for the actual testcase, see below, we might only need this for the larger test.
- Consider whether to make the large scale test as a testcase or standalone app. I would rather do it as an app, with lower logging and possibility to run different providers simultaneously to save time. Ideally all providers should run in parallel.
- Verify configuration and retrieval of transcripts (just like with Speechmatics, where we initially did not capture all that was returned). Especially Google might suffer from a similar problem.
- Optionally consider installing all provider SDKs in a separate project and ask AI to verify if there is not something that we should improve (though only limit it, to providers we would consider using).

### More Providers to Consider
- OpenAI (read the docs if they provide realtime STT)
- Soniox
- AWS
- Azure

### Publication
- Consider whether to publish this at GitHub and possibly PyPi.
- Consider licensing conditions.
- Update readmes etc.
- But primarily consider the pros and cons of doing this.


## Installation and Use

### Checkout the Project
`pass`

### .venv Creation in IntelliJ IDEA (on Mac)

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
7) Find the <project>/.venv/bin/python

Then restart Idea and check that the terminal automatically opens to (.venv). This confirms that Idea understands everything correctly.

Then install dependencies from requirements.

### Integration and Use

TODO

Sample code how the lib should be used.

## Architecture

### Dependencies

In general, we want to avoid using provider specific SDKs unless necessary, since using the SDK of each provider would make the lib rather dependence heavy. This has a drawback of additional work in case their API changes.

### Config Architecture

- In an ideal case, universal STT configuration should be taken from the config.py (which should contain universal STT parametrization that is provider independent like language, format, silences, VAD etc. - even though that each provider might use slightly different naming and notation).
- The provider specific object should contain provider specific settings like model name and url and translation of universal names to provider specific names.
- Finally, the API key should only be taken at the moment of provider object instantiation, as that is a secret, and we do not want to handle it inside the library. Secret should be something that the user provides at top level, so that various methods can be conveniently used without diving deep into the lib.


## Test Case

In addition to classical unit tests primarily aimed at testing the lib machinery, there is a standalone app that is used to run a big test.

The test app was built to extensively test various STT providers and get real world accuracy data for realtime stt.

1) A unit test suite that looks into the assets directory for any mp3 or wav file it finds there.
2) It will stream them to a realtime STT API and capture the resulting committed transcripts.
3) It will compare them to a txt file of the same name as the audio file (source of ground truth) and calculate diff.

**Note:** While we are running the test statically on files, it closely mimics behavior when the audio is streamed in real time, and we receive committed transcripts in real time.


### Potential Test Asset Sources
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
