"""Evidence-based mix suggestions; no model or external API required.

Plugin captures arrive as measurements, not as a mix decision. These rules turn
those measurements into notes the agent can say back. Absolute spectral energy
never by itself asserts an EQ fault: a bright source is allowed to be bright.
Band notes only fire when a track's share of its own energy is an outlier
against other captures in the same mix window.

A full-mix capture (master/mix/bus role) is also compared with a reference
profile built from finished tracks (scripts/calibrate_reference.py): a band is
only called too loud or too quiet when it sits outside what the references
themselves do, and loudness, width, low-end mono and punch get the same test.
"""

import json
import os
from statistics import median

import numpy as np
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


# Bands that set a capture's overall level when fitting it to a reference curve. Sub and air swing the most between
# sections (no sub in a breakdown), so they are judged against the fit but don't move it.
PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}
FULL_MIX_ROLES = {"master", "mix", "bus"}
REFERENCE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reference_{}.json")
# Two tiers, tuned on the reference folder itself (held-out reference drops vs. drops with a known EQ problem):
#  outside: past the references' 2.5/97.5% range by BAND_MARGIN_DB -> a real problem (12% of reference drops get one)
#  leaning: outside their middle half and >= LEAN_MIN_DB from the typical reference -> a softer comparison note
BAND_MARGIN_DB, BAND_HIGH_DB, LEAN_MIN_DB = 1.0, 4.0, 3.0
MAX_BAND_NOTES, MAX_LEAN_NOTES = 3, 2


def load_reference(genre: str = "edm") -> Optional[dict]:
    try:
        with open(REFERENCE_PATH.format(genre)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


REFERENCE = load_reference(os.getenv("ERNIE_REFERENCE", "edm"))

# (range, too loud: message/fix, too quiet: message/fix). Fixes name the likely source, not a master-bus EQ.
BAND_ADVICE = {
    "sub": ("20-60 Hz",
            ("The sub is heavier than your references.",
             "Pull the sub or 808 down {db} dB, or shelf below 50 Hz on it. Check on headphones; big subs eat headroom."),
            ("The sub is lighter than your references.",
             "Bring the sub layer up {db} dB or add a sine sub under the bass. Laptop speakers hide this, so check on headphones.")),
    "bass": ("60-120 Hz",
             ("The kick and bass fundamentals are boomy compared with your references.",
              "Sidechain the bass to the kick and cut about {db} dB around 80-100 Hz on whichever one is hogging it."),
             ("The low end lacks weight compared with your references.",
              "Lift the kick and bass fundamentals around 60-100 Hz by about {db} dB, or turn the bass bus up.")),
    "upper_bass": ("120-250 Hz",
                   ("There's a warm, boomy build-up around 120-250 Hz.",
                    "High-pass pads, chords and vocals around 120-150 Hz and dip about {db} dB near 200 Hz on the bass."),
                   ("The mix is thin around 120-250 Hz.",
                    "Let one part own the warmth here: a {db} dB lift around 180 Hz on the bass or chords.")),
    "low_mid": ("250-500 Hz",
                ("The mix is muddy: the low-mids are piling up.",
                 "Cut {db} dB around 300-400 Hz on chords, pads and mid basses (narrow-ish Q), not on the master."),
                ("The low-mids are scooped, so the mix sounds hollow.",
                 "Give chords or the mid bass about {db} dB more around 300-400 Hz.")),
    "mid": ("500 Hz-1 kHz",
            ("The mids sound boxy.",
             "Dip about {db} dB around 600-800 Hz on snares, synths or vocals, whichever is stacking up there."),
            ("The mids are scooped; leads and vocals will lack body.",
             "Bring the lead or vocal up, or lift about {db} dB around 700 Hz on it.")),
    "upper_mid": ("1-2.5 kHz",
                  ("The upper mids sound nasal and honky.",
                   "Find the synth or vocal poking out around 1-2 kHz and cut about {db} dB."),
                  ("The upper mids are recessed, so leads and vocals sit behind the drop.",
                   "Lift about {db} dB around 1.5-2 kHz on the lead or vocal so it cuts through.")),
    "presence": ("2.5-5 kHz",
                 ("The mix is harsh around 2.5-5 kHz, the range ears are most sensitive to.",
                  "Tame the brightest synth or vocal by about {db} dB around 3-4 kHz; a dynamic EQ keeps the bite."),
                 ("The mix is dull and distant around 2.5-5 kHz.",
                  "Lift about {db} dB around 3 kHz on the lead or vocal for presence.")),
    "brilliance": ("5-10 kHz",
                   ("The top end is splashy or sibilant around 5-10 kHz.",
                    "De-ess the vocals and bring hats and cymbals down, or dip about {db} dB around 6-8 kHz on them."),
                   ("The top end is dark around 5-10 kHz.",
                    "Brighten hats and cymbals, or add a gentle high shelf of about {db} dB on the music bus.")),
    "air": ("10-20 kHz",
            ("There's more air on top than your references; it can sound brittle.",
             "Ease the high shelf or the brightest hats and FX by about {db} dB above 10 kHz."),
            ("There's little air above 10 kHz compared with your references.",
             "A gentle shelf of about {db} dB above 10 kHz on the music bus or hats will open it up.")),
}

FIT_BANDS = ("bass", "upper_bass", "low_mid", "mid", "upper_mid", "presence", "brilliance")
BAND_SEQUENCE = ("sub", "bass", "upper_bass", "low_mid", "mid", "upper_mid", "presence", "brilliance", "air")


def balance_offsets(levels: Dict[str, float], target: Dict[str, float]) -> Dict[str, float]:
    """Per-band dB above (+) or below (-) the reference curve, after removing the capture's overall level.

    For each band the overall level is the median over FIT_BANDS *excluding that band and its neighbours*: a broad
    300 Hz boost also lifts 200 and 600 Hz, and letting those set the level would hide the boost being tested.
    """
    common = [n for n in target if n in levels and levels[n] is not None and np.isfinite(levels[n])]
    position = {n: i for i, n in enumerate(BAND_SEQUENCE)}
    out = {}
    for name in common:
        fit = [levels[n] - target[n] for n in common if n in FIT_BANDS and abs(position[n] - position[name]) > 1]
        if len(fit) >= 3:
            out[name] = float(levels[name] - target[name] - np.median(fit))
    return out


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
    elif peak is not None and peak >= -0.1 and _role(track) not in FULL_MIX_ROLES:   # a limited master sits there
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


def tonal_balance(measurements: Measurements, reference: Optional[dict] = None) -> Dict[str, dict]:
    """Each band's offset from the reference curve, where the references themselves sit, and a verdict:
    "outside" (beyond what any reference does), "leaning" (hotter/quieter than most) or "typical"."""
    reference = reference or REFERENCE
    if not reference or not measurements.octave_bands_db:
        return {}
    bands = reference["bands"]
    offsets = balance_offsets(measurements.octave_bands_db, {n: b["target"] for n, b in bands.items()})
    out = {}
    for name, offset in offsets.items():
        if name not in BAND_ADVICE:
            continue
        ref = bands[name]
        loud = offset > ref["p50"]
        outside_by = offset - ref["p97_5"] if loud else ref["p2_5"] - offset
        past_middle = offset - ref["p75"] if loud else ref["p25"] - offset
        if outside_by >= BAND_MARGIN_DB:
            verdict = "outside"
        elif past_middle > 0 and abs(offset - ref["p50"]) >= LEAN_MIN_DB:
            verdict = "leaning"
        else:
            verdict = "typical"
        out[name] = {"range": BAND_ADVICE[name][0], "offset_db": round(offset, 1),
                     "vs_typical_db": round(offset - ref["p50"], 1), "direction": "hot" if loud else "quiet",
                     "references_db": [ref["p2_5"], ref["p97_5"]], "verdict": verdict,
                     "outside_by_db": round(outside_by, 1), "past_middle_db": round(past_middle, 1)}
    return out


def _append_full_mix(by_track: Dict[str, List[Recommendation]], track: TrackState) -> None:
    measurements = track.latest_measurements
    if (measurements is None or measurements.is_silent or _role(track) not in FULL_MIX_ROLES
            or not REFERENCE or measurements.duration_seconds < 3.0):
        return
    genre = REFERENCE.get("genre", "reference")

    def note(priority, rank, code, message, reasoning, fix, evidence):
        by_track[track.id].append(_new_recommendation(priority, rank, code, track, message, reasoning, fix, evidence))

    # --- tonal balance against the references: real problems first, then the strongest leanings
    balance = tonal_balance(measurements)
    outside = sorted(((b["outside_by_db"], n, b) for n, b in balance.items() if b["verdict"] == "outside"), reverse=True)
    leaning = sorted(((b["past_middle_db"], n, b) for n, b in balance.items() if b["verdict"] == "leaning"), reverse=True)
    for tier, items in (("outside", outside[:MAX_BAND_NOTES]), ("leaning", leaning[:MAX_LEAN_NOTES])):
        for excess, name, band in items:
            loud = band["direction"] == "hot"
            message, fix = BAND_ADVICE[name][1 if loud else 2]
            if tier == "outside":
                # breakdowns and lo-fi parts legitimately lack sub and air, so "too little" there stays low priority
                gentle = not loud and name in ("sub", "air")
                priority = "low" if gentle else "high" if excess >= BAND_HIGH_DB else "medium"
                reasoning = "{} sits {:.1f} dB {} your typical {} reference, past anything in your references.".format(
                    band["range"], abs(band["vs_typical_db"]), "above" if loud else "below", genre)
                move = int(min(max(round(abs(band["vs_typical_db"]) / 2), 2), 4))
            else:
                priority = "low"
                message = "Compared with your {} references, {} leans {:.0f} dB {}.".format(
                    genre, band["range"], abs(band["vs_typical_db"]), "hot" if loud else "quiet")
                reasoning = ("Most of your references sit closer to the middle there; some do this on purpose. "
                             "If it sounds {} to you, this is the place to look.".format(
                                 "too thick or harsh" if loud else "thin or missing"))
                move = 2
            note(priority, 2 if tier == "outside" else 5, "{}_{}{}".format(
                     name, "heavy" if loud else "light", "" if tier == "outside" else "_lean"),
                 message, reasoning, fix.format(db=move),
                 {"band": name, "range": band["range"], "vs_typical_db": band["vs_typical_db"],
                  "references_db": band["references_db"], "tier": tier})

    # --- loudness: only "louder than the loudest references"; a quiet pre-master is normal
    ref = REFERENCE.get("loudness_lufs")
    lufs = measurements.loudness_lufs
    if ref and lufs is not None and lufs > ref["p95"] + 1.0:
        note("medium", 1, "louder_than_references",
             "This is {:.1f} LUFS, louder than nearly all of your {} references.".format(lufs, genre),
             "Your references sit between {:.1f} and {:.1f} LUFS in their main sections; past that the limiter is "
             "usually flattening the drop.".format(ref["p5"], ref["p95"]),
             "Back the limiter or clipper off {} dB and compare the drop's punch.".format(int(min(round(lufs - ref["p50"]), 4))),
             {"loudness_lufs": round(lufs, 1), "reference_lufs": ref})

    # --- low end in mono
    corr, low_side = measurements.low_end_correlation, measurements.low_side_to_mid_db
    ref_side = REFERENCE.get("low_side_to_mid_db")
    if (corr is not None and corr < 0.8) or (ref_side and low_side is not None and low_side > ref_side["p95"] + 3.0):
        note("high" if corr is not None and corr < 0.5 else "medium", 1, "wide_low_end",
             "The low end isn't mono: there's stereo content under 120 Hz.",
             "Below 120 Hz the channels correlate at {} with side energy {} dB under the mid; your references stay "
             "mono there. On a club system or phone the sub will lose power or cancel.".format(
                 "?" if corr is None else "{:.2f}".format(corr), "?" if low_side is None else "{:.1f}".format(low_side)),
             "Mono everything below about 120 Hz (Utility's Bass Mono, or a mid/side EQ cutting the side under 120 Hz).",
             {"low_end_correlation": corr, "low_side_to_mid_db": low_side, "reference_low_side_to_mid_db": ref_side})

    # --- overall width
    width, ref_width = measurements.side_to_mid_db, REFERENCE.get("side_to_mid_db")
    if ref_width and width is not None:
        if width > ref_width["p95"] + 2.0:
            note("medium", 3, "wider_than_references",
                 "The mix is wider than your references, so it can sound phasey and collapse in mono.",
                 "Side energy is {:.1f} dB under the mid; your references run {:.1f} to {:.1f} dB.".format(
                     width, ref_width["p5"], ref_width["p95"]),
                 "Narrow the widest pads or FX, check the stereo wideners, and listen in mono.",
                 {"side_to_mid_db": round(width, 1), "reference": ref_width})
        elif width < ref_width["p5"] - 4.0:
            note("low", 4, "narrower_than_references",
                 "The mix is narrower than your references.",
                 "Side energy is {:.1f} dB under the mid; your references run {:.1f} to {:.1f} dB.".format(
                     width, ref_width["p5"], ref_width["p95"]),
                 "Pan or widen pads, chords and FX; keep kick, bass and lead vocal centered.",
                 {"side_to_mid_db": round(width, 1), "reference": ref_width})

    # --- low-end punch: a steady wall instead of kick and bass taking turns
    punch, ref_punch = measurements.low_end_punch_db, REFERENCE.get("low_end_punch_db")
    if ref_punch and punch is not None and punch < ref_punch["p5"]:
        note("medium", 3, "low_end_wall",
             "The low end is a steady wall: the kick isn't punching through the bass.",
             "The low-end envelope only moves {:.1f} dB between hits; your references move {:.1f} dB or more.".format(
                 punch, ref_punch["p5"]),
             "Sidechain the bass to the kick (fast attack, around 50-100 ms release) or shorten the bass notes.",
             {"low_end_punch_db": round(punch, 1), "reference": ref_punch})


def build_recommendations(tracks: List[TrackState]) -> Dict[str, List[Recommendation]]:
    by_track: Dict[str, List[Recommendation]] = {track.id: [] for track in tracks}
    active = [track for track in tracks if track.latest_measurements is not None]
    for track in active:
        _append_level_and_width(by_track, track)
        _append_full_mix(by_track, track)
    _append_peer_notes(by_track, active)
    for recommendations in by_track.values():
        # most serious first; equal notes keep the order they were added in (tonal notes: furthest outside first)
        recommendations.sort(key=lambda item: (PRIORITY_ORDER.get(item.priority, 3), item.rank))
    return by_track
