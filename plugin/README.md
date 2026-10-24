# ERNIE JUCE plugin

The JUCE processor and loopback API are implemented under `source/` and built with the root `CMakeLists.txt`.

## Agent listen capture

Each plugin instance hosts its own loopback HTTP server, normally on `127.0.0.1:17831` (it scans through `17862` if needed). The editor shows the selected port and the current handshake token.

The backend can orchestrate the complete path through `POST /tracks/{track_id}/plugin-listen` on the FastAPI service. Pass the plugin port, minimum useful context, maximum capture duration, and optional playback start timeout:

```json
{
  "plugin_port": 17831,
  "minimum_context_seconds": 10,
  "max_duration_seconds": 60,
  "playback_start_timeout_seconds": 120
}
```

The service performs the plugin handshake, calls `POST /v1/listen`, and polls status. After DAW stop (or the duration cap), it reads the cache file named for that exact plugin session under `~/Documents/ernie/captures/`, analyzes it, and removes it. Captures are mono or stereo and capped at 120 seconds by the plugin's fixed RAM ring. The WAV cache is written off the audio thread.

For direct local control, `POST /v1/handshake` returns a token. Send it as `X-Ernie-Token` to `POST /v1/listen`, `POST /v1/recording/start`, `POST /v1/recording/stop`, `POST /v1/config`, `POST /v1/source-file`, and `GET /v1/capture.wav`. `GET /v1/status` reports `waiting_for_playback`, `recording`, and `capture_ready` independently.

The control API binds only to loopback. It does not continuously record: a listen request arms a bounded in-memory ring and audio is copied only while the host reports DAW transport playback.
