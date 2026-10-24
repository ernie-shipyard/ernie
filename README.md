# ernie

Stereo audio effect plugin template (AU, VST3, and Standalone) built with JUCE 9.0.4 and CMake.

JUCE is fetched automatically on the first configure. To use a local checkout instead:

```sh
cmake -S . -B build -DJUCE_SOURCE_DIR=/path/to/JUCE
```

## Build

```sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Debug
cmake --build build --config Debug
```

On macOS the AU and VST3 bundles land under `build/Ernie_artefacts/`. The standalone app is the fastest way to listen while developing.

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
