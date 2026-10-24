"""Hum / sing -> MIDI melody.  Beatbox -> MIDI drums.  Pure librosa + mido, no API."""

import os
import json
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
SLOTS_PER_BAR = 16          # known-tempo path: 16th-note grid in 4/4, loudest onset per slot wins
BPM_RANGE = (40, 240)       # accepted metronome tempos
PROTO_PATH = os.path.join(os.path.dirname(__file__), "prototypes.json")
PROTO_KINDS = ("kick", "snare", "hat")

PROTOTYPES = {k: v for k, v in json.load(open(PROTO_PATH)).items() if k in PROTO_KINDS}  # /calibrate overwrites


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


def _detect(wav_path):
    """Padded audio, raw onset times (in padded-audio seconds), and each onset's window peak."""
    y = np.concatenate([np.zeros(int(PAD_S * SR)), load(wav_path)])
    onsets = librosa.onset.onset_detect(y=y, sr=SR, units="time", backtrack=True, delta=0.08)
    peaks = np.array([np.max(np.abs(y[int(t * SR): int((t + HIT_WIN_S) * SR)]), initial=0.0) for t in onsets])
    return y, onsets, peaks


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
    """Known tempo: snap onsets to 16th slots counted from the end of the count-in, keep the loudest
    onset per slot (each slot owns +-1/32 note), drop anything before bar 1 or after the last bar.
    Returns padded audio, kept onset times (padded-audio seconds, for analysis) and their slot indices."""
    y, onsets, peaks = _detect(wav_path)
    slots = np.round((onsets - PAD_S - count_in_s) / (60.0 / bpm / 4)).astype(int)
    inside = (slots >= 0) & (slots < SLOTS_PER_BAR * bars if bars else True)
    onsets, peaks, slots = onsets[inside], peaks[inside], slots[inside]
    if len(peaks):
        keep = peaks >= GATE_RATIO * peaks.max()
        onsets, peaks, slots = onsets[keep], peaks[keep], slots[keep]
    best = {}
    for t, p, s in zip(onsets, peaks, slots):
        if s not in best or p > best[s][1]:
            best[s] = (t, p)
    order = sorted(best)
    return y, np.array([best[s][0] for s in order]), np.array(order, dtype=int)


def hit_features(y, t, sr=SR, win=HIT_WIN_S):
    i = int(t * sr)
    return samples.features(y[i: i + int(win * sr)], sr)


def classify_hit(y, t, sr=SR, win=HIT_WIN_S):
    """Nearest prototype in samples' scaled feature space. Prototypes come from prototypes.json
    (seed data) until POST /calibrate swaps in the performer's own kick / snare / hat."""
    f = hit_features(y, t, sr, win)
    if f is None:
        return HAT
    v = samples._vec(f)
    return PITCH[min(PROTOTYPES, key=lambda k: np.linalg.norm(samples._vec(PROTOTYPES[k]) - v))]


def clip_prototype(wav_path, kind):
    """Mean features over every hit in a calibration clip (one hit or a few)."""
    y, onsets = take_hits(wav_path)
    feats = [f for f in (hit_features(y, t) for t in onsets) if f]
    if not feats:
        raise ValueError(f"Didn't hear a {kind} — hold the button and hit it a couple of times.")
    return {k: float(np.mean([f[k] for f in feats])) for k in samples.FEAT_KEYS}


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
