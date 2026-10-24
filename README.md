# ERNIE local mix analysis API

FastAPI service for JUCE plugin telemetry and grounded, per-track mix suggestions. The DSP path is deterministic and has no external API dependency. Track state is held in memory and is cleared when the server restarts.

## Run locally

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

OpenAPI documentation is available at `http://127.0.0.1:8000/docs`.

## API contracts

### `GET /health`

Returns `200` while the local service is available.

```json
{"status":"ok","registered_tracks":1}
```

### `POST /tracks/register`

Register one plugin instance. The client supplies a unique per-instance ID; re-registering the same ID updates its name/role and keeps its existing measurements. New registrations return `201`; repeated registrations return `200`.

Request:

```json
{"instance_id":"juce-instance-7f3a","name":"Lead vocal","role":"vocal"}
```

`role` is optional. Response:

```json
{"id":"juce-instance-7f3a","name":"Lead vocal","role":"vocal","registered_at":"2026-10-24T15:00:00Z"}
```

### `POST /tracks/{id}/audio`

Send raw WAV bytes with `Content-Type: audio/wav`. Sample rate and channel count are read from the WAV header; supported layouts are mono and stereo. Windows are limited to 16 MiB and 30 seconds.

Returns the corresponding track view, including its latest measurements:

```json
{
  "id":"juce-instance-7f3a",
  "name":"Lead vocal",
  "role":"vocal",
  "registered_at":"2026-10-24T15:00:00Z",
  "last_seen_at":"2026-10-24T15:00:01Z",
  "is_stale":false,
  "latest_measurements":{
    "measured_at":"2026-10-24T15:00:01Z",
    "sample_rate":44100,
    "channels":1,
    "duration_seconds":0.5,
    "is_silent":false,
    "peak_dbfs":-6.02,
    "rms_dbfs":-9.03,
    "crest_factor_db":3.01,
    "frequency_bands":{
      "low":{"low_hz":20,"high_hz":120,"energy_dbfs":-58.1},
      "low_mid":{"low_hz":120,"high_hz":500,"energy_dbfs":-52.0},
      "mid":{"low_hz":500,"high_hz":2000,"energy_dbfs":-9.1},
      "high_mid":{"low_hz":2000,"high_hz":6000,"energy_dbfs":-55.4},
      "high":{"low_hz":6000,"high_hz":22050,"energy_dbfs":-68.0}
    },
    "stereo_correlation":null
  }
}
```

Band entries are omitted when the sample rate's Nyquist frequency does not reach that band's lower edge. Undefined values, such as dBFS for digital silence and stereo correlation for mono, are `null`. Band energy is measured evidence, not by itself an EQ diagnosis.

### `GET /tracks`

Returns `{ "tracks": [...], "stale_after_seconds": 10 }`. A track without an update in the last 10 seconds is marked `is_stale: true`; its last measurements remain visible with their original timestamp.

### `POST /mix/analyze`

No request body. Returns fresh tracks with their measurements and per-track recommendations, plus the same recommendations ranked across the mix. Stale tracks and registered tracks that have not uploaded audio are listed in `excluded_stale_track_ids` and are not treated as current.

```json
{
  "analyzed_at":"2026-10-24T15:00:02Z",
  "tracks":[
    {
      "track_id":"juce-instance-7f3a",
      "name":"Lead vocal",
      "role":"vocal",
      "measurements":{"sample_rate":44100,"channels":1,"peak_dbfs":-0.04,"rms_dbfs":-8.1,"crest_factor_db":8.06,"is_silent":false,"duration_seconds":0.5,"measured_at":"2026-10-24T15:00:01Z","frequency_bands":{},"stereo_correlation":null},
      "recommendations":[
        {
          "priority":"medium",
          "rank":1,
          "code":"near_full_scale",
          "track_id":"juce-instance-7f3a",
          "message":"Consider leaving more peak headroom on this track.",
          "reasoning":"The measured peak is within 0.1 dB of full scale; this is a headroom check, not proof of audible clipping.",
          "evidence":{"peak_dbfs":-0.04,"threshold_dbfs":-0.1}
        }
      ]
    }
  ],
  "recommendations":[
    {
      "priority":"medium",
      "rank":1,
      "code":"near_full_scale",
      "track_id":"juce-instance-7f3a",
      "message":"Consider leaving more peak headroom on this track.",
      "reasoning":"The measured peak is within 0.1 dB of full scale; this is a headroom check, not proof of audible clipping.",
      "evidence":{"peak_dbfs":-0.04,"threshold_dbfs":-0.1}
    }
  ],
  "excluded_stale_track_ids":[]
}
```

Recommendation objects always include a suggestion, reasoning, and measured evidence. Same-role RMS comparisons are phrased as listening cues, not target levels. Frequency-band concentration alone never creates an EQ warning.

## Errors, CORS, and tests

Invalid WAV data returns `400`; unsupported channel layouts return `422`; unsupported content types return `415`; unregistered tracks return `404`; upload limits return `413`. FastAPI uses its standard `{"detail":"..."}` error body.

CORS is restricted to explicit development origins: localhost and 127.0.0.1 on ports 3000 and 5173. Override the list with comma-separated `ERNIE_CORS_ORIGINS`; do not use `*` for production.

Run tests with:

```sh
pip install -r requirements-dev.txt
python -m pytest
```
