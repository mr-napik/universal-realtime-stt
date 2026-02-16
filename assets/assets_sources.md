# Test Report

## Converting Test Assets to WAV (on Mac)

Most sources are in MP3 or other compressed format. Most STT expect PCM16000 mono. 
Following commands can convert assets to acceptable format to run the test suite.

```
afconvert input.mp3 output.wav -f WAVE -d LEI16@16000 -c 1 -v
```
or
```
ffmpeg -i input.mp3 -ac 1 -ar 16000 -c:a pcm_s16le output.wav
```

##  Manual Volume Leveling

Test the file loudness (mark the max):

`ffmpeg -i assets/test2.wav -af volumedetect -f null - 2>&1 | egrep "mean_volume|max_volume"`

Increase the loudness but prevent oversaturation (select increase amount based on result above):

`ffmpeg -i assets/test2.wav -filter:a "volume=30dB,alimiter=limit=1.0" assets/test3.wav`

### Example of Volume Levelling

**test2.wav**
```
[Parsed_volumedetect_0 @ 0x7feffe108ec0] mean_volume: -43.1 dB
[Parsed_volumedetect_0 @ 0x7feffe108ec0] max_volume: -23.4 dB
```

**test3.wav** (which is test2 after levelling)
```
[Parsed_volumedetect_0 @ 0x7f8f39c07640] mean_volume: -13.8 dB
[Parsed_volumedetect_0 @ 0x7f8f39c07640] max_volume: 0.0 dB
```

## Assets Origin

All sample sound assets used for quality report were retrieved from
["Paměť Národa"](https://www.pametnaroda.cz/cs/pribehy-20-stoleti) (Memory of Nations).
Only assets belonging to: Příběhy 20. století (Post Bellum). Were selected.

## Quality Scale
- Good: Text is easily understandable and all details can be clearly distinguished.
- OK: Text is easy to understand in general, but small details like specific word endings are not clear and cannot be definitely discerned even by human.
- Poor: Even human needs to exert great effort to figure out general message of the text with individual words being unintelligible. 

## Asset Details

| File      | Person                | Event              | Quality             | Link                                                            |
|-----------|-----------------------|--------------------|---------------------|-----------------------------------------------------------------|
| test1.wav | kapitán Adolf Vodička | Motivace k odchodu | OK                  | https://www.pametnaroda.cz/system/files/witness/44/87-audio.mp3 |
| test2.wav | Staša Fleischmannová  | Fučík v ateliéru   | Poor                | https://www.pametnaroda.cz/cs/fleischmannova-stasa-1919         |
| test3.wav | Staša Fleischmannová  | Fučík v ateliéru   | OK - louder by 30dB | https://www.pametnaroda.cz/cs/fleischmannova-stasa-1919         |


## Other potential Test Asset Sources

- https://youtu.be/Der9UHsGinI
- https://youtu.be/DA6mbcmEZPc
- https://ceskepodcasty.cz/podcast/senior-life-podcast
