# ERNIE Hackathon Handoff: Backend + JUCE Capture Integration

## Project

ERNIE is an AI music production collaborator. I worked on the local FastAPI backend, audio mix analysis, and connecting it to the JUCE plugin’s capture workflow.

The goal is for the agent to request a capture from a specific plugin instance. The plugin waits for DAW playback, records a bounded audio window, and makes that WAV available for backend analysis.

## Work completed

### FastAPI backend and mix analysis

The backend provides:

- GET /health
- POST /tracks/register
- GET /tracks
- POST /tracks/{track_id}/plugin-listen
- GET /captures/{capture_id}
- POST /mix/analyze

The analysis measures peak and RMS level, crest factor, broad frequency-band energy, and stereo correlation when the audio is stereo. Recommendations are generated from measurement-based rules. Broad frequency-band energy by itself does not imply an EQ problem.

The capture and analysis flow handles mono/stereo WAV, silence, sample rate, short context, malformed inputs, and stale track snapshots. A capture can still be analyzed by its ID even if the track snapshot is marked stale.

### Master bus capture

I registered a track with ID master, name Master, role master, and used the ERNIE plugin on the DAW master bus. The plugin API port shown on my machine was 17831.

I requested a capture with:

```bash
curl -X POST http://127.0.0.1:8000/tracks/master/plugin-listen \
  -H 'Content-Type: application/json' \
  -d '{"plugin_port":17831,"minimum_context_seconds":2,"max_duration_seconds":15,"playback_start_timeout_seconds":30}'

The plugin waited for playback, recorded, and completed capture:

0b171117-4cd3-4009-b3cf-3ad3eb1af554

It returned a 15-second, 48 kHz stereo capture.

### Mix analysis result

I analyzed that capture with:

curl -X POST http://127.0.0.1:8000/mix/analyze \
  -H 'Content-Type: application/json' \
  -d '{"capture_ids":["0b171117-4cd3-4009-b3cf-3ad3eb1af554"]}'

Measurements included:

- Peak: about -2.06 dBFS
- RMS: about -15.94 dBFS
- Crest factor: about 13.88 dB
- Stereo correlation: about 0.649
- Duration: 15.0 seconds

No recommendations were returned. That was expected: the measured peak did not meet the near-full-scale trigger, there was only one track to compare, and broad band energy alone is not sufficient evidence for an EQ recommendation.

The response marked the snapshot stale because the analysis happened more than 10 seconds after capture. The explicit capture ID was still analyzed successfully.

## JUCE plugin capture behavior and debugging

The plugin runs a local HTTP API. On my machine it used port 17831; the backend itself used port 8000.

The intended workflow is:

1. Put the ERNIE plugin on the DAW track or master bus to capture.
2. Register that track with the backend.
3. Call its /plugin-listen endpoint.
4. The plugin waits for DAW playback, records until playback stops or the configured maximum duration is reached, and exposes capture status.
5. The backend retrieves the WAV and analyzes it.

Earlier debugging showed the API could report a short duration (around 1.875 seconds) even when the cached WAV was longer. The plugin status endpoint showed samples_written: 465408 at 48 kHz, and the corresponding WAV was 9.696 seconds. The fix on the merged main branch uses the capture session ID to locate the matching WAV, avoiding confusion with a previous cached file.

A later master-bus test completed at the configured 15-second maximum, confirming the bounded capture path.

## Build and test history

- The JUCE VST3 was built locally with JUCE 9.0.3.
- Local build output was placed under /tmp/ernie-plugin-local-build/Ernie_artefacts/Debug/VST3/Ernie.vst3.
- JUCE API and recorder tests passed during that build.
- Backend tests had previously passed: 11 passed.
- Main was pulled and fast-forwarded to merged commit 0a6d747 (PR #3) during this work.
- The backend runs with:

source .venv/bin/activate
uvicorn app:app --reload --port 8000

If Uvicorn says the port is already in use, a server is already listening on that port; check the existing process before starting another.

## Important integration details

- Plugin API port and backend port are different: plugin 17831, backend 8000.
- Track IDs must be registered before requesting /tracks/{track_id}/plugin-listen.
- The plugin should be placed on the DAW channel being captured. Use the master bus for the full mix.
- Captures have a maximum duration. The test above used 15 seconds.
- The plugin’s capture file is temporary/cache data; the backend associates it with the plugin session ID.
- Stale means the stored track measurement is older than the freshness threshold. It does not mean an explicitly requested historical capture ID cannot be analyzed.
- The local test capture ID above may no longer exist after restarting the backend because state is in memory.

## Still to coordinate

The project is a four-person hackathon. Other teammates are handling areas such as the JUCE plugin, voice UI, and MIDI transcription. This work did not implement the JUCE plugin, voice UI, or MIDI transcription itself. The next chat should inspect the current main branch and merge the other feature work against the latest code.
