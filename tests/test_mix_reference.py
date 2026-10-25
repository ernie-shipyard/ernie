"""Full-mix notes against the committed EDM reference profile, on synthetic mixes shaped like it."""

import numpy as np
import pytest

from mixengine.dsp import OCTAVE_BANDS, analyze_audio
from mixengine.feedback import REFERENCE, build_recommendations, tonal_balance
from mixengine.registry import TrackState, utc_now

SR = 44100
pytestmark = pytest.mark.skipif(REFERENCE is None, reason="no reference profile")


def shaped_mix(seconds=6.0, boost=None, low_end_side=0.0, seed=0):
    """Stereo noise whose per-octave balance follows the reference curve, with a pumping low end and a mono sub.

    boost: (band name, dB) to push one band away from the curve.
    """
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    freqs = np.fft.rfftfreq(n, 1 / SR)
    centers = np.array([np.sqrt(lo * hi) for _, lo, hi in OCTAVE_BANDS])
    levels = np.array([REFERENCE["bands"][name]["target"] for name, _, _ in OCTAVE_BANDS])
    if boost:
        levels = levels + np.array([boost[1] if name == boost[0] else 0.0 for name, _, _ in OCTAVE_BANDS])
    curve = np.interp(np.log2(np.maximum(freqs, 1.0)), np.log2(centers), levels)
    gain = 10 ** (curve / 20) / np.sqrt(np.maximum(freqs, 20.0))   # pink base: flat power per octave
    gain[freqs < 20] = 0.0

    def noise():
        return np.fft.irfft(np.fft.rfft(rng.standard_normal(n)) * gain, n)

    low_cut = (freqs < 120).astype(float)
    mid = noise()
    spectrum = np.fft.rfft(mid)
    low = np.fft.irfft(spectrum * low_cut, n)
    pump = 0.35 + 0.65 * (np.sin(2 * np.pi * 2.0 * np.arange(n) / SR) > 0)  # kick/bass taking turns at 120 bpm
    pump /= np.sqrt(np.mean(pump ** 2))                                      # same low-end power, just moving
    mid = mid - low + low * pump
    side = np.fft.irfft(np.fft.rfft(noise()) * (1 - low_cut), n) * 0.4      # width above 120 Hz only
    side += np.fft.irfft(np.fft.rfft(noise()) * low_cut, n) * low_end_side
    audio = np.stack([mid + side, mid - side], axis=1)
    return (audio / np.abs(audio).max() * 0.9).astype(np.float32)


def notes(audio):
    measurements = analyze_audio(audio, SR)
    track = TrackState(id="master", name="Master", role="master", registered_at=utc_now(),
                       latest_measurements=measurements)
    return measurements, build_recommendations([track])["master"]


def serious(recs):
    return [n.code for n in recs if n.priority in ("high", "medium")]


def test_mix_shaped_like_the_references_is_clean():
    measurements, recs = notes(shaped_mix())
    assert serious(recs) == []
    assert all(b["verdict"] == "typical" for b in tonal_balance(measurements).values())


@pytest.mark.parametrize("band, db, code", [
    ("low_mid", 14.0, "low_mid_heavy"),   # the references themselves span about -6..+8 dB here
    ("presence", 8.0, "presence_heavy"),
    ("brilliance", 8.0, "brilliance_heavy"),
    ("bass", -14.0, "bass_light"),
])
def test_a_band_far_outside_the_references_is_the_first_note(band, db, code):
    _, recs = notes(shaped_mix(boost=(band, db)))
    assert recs[0].code == code
    assert recs[0].priority in ("high", "medium")
    assert recs[0].fix


def test_a_moderate_band_offset_is_a_soft_leaning_note():
    measurements, recs = notes(shaped_mix(boost=("presence", 4.0)))
    assert tonal_balance(measurements)["presence"]["verdict"] == "leaning"
    lean = [n for n in recs if n.code == "presence_heavy_lean"]
    assert lean and lean[0].priority == "low"
    assert serious(recs) == []


def test_stereo_sub_is_flagged():
    _, recs = notes(shaped_mix(low_end_side=0.8))
    assert "wide_low_end" in serious(recs)


def test_master_at_the_limiter_ceiling_is_not_a_clipping_note():
    audio = shaped_mix()
    audio = audio / np.abs(audio).max() * 0.995    # -0.04 dBFS, like a limiter ceiling
    _, recs = notes(audio.astype(np.float32))
    assert "near_full_scale" not in [n.code for n in recs]


def test_loudness_matches_bs1770_for_a_reference_tone():
    tone = 0.1 * np.sin(2 * np.pi * 1000 * np.arange(SR * 3) / SR)
    measurements = analyze_audio(np.stack([tone, tone], axis=1).astype(np.float32), SR)
    assert measurements.loudness_lufs == pytest.approx(-20.0, abs=0.1)
