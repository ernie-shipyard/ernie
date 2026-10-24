"""Deterministic, block-based measurements for captured audio."""

from datetime import datetime, timezone
from typing import Dict, Iterable, Optional, Tuple

import numpy as np
import soundfile as sf
from scipy.signal import welch

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


def _make_measurements(
    frame_count: int,
    sample_rate: int,
    channels: int,
    peak: float,
    sum_squares: float,
    band_powers: Dict[str, float],
    stereo_correlation: Optional[float],
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
        frame_count, sample_rate, channels, peak, sum_squares, band_powers, correlation
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
