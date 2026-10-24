"""Evidence-based mix suggestions; no model or external API required.

Plugin captures arrive as measurements, not as a mix decision. These rules turn
those measurements into notes the agent can say back. Absolute spectral energy
never by itself asserts an EQ fault: a bright source is allowed to be bright.
Band notes only fire when a track's share of its own energy is an outlier
against other captures in the same mix window.
"""

from statistics import median
from typing import Dict, List, Optional, Tuple

from mixengine.models import Measurements, Recommendation
from mixengine.registry import TrackState


# Crest below this is a dynamics check, not a claim that the source is ruined.
# A steady tone naturally sits near 3 dB, so this only applies to transient roles.
CRUSHED_CREST_DB = 6.0
# Crest above this is unusually dynamic for a sustained program part.
VERY_DYNAMIC_CREST_DB = 18.0
# A band share has to lead the mix window by this much before it is a balance note.
BAND_SHARE_MARGIN = 0.12
BAND_ORDER = ("low", "low_mid", "mid", "high_mid", "high")
TRANSIENT_ROLES = {"drum", "drums", "percussion", "kick", "snare", "hat", "hats", "perc"}
SUSTAINED_ROLES = {"vocal", "vocals", "voice", "bass", "master", "mix", "bus"}


def _new_recommendation(
    priority: str,
    rank: int,
    code: str,
    track: TrackState,
    message: str,
    reasoning: str,
    fix: str,
    evidence: Dict[str, object],
) -> Recommendation:
    return Recommendation(
        priority=priority,
        rank=rank,
        code=code,
        track_id=track.id,
        message=message,
        reasoning=reasoning,
        fix=fix,
        evidence=evidence,
    )


def _role(track: TrackState) -> str:
    return (track.role or "").strip().lower()


def _finite(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    return float(value)


def _band_share(measurements: Measurements, name: str) -> Optional[float]:
    """Share of this capture's own band power, not an absolute EQ diagnosis."""
    powers = []
    selected = None
    for band_name in BAND_ORDER:
        band = measurements.frequency_bands.get(band_name)
        if band is None or band.energy_dbfs is None:
            continue
        power = 10.0 ** (band.energy_dbfs / 10.0)
        powers.append(power)
        if band_name == name:
            selected = power
    total = sum(powers)
    if selected is None or total <= 0.0:
        return None
    return selected / total


def _append_level_and_width(by_track: Dict[str, List[Recommendation]], track: TrackState) -> None:
    measurements = track.latest_measurements
    if measurements is None or measurements.is_silent:
        return

    peak = measurements.peak_dbfs
    if peak is not None and peak > 0.0:
        by_track[track.id].append(_new_recommendation(
            "high", 0, "over_full_scale", track,
            "The measured sample peak is above 0 dBFS.",
            "The recorded window exceeds full scale, so gain staging needs a look before this track hits the mix bus.",
            "Pull the clip or the plugin input down until the peak sits under 0 dBFS, then re-record the listen.",
            {"peak_dbfs": peak, "threshold_dbfs": 0.0},
        ))
    elif peak is not None and peak >= -0.1:
        by_track[track.id].append(_new_recommendation(
            "medium", 0, "near_full_scale", track,
            "The measured peak is within 0.1 dB of full scale.",
            "This is a headroom check, not proof of audible clipping.",
            "Leave a little more peak headroom on this track before it reaches the mix bus.",
            {"peak_dbfs": peak, "threshold_dbfs": -0.1},
        ))

    role = _role(track)
    crest = _finite(measurements.crest_factor_db)
    if crest is not None and crest < CRUSHED_CREST_DB and role in TRANSIENT_ROLES:
        by_track[track.id].append(_new_recommendation(
            "medium", 1, "low_crest_factor", track,
            "This {} capture has only {:.1f} dB between peak and average level.".format(role, crest),
            "Crest factor under 6 dB on a transient role means the hits are already densely packed. "
            "A steady tone can sit there naturally; a drum recording usually should not.",
            "Ease the compressor or limiter on this track and listen to whether the hits come back.",
            {"crest_factor_db": crest, "threshold_db": CRUSHED_CREST_DB, "role": track.role,
             "peak_dbfs": peak, "rms_dbfs": measurements.rms_dbfs},
        ))
    elif crest is not None and crest > VERY_DYNAMIC_CREST_DB and role in SUSTAINED_ROLES:
        by_track[track.id].append(_new_recommendation(
            "low", 4, "high_crest_factor", track,
            "This {} capture is very dynamic, with {:.1f} dB of crest.".format(role or "track", crest),
            "Crest factor above 18 dB means peaks are far above the average level of the recorded window.",
            "A little glue can help it sit, but only if the performance needs it.",
            {"crest_factor_db": crest, "threshold_db": VERY_DYNAMIC_CREST_DB, "role": track.role,
             "peak_dbfs": peak, "rms_dbfs": measurements.rms_dbfs},
        ))

    correlation = measurements.stereo_correlation
    if correlation is not None and correlation < -0.2:
        by_track[track.id].append(_new_recommendation(
            "medium", 2, "negative_stereo_correlation", track,
            "Left/right correlation is {:.2f}.".format(correlation),
            "Negative correlation can cancel when the track is summed to mono.",
            "Check this stereo track in mono and review its polarity or width processing.",
            {"stereo_correlation": correlation, "threshold": -0.2},
        ))
    elif correlation is not None and correlation > 0.98:
        by_track[track.id].append(_new_recommendation(
            "low", 5, "effectively_mono", track,
            "Left/right correlation is {:.2f}, so this stereo capture is effectively mono.".format(correlation),
            "The two channels carry almost the same signal, so width processing on this track will not create space.",
            "Pan or double something else if this part needs width; don't widen a correlated pair.",
            {"stereo_correlation": correlation, "threshold": 0.98},
        ))


def _append_peer_notes(by_track: Dict[str, List[Recommendation]], active: List[TrackState]) -> None:
    """Compare captures that were recorded into the same mix window.

    Same-role level outliers stay role-scoped. Spectral share is compared across
    the whole window, because a low-mid pile-up only matters relative to the other
    tracks that will occupy the bus with it.
    """
    audible = [
        track for track in active
        if track.latest_measurements is not None and not track.latest_measurements.is_silent
    ]
    if len(audible) < 2:
        return

    role_groups: Dict[str, List[TrackState]] = {}
    for track in audible:
        if track.role:
            role_groups.setdefault(track.role.strip().lower(), []).append(track)
    for peers in role_groups.values():
        levels: List[Tuple[TrackState, float]] = [
            (track, track.latest_measurements.rms_dbfs)
            for track in peers
            if track.latest_measurements is not None
            and track.latest_measurements.rms_dbfs is not None
        ]
        if len(levels) < 2:
            continue
        median_level = float(median(level for _, level in levels))
        for track, level in levels:
            difference = level - median_level
            if abs(difference) >= 6.0:
                direction = "above" if difference > 0 else "below"
                by_track[track.id].append(_new_recommendation(
                    "low", 3, "same_role_level_outlier", track,
                    "This {} sits {:.1f} dB {} the other {} tracks.".format(
                        track.role, abs(difference), direction, track.role),
                    "Its measured RMS differs from the same-role group median; this is a comparison cue, not a target level.",
                    "Listen to the balance and nudge the fader; don't chase the median as a target.",
                    {
                        "rms_dbfs": level,
                        "peer_median_rms_dbfs": median_level,
                        "difference_db": difference,
                        "direction": direction,
                        "role": track.role,
                        "peer_count": len(levels),
                    },
                ))

    # Only the bands that describe balance problems, not "this source is bright."
    balance_bands = {
        "low": (
            "low_end_share_outlier",
            "This capture is carrying more of the low end than the other recorded tracks.",
            "Its share of energy under 120 Hz leads the other captures in this window. "
            "That is a balance comparison, not proof the low end is wrong.",
            "High-pass anything in this track that is not meant to own 20-120 Hz.",
        ),
        "low_mid": (
            "low_mid_share_outlier",
            "This capture puts more of its energy in the low-mids than the other recorded tracks.",
            "Its share of the 120-500 Hz band leads the other captures. "
            "A bright source is allowed to be bright; this note only fires when one track's own balance is the outlier.",
            "Listen around 200-400 Hz before reaching for a broad cut.",
        ),
        "high": (
            "high_share_outlier",
            "This capture is the brightest thing in the recorded window.",
            "Its share of energy above 6 kHz leads the other captures. High band level on its own is not an EQ fault.",
            "If it is masking other detail, tame its top rather than boosting the rest of the mix.",
        ),
    }
    for band_name, (code, message, reasoning, fix) in balance_bands.items():
        shares: List[Tuple[TrackState, float]] = []
        for track in audible:
            if track.latest_measurements is None:
                continue
            share = _band_share(track.latest_measurements, band_name)
            if share is not None:
                shares.append((track, share))
        if len(shares) < 2:
            continue
        median_share = float(median(share for _, share in shares))
        for track, share in shares:
            lead = share - median_share
            if lead < BAND_SHARE_MARGIN or share < 0.35:
                continue
            band = track.latest_measurements.frequency_bands.get(band_name)
            by_track[track.id].append(_new_recommendation(
                "medium" if band_name == "low_mid" else "low",
                3,
                code,
                track,
                message,
                reasoning,
                fix,
                {
                    "band": band_name,
                    "band_share": share,
                    "peer_median_share": median_share,
                    "share_lead": lead,
                    "energy_dbfs": None if band is None else band.energy_dbfs,
                    "peer_count": len(shares),
                },
            ))


def build_recommendations(tracks: List[TrackState]) -> Dict[str, List[Recommendation]]:
    by_track: Dict[str, List[Recommendation]] = {track.id: [] for track in tracks}
    active = [track for track in tracks if track.latest_measurements is not None]
    for track in active:
        _append_level_and_width(by_track, track)
    _append_peer_notes(by_track, active)
    for recommendations in by_track.values():
        recommendations.sort(key=lambda item: (item.rank, item.code))
    return by_track
