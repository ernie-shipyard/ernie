"""Hum / sing -> MIDI melody.  Beatbox -> MIDI drums.  Pure librosa + mido, no API."""

import os
import json
import datetime
import numpy as np
import librosa
import mido

from . import samples

SR = 22050

# beatbox hit classification (classify_hit / beatbox_to_midi_file / calibrate)
HIT_WIN_S = 0.09            # feature window after each onset
PAD_S = 0.05                # silence prepended so a hit at t=0 is caught
GATE_RATIO = 0.25           # drop onsets whose window peak < this * loudest hit in the take
MIN_GAP_S = 0.2             # no-tempo path: drop an onset within this of a louder one (double triggers)
SLOTS_PER_BAR = 16          # known-tempo path: 16th-note grid in 4/4, strongest onset per slot wins
SLOT_MIN_STRENGTH = 0.1     # known-tempo path: onset-strength peak must reach this fraction of the take's max
MAX_BACKTRACK_S = 0.05      # known-tempo path: an onset starts at most this long before its strength peak
BPM_RANGE = (40, 240)       # accepted metronome tempos
PROTO_PATH = os.path.join(os.path.dirname(__file__), "prototypes.json")
PROTO_KINDS = ("kick", "snare", "hat")
STD_FLOOR = 0.1             # per-feature std floor, as a fraction of the feature's global scale (1 / samples._SCALE)

# calibration (one 2-bar clip per class, recorded with the click)
CAL_BARS = 2
CAL_MIN_HITS = 3            # fewer hits than this -> redo
CAL_MIN_PEAK = 0.05         # absolute floor for a clip's loudest hit (full scale = 1.0)
CAL_REL_PEAK = 0.25         # ... and at least this fraction of the loudest calibration clip
SEPARATION = 1.5            # warn when two prototypes' means are closer than this x their combined spread
TIPS = {"snare": "try 'pf'", "hat": "try a sharper 'ts'"}

PROTOTYPES = {k: v for k, v in json.load(open(PROTO_PATH)).items() if k in PROTO_KINDS}  # {kind: {mean, std, hits}}
PENDING = {}                # calibration clips this session: {kind: {"prototype", "peak", "hits", "centroid"}}
CALIBRATED = False          # True once finish_calibration has applied a full set


def load(path):
    y, _ = librosa.load(path, sr=SR, mono=True)
    return y


# ----------------------------------------------------------------- melody

def hum_to_notes(y, tempo_bpm=None, min_note_s=0.08):
    """Returns (notes, tempo). notes = [(midi_pitch, start_s, dur_s, velocity)]."""
    f0, voiced, _ = librosa.pyin(y, fmin=librosa.note_to_hz("C2"), fmax=librosa.note_to_hz("C6"), sr=SR)
    hop_s = 512 / SR
    midi = librosa.hz_to_midi(np.nan_to_num(f0, nan=0.0))
    midi = np.where(voiced, np.round(midi), 0).astype(int)
    rms = librosa.feature.rms(y=y, hop_length=512)[0]
    rms = rms[: len(midi)] if len(rms) >= len(midi) else np.pad(rms, (0, len(midi) - len(rms)))

    notes, cur, start = [], 0, 0
    for i, p in enumerate(np.append(midi, 0)):
        if p != cur:
            if cur > 0:
                dur = (i - start) * hop_s
                if dur >= min_note_s:
                    vel = int(np.clip(40 + 120 * np.mean(rms[start:i]) / (rms.max() + 1e-9), 40, 127))
                    notes.append((int(cur), start * hop_s, dur, vel))
            cur, start = p, i

    if tempo_bpm is None:
        t, _ = librosa.beat.beat_track(y=y, sr=SR)
        tempo_bpm = float(np.atleast_1d(t)[0])
        if not 50 <= tempo_bpm <= 200:
            tempo_bpm = 100.0
    return notes, tempo_bpm


def quantize(notes, tempo_bpm, grid=0.25):
    """Snap starts/durations to a grid in beats (0.25 = 16th notes)."""
    spb = 60.0 / tempo_bpm
    out = []
    for p, s, d, v in notes:
        sb = round(s / spb / grid) * grid
        db = max(grid, round(d / spb / grid) * grid)
        out.append((p, sb, db, v))
    return out  # now in beats


def notes_to_midi(notes_beats, tempo_bpm, path, channel=0, program=0):
    mid = mido.MidiFile(ticks_per_beat=480)
    tr = mido.MidiTrack(); mid.tracks.append(tr)
    tr.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(tempo_bpm)))
    if channel != 9:
        tr.append(mido.Message("program_change", program=program, channel=channel, time=0))
    events = []
    for p, sb, db, v in notes_beats:
        events.append((int(sb * 480), "on", p, v))
        events.append((int((sb + db) * 480), "off", p, 0))
    events.sort(key=lambda e: (e[0], e[1] == "on"))  # offs before ons at same tick
    t = 0
    for tick, kind, p, v in events:
        tr.append(mido.Message("note_on" if kind == "on" else "note_off", note=p, velocity=v, channel=channel, time=tick - t))
        t = tick
    mid.save(path)
    return path


def hum_to_midi_file(wav_path, out_path, tempo_bpm=None):
    y = load(wav_path)
    notes, tempo = hum_to_notes(y, tempo_bpm)
    if not notes:
        raise ValueError("No melody detected — hum louder, closer to the mic.")
    q = quantize(notes, tempo)
    notes_to_midi(q, tempo, out_path)
    return {"tempo": round(tempo), "note_count": len(q),
            "notes": [librosa.midi_to_note(p) for p, *_ in q][:16],
            "pitches": [int(p) for p, *_ in q]}


# ----------------------------------------------------------------- beatbox

KICK, SNARE, HAT = 36, 38, 42  # General MIDI, which is also Ableton's default drum rack mapping


PITCH = {"kick": KICK, "snare": SNARE, "hat": HAT}


def _window_peak(y, t):
    return float(np.max(np.abs(y[int(t * SR): int((t + HIT_WIN_S) * SR)]), initial=0.0))


def _detect(wav_path):
    """Padded audio, raw onset times (in padded-audio seconds), and each onset's window peak."""
    y = np.concatenate([np.zeros(int(PAD_S * SR)), load(wav_path)])
    onsets = librosa.onset.onset_detect(y=y, sr=SR, units="time", backtrack=True, delta=0.08)
    return y, onsets, np.array([_window_peak(y, t) for t in onsets])


def take_hits(wav_path):
    """Padded audio + onset times that survive the peak gate and the double-trigger debounce."""
    y, onsets, peaks = _detect(wav_path)
    if len(peaks):
        keep = peaks >= GATE_RATIO * peaks.max()             # drop breath / room-tail ghost onsets
        onsets, peaks = onsets[keep], peaks[keep]
    kept = []
    for j in np.argsort(-peaks):                             # loudest first, so the louder of a double trigger wins
        if all(abs(onsets[j] - onsets[k]) >= MIN_GAP_S for k in kept):
            kept.append(j)
    return y, onsets[sorted(kept)]


def grid_hits(wav_path, bpm, bars=None, count_in_s=0.0):
    """Known tempo: in each 16th slot (counted from the end of the count-in, owning +-1/32 note) take the
    strongest onset-strength peak, so soft hits right after loud ones aren't lost to onset_detect's
    adaptive threshold. Each peak is backtracked like onset_detect, but by at most MAX_BACKTRACK_S so a
    hit straight after a loud one doesn't walk back into that one's tail; the slot comes from the
    backtracked start (a soft 'b' peaks ~90 ms after it starts). Then the amplitude gate.
    Returns padded audio, kept onset times (padded-audio seconds, for analysis) and their slot indices."""
    y = np.concatenate([np.zeros(int(PAD_S * SR)), load(wav_path)])
    env = librosa.onset.onset_strength(y=y, sr=SR)                 # same envelope + hop (512) as onset_detect
    env = (env - env.min()) / (env.max() - env.min() + 1e-9)
    times = librosa.frames_to_time(np.arange(len(env)), sr=SR)
    peak_i = np.array([i for i in range(1, len(env) - 1)
                       if env[i] > env[i - 1] and env[i] >= env[i + 1] and env[i] >= SLOT_MIN_STRENGTH], dtype=int)
    if not len(peak_i):
        return y, np.array([]), np.array([], dtype=int)
    starts = np.maximum(times[librosa.onset.onset_backtrack(peak_i, env)], times[peak_i] - MAX_BACKTRACK_S)
    best = {}
    for i, t in zip(peak_i, starts):
        s = int(round((t - PAD_S - count_in_s) / (60.0 / bpm / 4)))
        if s < 0 or (bars and s >= SLOTS_PER_BAR * bars):
            continue
        if s not in best or env[i] > env[best[s][0]]:
            best[s] = (i, t)
    if not best:
        return y, np.array([]), np.array([], dtype=int)
    order = np.array(sorted(best), dtype=int)
    onsets = np.array([best[s][1] for s in order])
    peaks = np.array([_window_peak(y, t) for t in onsets])
    keep = peaks >= GATE_RATIO * peaks.max()                       # drop breath / room-tail ghost onsets
    return y, onsets[keep], order[keep]


def hit_features(y, t, sr=SR, win=HIT_WIN_S):
    i = int(t * sr)
    return samples.features(y[i: i + int(win * sr)], sr)


def _mean(p):
    return np.array([p["mean"][k] for k in samples.FEAT_KEYS])


def _std(p):
    """Per-feature std, floored at STD_FLOOR of the feature's global scale so one-hit or very tight
    prototypes don't turn into needles."""
    floor = np.array([STD_FLOOR / samples._SCALE[k] for k in samples.FEAT_KEYS])
    return np.maximum(np.array([p["std"][k] for k in samples.FEAT_KEYS]), floor)


def classify_hit(y, t, sr=SR, win=HIT_WIN_S):
    """Nearest prototype by distance in std units (diagonal Mahalanobis). Prototypes come from
    prototypes.json until calibration swaps in the performer's own kick / snare / hat."""
    f = hit_features(y, t, sr, win)
    if f is None:
        return HAT
    v = np.array([f[k] for k in samples.FEAT_KEYS])
    return PITCH[min(PROTOTYPES, key=lambda k: np.linalg.norm((v - _mean(PROTOTYPES[k])) / _std(PROTOTYPES[k])))]


def _prototype(feats):
    F = np.array([[f[k] for k in samples.FEAT_KEYS] for f in feats])
    sd = F.std(axis=0, ddof=1) if len(F) > 1 else np.zeros(F.shape[1])
    return {"mean": dict(zip(samples.FEAT_KEYS, map(float, F.mean(axis=0)))),
            "std": dict(zip(samples.FEAT_KEYS, map(float, sd))), "hits": len(feats)}


def calibration_step(wav_path, kind, bpm, bars=CAL_BARS, count_in_s=0.0):
    """One class's 2-bar clip -> hit count + mean centroid. Good clips are kept in PENDING; once all
    three classes are in, finish_calibration runs and its result is returned under "done"."""
    y, onsets, _ = grid_hits(wav_path, bpm, bars, count_in_s)
    feats = [f for f in (hit_features(y, t) for t in onsets) if f]
    peak = max((_window_peak(y, t) for t in onsets), default=0.0)
    out = {"kind": kind, "hits": len(feats), "peak": round(peak, 3),
           "centroid": round(float(np.mean([f["centroid"] for f in feats]))) if feats else None}
    if not feats:
        out.update(ok=False, msg=f"{kind.title()}: no hits heard after the count-in, redo")
    elif peak < CAL_MIN_PEAK:
        out.update(ok=False, msg=f"{kind.title()}: too quiet (peak {peak:.2f}), get closer and redo")
    elif len(feats) < CAL_MIN_HITS:
        out.update(ok=False, msg=f"{kind.title()}: only {len(feats)} hit{'s' * (len(feats) != 1)}, redo")
    else:
        out.update(ok=True, msg=f"{kind.title()}: {len(feats)} hits, {out['centroid']} Hz")
        PENDING[kind] = {"prototype": _prototype(feats), "peak": peak, "hits": len(feats), "centroid": out["centroid"]}
    if not out["ok"]:
        PENDING.pop(kind, None)
    if out["ok"] and all(k in PENDING for k in PROTO_KINDS):
        out["done"] = finish_calibration()
    return out


def finish_calibration():
    """All three clips in: reject any clip much quieter than the loudest, else apply the prototypes
    and warn about pairs whose means sit within SEPARATION x their combined spread."""
    global CALIBRATED
    loud = max(PENDING[k]["peak"] for k in PROTO_KINDS)
    quiet = [k for k in PROTO_KINDS if PENDING[k]["peak"] < CAL_REL_PEAK * loud]
    if quiet:
        msg = " ".join(f"{k.title()} is much quieter than the loudest clip ({PENDING[k]['peak']:.2f} vs {loud:.2f}), redo it."
                       for k in quiet)
        for k in quiet:
            PENDING.pop(k)
        return {"ok": False, "redo": quiet, "msg": msg}
    protos = {k: PENDING[k]["prototype"] for k in PROTO_KINDS}
    PROTOTYPES.update(protos)
    CALIBRATED = True
    w = np.array([samples._SCALE[k] for k in samples.FEAT_KEYS])
    warnings = []
    for a, b in [("kick", "snare"), ("kick", "hat"), ("snare", "hat")]:
        d = float(np.linalg.norm((_mean(protos[a]) - _mean(protos[b])) * w))
        spread = float(np.linalg.norm(np.sqrt(_std(protos[a]) ** 2 + _std(protos[b]) ** 2) * w))
        if d < SEPARATION * spread:
            tips = "; ".join(f"{k}: {TIPS[k]}" for k in (a, b) if k in TIPS)
            warnings.append({"pair": [a, b], "distance": round(d, 2), "spread": round(spread, 2),
                             "msg": f"{a.title()} and {b} sound alike (distance {d:.2f} < {SEPARATION} x spread {spread:.2f}). {tips}."})
    return {"ok": True, "warnings": warnings,
            "msg": "Calibrated." + ("" if warnings else " All three are clearly apart.")}


def save_prototypes(path=None):
    """Write the in-memory prototypes as the shipped default."""
    if not CALIBRATED:
        raise ValueError("Nothing calibrated this session yet: record kick, snare and hat first.")
    out = {"_note": f"Calibrated {datetime.date.today().isoformat()} in the UI (POST /calibrate/save): the performer's "
                    f"own 2-bar kick / snare / hat clips. Replace by recalibrating and saving again.",
           "_source": {"window_s": HIT_WIN_S, **{k: {"hits": PROTOTYPES[k]["hits"]} for k in PROTO_KINDS}},
           **{k: PROTOTYPES[k] for k in PROTO_KINDS}}
    with open(path or PROTO_PATH, "w") as fh:
        json.dump(out, fh, indent=2)
    return {k: PROTOTYPES[k]["hits"] for k in PROTO_KINDS}


def beatbox_to_midi_file(wav_path, out_path, tempo_bpm=None, bars=None, count_in_s=0.0):
    """tempo_bpm given (metronome take): grid-snap, no tempo estimation, no debounce.
    count_in_s = seconds from recording start to the downbeat of bar 1."""
    if tempo_bpm:
        y, onsets, slots = grid_hits(wav_path, tempo_bpm, bars, count_in_s)
    else:
        (y, onsets), slots = take_hits(wav_path), None
    if len(onsets) < 2:
        raise ValueError("Didn't hear hits — try harder consonants: 'b', 'k', 'ts'.")
    if tempo_bpm is None:
        t, _ = librosa.beat.beat_track(y=y, sr=SR)
        tempo_bpm = float(np.atleast_1d(t)[0])
        if not 60 <= tempo_bpm <= 200:
            tempo_bpm = 110.0
    rms = librosa.feature.rms(y=y)[0]
    notes, types = [], []
    for t in onsets:
        pitch = classify_hit(y, t)
        types.append({p: k for k, p in PITCH.items()}[pitch])
        fi = min(int(t * SR / 512), len(rms) - 1)
        vel = int(np.clip(60 + 100 * rms[fi] / (rms.max() + 1e-9), 60, 127))
        notes.append((pitch, t, 0.1, vel))
    if slots is None:
        q = quantize(notes, tempo_bpm, grid=0.25)
    else:
        q = [(p, int(s) * 0.25, 0.25, v) for (p, _, _, v), s in zip(notes, slots)]   # 16th slot -> beats
    notes_to_midi(q, tempo_bpm, out_path, channel=9)
    counts = {"kick": sum(p == KICK for p, *_ in q), "snare": sum(p == SNARE for p, *_ in q), "hat": sum(p == HAT for p, *_ in q)}
    info = {"tempo": round(tempo_bpm), "hits": len(q), **counts}
    info["_onsets"], info["_types"], info["_y"] = [float(t) for t in onsets], types, y   # stripped before JSON
    return info
