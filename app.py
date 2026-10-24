"""
hum — hum a melody, get a track, revise it by talking.

Pipeline:
  1. browser records a hum   -> POST /analyze   -> tempo + pitch contour -> text prompt
  2. prompt                   -> POST /compose   -> ElevenLabs Music API  -> mp3
  3. browser records speech   -> POST /revise    -> Scribe v2 transcribes -> prompt edited -> /compose again
"""

import io
import os
import re
import uuid
import tempfile
import subprocess

import numpy as np
import librosa
from dotenv import load_dotenv
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from elevenlabs.client import ElevenLabs

load_dotenv()

app = FastAPI(title="hum")
eleven = ElevenLabs(api_key=os.getenv("ELEVENLABS_API_KEY"))

OUT_DIR = os.path.join(tempfile.gettempdir(), "hum_tracks")
os.makedirs(OUT_DIR, exist_ok=True)

DEFAULT_LENGTH_MS = int(os.getenv("TRACK_LENGTH_MS", "15000"))
MUSIC_MODEL = os.getenv("MUSIC_MODEL", "music_v2_5")


# ---------------------------------------------------------------- audio I/O

def _to_wav(raw: bytes) -> str:
    """Browser MediaRecorder gives webm/opus; librosa wants wav. Use ffmpeg."""
    src = tempfile.NamedTemporaryFile(suffix=".webm", delete=False)
    src.write(raw)
    src.close()
    dst = src.name + ".wav"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", src.name, "-ac", "1", "-ar", "22050", dst],
        check=True,
    )
    return dst


# ---------------------------------------------------------------- analysis

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def analyze_hum(wav_path: str) -> dict:
    y, sr = librosa.load(wav_path, sr=22050, mono=True)
    if len(y) < sr // 2:
        raise ValueError("Recording too short — hum for at least a couple of seconds.")

    # tempo
    tempo, beats = librosa.beat.beat_track(y=y, sr=sr)
    tempo = float(np.atleast_1d(tempo)[0]) if tempo is not None else 0.0
    if tempo < 40 or tempo > 220:   # beat tracker is unreliable on a lone voice; clamp
        tempo = 96.0

    # pitch contour (pyin handles humming well)
    f0, voiced, _ = librosa.pyin(y, fmin=librosa.note_to_hz("C2"), fmax=librosa.note_to_hz("C6"), sr=sr)
    f0 = f0[voiced & ~np.isnan(f0)]
    if len(f0) < 10:
        raise ValueError("Couldn't hear a melody — try humming louder or closer to the mic.")

    midi = librosa.hz_to_midi(f0)
    root = NOTE_NAMES[int(np.round(np.median(midi))) % 12]
    span = float(np.percentile(midi, 95) - np.percentile(midi, 5))

    # contour shape: compare first third to last third
    n = len(midi)
    start, end = np.mean(midi[: n // 3]), np.mean(midi[-n // 3 :])
    drift = end - start
    if drift > 2:
        contour = "a melody that rises toward the end"
    elif drift < -2:
        contour = "a melody that falls and resolves downward"
    else:
        contour = "a melody that circles around a central note"

    # how jumpy is it?
    steps = np.abs(np.diff(midi))
    leapy = float(np.mean(steps > 2))
    movement = "with wide, leaping intervals" if leapy > 0.15 else "with smooth, stepwise motion"

    # energy / dynamics
    rms = librosa.feature.rms(y=y)[0]
    dyn = float(np.std(rms) / (np.mean(rms) + 1e-9))
    feel = "energetic and punchy" if dyn > 0.6 else "steady and laid-back"

    return {
        "tempo": round(tempo),
        "key_center": root,
        "range_semitones": round(span),
        "contour": contour,
        "movement": movement,
        "feel": feel,
        "duration_s": round(len(y) / sr, 1),
    }


def build_prompt(a: dict, genre: str = "lo-fi hip hop") -> str:
    return (
        f"A {genre} instrumental track at about {a['tempo']} bpm, centered on {a['key_center']}. "
        f"The lead melody is {a['contour']}, {a['movement']}, spanning roughly {a['range_semitones']} semitones. "
        f"The overall feel is {a['feel']}. Keep the melody prominent and hummable; no vocals."
    )


# ---------------------------------------------------------------- revision

# cheap, deterministic edits so the demo never depends on a second LLM.
# swap this for an LLM call later if you want richer edits.
REVISIONS = [
    (r"\b(slower|slow it down|halve the tempo)\b", lambda p: _retempo(p, 0.75)),
    (r"\b(faster|speed it up|double the tempo)\b",  lambda p: _retempo(p, 1.3)),
    (r"\b(sad|melancholy|darker|minor)\b",         lambda p: p + " Make it melancholic, in a minor key."),
    (r"\b(happy|brighter|major|uplifting)\b",      lambda p: p + " Make it bright and uplifting, in a major key."),
    (r"\b(warmer|warm)\b",                         lambda p: p + " Use warm analog textures, soft tape saturation."),
    (r"\b(colder|cold|icy)\b",                     lambda p: p + " Use cold, glassy digital synths."),
    (r"\b(add|with) (some )?drums\b",              lambda p: p + " Add a clear drum groove."),
    (r"\b(no|remove the|drop the) drums\b",        lambda p: p + " No drums or percussion."),
    (r"\b(more|add) bass\b",                       lambda p: p + " Heavier, more prominent bass."),
    (r"\b(piano)\b",                               lambda p: p + " Lead the melody with piano."),
    (r"\b(guitar)\b",                              lambda p: p + " Lead the melody with guitar."),
    (r"\b(strings|orchestra|orchestral)\b",        lambda p: p + " Add lush orchestral strings."),
    (r"\b(synth|electronic|edm)\b",                lambda p: p + " Make it electronic with synths."),
    (r"\b(jazz|jazzy)\b",                          lambda p: p + " Give it a jazz feel with extended chords."),
    (r"\b(rock)\b",                                lambda p: p + " Make it a rock arrangement with electric guitars."),
    (r"\b(acoustic)\b",                            lambda p: p + " Acoustic instruments only."),
    (r"\b(epic|cinematic|bigger)\b",               lambda p: p + " Make it epic and cinematic, building to a big climax."),
    (r"\b(calm|chill|softer|quieter)\b",           lambda p: p + " Make it calmer and softer."),
]


def _retempo(prompt: str, factor: float) -> str:
    m = re.search(r"about (\d+) bpm", prompt)
    if not m:
        return prompt
    new = int(int(m.group(1)) * factor)
    return prompt.replace(m.group(0), f"about {new} bpm")


def apply_revision(prompt: str, instruction: str) -> tuple[str, list[str]]:
    text = instruction.lower()
    applied = []
    for pattern, fn in REVISIONS:
        if re.search(pattern, text):
            prompt = fn(prompt)
            applied.append(pattern)
    if not applied:
        # fall through: just append what they said. The music model copes surprisingly well.
        prompt = prompt + f" Also: {instruction.strip()}."
        applied.append("freeform")
    return prompt, applied


# ---------------------------------------------------------------- music

def compose(prompt: str, length_ms: int = DEFAULT_LENGTH_MS) -> str:
    try:
        track = eleven.music.compose(prompt=prompt, music_length_ms=length_ms, model_id=MUSIC_MODEL)
    except Exception as e:  # ElevenLabs returns a prompt_suggestion on bad_prompt — use it
        body = getattr(e, "body", None) or {}
        detail = body.get("detail", {}) if isinstance(body, dict) else {}
        suggestion = (detail.get("data") or {}).get("prompt_suggestion")
        if suggestion:
            track = eleven.music.compose(prompt=suggestion, music_length_ms=length_ms, model_id=MUSIC_MODEL)
        else:
            raise
    track_id = uuid.uuid4().hex[:10]
    path = os.path.join(OUT_DIR, f"{track_id}.mp3")
    with open(path, "wb") as f:
        for chunk in track:
            f.write(chunk)
    return track_id


# ---------------------------------------------------------------- routes

@app.post("/analyze")
async def analyze(audio: UploadFile = File(...), genre: str = Form("lo-fi hip hop")):
    raw = await audio.read()
    try:
        wav = _to_wav(raw)
        a = analyze_hum(wav)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except subprocess.CalledProcessError:
        raise HTTPException(500, "ffmpeg failed to decode the recording")
    return {"analysis": a, "prompt": build_prompt(a, genre)}


@app.post("/compose")
async def compose_route(prompt: str = Form(...)):
    try:
        track_id = compose(prompt)
    except Exception as e:
        raise HTTPException(502, f"Music API error: {e}")
    return {"track_id": track_id, "url": f"/track/{track_id}", "prompt": prompt}


@app.post("/revise")
async def revise(audio: UploadFile = File(...), prompt: str = Form(...)):
    raw = await audio.read()
    try:
        t = eleven.speech_to_text.convert(
            file=io.BytesIO(raw),
            model_id="scribe_v2",
            language_code="eng",
            tag_audio_events=False,
            diarize=False,
        )
        heard = t.text
    except Exception as e:
        raise HTTPException(502, f"Scribe error: {e}")
    new_prompt, applied = apply_revision(prompt, heard)
    return {"heard": heard, "prompt": new_prompt, "applied": applied}


@app.get("/track/{track_id}")
def get_track(track_id: str):
    path = os.path.join(OUT_DIR, f"{track_id}.mp3")
    if not os.path.exists(path):
        raise HTTPException(404)
    return FileResponse(path, media_type="audio/mpeg")


@app.get("/health")
def health():
    return {"ok": True, "has_key": bool(os.getenv("ELEVENLABS_API_KEY"))}


app.mount("/", StaticFiles(directory="static", html=True), name="static")
