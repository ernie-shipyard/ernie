"""
Held-out evaluation of the full-mix notes against your reference folder.

    python evals/mix_eval.py "/path/to/Reference Tracks" [--folds 5] [--seed 0] [--out evals/results/<date>]

Tracks are split into folds by track. For each fold, a reference profile is calibrated on the other folds exactly as
scripts/calibrate_reference.py does, and the held-out tracks' main-section windows are judged against it:

  clean     the windows as mastered; any medium/high note is a false alarm
  injected  each window with one known problem (evals/inject.py) at each severity; caught = the expected code is
            among the medium/high notes, top-1 = it's the first of them

MP3 references decode with sample peaks above 0 dBFS (a 0 dBFS master overshoots through the codec); the plugin never
hears that, so windows over full scale get a plain gain change back to -0.1 dBFS before calibration and judging.

Confidence intervals are 95% bootstraps over tracks (windows from one track aren't independent).
Writes report.json, summary.md, severity.png and confusion.png.
"""

import argparse
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import date

import numpy as np

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from calibrate_reference import AUDIO, WINDOW_S, build_profile, main_sections           # noqa: E402
from evals.inject import PROBLEMS, inject                                                # noqa: E402
from mixengine.dsp import analyze_audio                                                    # noqa: E402
from mixengine.feedback import build_recommendations                                      # noqa: E402
from mixengine.registry import TrackState, utc_now                                        # noqa: E402

BOOTSTRAPS = 2000
CEILING_DBFS = -0.1


def sections(path):
    """main_sections, with lossy-decode overshoot taken back under full scale (a gain change: balance is untouched)."""
    out = []
    for audio, sr, measured in main_sections(path):
        if measured.peak_dbfs is not None and measured.peak_dbfs > 0.0:
            audio = (audio * 10 ** ((CEILING_DBFS - measured.peak_dbfs) / 20)).astype(np.float32)
            measured = analyze_audio(audio, sr)
        out.append((audio, sr, measured))
    return out


def windows(path):
    return [m for _, _, m in sections(path)]


def serious_codes(measurements, profile):
    track = TrackState(id="master", name="Master", role="master", registered_at=utc_now(),
                       latest_measurements=measurements)
    return [n.code for n in build_recommendations([track], profile)["master"] if n.priority in ("high", "medium")]


def judge_track(job):
    """Every main-section window of one held-out track, clean and with each problem injected."""
    path, profile = job
    rows, analyses, seconds = [], 0, 0.0
    for w, (audio, sr, measured) in enumerate(sections(path)):
        rows.append({"window": w, "problem": "clean", "severity": 0.0, "codes": serious_codes(measured, profile)})
        for name, (_, severities, _, _) in PROBLEMS.items():
            for severity in severities:
                injected = inject(name, audio, sr, severity)
                started = time.perf_counter()   # time what a capture costs: analysis + notes, not the injection
                codes = serious_codes(analyze_audio(injected, sr), profile)
                seconds += time.perf_counter() - started
                rows.append({"window": w, "problem": name, "severity": severity, "codes": codes})
                analyses += 1
    return os.path.basename(path), rows, analyses, seconds


def bootstrap(hits, totals, rng):
    """Pooled rate over tracks and its 95% CI, resampling whole tracks."""
    hits, totals = np.asarray(hits, float), np.asarray(totals, float)
    if totals.sum() == 0:
        return None
    idx = rng.integers(0, len(totals), (BOOTSTRAPS, len(totals)))
    rates = hits[idx].sum(1) / np.maximum(totals[idx].sum(1), 1)
    return {"rate": round(hits.sum() / totals.sum(), 4), "ci95": [round(float(np.percentile(rates, q)), 4)
                                                                   for q in (2.5, 97.5)], "n": int(totals.sum())}


def threshold(severities, rates, level=0.5):
    """Severity where detection first reaches `level`, linearly interpolated; None if it never does."""
    for i, (s, r) in enumerate(zip(severities, rates)):
        if r >= level:
            if i == 0:
                return s
            s0, r0 = severities[i - 1], rates[i - 1]
            return round(s0 + (level - r0) / (r - r0) * (s - s0), 1)
    return None


def summarize(results, folds, seed):
    rng = np.random.default_rng(seed)
    tracks = sorted(results)

    def per_track(problem, severity, test):
        hits, totals = [], []
        for t in tracks:
            rows = [r for r in results[t] if r["problem"] == problem and r["severity"] == severity]
            hits.append(sum(test(r["codes"]) for r in rows))
            totals.append(len(rows))
        return hits, totals

    clean_codes = {}
    for t in tracks:
        for r in results[t]:
            if r["problem"] == "clean":
                for c in set(r["codes"]):
                    clean_codes[c] = clean_codes.get(c, 0) + 1
    n_clean = sum(1 for t in tracks for r in results[t] if r["problem"] == "clean")
    report = {
        "tracks": len(tracks), "folds": folds, "seed": seed, "window_seconds": WINDOW_S,
        "clean": {"false_alarm": bootstrap(*per_track("clean", 0.0, bool), rng),
                  "false_alarms_by_code": {c: round(n / n_clean, 4) for c, n in sorted(clean_codes.items())}},
        "problems": {},
    }
    confusion = {}
    for name, (_, severities, unit, expected) in PROBLEMS.items():
        levels = []
        for s in severities:
            caught = bootstrap(*per_track(name, s, lambda codes: bool(expected & set(codes))), rng)
            top1 = bootstrap(*per_track(name, s, lambda codes: bool(codes) and codes[0] in expected), rng)
            levels.append({"severity": s, "caught": caught, "top1": top1})
        rates = [lv["caught"]["rate"] for lv in levels]
        firsts = {}
        for t in tracks:
            for r in results[t]:
                if r["problem"] == name and r["severity"] == severities[-1]:
                    key = r["codes"][0] if r["codes"] else "(no note)"
                    firsts[key] = firsts.get(key, 0) + 1
        confusion[name] = firsts
        report["problems"][name] = {"unit": unit, "expected": sorted(expected), "levels": levels,
                                    "severity_for_50pct": threshold(list(severities), rates),
                                    "severity_for_90pct": threshold(list(severities), rates, 0.9)}
    report["confusion_at_max_severity"] = confusion
    # headline: every problem at its strongest and second-strongest severity, averaged across problems
    for key, index in (("macro_caught_at_max", -1), ("macro_caught_at_second", -2)):
        report[key] = round(float(np.mean([p["levels"][index]["caught"]["rate"]
                                           for p in report["problems"].values()])), 4)
        report[key.replace("caught", "top1")] = round(float(np.mean([p["levels"][index]["top1"]["rate"]
                                                                     for p in report["problems"].values()])), 4)
    return report


def pct(x):
    return "-" if x is None else f"{100 * x['rate']:.0f}% [{100 * x['ci95'][0]:.0f}-{100 * x['ci95'][1]:.0f}]"


def write_summary(report, timing, out):
    fa = report["clean"]["false_alarm"]
    lines = [
        "# Mix feedback engine: held-out eval", "",
        f"{report['tracks']} reference tracks, {report['folds']}-fold by track, seed {report['seed']}. "
        f"{fa['n']} clean {report['window_seconds']:.0f} s windows; every window also gets each problem at each "
        "severity. Brackets are 95% CIs, bootstrapped over tracks.", "",
        f"- **False alarm on clean masters:** {pct(fa)} of windows get a medium/high note",
        f"- **Caught, averaged over {len(report['problems'])} problems:** {100 * report['macro_caught_at_max']:.0f}% "
        f"at the strongest severity, {100 * report['macro_caught_at_second']:.0f}% at the second strongest",
        f"- **Top-1 (the right note first):** {100 * report['macro_top1_at_max']:.0f}% at the strongest, "
        f"{100 * report['macro_top1_at_second']:.0f}% at the second strongest",
        f"- **Speed:** {timing['seconds_per_window'] * 1000:.0f} ms to analyze and judge a 15 s window "
        f"({timing['realtime_factor']:.0f}x real time, one core)", "",
        "| Problem | Severity | Caught | Top-1 | 50% caught at |", "|---|---|---|---|---|",
    ]
    for name, p in report["problems"].items():
        for i, lv in enumerate(p["levels"]):
            at50 = ("" if i else ("never" if p["severity_for_50pct"] is None
                                  else f"{p['severity_for_50pct']:g} {p['unit']}"))
            lines.append(f"| {name if i == 0 else ''} | {lv['severity']:g} {p['unit']} | {pct(lv['caught'])} | "
                         f"{pct(lv['top1'])} | {at50} |")
    lines += ["", "False alarms on clean windows, by note: " + (", ".join(
        f"`{c}` {100 * r:.1f}%" for c, r in report["clean"]["false_alarms_by_code"].items()) or "none"), "",
        "![Detection vs. severity](severity.png)", "", "![First note at the strongest severity](confusion.png)", ""]
    with open(os.path.join(out, "summary.md"), "w") as f:
        f.write("\n".join(lines))


def plot(report, out):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ink, muted, blue, grid = "#0b0b0b", "#52514e", "#2a78d6", "#e4e3df"
    plt.rcParams.update({"font.size": 9, "axes.edgecolor": grid, "axes.labelcolor": muted, "xtick.color": muted,
                         "ytick.color": muted, "text.color": ink, "figure.facecolor": "#fcfcfb",
                         "axes.facecolor": "#fcfcfb"})
    fa = report["clean"]["false_alarm"]["rate"]
    problems = list(report["problems"].items())
    cols = 4
    fig, axes = plt.subplots((len(problems) + cols - 1) // cols, cols, figsize=(11, 7.5), sharey=True)
    for ax, (name, p) in zip(axes.flat, problems):
        xs = [lv["severity"] for lv in p["levels"]]
        caught = [100 * lv["caught"]["rate"] for lv in p["levels"]]
        lo = [100 * lv["caught"]["ci95"][0] for lv in p["levels"]]
        hi = [100 * lv["caught"]["ci95"][1] for lv in p["levels"]]
        ax.fill_between(xs, lo, hi, color=blue, alpha=0.15, linewidth=0)
        ax.plot(xs, caught, color=blue, linewidth=2, marker="o", markersize=4)
        ax.axhline(100 * fa, color=muted, linewidth=1, linestyle=":")
        ax.set_title(name, fontsize=9, loc="left")
        ax.set_xticks(xs)
        ax.set_xlabel(p["unit"], fontsize=8)
        ax.set_ylim(0, 102)
        ax.grid(axis="y", color=grid, linewidth=0.6)
        ax.spines[["top", "right"]].set_visible(False)
    for ax in axes.flat[len(problems):]:
        ax.set_visible(False)
    for ax in axes[:, 0]:
        ax.set_ylabel("% of windows caught")
    fig.suptitle("Detection vs. severity on held-out reference tracks (band: 95% CI; dotted: false-alarm rate on "
                 "clean windows)", x=0.01, ha="left", fontsize=10)
    fig.tight_layout()
    fig.savefig(os.path.join(out, "severity.png"), dpi=160)
    plt.close(fig)

    confusion = report["confusion_at_max_severity"]
    rows = list(confusion)
    columns = sorted({c for v in confusion.values() for c in v}, key=lambda c: (c == "(no note)", c))
    matrix = np.array([[confusion[r].get(c, 0) / max(sum(confusion[r].values()), 1) for c in columns] for r in rows])
    fig, ax = plt.subplots(figsize=(1.1 + 0.55 * len(columns), 0.9 + 0.42 * len(rows)))
    ax.imshow(matrix, cmap="Blues", vmin=0, vmax=1, aspect="auto")
    ax.set_xticks(range(len(columns)), columns, rotation=45, ha="right")
    ax.set_yticks(range(len(rows)), rows)
    for i in range(len(rows)):
        for j in range(len(columns)):
            if matrix[i, j] >= 0.05:
                ax.text(j, i, f"{100 * matrix[i, j]:.0f}", ha="center", va="center", fontsize=7,
                        color="#fcfcfb" if matrix[i, j] > 0.55 else ink)
    ax.set_title("First medium/high note at the strongest severity (% of windows)", loc="left", fontsize=10)
    ax.set_xlabel("first note")
    for s in ax.spines.values():
        s.set_visible(False)
    fig.tight_layout()
    fig.savefig(os.path.join(out, "confusion.png"), dpi=160)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--genre", default="edm")
    ap.add_argument("--workers", type=int, default=os.cpu_count())
    ap.add_argument("--out", default=os.path.join(ROOT, "evals", "results", date.today().isoformat()))
    args = ap.parse_args()

    files = sorted(os.path.join(root, f) for root, _, names in os.walk(args.folder)
                   for f in names if f.lower().endswith(AUDIO) and not f.startswith("._"))
    with ProcessPoolExecutor(args.workers) as pool:
        measured = dict(zip(files, pool.map(windows, files)))
        files = [f for f in files if measured[f]]
        print(f"{len(files)} tracks, {sum(len(measured[f]) for f in files)} main-section windows")

        order = np.random.default_rng(args.seed).permutation(len(files))
        fold_of = {files[i]: k % args.folds for k, i in enumerate(order)}
        jobs = []
        for k in range(args.folds):
            train = [m for f in files if fold_of[f] != k for m in measured[f]]
            profile = build_profile(train, args.genre, sum(1 for f in files if fold_of[f] != k))
            jobs += [(f, profile) for f in files if fold_of[f] == k]
        results, analyses, seconds = {}, 0, 0.0
        for done, (name, rows, n, s) in enumerate(pool.map(judge_track, jobs), 1):
            results[name], analyses, seconds = rows, analyses + n, seconds + s
            print(f"[{done}/{len(jobs)}] {name}")

    report = summarize(results, args.folds, args.seed)
    per_window = seconds / max(analyses, 1)
    timing = {"seconds_per_window": round(per_window, 4), "realtime_factor": round(WINDOW_S / per_window, 1)}
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "report.json"), "w") as f:
        json.dump(report, f, indent=2)
    with open(os.path.join(args.out, "timing.json"), "w") as f:
        json.dump(timing, f, indent=2)
    write_summary(report, timing, args.out)
    plot(report, args.out)
    print(open(os.path.join(args.out, "summary.md")).read())


if __name__ == "__main__":
    main()
