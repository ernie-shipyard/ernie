# foldback

A loud little studio assistant that lives next to your DAW. It reads your Ableton set, knows what plugins and samples you're using, gives you mixing notes out loud (with a personality), turns your humming into MIDI or a full track, and lets you argue with it by voice.

Built at Tech@NYU Shipyard, Oct 24 2026, for the Best Use of ElevenLabs track.

## What it does

| Tab | What happens | ElevenLabs |
|---|---|---|
| **Session** | Reads tempo, tracks, devices/plugins, clips and sample files from the live Ableton set | — |
| **Mix notes** | Rule-based notes on the session graph (no sidechain on bass, everything panned center, 3× compressor, a sample called `final_FINAL`), or spectral notes on an exported bounce (mud, crushed dynamics, missing top end). Spoken in character. | Text to Speech |
| **Talk to it** | Hold the mic and ask: what plugins am I using, what's the tempo, which samples are in here | Scribe v2 + TTS |
| **Hum → MIDI** | Hum a melody → quantized `.mid`, a new MIDI track in Live, and an instrument suggestion. Beatbox → kick/snare/hat `.mid` on a drum-rack mapping, **plus the closest real samples from your own library** for each hit (`SAMPLE_DIRS`). | — |
| **Hum → Track** | Hum → analysed → full instrumental track; then say "slower and warmer" to recompose | Music API + Scribe v2 |

## Run it

```bash
brew install ffmpeg                      # or apt install ffmpeg
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                     # paste ELEVENLABS_API_KEY
uvicorn app:app --reload
```
Open http://localhost:8000.

**Without Ableton** (demo laptop, judging table): set `FOLDBACK_MOCK=1` in `.env` and it reads `foldback/session.mock.json`.

**With Ableton Live 11/12:** install [AbletonOSC](https://github.com/ideoforms/AbletonOSC) — copy the `AbletonOSC` folder into `~/Music/Ableton/User Library/Remote Scripts/`, then in Live: Settings → Link/Tempo/MIDI → Control Surface → **AbletonOSC**. foldback talks to it on UDP 11000/11001.

## Team

See `docs/ARCHITECTURE.md` for the component map, who owns what, the JUCE plugin → companion contract, and the ElevenLabs agent tool endpoints.

## Personality

`foldback/persona.py` holds the system prompt and a template-driven script that works with no LLM at all. Set `LLM_BASE_URL` / `LLM_API_KEY` (any OpenAI-compatible endpoint; Grok's is) and the same facts get rewritten with more range.

## Layout

```
app.py                  FastAPI routes
foldback/live.py        Ableton session reader/writer (AbletonOSC) + mock
foldback/advise.py      structural + spectral mixing notes, inventory
foldback/persona.py     the voice: prompt, script, LLM rewrite, TTS
foldback/hum2midi.py    hum → melody MIDI, beatbox → drum MIDI
foldback/samples.py     sample library index + nearest-sample matching
plugin/                 JUCE analyzer (spec only, not built yet)
docs/ARCHITECTURE.md    component map + contracts
static/index.html       the whole UI
```

MIT.
```sh
scripts/install-debug-vst3.sh
```

That moves `build/Ernie_artefacts/Debug/VST3/Ernie.vst3` to `~/Library/Audio/Plug-Ins/VST3/`. The next Debug build recreates the bundle, so run the script again after rebuilding. Rescan plugins in the host.

`COPY_PLUGIN_AFTER_BUILD` is off, so builds do not install into system plugin folders. Copy the bundles yourself, or flip that flag in `CMakeLists.txt` once you want hosts to pick them up.

## Layout

- `CMakeLists.txt` — plugin target, formats, and JUCE fetch
- `source/PluginProcessor.*` — processor, gain parameter, state
- `source/PluginEditor.*` — editor, record controls, oscilloscope
- `source/TrackRecorder.*` — passthrough RAM capture and resampling
- `source/MetadataWriter.*` — session JSON in `~/Documents/ernie`
- `source/RecordingApi.*` — localhost handshake that arms and disarms recording
- `source/WaveformDisplay.*` — scope view of the RAM capture

## Recording

Recording copies the track into a fixed RAM ring and leaves the live buffer alone. While it is armed, sample rate and buffer duration cannot change, and the gain parameter is not applied. The ring holds the last N seconds at the chosen capture rate (22.05–192 kHz, or match host). The cap is 256 MB.

Session JSON is written to `~/Documents/ernie/ernie-latest.json` and `ernie-<session>.json` about four times a second. Fields the host does not provide are `null`, not guessed.

Playhead location (samples, seconds, PPQ, measure, beat) is written only while the transport is playing and the position has moved since the previous block. A stopped or stuck playhead keeps `playing` / `moving` and clears the location. Seconds are rounded to 0.1 s and the beat to the nearest 16th note.

BPM and key prefer the DAW. BPM comes from the host tempo. Key comes from the VST3 process-context chord when the host sends one; AU does not expose a key. If the host value is missing, tags on a source audio file are used. If neither exists, the field is null. The host does not tell an insert which clip it is playing, so the file has to be named explicitly:

```sh
curl -s -X POST http://127.0.0.1:17831/v1/source-file \
  -H "X-Ernie-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"path":"/path/to/clip.wav"}'
```

### API

The control server binds to `127.0.0.1` only. Handshake once, then send the token on later calls.

```sh
curl -s -X POST http://127.0.0.1:17831/v1/handshake
curl -s -X POST http://127.0.0.1:17831/v1/recording/start \
  -H "X-Ernie-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"capture_sample_rate":48000,"buffer_duration_seconds":8}'
curl -s http://127.0.0.1:17831/v1/status
curl -s -X POST http://127.0.0.1:17831/v1/recording/stop -H "X-Ernie-Token: $TOKEN"
```

`POST /v1/config` takes the same JSON fields and is rejected while recording. The port is shown in the editor if 17831 was already taken.
