import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.signal import find_peaks

from archive_model import fit_archive_model, score_record
from data import load_archive, load_experiment


NANOSECONDS_PER_MINUTE = 60 * 1_000_000_000


COLORS = {
    "work": "#8ad6aa",
    "distraction": "#f5bc89",
    "neutral": "#dbe0e6",
    "post-task": "#dbe0e6",
}


def activity_intervals(rows, start, end):
    confirmations = []
    completion = end
    found_completion = False
    for row in rows:
        event = row.get("event")
        if event == "segment_transition_confirmed":
            confirmations.append(row)
        elif event == "session_target_complete" and not found_completion:
            completion = int(row["unix_ns"])
            found_completion = True

    confirmations = sorted(confirmations, key=lambda row: row["unix_ns"])
    if not confirmations:
        raise ValueError("Experiment file contains no segment confirmations")
    times = np.array([int(row["unix_ns"]) for row in confirmations], dtype=np.int64)
    if np.any(np.diff(times) <= 0) or times[0] < start or times[-1] > end:
        raise ValueError("Invalid activity confirmation times")
    if not times[-1] <= completion <= end:
        raise ValueError("Completion must fall between the final confirmation and recording end")

    intervals = [{"start": int(start), "end": int(times[0]),
                  "condition": "neutral", "label": "Setup / baseline"}]
    count = {"work": 0, "distraction": 0}
    for i, row in enumerate(confirmations):
        condition = row.get("condition")
        if condition not in count:
            raise ValueError(f"Unknown condition {condition}")
        count[condition] += 1
        if i+1 < len(times):
            stop = int(times[i+1])
        else:
            stop = completion
        label = f"{condition.capitalize()} {count[condition]}"
        intervals.append({"start": int(times[i]), "end": stop,
                          "condition": condition, "label": label, "enter": i+1})

    if completion < end:
        intervals.append({"start": completion, "end": int(end),
                          "condition": "post-task", "label": "Post-task"})
    return intervals, confirmations, completion


def selected_peaks(values, threshold, interval_seconds=120):
    out = []
    distance = max(1, int(interval_seconds / 5))
    finite = np.isfinite(values)
    edges = np.diff(np.r_[False, finite, False].astype(int))
    starts = np.flatnonzero(edges == 1)
    stops = np.flatnonzero(edges == -1)

    # find peaks separately in each valid stretch, without joining across gaps
    for start, stop in zip(starts, stops):
        if stop-start < 3:
            continue
        idx, _ = find_peaks(values[start:stop], height=threshold, distance=distance)
        out.extend((start + idx).tolist())
    return out


def plot(target, intervals, confirmations, scores, peaks, thresholds, outfile):
    start = target["start"]
    x = (target["times"] - start) / NANOSECONDS_PER_MINUTE
    fig, axes = plt.subplots(4, 1, figsize=(17, 8), sharex=True,
                            gridspec_kw={"height_ratios": [0.6, 2, 2, 1]},
                            layout="constrained")

    # activity sections, then Enter presses
    for interval in intervals:
        a = (interval["start"] - start) / NANOSECONDS_PER_MINUTE
        b = (interval["end"] - start) / NANOSECONDS_PER_MINUTE
        color = COLORS[interval["condition"]]
        for ax in axes[1:4]:
            ax.axvspan(a, b, color=color, alpha=0.12, lw=0)
        axes[0].axvspan(a, b, color=color, alpha=0.9, lw=0)
        label = interval["label"]
        if interval["condition"] == "neutral":
            label = label.replace(" ", "\n", 1)
        axes[0].text((a+b)/2, 0.55, label, ha="center", va="center", fontsize=9)

    for row in confirmations:
        at = (int(row["unix_ns"]) - start) / NANOSECONDS_PER_MINUTE
        for ax in axes[1:4]:
            ax.axvline(at, color="#354052", ls="--", lw=0.8)

    axes[0].set_ylim(0, 1)
    axes[0].set_yticks([])
    axes[0].set_ylabel("Task")

    # the two score traces and their strong peaks
    for row, key, color in ((1, "baseline", "#2860a8"), (2, "conditioned", "#8050a5")):
        ax = axes[row]
        values = scores[key]
        threshold = thresholds[key]
        ax.plot(x, values, color=color, lw=1.1)
        ax.axhline(threshold, color="#657082", ls=":", lw=1.3)
        for i in peaks[key]:
            ax.scatter(x[i], values[i], color="#c93c4f", marker="D", s=28, zorder=5)

        if key == "baseline":
            ax.set_ylabel("Pope change")
        else:
            ax.set_ylabel("Archive surprise\n(standardized)")
        ax.grid(axis="y", alpha=0.3)

    axes[3].plot(x, 100*target["bad_context"], color="#b77720", lw=1)
    axes[3].set_ylabel("Poor signal\ncontext (%)")
    axes[3].set_xlabel("Minutes")
    axes[3].grid(axis="y", alpha=0.3)
    axes[3].set_xticks(np.arange(0, max(x)+1, 2))
    fig.savefig(outfile, dpi=180, bbox_inches="tight")
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description="Plot work/distraction metadata and EEG-change scores")
    parser.add_argument("--data-dir", type=Path, default=Path("private_data"))
    parser.add_argument("--experiment", type=Path, required=True, help="relative to data-dir")
    parser.add_argument("--output", type=Path, default=Path("distraction-timeline.png"))
    args = parser.parse_args()

    archive = load_archive(args.data_dir)
    source, target = load_experiment(args.data_dir / args.experiment)
    archive_paths = [Path(record["path"]).resolve() for record in archive]
    if Path(target["path"]).resolve() in archive_paths:
        raise ValueError("The target experiment must be separate from archive fit/calibration files")

    bundle = fit_archive_model(archive)
    conditioned = score_record(target, bundle)
    scores = {"baseline": target["change"], "conditioned": conditioned["global_score"]}
    thresholds = {"baseline": float(np.nanquantile(target["change"], 0.95)),
                  "conditioned": bundle["threshold"]}
    peaks = {}
    for key, values in scores.items():
        peaks[key] = selected_peaks(values, thresholds[key])

    # add the recorded activity labels after computing the scores
    intervals, confirmations, _ = activity_intervals(source["rows"], target["start"], target["end"])
    plot(target, intervals, confirmations, scores, peaks, thresholds, args.output)

    report = {
        "archive_hours": bundle["archive_hours"],
        "archive_recordings": bundle["archive_files"],
        "target_session": str(args.experiment),
        "fit_rows": bundle["fit_rows"],
        "calibration_rows": bundle["calibration_rows"],
        "conditioned_archive_q95": bundle["threshold"],
        "detections": {key: len(value) for key, value in peaks.items()},
        "figure": str(args.output),
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
