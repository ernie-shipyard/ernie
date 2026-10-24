# Architecture

```text
Producer (voice / chat)
   │
Electron window ── React app (ernie-ui) ── ElevenLabs Conversational Agent "Ernie"
   │   avatar, chat, tool cards          LLM + voice + prompt (scripts/setup_agent.py)
   │   client tools ◄──────────────────── tool calls
   ▼
FastAPI (app.py, :8000)
   ├─ /agent/*   signed URL, plugin discovery, listen → analyze (foldback/agent.py)
   ├─ mixengine  capture registry, DSP, evidence-based notes
   ├─ foldback   Ableton session (AbletonOSC), hum→MIDI, samples, ElevenLabs Music / Sound Effects
   └─ /app       built React UI; / is the original button UI (static/)
   │
JUCE plugin on 127.0.0.1:17831–17862 ── bounded RAM capture gated by DAW transport
```

## Voice agent

One conversation pipeline: the ElevenLabs agent (`@elevenlabs/react`). The React app registers client tools with
`useConversationClientTool`; `scripts/setup_agent.py` registers the same names on the agent, plus the system prompt.

| Tool | What the app does |
|---|---|
| `start_listening` | `POST /agent/listen`: registers `master` if needed, finds the plugin port, arms it. Returns at once. |
| `stop_listening` | `POST /agent/listen/{id}/cancel` |
| `generate_sound` | `POST /generate/oneshot` (drums/fx) or `POST /compose` 8 s loop |
| `find_samples` | `GET /samples/pick` |
| `record_hum` | mutes the agent mic, count-in + MediaRecorder, `POST /midi/hum` |
| `get_session` | `GET /tools/session_context` |

Listening is two-phase because a capture can wait a minute for playback: the app polls `GET /agent/listen/{id}`
(phase, then measurements + notes + `verdict`: `clean` / `has_notes` / `silent` / `too_short`) and on completion sends
the agent a hidden `[capture finished] {json}` message, which Ernie turns into spoken feedback. Ernie only quotes
numbers that came from a tool.

The browser never sees `ELEVENLABS_API_KEY`: `GET /agent/signed-url` mints a conversation URL for
`ELEVENLABS_AGENT_ID`. `VITE_ELEVENLABS_AGENT_ID` in `ernie-ui/.env.local` is only a fallback for a public agent.

Electron (`ernie-ui/electron/main.cjs`) loads Vite on the fixed port 5173 in dev, or `http://127.0.0.1:8000/app/` when
packaged / `ERNIE_PROD=1`; it grants microphone permission only and starts uvicorn from `.venv` if nothing answers
on :8000.

## Mix capture

The root FastAPI service hosts the Foldback product routes and the independent `mixengine` WAV analysis API. Each JUCE plugin instance runs a loopback-only control server. A client selects a plugin instance by the port shown in that instance's editor; `/agent/listen` finds it by probing the unauthenticated `GET /v1/status` across the plugin's port range.

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

The service handshakes with the plugin, issues `POST /v1/listen`, and returns `202` with `capture_id`. The plugin waits for DAW transport playback before copying audio into its fixed RAM ring. It stops when transport stops or the ring reaches the requested maximum, then writes a WAV cache file named for that session off the audio thread. The service waits for that session to stop, opens its exact cache file, measures it, stores mixing notes on the capture, and removes the file. Poll `GET /captures/{capture_id}` until `completed` or `context_too_short`; a completed capture includes `recommendations`. `POST /mix/analyze` ranks the current window. `POST /advise/captures` turns those notes into the spoken assistant format without requiring Ableton.

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
