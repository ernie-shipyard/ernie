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


def name_tag(name):
    """Tag from a filename or folder name alone, or None."""
    n = name.lower()
    for word, t in [("kick", "kick"), ("bd", "kick"), ("808", "bass"), ("snare", "snare"), ("clap", "snare"),
                    ("rim", "snare"), ("hat", "hat"), ("hh", "hat"), ("cymbal", "hat"), ("ride", "hat"),
                    ("bass", "bass"), ("sub", "bass"), ("pad", "pad"), ("synth", "synth"), ("lead", "synth"),
                    ("keys", "synth"), ("vocal", "vocal"), ("vox", "vocal"), ("fx", "fx"), ("riser", "fx"),
                    ("impact", "fx"), ("loop", "loop"), ("perc", "perc"), ("shaker", "perc")]:
        if word in n:
            return t
    return None


def path_tag(path, bpm=None):
    """Index-time tag without touching the audio: filename, then the two nearest folders ("…/Leads/Wet/x.wav",
    "…/Kicks/x.wav"), then "loop" if the name carries a tempo."""
    parent = os.path.dirname(path)
    for part in (os.path.basename(path), os.path.basename(parent), os.path.basename(os.path.dirname(parent))):
        t = name_tag(part)
        if t:
            return t
    return "loop" if bpm else "other"


def tag(feat, name=""):
    # filename first — producers name things. then the spectrum.
    t = name_tag(name) if name else None
    if t:
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


# --------------------------------------------------------------------- tempo

_BPM_TAG = re.compile(r"(\d{2,3})[ _-]?bpm", re.I)                 # "120bpm", "75_bpm"
_BPM_TOKEN = re.compile(r"(?:^|[ _-])(\d{2,3})(?=[ _-]|$)")        # "_130_", "124_" — a bare number token


def parse_bpm(name):
    """Tempo from a filename (without extension), or None. Only 60–200 counts, so "808", "_12_" or "_01" don't."""
    for rx in (_BPM_TAG, _BPM_TOKEN):
        for m in rx.finditer(name):
            if 60 <= int(m.group(1)) <= 200:
                return int(m.group(1))
    return None


TEMPO_TIERS = ["exact", "near", "unknown", "far"]                   # pick() rank order


def tempo_tier(sample_bpm, want_bpm):
    if not sample_bpm:
        return "unknown"
    if abs(sample_bpm - want_bpm) < 0.5:
        return "exact"
    return "near" if abs(sample_bpm - want_bpm) <= 0.03 * want_bpm else "far"


GROUPS = {"drums": {"kick", "snare", "hat", "perc"}}
# "loop"-tagged files also count for a row when the filename names that layer ("drum_loop_124", "perc_loop")
LOOP_WORDS = {"drums": ("drum", "beat", "perc"), "bass": ("bass",),
              "pad": ("pad", "synth", "keys"), "synth": ("pad", "synth", "keys")}


def pick(idx, want_tag, key_text="", bpm=None, k=5):
    """Up to k library candidates for a stack row with the right tag. With bpm: exact tempo first, then within
    3%, unknown, far. Within a tempo tier: in-key, unkeyed, clashing keys.
    Tag/key/bpm come from the index (filenames only), so rule changes need a rescan — which takes seconds."""
    want = parse_key(key_text, loose=True) if key_text else None
    tags = GROUPS.get(want_tag, {want_tag})
    scored = []
    for it in idx.get("items", []):
        t, n = it["tag"], it["name"].lower()
        if t not in tags and not (t == "loop" and any(w in n for w in LOOP_WORDS.get(want_tag, ()))):
            continue
        sk, sb = it["key"], it["bpm"]
        tt = tempo_tier(sb, bpm) if bpm else "unknown"
        scored.append((TEMPO_TIERS.index(tt), -key_score(sk, want), random.random(), it, sk, sb, tt))
    scored.sort(key=lambda x: x[:3])
    return [{"name": it["name"], "path": it["path"], "tag": it["tag"], "key": key_name(sk), "key_match": ks < 0,
             "bpm": sb, "tempo": tt}
            for _, ks, _, it, sk, sb, tt in scored[:k]]


_paths = {}                # index key -> set of paths, for /sample/file's allow-list check


def in_index(idx, path):
    if idx.get("key") not in _paths:
        _paths[idx.get("key")] = {it["path"] for it in idx.get("items", [])}
    return path in _paths[idx.get("key")]


# --------------------------------------------------------------------- index

def _walk(dirs):
    for d in dirs:
        for root, _, files in os.walk(os.path.expanduser(d)):
            for fn in files:
                if os.path.splitext(fn)[1].lower() in AUDIO_EXT and not fn.startswith("."):
                    yield os.path.join(root, fn)


INDEX_VERSION = 2          # bump when the item shape or naming rules change, so old caches rebuild
_idx_mem = {}              # cache key -> index, so pick/file requests don't re-read the JSON every time


def index(dirs=None, force=False):
    """Filenames only — path, name, tag, key, bpm. No audio is opened, so thousands of files take seconds.
    Audio features are computed later, per file, only when matching needs them (file_features)."""
    dirs = dirs or [d for d in os.getenv("SAMPLE_DIRS", "").split(":") if d]
    if not dirs:
        return {"count": 0, "dirs": [], "error": "Set SAMPLE_DIRS=/path/one:/path/two in .env"}
    key = hashlib.md5(f"v{INDEX_VERSION}:{':'.join(sorted(dirs))}".encode()).hexdigest()[:8]
    if not force:
        if key in _idx_mem:
            return _idx_mem[key]
        if os.path.exists(CACHE):
            cached = json.load(open(CACHE))
            if cached.get("key") == key:
                _idx_mem[key] = cached
                return cached
    items = []
    for p in _walk(dirs):
        name = os.path.basename(p)
        stem = os.path.splitext(name)[0]
        k, bpm = parse_key(stem), parse_bpm(stem)
        items.append({"path": p, "name": name, "tag": path_tag(p, bpm), "key": k, "bpm": bpm})
    out = {"key": key, "dirs": dirs, "count": len(items), "items": items,
           "tags": {t: sum(1 for it in items if it["tag"] == t) for t in TAGS}}
    json.dump(out, open(CACHE, "w"))
    _idx_mem.clear(); _idx_mem[key] = out; _paths.pop(key, None)
    return out


# --------------------------------------------------------------------- lazy features

FEAT_CACHE = os.path.join(os.path.dirname(__file__), ".sample_features.json")
_feats = None              # path -> {"mtime": float, "f": features | None}


def file_features(path):
    """The 7 features for one library file, computed on first use and cached per path (invalidated by mtime).
    Call save_features() after a batch."""
    global _feats
    if _feats is None:
        _feats = json.load(open(FEAT_CACHE)) if os.path.exists(FEAT_CACHE) else {}
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    e = _feats.get(path)
    if e and e["mtime"] == mtime:
        return e["f"]
    try:
        y, _ = librosa.load(path, sr=SR, mono=True, duration=3.0)
        f = features(y)
    except Exception:
        f = None
    _feats[path] = {"mtime": mtime, "f": f}
    return f


def save_features():
    if _feats is not None:
        json.dump(_feats, open(FEAT_CACHE, "w"))


# --------------------------------------------------------------------- match

_SCALE = {"dur": 1.0, "centroid": 1 / 3000, "low": 3, "mid": 3, "high": 3, "attack": 10, "zcr": 5}


def _vec(f):
    return np.array([f[k] * _SCALE[k] for k in FEAT_KEYS])


MATCH_POOL = 300           # most files whose features one match may load; first run ~10 ms each, then cached


def nearest(feat, items, k=3, want_tag=None):
    pool = [it for it in items if (want_tag is None or it["tag"] == want_tag)]
    if not pool:
        pool = items
    if not pool:
        return []
    if len(pool) > MATCH_POOL:                                 # stable subset, so the feature cache keeps hitting
        pool = random.Random(0).sample(pool, MATCH_POOL)
    v = _vec(feat)
    d = []
    for it in pool:
        f = file_features(it["path"])
        if f:
            d.append((float(np.linalg.norm(_vec(f) - v)), it))
    save_features()
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
