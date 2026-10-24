"""Evidence-based mix suggestions; no model or external API required."""

from statistics import median
from typing import Dict, List, Tuple

from app.models import Recommendation
from app.registry import TrackState


def _new_recommendation(
    priority: str,
    rank: int,
    code: str,
    track: TrackState,
    message: str,
    reasoning: str,
    evidence: Dict[str, object],
) -> Recommendation:
    return Recommendation(
        priority=priority,
        rank=rank,
        code=code,
        track_id=track.id,
        message=message,
        reasoning=reasoning,
        evidence=evidence,
    )


def build_recommendations(tracks: List[TrackState]) -> Dict[str, List[Recommendation]]:
    by_track: Dict[str, List[Recommendation]] = {track.id: [] for track in tracks}
    active = [track for track in tracks if track.latest_measurements is not None]

    for track in active:
        measurements = track.latest_measurements
        if measurements is None or measurements.is_silent:
            continue
        peak = measurements.peak_dbfs
        if peak is not None and peak > 0.0:
            by_track[track.id].append(_new_recommendation(
                "high", 0, "over_full_scale", track,
                "Check this track's gain staging before it reaches the mix bus.",
                "The measured sample peak is above 0 dBFS, so the window exceeds full scale.",
                {"peak_dbfs": peak, "threshold_dbfs": 0.0},
            ))
        elif peak is not None and peak >= -0.1:
            by_track[track.id].append(_new_recommendation(
                "medium", 0, "near_full_scale", track,
                "Consider leaving more peak headroom on this track.",
                "The measured peak is within 0.1 dB of full scale; this is a headroom check, not proof of audible clipping.",
                {"peak_dbfs": peak, "threshold_dbfs": -0.1},
            ))

        correlation = measurements.stereo_correlation
        if correlation is not None and correlation < -0.2:
            by_track[track.id].append(_new_recommendation(
                "medium", 1, "negative_stereo_correlation", track,
                "Check this stereo track in mono and review its polarity or width processing.",
                "The measured left/right correlation is negative, which can indicate cancellation when summed to mono.",
                {"stereo_correlation": correlation, "threshold": -0.2},
            ))

    role_groups: Dict[str, List[TrackState]] = {}
    for track in active:
        if track.role and track.latest_measurements and not track.latest_measurements.is_silent:
            role_groups.setdefault(track.role.strip().lower(), []).append(track)
    for peers in role_groups.values():
        if len(peers) < 2:
            continue
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
                    "low", 2, "same_role_level_outlier", track,
                    "Listen to this track's balance against the other tracks with the same role.",
                    "Its measured RMS differs from the same-role group median; this is a comparison cue, not a target level.",
                    {
                        "rms_dbfs": level,
                        "peer_median_rms_dbfs": median_level,
                        "difference_db": difference,
                        "direction": direction,
                        "role": track.role,
                        "peer_count": len(levels),
                    },
                ))

    for recommendations in by_track.values():
        recommendations.sort(key=lambda item: (item.rank, item.code))
    return by_track
