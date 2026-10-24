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

1. `POST /tracks/register` with `{"instance_id":"lead-vocal","name":"Lead","role":"vocal"}` to create the analysis track.
2. Select the plugin's loopback port shown in its editor, then call `POST /tracks/{id}/plugin-listen` with `{"plugin_port":17831,"minimum_context_seconds":10,"max_duration_seconds":60}`.
3. The service handshakes with that specific plugin, arms capture, and returns `202` with a capture ID. The plugin waits for DAW playback, records until transport stops or its configured duration is reached, and writes a WAV cache file.
4. The service detects completion, downloads that WAV from the same plugin instance, runs DSP, and stores the measurements. `GET /captures/{capture_id}` reports status/context length. `GET /tracks` returns latest measurements and staleness. `POST /mix/analyze` returns ranked suggestions with measured evidence.

The direct plugin endpoints are also available after `POST /v1/handshake`: authenticated `POST /v1/listen`, `GET /v1/status`, and `GET /v1/capture.wav`. The FastAPI bridge polls the plugin and handles the WAV upload/analysis path automatically. Plugin listen duration is capped at 120 seconds by its RAM ring; playback start timeout defaults to 120 seconds and can be set with `playback_start_timeout_seconds`.

`GET /health` returns service status. `POST /tracks/{id}/listen` and `GET /tracks/{id}/listen` remain available for clients that manage plugin polling themselves; `POST /captures/{id}/cancel` cancels a request. Mono/stereo PCM WAV is supported; malformed WAVs, unsupported channel counts and oversized/duration-limited captures receive explicit errors. Analysis measures peak and RMS dBFS, crest factor, band energy and stereo correlation. A high band level alone does not imply an EQ problem. Stale captures are identified and excluded from current-mix recommendations.

## JUCE plugin

`source/` contains the JUCE plugin. Build with CMake (JUCE 9.0.4 is fetched by default). The plugin has a loopback-only control API on `127.0.0.1:17831` (scans through port 17862 if occupied). `POST /v1/handshake` returns the per-instance token; send it as `X-Ernie-Token` to control endpoints. `GET /v1/status` reports capture state. See `plugin/README.md` and `docs/ARCHITECTURE.md` for the current plugin API and build details.

The plugin retains manual `/v1/recording/start` and `/v1/recording/stop` controls. Agent listen captures use transport gating, and completed WAVs are cached under `~/Documents/ernie/captures/` until retrieved over the authenticated loopback API.

## Other features

Foldback routes provide Ableton session context, structural/spectral mix notes, speech interaction, hum-to-MIDI, sample matching, and music composition through ElevenLabs. Set `FOLDBACK_MOCK=1` to use `foldback/session.mock.json` without Ableton. See `docs/ARCHITECTURE.md` for component ownership and route details.

## Tests

```sh
python -m pytest
```
