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
import re
import json
import random
import hashlib
import numpy as np
import librosa

SR = 22050
AUDIO_EXT = {".wav", ".aif", ".aiff", ".flac", ".mp3", ".ogg", ".m4a"}
CACHE = os.path.join(os.path.dirname(__file__), ".sample_index.json")
FEAT_KEYS = ["dur", "centroid", "low", "mid", "high", "attack", "zcr"]
TAGS = ["kick", "snare", "hat", "perc", "bass", "pad", "synth", "vocal", "fx", "loop", "other"]


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
                    ("bass", "bass"), ("sub", "bass"), ("pad", "pad"), ("synth", "synth"), ("lead", "synth"),
                    ("keys", "synth"), ("vocal", "vocal"), ("vox", "vocal"), ("fx", "fx"), ("riser", "fx"),
                    ("impact", "fx"), ("loop", "loop"), ("perc", "perc"), ("shaker", "perc")]:
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


# --------------------------------------------------------------------- key

_ROOTS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
# "Cmin", "F#maj", "Amin", "Ebmajor", "A_minor", "Am" — root + mode, not glued to other letters
_KEY_RE = re.compile(r"(?<![A-Za-z])([A-Ga-g])(#|b|♯|♭)?(?:[ _-]?(minor|major|min|maj)|(m))(?![A-Za-z])", re.I)


def parse_key(text, loose=False):
    """Key from a filename (without extension) -> (pitch_class, "min"|"maj"|None), or None.
    loose=True is for the key box, where a bare root ("C", "F#") is the whole entry."""
    for m in _KEY_RE.finditer(text):
        root, acc, word, bare_m = m.groups()
        if bare_m and (root.islower() or bare_m == "M"):   # "Am" yes, "am"/"AM" no
            continue
        pc = (_ROOTS[root.upper()] + {"#": 1, "♯": 1, "b": -1, "♭": -1}.get(acc or "", 0)) % 12
        return pc, "min" if (bare_m or word.lower().startswith("min")) else "maj"
    # bare root: the whole key-box entry ("C", "F#"), or the last token of a filename ("808_punch_F")
    m = re.fullmatch(r"\s*([A-Ga-g])(#|b)?\s*", text) if loose else re.search(r"[ _-]([A-G])(#|b)?$", text)
    if m:
        return (_ROOTS[m.group(1).upper()] + {"#": 1, "b": -1}.get(m.group(2) or "", 0)) % 12, None
    return None


def key_name(k):
    return f"{NOTE_NAMES[k[0]]}{'' if k[1] is None else ' ' + k[1]}" if k else None


def key_score(sample_key, want):
    """2 same key (or same root when the mode is unknown), 1 relative major/minor, 0 unkeyed, -1 clashes."""
    if not sample_key or not want:
        return 0
    (pc, mode), (wpc, wmode) = sample_key, want
    if mode is None:                                              # filename only gave a root
        return 2 if pc == wpc else -1
    if pc == wpc and (wmode is None or mode == wmode):
        return 2
    rel = (pc + 3) % 12 if mode == "min" else (pc - 3) % 12     # A min <-> C maj
    if wmode is not None and mode != wmode and rel == wpc:
        return 1
    if wmode is None and mode == "min" and rel == wpc:            # bare "C" also accepts A minor
        return 1
    return -1


GROUPS = {"drums": {"kick", "snare", "hat", "perc"}}
# "loop"-tagged files also count for a row when the filename names that layer ("drum_loop_124", "perc_loop")
LOOP_WORDS = {"drums": ("drum", "beat", "perc"), "bass": ("bass",),
              "pad": ("pad", "synth", "keys"), "synth": ("pad", "synth", "keys")}


def pick(idx, want_tag, key_text="", k=5):
    """Up to k library candidates for a stack row: right tag, in-key first, then unkeyed, clashing keys last.
    Tags/keys are recomputed from the cached features + filename so new keywords work without a rescan."""
    want = parse_key(key_text, loose=True) if key_text else None
    tags = GROUPS.get(want_tag, {want_tag})
    scored = []
    for it in idx.get("items", []):
        t, n = tag(it, it["name"]), it["name"].lower()
        if t not in tags and not (t == "loop" and any(w in n for w in LOOP_WORDS.get(want_tag, ()))):
            continue
        sk = parse_key(os.path.splitext(it["name"])[0])
        scored.append((key_score(sk, want), random.random(), it, sk))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [{"name": it["name"], "path": it["path"], "tag": tag(it, it["name"]), "key": key_name(sk), "key_match": sc > 0}
            for sc, _, it, sk in scored[:k]]


def in_index(idx, path):
    return any(it["path"] == path for it in idx.get("items", []))


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
           "tags": {t: sum(1 for it in items if it["tag"] == t) for t in TAGS}}
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
