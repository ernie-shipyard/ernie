# ERNIE local mix analysis API

FastAPI service for agent-requested, one-shot JUCE captures and grounded mix suggestions. The plugin does not stream or continuously record audio. The agent requests a capture for a specific plugin instance; the plugin waits for DAW playback, records until playback stops (or the requested maximum duration), then uploads one WAV. The service stores track state in memory and deletes each temporary WAV after analysis.

## Run locally

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

OpenAPI documentation is available at `http://127.0.0.1:8000/docs`.

## Agent and plugin handshake

1. Register the plugin instance with `POST /tracks/register`; the returned `id` identifies that instance.
2. The agent requests a one-shot listen with `POST /tracks/{id}/listen`.
3. The plugin polls `GET /tracks/{id}/listen`. When it receives a capture ID, it reports `waiting_for_playback`, then `recording` when the DAW starts.
4. At DAW stop, the plugin uploads one raw WAV to `POST /tracks/{id}/audio` with the `X-Capture-ID` header.
5. The agent polls `GET /captures/{capture_id}` for processing status and measurements. `POST /mix/analyze` returns feedback for recent, sufficiently long captures.

If playback never starts or the agent stops waiting, it can cancel the request with `POST /captures/{capture_id}/cancel`; this frees the plugin instance for another listen request.

Only one listen request can be active per plugin instance. Plugin status updates use `POST /captures/{capture_id}/status` with `{"status":"waiting_for_playback"}` or `{"status":"recording"}`. The plugin should stop at the command's `max_duration_seconds` even if the DAW continues playing.

## API contracts

### `GET /health`

Returns `200` while the local service is available.

```json
{"status":"ok","registered_tracks":1}
```

### `POST /tracks/register`

Request:

```json
{"instance_id":"juce-instance-7f3a","name":"Lead vocal","role":"vocal"}
```

`role` is optional. The client supplies a unique per-instance ID. Re-registering the same ID updates its name/role and retains its measurements. New registrations return `201`; repeated registrations return `200`.

```json
{"id":"juce-instance-7f3a","name":"Lead vocal","role":"vocal","registered_at":"2026-10-24T15:00:00Z"}
```

### `POST /tracks/{id}/listen`

The agent requests a capture for one plugin. Both request fields are optional; defaults are a minimum useful context of 10 seconds and maximum capture of 300 seconds. Maximum duration is capped at 600 seconds.

```json
{"minimum_context_seconds":10,"max_duration_seconds":300}
```

Returns `202` and a unique capture ID:

```json
{
  "capture_id":"c3435f45-0e35-495f-b94c-950e0eeb752f",
  "track_id":"juce-instance-7f3a",
  "status":"requested",
  "requested_at":"2026-10-24T15:00:00Z",
  "recording_started_at":null,
  "completed_at":null,
  "minimum_context_seconds":10,
  "max_duration_seconds":300,
  "duration_seconds":null,
  "context_too_short":null,
  "measurements":null
}
```

### `GET /tracks/{id}/listen`

Plugin polling endpoint. Returns `{"capture":null}` when no listen command is active, or the current command with its capture ID, minimum context, maximum duration, and status. Repeated polls return the same capture ID, so the plugin can safely deduplicate requests.

### `POST /captures/{capture_id}/status`

The plugin reports `waiting_for_playback` after receiving a command and `recording` when the DAW starts playback. Invalid transitions return `409`.

### `POST /tracks/{id}/audio`

Upload the completed capture once, after DAW stop, as raw WAV bytes. Set `Content-Type: audio/wav` and `X-Capture-ID: <capture_id>`. Sample rate and mono/stereo channel count are read from the WAV. Audio is streamed to a temporary file and analyzed in bounded-size blocks; that cache file is deleted when analysis completes.

Captures are limited to 512 MiB and the per-request duration limit (300 seconds by default; up to 600 seconds). Unsupported channel layouts return `422`; malformed WAVs return `400`.

### `GET /captures/{capture_id}`

Returns the capture status and, after upload, the duration and measurements. If the recording is shorter than the request's `minimum_context_seconds`, status becomes `context_too_short` and `context_too_short` is `true`. Measurements remain available for inspection, but `/mix/analyze` excludes that capture from feedback.

The agent can cancel an active request with `POST /captures/{capture_id}/cancel`. Completed or cancelled captures cannot be uploaded again.

### Measurements and `GET /tracks`

WAV analysis measures peak and RMS dBFS, crest factor, frequency-band energy, and stereo correlation. Silence and undefined metrics are `null`. Band entries are omitted when the sample rate's Nyquist frequency does not reach that band's lower edge. `GET /tracks` returns each track's latest capture ID, latest measurements, capture timestamp, and `is_stale` flag. Measurements older than 10 seconds are marked stale and excluded from `/mix/analyze`; their timestamps remain visible.

### `POST /mix/analyze`

With no body, returns per-track and globally ranked recommendations for recent, sufficiently long captures, plus IDs of stale and too-short captures that were excluded. To analyze the exact captures returned by the listen flow, pass their IDs; this analyzes those snapshots even if they are older than 10 seconds, while marking each `snapshot_is_stale` rather than presenting it as current:

```json
{"capture_ids":["c3435f45-0e35-495f-b94c-950e0eeb752f"]}
```

The response lists `analyzed_capture_ids` and each track's `capture_id`. Recommendation objects include priority, rank, suggestion, reasoning, and measured evidence. A strong frequency band alone never creates an EQ warning. Same-role RMS differences are framed as listening cues, not target levels.

Example recommendation:

```json
{
  "priority":"medium",
  "rank":1,
  "code":"near_full_scale",
  "track_id":"juce-instance-7f3a",
  "message":"Consider leaving more peak headroom on this track.",
  "reasoning":"The measured peak is within 0.1 dB of full scale; this is a headroom check, not proof of audible clipping.",
  "evidence":{"peak_dbfs":-0.04,"threshold_dbfs":-0.1}
}
```

## CORS, errors, and tests

CORS is restricted to explicit localhost/127.0.0.1 development origins on ports 3000 and 5173 by default. `ERNIE_CORS_ORIGINS` can add explicit HTTP loopback origins with ports; public origins and wildcards are ignored.

Errors use FastAPI's `{"detail":"..."}` format. Common statuses: `404` unknown track/capture, `409` a conflicting listen request or invalid state, `413` capture exceeds size/duration limits, `415` unsupported content type, and `422` unsupported WAV channels or invalid request values.

Run tests with:

```sh
pip install -r requirements-dev.txt
python -m pytest
```
