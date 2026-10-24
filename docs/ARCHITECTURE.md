# Architecture

The root FastAPI service hosts the Foldback product routes and the independent `mixengine` WAV analysis API. Each JUCE plugin instance runs a loopback-only control server. A client selects a plugin instance by the port shown in that instance's editor.

```text
Agent / UI
   │ POST /tracks/{id}/plugin-listen
   ▼
FastAPI (app.py)
   │ handshake, arm, poll status, fetch WAV
   ▼
JUCE instance on 127.0.0.1:17831–17862
   │ bounded RAM capture gated by DAW transport
   ▼
WAV cache → mixengine DSP → ranked measured suggestions
```

Foldback also provides Ableton session context (`foldback/live.py`), structural and bounce notes (`foldback/advise.py`), speech/LLM phrasing (`foldback/persona.py`), hum-to-MIDI (`foldback/hum2midi.py`), and sample matching (`foldback/samples.py`). ElevenLabs speech/music routes are in `app.py`.

## One-shot capture contract

Register a logical analysis track:

```http
POST /tracks/register
Content-Type: application/json
```

```json
{"instance_id":"lead-vocal","name":"Lead","role":"vocal"}
```

Arm a chosen plugin by its editor port:

```http
POST /tracks/lead-vocal/plugin-listen
Content-Type: application/json
```

```json
{"plugin_port":17831,"minimum_context_seconds":10,"max_duration_seconds":60}
```

The service handshakes with the plugin, issues `POST /v1/listen`, and returns `202` with `capture_id`. The plugin waits for DAW transport playback before copying audio into its fixed RAM ring. It stops when transport stops or the ring reaches the requested maximum, then writes a WAV cache file named for that session off the audio thread. The service waits for that session to stop, opens its exact cache file, analyzes it, and removes it; poll `GET /captures/{capture_id}` until completed or `context_too_short`.

Direct plugin API:

| Request | Purpose |
|---|---|
| `POST /v1/handshake` | Get a process-local token and the instance's port |
| `POST /v1/listen` | Arm transport-gated capture; optional capture rate and duration |
| `GET /v1/status` | Read waiting, recording and WAV-ready state |
| `GET /v1/capture.wav` | Retrieve the latest completed WAV (requires token) |
| `POST /v1/recording/start` / `/stop` | Manual capture controls retained for debugging |

Plugin status fields distinguish `waiting_for_playback`, `recording`, and `capture_ready`. Plugin API binds only to `127.0.0.1`. Capture duration is limited to 120 seconds by the JUCE ring. The Python analysis API accepts mono/stereo WAV, measures peak/RMS, crest factor, frequency-band energy and stereo correlation, then creates recommendations with measured evidence. Spectral concentration alone does not assert an EQ fault.

The older explicit plugin-poll/upload flow remains available through `POST /tracks/{id}/listen`, `GET /tracks/{id}/listen`, `POST /captures/{id}/status`, and `POST /tracks/{id}/audio` for non-JUCE clients.

## Agent tool routes

Foldback exposes agent/webhook tools including `GET /tools/session_context`, `GET /tools/master_analysis`, `GET /tools/track_analysis/{label}`, and `GET /tools/compare/{a}/{b}`. Set the required ElevenLabs credentials in `.env` for voice features. Keep loopback plugin control local; a public webhook should call the local companion rather than exposing plugin ports.

## Build and run

- Python: `pip install -r requirements.txt`, then `uvicorn app:app --reload`.
- JUCE: configure/build the root `CMakeLists.txt`; CMake fetches JUCE 9.0.4 unless `JUCE_SOURCE_DIR` is set.
- Capture tests: `python -m pytest` and the JUCE `ErnieApiTest` / `ErnieRecorderTest` targets with `ERNIE_BUILD_TESTS=ON`.
