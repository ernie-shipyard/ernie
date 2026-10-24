# foldback

A loud little studio assistant that lives next to your DAW. It reads your Ableton set, knows what plugins and samples you're using, gives you mixing notes out loud (with a personality), turns your humming into MIDI or a full track, and lets you argue with it by voice.

Built at Tech@NYU Shipyard, Oct 24 2026, for the Best Use of ElevenLabs track.

## What it does

| Tab | What happens | ElevenLabs |
|---|---|---|
| **Session** | Reads tempo, tracks, devices/plugins, clips and sample files from the live Ableton set | — |
| **Mix notes** | Rule-based notes on the session graph (no sidechain on bass, everything panned center, 3× compressor, a sample called `final_FINAL`), or spectral notes on an exported bounce (mud, crushed dynamics, missing top end). Spoken in character. | Text to Speech |
| **Talk to it** | Hold the mic and ask: what plugins am I using, what's the tempo, which samples are in here | Scribe v2 + TTS |
| **Hum → MIDI** | Hum a melody → quantized `.mid`, a new MIDI track in Live, and an instrument suggestion. Beatbox → kick/snare/hat `.mid` on a drum-rack mapping, **plus the closest real samples from your own library** for each hit (`SAMPLE_DIRS`). | — |
| **Hum → Track** | Hum → analysed → full instrumental track; then say "slower and warmer" to recompose | Music API + Scribe v2 |

## Run it

```bash
brew install ffmpeg                      # or apt install ffmpeg
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                     # paste ELEVENLABS_API_KEY
uvicorn app:app --reload
```
Open http://localhost:8000.

**Without Ableton** (demo laptop, judging table): set `FOLDBACK_MOCK=1` in `.env` and it reads `foldback/session.mock.json`.

**With Ableton Live 11/12:** install [AbletonOSC](https://github.com/ideoforms/AbletonOSC) — copy the `AbletonOSC` folder into `~/Music/Ableton/User Library/Remote Scripts/`, then in Live: Settings → Link/Tempo/MIDI → Control Surface → **AbletonOSC**. foldback talks to it on UDP 11000/11001.

## Team

See `docs/ARCHITECTURE.md` for the component map, who owns what, the JUCE plugin → companion contract, and the ElevenLabs agent tool endpoints.

## Personality

`foldback/persona.py` holds the system prompt and a template-driven script that works with no LLM at all. Set `LLM_BASE_URL` / `LLM_API_KEY` (any OpenAI-compatible endpoint; Grok's is) and the same facts get rewritten with more range.

## Layout

```
app.py                  FastAPI routes
foldback/live.py        Ableton session reader/writer (AbletonOSC) + mock
foldback/advise.py      structural + spectral mixing notes, inventory
foldback/persona.py     the voice: prompt, script, LLM rewrite, TTS
foldback/hum2midi.py    hum → melody MIDI, beatbox → drum MIDI
foldback/samples.py     sample library index + nearest-sample matching
plugin/                 JUCE analyzer (spec only, not built yet)
docs/ARCHITECTURE.md    component map + contracts
static/index.html       the whole UI
```

MIT.
