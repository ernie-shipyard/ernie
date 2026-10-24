# hum

Hum a melody. Get a full track. Then tell it what to change — out loud.

Built at Tech@NYU Shipyard (Oct 24, 2026) for the Best Use of ElevenLabs track.

## How it works

1. **Hum** into the mic. `librosa` pulls out tempo, pitch contour, key center, range and dynamics.
2. Those become a natural-language brief, which the **ElevenLabs Music API** turns into an original track.
3. **Talk back** — "slower and warmer", "make it sad", "swap in a piano". **ElevenLabs Scribe v2** transcribes it, the brief is edited, and the track is recomposed.

No typing anywhere in the loop.

## Run it

```bash
brew install ffmpeg            # or apt install ffmpeg
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env           # paste your ELEVENLABS_API_KEY
uvicorn app:app --reload
```

Open http://localhost:8000 — the browser will ask for mic access.

> The Music API requires a paid ElevenLabs tier. Check `GET /health` to confirm the key loaded.

## Stack

FastAPI · librosa · ElevenLabs Music API (`music_v2_5`) · ElevenLabs Scribe v2 · vanilla JS + MediaRecorder
