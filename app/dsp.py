"""Deterministic measurements for short audio windows."""

from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

import numpy as np
from scipy.signal import welch

from app.models import BandEnergy, Measurements


class AudioDecodeError(ValueError):
    """Raised when an audio payload is not a supported WAV window."""


def _dbfs(amplitude: float) -> Optional[float]:
    if amplitude <= 0.0:
        return None
    return float(20.0 * np.log10(amplitude))


def _frequency_bands(audio: np.ndarray, sample_rate: int) -> Dict[str, BandEnergy]:
    nyquist = sample_rate / 2.0
    definitions: Tuple[Tuple[str, float, float], ...] = (
        ("low", 20.0, 120.0),
        ("low_mid", 120.0, 500.0),
        ("mid", 500.0, 2000.0),
        ("high_mid", 2000.0, 6000.0),
        ("high", 6000.0, nyquist),
    )
    segment_length = min(audio.shape[0], 4096)
    frequencies, density = welch(
        audio,
        fs=sample_rate,
        nperseg=segment_length,
        detrend=False,
        scaling="density",
        axis=0,
    )
    result: Dict[str, BandEnergy] = {}
    bin_width = frequencies[1] - frequencies[0] if len(frequencies) > 1 else 0.0
    for name, lower, upper in definitions:
        actual_upper = min(upper, nyquist)
        if actual_upper <= lower:
            continue
        selected = (frequencies >= lower) & (frequencies <= actual_upper)
        if not np.any(selected):
            power = 0.0
        else:
            channel_power = np.sum(density[selected], axis=0) * bin_width
            power = float(np.mean(channel_power))
        result[name] = BandEnergy(
            low_hz=lower,
            high_hz=actual_upper,
            energy_dbfs=_dbfs(float(np.sqrt(max(power, 0.0)))),
        )
    return result


def analyze_audio(audio: np.ndarray, sample_rate: int) -> Measurements:
    """Measure normalized float audio shaped (frames, channels)."""
    if audio.ndim != 2 or audio.shape[1] not in (1, 2):
        raise AudioDecodeError("Only mono and stereo WAV audio is supported.")
    if audio.shape[0] == 0 or sample_rate <= 0:
        raise AudioDecodeError("WAV audio must contain samples and a valid sample rate.")
    if not np.isfinite(audio).all():
        raise AudioDecodeError("WAV audio contains non-finite sample values.")

    peak = float(np.max(np.abs(audio)))
    rms = float(np.sqrt(np.mean(np.square(audio, dtype=np.float64))))
    peak_dbfs = _dbfs(peak)
    rms_dbfs = _dbfs(rms)
    crest = None if peak_dbfs is None or rms_dbfs is None else peak_dbfs - rms_dbfs

    correlation = None
    if audio.shape[1] == 2:
        left = audio[:, 0] - np.mean(audio[:, 0])
        right = audio[:, 1] - np.mean(audio[:, 1])
        if np.std(left) > 0.0 and np.std(right) > 0.0:
            correlation = float(np.corrcoef(left, right)[0, 1])

    return Measurements(
        measured_at=datetime.now(timezone.utc),
        sample_rate=sample_rate,
        channels=audio.shape[1],
        duration_seconds=audio.shape[0] / float(sample_rate),
        is_silent=peak == 0.0,
        peak_dbfs=peak_dbfs,
        rms_dbfs=rms_dbfs,
        crest_factor_db=crest,
        frequency_bands=_frequency_bands(audio, sample_rate),
        stereo_correlation=correlation,
    )
