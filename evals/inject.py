"""Known mix problems, injected into finished reference audio at graded severity.

Each problem is a DSP degradation a producer could actually commit (an EQ boost, stereo sub, an over-driven
clipper), the severity it's applied at, and the note codes that count as Ernie catching it. Injected audio never
peaks higher than the window it came from: a limiter ceiling stays a ceiling, so a boost can't trip clipping notes.
"""

import numpy as np
from scipy.signal import butter, hilbert, sosfilt

from mixengine.dsp import LOW_END_HZ


def _peaking(audio, sr, freq, gain_db, q=1.4):
    """RBJ peaking EQ; q 1.4 is about an octave wide, like the bands Ernie reports on."""
    a = 10 ** (gain_db / 40)
    w0 = 2 * np.pi * freq / sr
    alpha = np.sin(w0) / (2 * q)
    b = np.array([1 + alpha * a, -2 * np.cos(w0), 1 - alpha * a])
    den = np.array([1 + alpha / a, -2 * np.cos(w0), 1 - alpha / a])
    sos = np.concatenate([b / den[0], den / den[0]])[None, :]
    return sosfilt(sos, audio, axis=0)


def _mid_side(audio):
    return (audio[:, 0] + audio[:, 1]) / 2, (audio[:, 0] - audio[:, 1]) / 2


def _stereo(mid, side):
    return np.stack([mid + side, mid - side], axis=1)


def _low(signal, sr):
    return sosfilt(butter(4, LOW_END_HZ, "low", fs=sr, output="sos"), signal)


def eq(freq):
    return lambda audio, sr, db: _peaking(audio, sr, freq, db)


def stereo_sub(audio, sr, db):
    """Side content under 120 Hz at `db` relative to the mid's low end, 90 degrees out so it's fully decorrelated."""
    mid, side = _mid_side(audio)
    low = _low(mid, sr)
    return _stereo(mid, side + np.imag(hilbert(low)) * 10 ** (db / 20))


def too_wide(audio, sr, db):
    """Side above 120 Hz pushed up by `db`, like a stereo widener on the master."""
    mid, side = _mid_side(audio)
    high_side = side - _low(side, sr)
    return _stereo(mid, side + high_side * (10 ** (db / 20) - 1))


def clipped(audio, sr, db):
    """Driven `db` into a hard clipper at the original peak: louder and flatter, like an over-cooked master."""
    peak = np.abs(audio).max()
    return np.clip(audio * 10 ** (db / 20), -peak, peak)


EQ_DB = (3.0, 6.0, 9.0, 12.0)

# name: (inject(audio, sr, severity), severities, unit, codes that count as caught)
PROBLEMS = {
    "boomy (180 Hz)": (eq(180), EQ_DB, "dB boost", {"upper_bass_heavy"}),
    "muddy (350 Hz)": (eq(350), EQ_DB, "dB boost", {"low_mid_heavy"}),
    "boxy (700 Hz)": (eq(700), EQ_DB, "dB boost", {"mid_heavy"}),
    "nasal (1.6 kHz)": (eq(1600), EQ_DB, "dB boost", {"upper_mid_heavy"}),
    "harsh (3.5 kHz)": (eq(3500), EQ_DB, "dB boost", {"presence_heavy"}),
    "sibilant (7 kHz)": (eq(7000), EQ_DB, "dB boost", {"brilliance_heavy"}),
    "brittle (14 kHz)": (eq(14000), EQ_DB, "dB boost", {"air_heavy"}),
    "weak low end (85 Hz)": (lambda a, sr, db: _peaking(a, sr, 85, -db), EQ_DB, "dB cut", {"bass_light"}),
    "dark (7 kHz)": (lambda a, sr, db: _peaking(a, sr, 7000, -db), EQ_DB, "dB cut", {"brilliance_light"}),
    "stereo sub": (stereo_sub, (-12.0, -9.0, -6.0, -3.0), "dB side vs. mid <120 Hz", {"wide_low_end"}),
    "too wide": (too_wide, EQ_DB, "dB side boost", {"wider_than_references"}),
    "over-limited": (clipped, EQ_DB, "dB into clipper", {"louder_than_references", "low_end_wall"}),
}


def inject(name, audio, sr, severity):
    out = PROBLEMS[name][0](audio.astype(np.float64), sr, severity)
    peak, new_peak = np.abs(audio).max(), np.abs(out).max()
    if new_peak > peak > 0:
        out *= peak / new_peak
    return out.astype(np.float32)
