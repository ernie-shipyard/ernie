"""
Sample discovery.

Two jobs:
  1. index()            — crawl the producer's sample folders, fingerprint every one-shot/loop,
                          tag it (kick / snare / hat / perc / bass / loop / other). Cached to JSON.
  2. match_hits(...)    — given a beatbox recording, return the closest real samples for each hit,
                          so "boom-kat-tss" becomes "Kick_808_Sub.wav, Snare_Clap_03.wav, HiHat_Closed_Tight.wav".
     suggest_for_melody — given a hummed melody's range, suggest an instrument family.

Features are cheap and robust on tiny files: duration, spectral centroid, low/mid/high energy
ratios, attack sharpness, zero-crossing rate. Nearest-neighbour in that space is plenty for a demo.
"""

import os
import json
import hashlib
import numpy as np
import librosa

SR = 22050
AUDIO_EXT = {".wav", ".aif", ".aiff", ".flac", ".mp3", ".ogg", ".m4a"}
CACHE = os.path.join(os.path.dirname(__file__), ".sample_index.json")
FEAT_KEYS = ["dur", "centroid", "low", "mid", "high", "attack", "zcr"]


def features(y, sr=SR):
    y = y[: sr * 3] if len(y) > sr * 3 else y             # first 3 s is enough to characterise it
    if len(y) < 256:
        return None
    S = np.abs(np.fft.rfft(y * np.hanning(len(y)))) ** 2
    f = np.fft.rfftfreq(len(y), 1 / sr)
    tot = S.sum() + 1e-9
    env = np.abs(y)
    peak_i = int(np.argmax(env))
    attack = peak_i / sr                                   # seconds to peak; one-shots are tiny
    centroid = float((S * f).sum() / tot)
    return {
        "dur": float(len(y) / sr),
        "centroid": centroid,
        "low": float(S[f < 180].sum() / tot),
        "mid": float(S[(f >= 180) & (f < 2500)].sum() / tot),
        "high": float(S[f >= 2500].sum() / tot),
        "attack": float(min(attack, 0.5)),
        "zcr": float(np.mean(librosa.zero_crossings(y))),
    }


def tag(feat, name=""):
    n = name.lower()
    # filename first — producers name things. then the spectrum.
    for word, t in [("kick", "kick"), ("bd", "kick"), ("808", "bass"), ("snare", "snare"), ("clap", "snare"),
                    ("rim", "snare"), ("hat", "hat"), ("hh", "hat"), ("cymbal", "hat"), ("ride", "hat"),
                    ("bass", "bass"), ("sub", "bass"), ("loop", "loop"), ("perc", "perc"), ("shaker", "perc")]:
        if word in n:
            return t
    if feat["dur"] > 2.0:
        return "loop"
    if feat["low"] > 0.5 and feat["dur"] < 1.5:
        return "kick"
    if feat["high"] > 0.6 and feat["dur"] < 0.6:
        return "hat"
    if feat["mid"] > 0.4 and feat["dur"] < 1.0:
        return "snare"
    return "perc" if feat["dur"] < 1.0 else "other"


# --------------------------------------------------------------------- index

def _walk(dirs):
    for d in dirs:
        for root, _, files in os.walk(os.path.expanduser(d)):
            for fn in files:
                if os.path.splitext(fn)[1].lower() in AUDIO_EXT and not fn.startswith("."):
                    yield os.path.join(root, fn)


def index(dirs=None, limit=3000, force=False):
    dirs = dirs or [d for d in os.getenv("SAMPLE_DIRS", "").split(":") if d]
    if not dirs:
        return {"count": 0, "dirs": [], "error": "Set SAMPLE_DIRS=/path/one:/path/two in .env"}
    key = hashlib.md5(":".join(sorted(dirs)).encode()).hexdigest()[:8]
    if not force and os.path.exists(CACHE):
        cached = json.load(open(CACHE))
        if cached.get("key") == key:
            return cached
    items = []
    for i, p in enumerate(_walk(dirs)):
        if i >= limit:
            break
        try:
            y, _ = librosa.load(p, sr=SR, mono=True, duration=3.0)
            f = features(y)
            if f:
                items.append({"path": p, "name": os.path.basename(p), "tag": tag(f, os.path.basename(p)), **f})
        except Exception:
            continue
    out = {"key": key, "dirs": dirs, "count": len(items), "items": items,
           "tags": {t: sum(1 for it in items if it["tag"] == t) for t in ["kick", "snare", "hat", "perc", "bass", "loop", "other"]}}
    json.dump(out, open(CACHE, "w"))
    return out


# --------------------------------------------------------------------- match

_SCALE = {"dur": 1.0, "centroid": 1 / 3000, "low": 3, "mid": 3, "high": 3, "attack": 10, "zcr": 5}


def _vec(f):
    return np.array([f[k] * _SCALE[k] for k in FEAT_KEYS])


def nearest(feat, items, k=3, want_tag=None):
    pool = [it for it in items if (want_tag is None or it["tag"] == want_tag)]
    if not pool:
        pool = items
    if not pool:
        return []
    v = _vec(feat)
    d = [(float(np.linalg.norm(_vec(it) - v)), it) for it in pool]
    d.sort(key=lambda x: x[0])
    return [{"name": it["name"], "path": it["path"], "tag": it["tag"], "distance": round(dist, 3)} for dist, it in d[:k]]


def match_hits(y, onsets, hit_types, idx, k=2):
    """For each distinct hit type in a beatbox take, find the closest real samples.
    Uses the loudest example of each type so the fingerprint is clean."""
    items = idx.get("items", [])
    if not items:
        return {}
    by_type = {}
    for t, kind in zip(onsets, hit_types):
        i = int(t * SR); seg = y[i: i + int(0.25 * SR)]
        if len(seg) < 256:
            continue
        loud = float(np.max(np.abs(seg)))
        if kind not in by_type or loud > by_type[kind][0]:
            by_type[kind] = (loud, seg)
    out = {}
    for kind, (_, seg) in by_type.items():
        f = features(seg)
        out[kind] = nearest(f, items, k=k, want_tag=kind) if f else []
    return out


def suggest_for_melody(notes_midi):
    """Instrument family from range/register — enough to pick a preset and move on."""
    if not notes_midi:
        return "anything"
    lo, hi = min(notes_midi), max(notes_midi)
    if hi < 48:
        return "bass — sub or a plucky bass synth"
    if lo >= 72:
        return "lead — bright pluck, bell or whistle-y synth"
    if hi - lo > 14:
        return "lead with range — keys or a vocal-style synth"
    return "chords or a mellow lead — keys, pad or guitar"
