"""
Build the reference profile mix notes are judged against, from finished tracks you trust.

    python scripts/calibrate_reference.py "/path/to/Reference Tracks" [--genre edm]

Every track is cut into capture-sized windows (15 s, like a plugin listen) and run through the same DSP as a
capture (mixengine.dsp). Only the main sections count: windows within 6 dB of the track's loudest window, so
intros and breakdowns don't drag the target down. The result is mixengine/reference_<genre>.json:

  bands.<name>.target     median level of the band vs. the track's own overall balance (dB)
  bands.<name>.p2_5..p97_5  percentiles of each window's offset from that target (mixengine.feedback.balance_offsets)
  <metric>.p5/p50/p95     loudness, width, low-end correlation and punch across the same windows

A capture is only told a band is too loud or too quiet when it falls outside what the references themselves do.
"""

import argparse
import json
import os
import sys
from datetime import date

import numpy as np
import soundfile as sf

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from mixengine.dsp import OCTAVE_BANDS, analyze_audio            # noqa: E402
from mixengine.feedback import balance_offsets                    # noqa: E402

WINDOW_S = 15.0
MAIN_SECTION_DB = 6.0
AUDIO = (".wav", ".aif", ".aiff", ".flac", ".mp3")
METRICS = ("loudness_lufs", "side_to_mid_db", "low_side_to_mid_db", "low_end_correlation", "low_end_punch_db",
           "crest_factor_db")


def windows(path):
    audio, sr = sf.read(path, dtype="float32", always_2d=True)
    if audio.shape[1] > 2:
        audio = audio[:, :2]
    if audio.shape[1] == 1:
        audio = np.repeat(audio, 2, axis=1)
    size = int(WINDOW_S * sr)
    cuts = [audio[i:i + size] for i in range(0, len(audio) - size + 1, size)]
    measured = [analyze_audio(c, sr) for c in cuts]
    measured = [m for m in measured if not m.is_silent and m.loudness_lufs is not None]
    if not measured:
        return []
    loudest = max(m.loudness_lufs for m in measured)
    return [m for m in measured if m.loudness_lufs >= loudest - MAIN_SECTION_DB]


def pct(values, q):
    return round(float(np.nanpercentile(values, q)), 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--genre", default="edm")
    args = ap.parse_args()

    files = sorted(os.path.join(root, f) for root, _, names in os.walk(args.folder)
                   for f in names if f.lower().endswith(AUDIO) and not f.startswith("._"))
    all_windows, used = [], []
    for path in files:
        try:
            got = windows(path)
        except Exception as e:  # unreadable or exotic file: skip, say so
            print(f"skip {os.path.basename(path)}: {e}")
            continue
        if got:
            all_windows.extend(got)
            used.append(os.path.basename(path))
            print(f"{len(got):3d} windows  {os.path.basename(path)}")
    if not all_windows:
        sys.exit("No usable audio found.")

    names = [n for n, _, _ in OCTAVE_BANDS]
    levels = np.array([[m.octave_bands_db.get(n, np.nan) for n in names] for m in all_windows])
    # target: each window's curve with its own overall level removed, then the median across windows
    centered = levels - np.nanmedian(levels, axis=1, keepdims=True)
    target = np.nanmedian(centered, axis=0)
    # spread: deviation from the target after the same offset fit a capture gets
    deviations = np.array([[balance_offsets(dict(zip(names, row)), dict(zip(names, target))).get(n, np.nan)
                            for n in names] for row in levels])
    profile = {
        "genre": args.genre,
        "built": date.today().isoformat(),
        "source_tracks": len(used),
        "windows": len(all_windows),
        "window_seconds": WINDOW_S,
        "bands": {n: {"target": round(float(target[i]), 2),
                  **{"p" + str(q).replace(".", "_"): pct(deviations[:, i], q) for q in (2.5, 25, 50, 75, 97.5)}}
              for i, n in enumerate(names)},
    }
    for metric in METRICS:
        values = [getattr(m, metric) for m in all_windows if getattr(m, metric) is not None]
        if values:
            profile[metric] = {"p5": pct(values, 5), "p50": pct(values, 50), "p95": pct(values, 95)}

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mixengine", f"reference_{args.genre}.json")
    with open(out, "w") as f:
        json.dump(profile, f, indent=2)
    print(f"\n{len(used)} tracks, {len(all_windows)} windows -> {os.path.normpath(out)}")
    print(json.dumps(profile, indent=2))


if __name__ == "__main__":
    main()
