"""Hum / sing -> MIDI melody.  Beatbox -> MIDI drums.  Pure librosa + mido, no API."""

import numpy as np
import librosa
import mido

SR = 22050

# beatbox hit classification (classify_hit / beatbox_to_midi_file)
HIT_WIN_S = 0.09            # analysis window after each onset
GATE_RATIO = 0.25           # drop onsets whose window peak < this * loudest hit in the take
LOW_HZ, HIGH_HZ = 180, 2500  # band edges: low < LOW_HZ <= mid < HIGH_HZ <= high
KICK_LOW = 0.35             # kick if low ratio above this ...
KICK_CENTROID_HZ = 600      # ... or centroid below this (laptop mics lose the sub)
HAT_HIGH = 0.55             # hat if high ratio above this ...
HAT_CENTROID_HZ = 2500      # ... or centroid above this
TIE_HIGH = (0.4, 0.55)      # ambiguous zone: high ratio in this range and centroid between the two above
DECAY_REL = 0.3             # decay = time RMS stays above this fraction of its peak
DECAY_HAT_MAX_S = 0.15      # in the ambiguous zone, shorter decay than this = hat, else snare


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


def classify_hit(y, t, sr=SR, win=HIT_WIN_S):
    """Band-energy + centroid vote. Beatbox kicks are low thumps (on a laptop mic that means
    a low centroid, not sub energy), hats are bright hiss, snares are everything in between.
    Decay length only breaks ties in the ambiguous zone: real snares ring longer than hats."""
    i = int(t * sr); seg = y[i: i + int(win * sr)]
    if len(seg) < 256:
        return HAT
    S = np.abs(np.fft.rfft(seg * np.hanning(len(seg)))) ** 2
    freqs = np.fft.rfftfreq(len(seg), 1 / sr)
    tot = S.sum() + 1e-9
    low = S[freqs < LOW_HZ].sum() / tot
    high = S[freqs >= HIGH_HZ].sum() / tot
    centroid = (S * freqs).sum() / tot
    if low > KICK_LOW or centroid < KICK_CENTROID_HZ:
        return KICK
    if high > HAT_HIGH or centroid > HAT_CENTROID_HZ:
        return HAT
    if TIE_HIGH[0] <= high <= TIE_HIGH[1]:
        r = librosa.feature.rms(y=y[i: i + int(0.5 * sr)], frame_length=512, hop_length=128, center=False)[0]
        if len(r):
            p = int(np.argmax(r)); below = np.nonzero(r[p:] < DECAY_REL * r[p])[0]
            decay = (p + (below[0] if len(below) else len(r) - p)) * 128 / sr
            if decay < DECAY_HAT_MAX_S:
                return HAT
    return SNARE


def beatbox_to_midi_file(wav_path, out_path, tempo_bpm=None):
    y = np.concatenate([np.zeros(int(0.05 * SR)), load(wav_path)])  # pad so a hit at t=0 is caught
    onsets = librosa.onset.onset_detect(y=y, sr=SR, units="time", backtrack=True, delta=0.08)
    peaks = np.array([np.max(np.abs(y[int(t * SR): int((t + HIT_WIN_S) * SR)]), initial=0.0) for t in onsets])
    if len(peaks):
        onsets = onsets[peaks >= GATE_RATIO * peaks.max()]   # drop breath / room-tail ghost onsets
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
        types.append({KICK: "kick", SNARE: "snare", HAT: "hat"}[pitch])
        fi = min(int(t * SR / 512), len(rms) - 1)
        vel = int(np.clip(60 + 100 * rms[fi] / (rms.max() + 1e-9), 60, 127))
        notes.append((pitch, t, 0.1, vel))
    q = quantize(notes, tempo_bpm, grid=0.25)
    notes_to_midi(q, tempo_bpm, out_path, channel=9)
    counts = {"kick": sum(p == KICK for p, *_ in q), "snare": sum(p == SNARE for p, *_ in q), "hat": sum(p == HAT for p, *_ in q)}
    info = {"tempo": round(tempo_bpm), "hits": len(q), **counts}
    info["_onsets"], info["_types"], info["_y"] = [float(t) for t in onsets], types, y   # stripped before JSON
    return info
