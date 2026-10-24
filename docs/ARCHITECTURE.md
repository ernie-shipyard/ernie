# Architecture

Five components, matching the team plan. Everything talks to the companion app over localhost HTTP.

```
 ┌──────────────┐  POST /ingest/metrics      ┌───────────────────────────┐
 │ JUCE plugin  │  POST /ingest/audio  ────▶ │ companion app   (app.py)  │
 │ (pass-thru   │   (worker thread only)     │                           │
 │  analyzer)   │                            │  analysis  foldback/advise│◀── bounce upload
 └──────────────┘                            │  session   foldback/live  │◀── AbletonOSC (optional)
                                             │  midi      foldback/hum2midi
 ┌──────────────┐  GET /tools/*  ◀────────── │  samples   foldback/samples
 │ ElevenLabs   │  (agent webhook tools)     │  voice     foldback/persona
 │ Agent / TTS  │ ◀───────────────────────── │                           │
 │ / Scribe     │                            └───────────────────────────┘
 └──────────────┘                                        ▲
                                                 static/index.html (push-to-talk UI)
```

## Ownership

| Area | Owner | Files |
|---|---|---|
| JUCE plugin (capture + DSP in C++) | — | `plugin/` |
| DSP analysis in Python, mix notes | — | `foldback/advise.py` |
| Audio → MIDI, sample discovery | Kristyn | `foldback/hum2midi.py`, `foldback/samples.py`, `/midi/*`, `/samples/*` |
| Voice: agent config, persona, TTS/STT | — | `foldback/persona.py`, `/ask`, `/tools/*` |
| Session reader (Ableton) | — | `foldback/live.py` |
| UI | — | `static/index.html` |

## Plugin → companion contract

The plugin never blocks `processBlock()`. A worker thread drains the ring buffer and, every N seconds, does one of:

**A. Send metrics it computed itself**
```
POST http://127.0.0.1:8000/ingest/metrics
{"label": "Master",
 "metrics": {"peak_dbfs": -1.2, "rms_dbfs": -9.5, "crest_factor_db": 8.3,
             "stereo_correlation": 0.35, "lufs": -10.1,
             "bands": {"sub": 0.12, "low": 0.30, "lowmid": 0.18, "mid": 0.30, "high": 0.10}}}
```

**B. Send a short audio window and let Python do the DSP**
```
POST http://127.0.0.1:8000/ingest/audio     multipart: audio=<wav, 2–5 s>, label=Master
```

`label` is whatever the producer typed into the plugin instance ("Master", "Kick", "Bass"). One instance per bus you care about. The companion keeps the latest analysis per label in memory.

## Agent tools (ElevenLabs Agents → webhook)

| Tool | Endpoint |
|---|---|
| `getSessionContext()` | `GET /tools/session_context` |
| `getMasterAnalysis()` | `GET /tools/master_analysis` |
| `getTrackAnalysis(label)` | `GET /tools/track_analysis/{label}` |
| `compareTracks(a, b)` | `GET /tools/compare/{a}/{b}` |

For local dev, expose the companion with `ngrok http 8000` and paste the URL into the agent's tool config.

## Audio → MIDI → samples (Kristyn's lane)

```
mic ──▶ /midi/hum      ──▶ pyin pitch track ──▶ quantize ──▶ .mid  + instrument suggestion
mic ──▶ /midi/beatbox  ──▶ onsets ──▶ band-energy classify (kick/snare/hat) ──▶ .mid (GM drum map)
                                   └──▶ loudest hit per type ──▶ fingerprint ──▶ nearest samples in SAMPLE_DIRS
file ──▶ /samples/match ──▶ fingerprint one sound ──▶ nearest samples
        /samples/scan   ──▶ crawl SAMPLE_DIRS, tag everything, cache to foldback/.sample_index.json
```

Open problems worth attacking next, in order:
1. Snare vs. hat on real beatboxing (noisy; current rule is band-energy only — add decay length).
2. Tempo lock: use the DAW tempo from `/session` instead of estimating from the take.
3. Melody: pitch-bend or glide is currently snapped to the nearest semitone; fine for a demo.
