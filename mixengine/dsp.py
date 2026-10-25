"""Deterministic, block-based measurements for captured audio."""

from datetime import datetime, timezone
from typing import Dict, Iterable, Optional, Tuple

import numpy as np
import soundfile as sf
from scipy.signal import butter, sosfilt, sosfilt_zi, welch

from mixengine.models import BandEnergy, Measurements


class AudioDecodeError(ValueError):
    """Raised when audio is not a supported mono or stereo stream."""


def _dbfs(amplitude: float) -> Optional[float]:
    if amplitude <= 0.0:
        return None
    return float(20.0 * np.log10(amplitude))


def _band_definitions(sample_rate: int) -> Tuple[Tuple[str, float, float], ...]:
    nyquist = sample_rate / 2.0
    return (
        ("low", 20.0, 120.0),
        ("low_mid", 120.0, 500.0),
        ("mid", 500.0, 2000.0),
        ("high_mid", 2000.0, 6000.0),
        ("high", 6000.0, nyquist),
    )


def _band_powers(audio: np.ndarray, sample_rate: int) -> Dict[str, float]:
    """Return mean channel power in each represented frequency band."""
    if audio.shape[0] < 2:
        return {}
    segment_length = min(audio.shape[0], 4096)
    frequencies, density = welch(
        audio,
        fs=sample_rate,
        nperseg=segment_length,
        detrend=False,
        scaling="density",
        axis=0,
    )
    bin_width = frequencies[1] - frequencies[0] if len(frequencies) > 1 else 0.0
    powers: Dict[str, float] = {}
    for name, lower, upper in _band_definitions(sample_rate):
        upper = min(upper, sample_rate / 2.0)
        if upper <= lower:
            continue
        selected = (frequencies >= lower) & (frequencies <= upper)
        if np.any(selected):
            powers[name] = float(np.mean(np.sum(density[selected], axis=0) * bin_width))
        else:
            powers[name] = 0.0
    return powers


def _band_energy_models(powers: Dict[str, float], sample_rate: int) -> Dict[str, BandEnergy]:
    result: Dict[str, BandEnergy] = {}
    for name, lower, upper in _band_definitions(sample_rate):
        upper = min(upper, sample_rate / 2.0)
        if upper <= lower:
            continue
        power = max(powers.get(name, 0.0), 0.0)
        result[name] = BandEnergy(
            low_hz=lower,
            high_hz=upper,
            energy_dbfs=_dbfs(float(np.sqrt(power))),
        )
    return result


# Musically named bands for full-mix tonal balance. Levels are power per octave, so a band's width doesn't
# change its level and a flat line means "pink" (equal energy per octave).
OCTAVE_BANDS = (
    ("sub", 20.0, 60.0),
    ("bass", 60.0, 120.0),
    ("upper_bass", 120.0, 250.0),
    ("low_mid", 250.0, 500.0),
    ("mid", 500.0, 1000.0),
    ("upper_mid", 1000.0, 2500.0),
    ("presence", 2500.0, 5000.0),
    ("brilliance", 5000.0, 10000.0),
    ("air", 10000.0, 20000.0),
)
LOW_END_HZ = 120.0
_HOP_S, _GATE_BLOCK_HOPS = 0.1, 4          # BS.1770: 400 ms blocks, 75% overlap
_PUNCH_WINDOW_S = 0.05


def _biquad(b, a):
    return np.array([[b[0] / a[0], b[1] / a[0], b[2] / a[0], 1.0, a[1] / a[0], a[2] / a[0]]])


def _k_weighting(sample_rate: int) -> np.ndarray:
    """BS.1770 K-weighting (high shelf + RLB high-pass) designed for any sample rate."""
    w0, q = 2 * np.pi * 1500.0 / sample_rate, 1 / np.sqrt(2)
    big_a, alpha, cos = 10 ** (4.0 / 40), np.sin(w0) / (2 * q), np.cos(w0)
    sq = 2 * np.sqrt(big_a) * alpha
    shelf = _biquad(
        [big_a * ((big_a + 1) + (big_a - 1) * cos + sq), -2 * big_a * ((big_a - 1) + (big_a + 1) * cos),
         big_a * ((big_a + 1) + (big_a - 1) * cos - sq)],
        [(big_a + 1) - (big_a - 1) * cos + sq, 2 * ((big_a - 1) - (big_a + 1) * cos), (big_a + 1) - (big_a - 1) * cos - sq])
    w0, q = 2 * np.pi * 38.0 / sample_rate, 0.5
    alpha, cos = np.sin(w0) / (2 * q), np.cos(w0)
    highpass = _biquad([(1 + cos) / 2, -(1 + cos), (1 + cos) / 2], [1 + alpha, -2 * cos, 1 - alpha])
    return np.vstack([shelf, highpass])


class _Windows:
    """Mean of a per-sample stream over fixed windows, carrying the remainder across blocks."""

    def __init__(self, size: int) -> None:
        self.size, self.carry, self.means = max(1, size), np.zeros(0), []

    def add(self, values: np.ndarray) -> None:
        values = np.concatenate([self.carry, values])
        whole = len(values) // self.size * self.size
        if whole:
            self.means.extend(values[:whole].reshape(-1, self.size).mean(axis=1).tolist())
        self.carry = values[whole:]


class _FullMix:
    """Streaming full-mix measurements: octave balance, loudness, width, low-end mono and punch."""

    def __init__(self, sample_rate: int, channels: int) -> None:
        self.sr, self.channels = sample_rate, channels
        self.k_sos = _k_weighting(sample_rate)
        self.k_zi = [sosfilt_zi(self.k_sos) * 0.0 for _ in range(channels)]
        self.low_sos = butter(4, LOW_END_HZ, btype="lowpass", fs=sample_rate, output="sos")
        self.low_zi = [sosfilt_zi(self.low_sos) * 0.0 for _ in range(channels)]
        self.hops = _Windows(int(round(_HOP_S * sample_rate)))
        self.punch = _Windows(int(round(_PUNCH_WINDOW_S * sample_rate)))
        self.octave_power: Dict[str, float] = {}
        self.frames = 0
        self.mid_sq = self.side_sq = self.low_mid_sq = self.low_side_sq = 0.0
        self.low_sums = [0.0] * 5

    def add(self, block: np.ndarray) -> None:
        count = block.shape[0]
        self.frames += count
        mid = block.mean(axis=1)
        for name, power in _octave_powers(mid, self.sr).items():
            self.octave_power[name] = self.octave_power.get(name, 0.0) + power * count

        weighted = np.zeros(count)
        lows = []
        for ch in range(self.channels):
            k, self.k_zi[ch] = sosfilt(self.k_sos, block[:, ch], zi=self.k_zi[ch])
            weighted += np.square(k)                      # BS.1770 channel weights are 1.0 for L and R
            low, self.low_zi[ch] = sosfilt(self.low_sos, block[:, ch], zi=self.low_zi[ch])
            lows.append(low)
        self.hops.add(weighted)
        low_mid = np.mean(lows, axis=0)
        self.punch.add(np.square(low_mid))

        if self.channels == 2:
            left, right = block[:, 0], block[:, 1]
            self.mid_sq += float(np.sum(np.square((left + right) / 2)))
            self.side_sq += float(np.sum(np.square((left - right) / 2)))
            low_l, low_r = lows
            self.low_mid_sq += float(np.sum(np.square((low_l + low_r) / 2)))
            self.low_side_sq += float(np.sum(np.square((low_l - low_r) / 2)))
            for i, value in enumerate((np.sum(low_l), np.sum(low_r), np.sum(low_l * low_l),
                                       np.sum(low_r * low_r), np.sum(low_l * low_r))):
                self.low_sums[i] += float(value)

    def loudness(self) -> Optional[float]:
        hops = np.array(self.hops.means)
        if len(hops) < _GATE_BLOCK_HOPS:
            return None
        blocks = np.convolve(hops, np.ones(_GATE_BLOCK_HOPS) / _GATE_BLOCK_HOPS, mode="valid")
        lufs = -0.691 + 10 * np.log10(np.maximum(blocks, 1e-20))
        gated = blocks[lufs > -70.0]
        if not len(gated):
            return None
        relative = -0.691 + 10 * np.log10(np.mean(gated)) - 10.0
        gated = blocks[(lufs > -70.0) & (lufs > relative)]
        return float(-0.691 + 10 * np.log10(np.mean(gated))) if len(gated) else None

    def results(self) -> dict:
        def ratio_db(num, den):
            return None if den <= 0 or num <= 0 else float(10 * np.log10(num / den))

        envelope = np.sqrt(np.array(self.punch.means)) if self.punch.means else np.zeros(0)
        audible = envelope[envelope > 10 ** (-60 / 20)]
        punch = None
        if len(audible) >= 20:
            punch = float(20 * np.log10(np.percentile(audible, 95) / max(np.median(audible), 1e-12)))
        octave = {name: 10 * np.log10(total / self.frames)
                  for name, total in self.octave_power.items() if total > 0}
        return {
            "octave_bands_db": {k: float(v) for k, v in octave.items()},
            "loudness_lufs": self.loudness(),
            "side_to_mid_db": ratio_db(self.side_sq, self.mid_sq) if self.channels == 2 else None,
            "low_side_to_mid_db": ratio_db(self.low_side_sq, self.low_mid_sq) if self.channels == 2 else None,
            "low_end_correlation": _correlation_from_sums(self.frames, *self.low_sums) if self.channels == 2 else None,
            "low_end_punch_db": punch,
        }


def _octave_powers(mono: np.ndarray, sample_rate: int) -> Dict[str, float]:
    if len(mono) < 256:
        return {}
    frequencies, density = welch(mono, fs=sample_rate, nperseg=min(len(mono), 8192), detrend=False)
    bin_width = frequencies[1] - frequencies[0]
    powers = {}
    for name, lower, upper in OCTAVE_BANDS:
        upper = min(upper, sample_rate / 2.0)
        selected = (frequencies >= lower) & (frequencies < upper)
        if upper > lower and np.any(selected):
            powers[name] = float(np.sum(density[selected]) * bin_width / np.log2(upper / lower))
    return powers


def _make_measurements(
    frame_count: int,
    sample_rate: int,
    channels: int,
    peak: float,
    sum_squares: float,
    band_powers: Dict[str, float],
    stereo_correlation: Optional[float],
    full_mix: Optional[dict] = None,
) -> Measurements:
    rms = float(np.sqrt(sum_squares / (frame_count * channels))) if frame_count else 0.0
    peak_dbfs = _dbfs(peak)
    rms_dbfs = _dbfs(rms)
    crest = None if peak_dbfs is None or rms_dbfs is None else peak_dbfs - rms_dbfs
    return Measurements(
        measured_at=datetime.now(timezone.utc),
        sample_rate=sample_rate,
        channels=channels,
        duration_seconds=frame_count / float(sample_rate),
        is_silent=peak == 0.0,
        peak_dbfs=peak_dbfs,
        rms_dbfs=rms_dbfs,
        crest_factor_db=crest,
        frequency_bands=_band_energy_models(band_powers, sample_rate),
        stereo_correlation=stereo_correlation,
        **(full_mix or {}),
    )


def _correlation_from_sums(
    frame_count: int,
    sum_left: float,
    sum_right: float,
    squares_left: float,
    squares_right: float,
    product: float,
) -> Optional[float]:
    if not frame_count:
        return None
    covariance = product - (sum_left * sum_right / frame_count)
    variance_left = squares_left - (sum_left * sum_left / frame_count)
    variance_right = squares_right - (sum_right * sum_right / frame_count)
    if variance_left <= 0.0 or variance_right <= 0.0:
        return None
    value = covariance / np.sqrt(variance_left * variance_right)
    return float(np.clip(value, -1.0, 1.0))


def analyze_blocks(blocks: Iterable[np.ndarray], sample_rate: int, channels: int) -> Measurements:
    """Measure a stream of (frames, channels) blocks without retaining the full capture."""
    if channels not in (1, 2) or sample_rate <= 0:
        raise AudioDecodeError("Only mono or stereo audio with a valid sample rate is supported.")

    frame_count = 0
    peak = 0.0
    sum_squares = 0.0
    weighted_bands: Dict[str, float] = {}
    stereo_sums = [0.0, 0.0, 0.0, 0.0, 0.0]
    full_mix = _FullMix(sample_rate, channels)

    for block in blocks:
        if block.ndim != 2 or block.shape[1] != channels:
            raise AudioDecodeError("Audio blocks do not match the WAV channel count.")
        if not np.isfinite(block).all():
            raise AudioDecodeError("WAV audio contains non-finite sample values.")
        count = block.shape[0]
        if count == 0:
            continue
        as_float64 = np.asarray(block, dtype=np.float64)
        frame_count += count
        peak = max(peak, float(np.max(np.abs(as_float64))))
        sum_squares += float(np.sum(np.square(as_float64)))
        full_mix.add(as_float64)
        for band, power in _band_powers(as_float64, sample_rate).items():
            weighted_bands[band] = weighted_bands.get(band, 0.0) + power * count
        if channels == 2:
            left = as_float64[:, 0]
            right = as_float64[:, 1]
            stereo_sums[0] += float(np.sum(left))
            stereo_sums[1] += float(np.sum(right))
            stereo_sums[2] += float(np.sum(np.square(left)))
            stereo_sums[3] += float(np.sum(np.square(right)))
            stereo_sums[4] += float(np.sum(left * right))

    if frame_count == 0:
        raise AudioDecodeError("WAV audio must contain samples.")
    band_powers = {band: total / frame_count for band, total in weighted_bands.items()}
    correlation = None
    if channels == 2:
        correlation = _correlation_from_sums(frame_count, *stereo_sums)
    return _make_measurements(
        frame_count, sample_rate, channels, peak, sum_squares, band_powers, correlation,
        full_mix.results() if peak > 0.0 else None,
    )


def analyze_wav_file(path: str, block_frames: int = 65536) -> Measurements:
    """Read and analyze a WAV from disk in bounded-size blocks."""
    try:
        info = sf.info(path)
    except (RuntimeError, ValueError, TypeError) as error:
        raise AudioDecodeError("Request body is not a readable WAV file.") from error
    if info.channels not in (1, 2):
        raise AudioDecodeError("Only mono and stereo WAV audio is supported.")
    if info.frames <= 0 or info.samplerate <= 0:
        raise AudioDecodeError("WAV audio must contain samples and a valid sample rate.")
    try:
        blocks = sf.blocks(
            path,
            blocksize=block_frames,
            dtype="float32",
            always_2d=True,
        )
        return analyze_blocks(blocks, int(info.samplerate), int(info.channels))
    except (RuntimeError, ValueError, TypeError) as error:
        if isinstance(error, AudioDecodeError):
            raise
        raise AudioDecodeError("Could not decode WAV audio blocks.") from error


def analyze_audio(audio: np.ndarray, sample_rate: int) -> Measurements:
    """Analyze an in-memory short signal; useful for tests and small windows."""
    if audio.ndim != 2 or audio.shape[1] not in (1, 2):
        raise AudioDecodeError("Only mono and stereo WAV audio is supported.")
    return analyze_blocks((audio,), sample_rate, audio.shape[1])
