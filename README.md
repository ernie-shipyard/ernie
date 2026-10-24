# ERNIE / Foldback

## ERNIE at a glance

ernie reads the session (AbletonOSC + JUCE capture), builds with you (Stack: library picks by key/tempo, ElevenLabs Sound Effects + Music generation, hum→MIDI), and critiques (mixengine measurements, rule-based notes spoken via TTS). Voice in/out via Scribe + TTS in the web UI; an ElevenLabs Agent front end (React/Electron) is in progress.

### Run

**Backend + web UI** (port 8000)

```sh
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # ELEVENLABS_API_KEY, SAMPLE_DIRS, OUT_DIR=./demo/generated (demo cache)
uvicorn app:app --reload --port 8000
```

**JUCE plugin** — build with CMake (see `plugin/README.md`); its loopback API listens on `127.0.0.1:17831`. Then:

```sh
curl -X POST http://127.0.0.1:8000/tracks/register -H 'Content-Type: application/json' \
  -d '{"instance_id":"master","name":"Master","role":"master"}'
curl -X POST http://127.0.0.1:8000/tracks/master/plugin-listen -H 'Content-Type: application/json' \
  -d '{"plugin_port":17831,"minimum_context_seconds":2,"max_duration_seconds":15,"playback_start_timeout_seconds":30}'
curl -X POST http://127.0.0.1:8000/mix/analyze -H 'Content-Type: application/json' \
  -d '{"capture_ids":["<capture_id from plugin-listen>"]}'
```

**React/Electron UI** (port 5173, `strictPort`)

```sh
cd ernie-ui
npm install
npm run dev
```

Put `VITE_ELEVENLABS_AGENT_ID` in `ernie-ui/.env.local`. Never put the ElevenLabs API key in the UI.

### Feature status

| Feature | Verified by | Status |
|---|---|---|
| Stack picks / preview | human | working |
| Hum → MIDI | human | working |
| Sound Effects + Music generation, cached | real API | working |
| TTS mix notes | real API | working |
| Plugin capture + `/mix/analyze` | real capture | working |
| Ableton session read | mock | mock only |
| Stack hum-with-metronome, Play stack, Regenerate by voice | machine (synthetic audio, stubs) | machine-tested |
| Calibration / beatbox | — | in progress |
| React Agent voice | — | connection unresolved |
| Electron packaging | — | not done |

### ElevenLabs usage

| API | Used by |
|---|---|
| TTS | `/advise`, `/advise/captures`, `/ask` |
| Scribe | `/ask`, `/revise` |
| Music | `/compose`, `/stack/pregen` |
| Sound Effects | `/generate/oneshot` |
| Agents | `ernie-ui` (in progress) |

Handoffs: `docs/HANDOFF-midi-samples-stack.md`, `docs/HANDOFF-backend-juce.md`, `docs/INTEGRATION-HANDOFF.md`.

Local music-production collaborator: Ableton session context, mix analysis, voice, MIDI and sample tools, plus a JUCE plugin that can capture a track into a bounded RAM buffer.

## Run

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app:app --reload
```

Open `http://127.0.0.1:8000`. OpenAPI docs are at `/docs`. `ELEVENLABS_API_KEY` enables speech/music features; DSP analysis works without it. Optional `LLM_BASE_URL` and `LLM_API_KEY` enable conversational rewrites.

## Mix analysis API

The analysis API is implemented in `mixengine/` and is served alongside the Foldback routes at the root path.

1. `POST /tracks/register` with `{"instance_id":"lead-vocal","name":"Lead","role":"vocal"}` to create the analysis track.
2. Select the plugin's loopback port shown in its editor, then call `POST /tracks/{id}/plugin-listen` with `{"plugin_port":17831,"minimum_context_seconds":10,"max_duration_seconds":60}`.
3. The service handshakes with that specific plugin, arms capture, and returns `202` with a capture ID. The plugin waits for DAW playback, records until transport stops or its configured duration is reached, and writes a WAV cache file.
4. The service detects completion, opens the WAV cache file named for that exact plugin session, runs DSP, stores mixing notes on the capture, and removes the temporary cache. `GET /captures/{capture_id}` reports status, measurements, and the mixing notes for that listen. `GET /tracks` returns latest measurements and staleness. `POST /mix/analyze` ranks those notes across the current window, including level, crest factor on transient roles, polarity, and relative band balance. A bright capture on its own is not treated as an EQ problem.

The direct plugin endpoints are also available after `POST /v1/handshake`: authenticated `POST /v1/listen`, `GET /v1/status`, and `GET /v1/capture.wav`. The FastAPI bridge polls the plugin and handles the WAV upload/analysis path automatically. Plugin listen duration is capped at 120 seconds by its RAM ring; playback start timeout defaults to 120 seconds and can be set with `playback_start_timeout_seconds`.

`GET /health` returns service status. `POST /tracks/{id}/listen` and `GET /tracks/{id}/listen` remain available for clients that manage plugin polling themselves; `POST /captures/{id}/cancel` cancels a request. Mono/stereo PCM WAV is supported; malformed WAVs, unsupported channel counts and oversized/duration-limited captures receive explicit errors. Analysis measures peak and RMS dBFS, crest factor, band energy and stereo correlation. A high band level alone does not imply an EQ problem. Stale captures are identified and excluded from current-mix recommendations.

## JUCE plugin

`source/` contains the JUCE plugin. Build with CMake (JUCE 9.0.4 is fetched by default). The plugin has a loopback-only control API on `127.0.0.1:17831` (scans through port 17862 if occupied). `POST /v1/handshake` returns the per-instance token; send it as `X-Ernie-Token` to control endpoints. `GET /v1/status` reports capture state. See `plugin/README.md` and `docs/ARCHITECTURE.md` for the current plugin API and build details.

The plugin retains manual `/v1/recording/start` and `/v1/recording/stop` controls. Agent listen captures use transport gating, and completed WAVs are cached under `~/Documents/ernie/captures/` until retrieved over the authenticated loopback API.

## Other features

Foldback routes provide Ableton session context, structural/spectral mix notes, speech interaction, hum-to-MIDI, sample matching, and music composition through ElevenLabs. Finished plugin listens are included: `POST /advise` adds their notes to the session notes, `POST /advise/captures` speaks them without Ableton, and `/tools/track_analysis/{label}` reads a plugin capture by track id, name, or role. Set `FOLDBACK_MOCK=1` to use `foldback/session.mock.json` without Ableton. See `docs/ARCHITECTURE.md` for component ownership and route details.

**Stack tab.** Builds a track one layer at a time: Drums, Bass, Pad, Synth/Lead, FX, Vocals. Header shows session tempo and a key, set from the chosen sample's filename. *Pick from library* (`GET /samples/pick?tag=&key=&bpm=`) returns up to 5 indexed samples ranked by tempo then key, with mismatches flagged. *Preview* plays the chosen file. *Generate* calls ElevenLabs Sound Effects for Drums/FX one-shots (`POST /generate/oneshot`) or the Music API for 8 s loops on tonal rows (`POST /compose`, prompt includes key and tempo). *Regenerate by voice* sends your instruction through Scribe (`POST /revise`) and regenerates. *Hum melody* plays the key as a reference tone, runs a metronome, and turns the hum into quantized MIDI on a new Live track named after the row (`POST /midi/hum`). *Play stack* loops all chosen layers together in the browser at the session tempo. Beatbox → drums is in progress.

### Demo setup

```sh
git clone <repo> && cd ernie
brew install ffmpeg
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
cp .env.example .env   # ELEVENLABS_API_KEY, SAMPLE_DIRS=/path/to/samples, OUT_DIR=./demo/generated
uvicorn app:app --port 8000
curl -X POST -F force=true http://127.0.0.1:8000/samples/scan           # index the library (seconds)
curl -X POST -F key=Am -F bpm=124 http://127.0.0.1:8000/stack/pregen    # cache the demo loops
```

## Tests

```sh
python -m pytest
```
