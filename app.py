"""
foldback — a loud little studio assistant that lives next to your DAW.

  /session        read the Ableton set (or the mock)            -> inventory: DAW, plugins, samples
  /advise         structural mix notes, spoken in character     -> text + mp3
  /advise/audio   upload a bounce -> spectral notes, spoken     -> text + mp3
  /ask            talk to it (Scribe) about the session          -> answer, spoken
  /midi/hum       hum  -> .mid melody   (+ new MIDI track in Live)
  /midi/beatbox   beatbox -> .mid drums (+ new MIDI track in Live)
  /analyze /compose /revise   hum -> full audio track via ElevenLabs Music, revise by voice
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
from fastapi.middleware.cors import CORSMiddleware
from elevenlabs.client import ElevenLabs
from fastapi.routing import APIRoute

from foldback import live, advise, persona, hum2midi, samples
from mixengine.feedback import build_recommendations
from mixengine.main import app as mix_analysis_app, registry as mix_registry

load_dotenv()

app = FastAPI(title="foldback")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000", "http://127.0.0.1:3000",
        "http://localhost:5173", "http://127.0.0.1:5173",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Capture-ID"],
)
eleven = ElevenLabs(api_key=os.getenv("ELEVENLABS_API_KEY"))

OUT_DIR = os.path.join(tempfile.gettempdir(), "foldback_out")
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
    # most-sung pitch class, not the median — a C-E-G hum should read as C, not F#
    pcs = np.bincount((np.round(midi).astype(int) % 12), minlength=12)
    root = NOTE_NAMES[int(np.argmax(pcs))]
    span = float(np.percentile(midi, 95) - np.percentile(midi, 5))

    # contour shape: compare first third to last third
    n = len(midi)
    start, end = np.mean(midi[: n // 3]), np.mean(midi[-n // 3 :])
    drift = end - start
    if drift > 2:
        contour = "rises toward the end"
    elif drift < -2:
        contour = "falls and resolves downward"
    else:
        contour = "circles around a central note"

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
        f"The lead melody {a['contour']}, {a['movement']}, spanning roughly {a['range_semitones']} semitones. "
        f"The overall feel is {a['feel']}. Keep the melody prominent and hummable; no vocals."
    )


# ---------------------------------------------------------------- revision

# cheap, deterministic edits so the demo never depends on a second LLM.
# swap this for an LLM call later if you want richer edits.
REVISIONS = [
    (r"\b(slower|slow it down|halve the tempo)\b", lambda p: _retempo(p, 0.75)),
    (r"\b(faster|speed it up|double the tempo)\b",  lambda p: _retempo(p, 1.3)),
    (r"\b(sad\w*|melanchol\w*|dark\w*|minor|moodier)\b",         lambda p: p + " Make it melancholic, in a minor key."),
    (r"\b(happ\w*|bright\w*|major|uplift\w*|cheer\w*)\b",      lambda p: p + " Make it bright and uplifting, in a major key."),
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
    (r"\b(epic|cinematic|bigger|huge|dramatic)\b",               lambda p: p + " Make it epic and cinematic, building to a big climax."),
    (r"\b(calm\w*|chill\w*|soft\w*|quiet\w*|gentl\w*)\b",           lambda p: p + " Make it calmer and softer."),
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


# ---------------------------------------------------------------- session / advice / voice

def _session_or_503():
    try:
        return live.read_session()
    except Exception as e:
        raise HTTPException(503, f"Can't reach Ableton: {e}. Set FOLDBACK_MOCK=1 to demo without Live.")


def _speak(text: str) -> str:
    """TTS -> file id. Never let TTS failure kill the response."""
    try:
        sid = uuid.uuid4().hex[:10]
        persona.tts(eleven, text, os.path.join(OUT_DIR, f"{sid}.mp3"))
        return f"/track/{sid}"
    except Exception:
        return ""


@app.get("/session")
def session():
    s = _session_or_503()
    return {"inventory": advise.inventory(s), "session": s.to_dict()}


@app.post("/advise")
def advise_route(speak: bool = Form(True)):
    s = _session_or_503()
    notes = advise.structural_notes(s) + _plugin_capture_notes()
    inv = advise.inventory(s)
    script = persona.llm_rewrite(persona.script_from_notes(notes, inv))
    return {"notes": notes, "inventory": inv, "script": script, "audio": _speak(script) if speak else ""}


@app.post("/advise/captures")
def advise_captures(speak: bool = Form(False)):
    """Speak mixing notes from plugin listens. Does not need Ableton."""
    notes = _plugin_capture_notes()
    script = persona.script_from_notes(notes)
    return {"notes": notes, "script": script, "audio": _speak(script) if speak and notes else ""}


@app.post("/advise/audio")
async def advise_audio(audio: UploadFile = File(...), speak: bool = Form(True)):
    raw = await audio.read()
    p = os.path.join(OUT_DIR, f"bounce_{uuid.uuid4().hex[:6]}{os.path.splitext(audio.filename)[1] or '.wav'}")
    open(p, "wb").write(raw)
    try:
        notes, bands = advise.sonic_notes(p)
    except Exception as e:
        raise HTTPException(400, f"Couldn't analyze that file: {e}")
    script = persona.llm_rewrite(persona.script_from_notes(notes))
    return {"notes": notes, "bands": bands, "script": script, "audio": _speak(script) if speak else ""}


@app.post("/ask")
async def ask(audio: UploadFile = File(None), text: str = Form("")):
    if audio is not None and not text:
        raw = await audio.read()
        try:
            text = eleven.speech_to_text.convert(file=io.BytesIO(raw), model_id="scribe_v2",
                                                 language_code="eng", tag_audio_events=False, diarize=False).text
        except Exception as e:
            raise HTTPException(502, f"Scribe error: {e}")
    s = _session_or_503()
    inv, notes = advise.inventory(s), advise.structural_notes(s)
    reply = persona.answer_question(text, inv, notes)
    return {"heard": text, "reply": reply, "audio": _speak(reply)}


# ---------------------------------------------------------------- hum / beatbox -> MIDI

@app.post("/midi/hum")
async def midi_hum(audio: UploadFile = File(...), add_track: bool = Form(True)):
    raw = await audio.read()
    mid_id = uuid.uuid4().hex[:10]
    out = os.path.join(OUT_DIR, f"{mid_id}.mid")
    try:
        info = hum2midi.hum_to_midi_file(_to_wav(raw), out)
    except ValueError as e:
        raise HTTPException(400, str(e))
    track = live.create_midi_track("foldback melody") if add_track else None
    instrument = samples.suggest_for_melody(info.pop("pitches"))
    return {"midi": f"/midi/{mid_id}", "info": info, "track": track, "instrument": instrument,
            "say": f"{info['note_count']} notes at {info['tempo']} bpm, starting around {info['notes'][0] if info['notes'] else '?'}. "
                   f"That register wants {instrument}. Drag the file onto the new track."}


@app.post("/midi/beatbox")
async def midi_beatbox(audio: UploadFile = File(...), add_track: bool = Form(True)):
    raw = await audio.read()
    mid_id = uuid.uuid4().hex[:10]
    out = os.path.join(OUT_DIR, f"{mid_id}.mid")
    try:
        info = hum2midi.beatbox_to_midi_file(_to_wav(raw), out)
    except ValueError as e:
        raise HTTPException(400, str(e))
    track = live.create_midi_track("foldback drums") if add_track else None
    onsets, types, y = info.pop("_onsets"), info.pop("_types"), info.pop("_y")
    matches = samples.match_hits(y, onsets, types, samples.index()) if os.getenv("SAMPLE_DIRS") else {}
    picks = ", ".join(f"{k}: {v[0]['name']}" for k, v in matches.items() if v)
    return {"midi": f"/midi/{mid_id}", "info": info, "track": track, "samples": matches,
            "say": f"{info['hits']} hits: {info['kick']} kicks, {info['snare']} snares, {info['hat']} hats at {info['tempo']} bpm. "
                   + (f"From your library I'd load {picks}." if picks else "Load a Drum Rack on the new track and drop this in.")}


# ---------------------------------------------------------------- sample library

@app.post("/samples/scan")
def samples_scan(force: bool = Form(False)):
    idx = samples.index(force=force)
    return {k: v for k, v in idx.items() if k != "items"} | {"sample_names": [i["name"] for i in idx.get("items", [])][:200]}


@app.post("/samples/match")
async def samples_match(audio: UploadFile = File(...), k: int = Form(3)):
    """One sound in -> closest samples out. Works for a single beatboxed hit or an uploaded one-shot."""
    raw = await audio.read()
    y = hum2midi.load(_to_wav(raw))
    f = samples.features(y)
    if not f:
        raise HTTPException(400, "Too short to fingerprint.")
    idx = samples.index()
    return {"guess": samples.tag(f), "features": f, "matches": samples.nearest(f, idx.get("items", []), k=k)}


# ---------------------------------------------------------------- JUCE plugin ingest (team contract)
# The plugin POSTs here from its worker thread. Never from processBlock().
# Metrics are keyed by the label the producer gave that plugin instance ("Master", "Kick", "Bass"...).

LATEST = {}   # label -> {"metrics": {...}, "notes": [...], "ts": float}


@app.post("/ingest/metrics")
async def ingest_metrics(payload: dict):
    """{"label": "Master", "metrics": {"peak_dbfs":..,"rms_dbfs":..,"crest_factor_db":..,"stereo_correlation":..,"bands":{...}}}"""
    import time
    label = payload.get("label", "Master")
    LATEST[label] = {"metrics": payload.get("metrics", {}), "notes": [], "ts": time.time()}
    return {"ok": True, "label": label}


@app.post("/ingest/audio")
async def ingest_audio(audio: UploadFile = File(...), label: str = Form("Master")):
    """Plugin can also send a short wav window and let the Python side do the DSP."""
    import time
    raw = await audio.read()
    p = os.path.join(OUT_DIR, f"ingest_{label}_{uuid.uuid4().hex[:6]}.wav")
    open(p, "wb").write(raw)
    notes, bands = advise.sonic_notes(p)
    LATEST[label] = {"metrics": bands, "notes": notes, "ts": time.time()}
    return {"ok": True, "label": label, "metrics": bands, "notes": notes}


# ---------------------------------------------------------------- agent tools
# Register these as webhook tools on the ElevenLabs Agent so it can call them mid-conversation.

@app.get("/tools/session_context")
def tool_session_context():
    s = _session_or_503()
    return advise.inventory(s)


def _plugin_capture_notes():
    """Translate finished plugin listens into the note shape the persona already speaks."""
    tracks = [
        track for track in mix_registry.list_tracks()
        if track.latest_measurements is not None and not track.is_stale()
    ]
    if not tracks:
        return []
    notes = []
    for track_notes in build_recommendations(tracks).values():
        for note in track_notes:
            track = mix_registry.get(note.track_id)
            notes.append({
                "severity": {"high": "high", "medium": "med", "low": "low"}.get(note.priority, "low"),
                "track": None if track is None else track.name,
                "msg": note.message,
                "fix": note.fix or note.reasoning,
                "code": note.code,
                "evidence": note.evidence,
            })
    return notes


def _plugin_capture_view(label):
    wanted = label.strip().lower()
    match = next(
        (
            track for track in mix_registry.list_tracks()
            if track.latest_measurements is not None
            and wanted in {track.id.lower(), track.name.lower(), (track.role or "").lower()}
        ),
        None,
    )
    if match is None or match.latest_measurements is None:
        return None
    measurements = match.latest_measurements
    return {
        "label": match.name,
        "track_id": match.id,
        "role": match.role,
        "capture_id": match.latest_capture_id,
        "stale": match.is_stale(),
        "metrics": measurements.model_dump(mode="json"),
        "notes": [note for note in _plugin_capture_notes() if note.get("track") == match.name],
    }


@app.get("/tools/master_analysis")
def tool_master():
    captured = _plugin_capture_view("master")
    if captured is not None:
        return captured
    return LATEST.get("Master") or {"error": "No master analysis yet. Put the plugin on the master bus or upload a bounce to /advise/audio."}


@app.get("/tools/track_analysis/{label}")
def tool_track(label: str):
    captured = _plugin_capture_view(label)
    if captured is not None:
        return captured
    return LATEST.get(label) or {
        "error": f"No analysis for '{label}'. Labels seen: {list(LATEST) or [track.name for track in mix_registry.list_tracks()]}"
    }


@app.get("/tools/compare/{a}/{b}")
def tool_compare(a: str, b: str):
    A, B = LATEST.get(a), LATEST.get(b)
    if not (A and B):
        return {"error": f"Need analysis for both. Have: {list(LATEST)}"}
    ma, mb = A["metrics"], B["metrics"]
    diff = {k: round(float(ma[k]) - float(mb[k]), 3) for k in ma if k in mb and isinstance(ma[k], (int, float))}
    return {"a": a, "b": b, "a_minus_b": diff}


@app.get("/midi/{mid_id}")
def get_midi(mid_id: str):
    path = os.path.join(OUT_DIR, f"{mid_id}.mid")
    if not os.path.exists(path):
        raise HTTPException(404)
    return FileResponse(path, media_type="audio/midi", filename=f"foldback_{mid_id}.mid")


@app.get("/track/{track_id}")
def get_track(track_id: str):
    path = os.path.join(OUT_DIR, f"{track_id}.mp3")
    if not os.path.exists(path):
        raise HTTPException(404)
    return FileResponse(path, media_type="audio/mpeg")


@app.get("/health")
def health():
    key = os.getenv("ELEVENLABS_API_KEY", "")
    return {"status": "ok", "registered_tracks": mix_registry.count(),
            "ok": True, "has_key": bool(key) and key != "your_key_here", "mock": live.MOCK,
            "llm": bool(os.getenv("LLM_API_KEY"))}


app.mount("/", StaticFiles(directory="static", html=True), name="static")

# Publish the modular WAV capture/analyze API at the same origin. Keep this
# before the static catch-all so these documented API paths reach FastAPI.
_static_mount = app.router.routes.pop()
app.router.routes.extend(
    route for route in mix_analysis_app.router.routes
    if isinstance(route, APIRoute) and route.path != "/health"
)
app.router.routes.append(_static_mount)
