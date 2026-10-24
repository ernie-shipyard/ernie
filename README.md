# ERNIE / Foldback

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

1. `POST /tracks/register` with `{"instance_id":"plugin-guid-1","name":"Lead","role":"vocal"}`. The unique JUCE instance ID is the returned track `id`.
2. `POST /tracks/{id}/listen` with optional `minimum_context_seconds` and `max_duration_seconds`; returns `202` with a capture ID.
3. Plugin reports `waiting_for_playback` then `recording` to `POST /captures/{capture_id}/status`.
4. At the end of playback, plugin uploads one WAV to `POST /tracks/{id}/audio`, `Content-Type: audio/wav`, `X-Capture-ID: ...`.
5. `GET /captures/{capture_id}` returns processing status, duration, and measurements. `GET /tracks` returns latest track measurements and staleness. `POST /mix/analyze` returns ranked suggestions with measured evidence.

`GET /health` returns `{"status":"ok","registered_tracks":0}`. `GET /tracks/{id}/listen` is the plugin's poll for its pending listen command. `POST /captures/{id}/cancel` cancels a request. Mono/stereo PCM WAV is supported; malformed WAVs, unsupported channel counts and oversized/duration-limited captures receive explicit errors. Analysis measures peak and RMS dBFS, crest factor, band energy and stereo correlation. A high band level alone does not imply an EQ problem. Stale captures are identified and excluded from current-mix recommendations.

## JUCE plugin

`source/` contains the JUCE plugin. Build with CMake (JUCE 9.0.4 is fetched by default). The plugin has a loopback-only control API on `127.0.0.1:17831` (scans through port 17862 if occupied). `POST /v1/handshake` returns the per-instance token; send it as `X-Ernie-Token` to control endpoints. `GET /v1/status` reports capture state. See `plugin/README.md` and `docs/ARCHITECTURE.md` for the current plugin API and build details.

The plugin presently exposes manual `/v1/recording/start` and `/v1/recording/stop` controls and keeps the latest capture in RAM. The mix-analysis listen/upload contract above is the target agent integration contract; the plugin still needs transport-gated listen and WAV export/upload wiring to complete that flow.

## Other features

Foldback routes provide Ableton session context, structural/spectral mix notes, speech interaction, hum-to-MIDI, sample matching, and music composition through ElevenLabs. Set `FOLDBACK_MOCK=1` to use `foldback/session.mock.json` without Ableton. See `docs/ARCHITECTURE.md` for component ownership and route details.

## Tests

```sh
python -m pytest
```
