# Evals

## Mix feedback engine (`mix_eval.py`)

How well do Ernie's full-mix notes catch real problems, and how often do they cry wolf on finished masters?

    python evals/mix_eval.py "/path/to/Reference Tracks" --folds 5 --seed 0

- **Held out by track.** The reference folder is split into folds by track. Each fold's profile is calibrated on the
  other folds with the same code as `scripts/calibrate_reference.py`, so no track is ever judged against a profile it
  helped build.
- **Clean:** every main-section 15 s window as mastered. Any medium/high note counts as a false alarm.
- **Injected:** every window again with one known problem (`inject.py`) at four severities: octave-wide EQ boosts
  and cuts, stereo sub, a widener and an over-driven clipper. The injected audio never peaks above the original window.
  - *Caught* means the expected note is among the medium/high notes.
  - *Top-1* means it is the first of them.
- **CIs** are 95% bootstraps over tracks, because windows from the same track aren't independent.
- **Deterministic:** the same folder and seed give a byte-identical `report.json`. Timing goes to `timing.json`.
- **MP3 overshoot.** MP3 references decode with sample peaks above 0 dBFS (97% of MP3 windows here; none of the WAVs).
  This is a codec artifact the plugin never hears, so those windows get a plain gain change down to -0.1 dBFS first.
  Without that change, `over_full_scale` fires on 41% of clean windows.

Results go to `evals/results/<date>/`: `summary.md` with the full table, `report.json`, `severity.png` and
`confusion.png`.

### Latest (61 EDM tracks, 735 windows, 5-fold, seed 0)

- 20% [16–25] of clean windows get a medium/high note. The largest share is `low_end_wall` at 5.4%.
- Stereo sub is caught 100% of the time from −6 dB side/mid, and over-limiting 96% at 12 dB of clipping.
- Mid and high EQ problems (boxy, nasal, harsh, sibilant) are caught 81–86% of the time at +12 dB, crossing 50%
  at +7 to +9 dB.
- The weak spots are cuts: a −12 dB dip is caught 44% of the time at 85 Hz and 23% at 7 kHz. Low-mid boosts reach
  only ~55% at +12 dB, because the references themselves vary that much there.
- "Too wide" is caught 98% of the time, but the first note is usually `negative_stereo_correlation`. That is a
  correct diagnosis too, but the eval is strict and doesn't count it.

The synthetic version of the same sweep (no audio needed) runs in CI as part of `tests/test_mix_reference.py`.
