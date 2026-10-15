import json
from pathlib import Path

import numpy as np


BANDS = ("delta", "theta", "low_alpha", "high_alpha",
         "low_beta", "high_beta", "low_gamma", "middle_gamma")
NANOSECONDS_PER_SECOND = 1_000_000_000
BIN_NS = 5 * NANOSECONDS_PER_SECOND


def read_session(path):
    path = Path(path)
    rows = []
    for line in path.read_text().splitlines():
        if line.strip():
            rows.append(json.loads(line))

    samples = []
    for row in rows:
        if row.get("event") == "sample" and isinstance(row.get("bands"), dict):
            samples.append(row)
    if not samples:
        raise ValueError(f"No decoded samples in {path}")

    times = np.asarray([row["unix_ns"] for row in samples], dtype=np.int64)
    order = np.argsort(times)
    samples = [samples[i] for i in order]
    times = times[order]
    if np.any(np.diff(times) <= 0):
        raise ValueError(f"Sample timestamps must be distinct in {path}")

    try:
        power = np.asarray([[row["bands"][band] for band in BANDS] for row in samples], dtype=float)
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"Invalid or missing band powers in {path}") from error
    if not np.isfinite(power).all() or np.any(power < 0):
        raise ValueError(f"Band powers must be finite and nonnegative in {path}")
    values = np.log1p(power)
    quality = np.asarray([row.get("poor_signal", 200) for row in samples], dtype=int)
    return {
        "path": path,
        "rows": rows,
        "samples": samples,
        "times": times,
        "values": values,
        "quality": quality,
        "first": int(times[0]),
        "last": int(times[-1]),
    }


def signed_changes(x):
    # compare the newer 20 seconds with the previous 20 seconds
    n = len(x)
    out = np.full(n, np.nan)
    for i in range(7, n):
        window = x[i-7:i+1]
        if np.isfinite(window).all():
            older = np.mean(window[:4])
            newer = np.mean(window[4:])
            out[i] = newer - older
    return out


def aggregate(session):
    start, end = session["first"], session["last"]
    bins = ((session["times"] - start) // BIN_NS).astype(int)
    n = int(bins[-1]) + 1
    values = np.full((n, 8), np.nan)
    count = np.bincount(bins, minlength=n)
    bad = np.bincount(bins, weights=session["quality"] != 0, minlength=n)

    # median log powers in each 5 second bin, using only quality-zero samples
    for i in np.unique(bins):
        selected = (bins == i) & (session["quality"] == 0)
        if selected.any():
            values[i] = np.median(session["values"][selected], axis=0)
    times = start + np.arange(n, dtype=np.int64)*BIN_NS + BIN_NS//2
    values[times > end] = np.nan

    # undo log1p before adding band powers together
    power = np.expm1(values)
    alpha = power[:, 2] + power[:, 3]
    beta = power[:, 4] + power[:, 5]
    theta = power[:, 1]
    pope = np.log1p(beta) - np.log1p(alpha + theta)
    signed = signed_changes(pope)

    bad_context = np.full(n, np.nan)
    for i in range(7, n):
        amount = count[i-7:i+1].sum()
        if amount:
            bad_context[i] = bad[i-7:i+1].sum() / amount

    return {
        "path": str(session["path"]),
        "start": start,
        "end": end,
        "times": times,
        "bands": values,
        "power": power,
        "pope": pope,
        "signed_change": signed,
        "change": np.abs(signed),
        "bad_context": bad_context,
        "sample_count": len(session["samples"]),
    }


def load_archive(data_root):
    paths = sorted((Path(data_root) / "recordings").glob("*.jsonl"))
    sessions = []
    for path in paths:
        if "_record_" not in path.name:
            continue
        session = read_session(path)
        if session["last"] - session["first"] >= 300 * NANOSECONDS_PER_SECOND:
            sessions.append(aggregate(session))
    return sessions


def load_experiment(path):
    session = read_session(path)
    return session, aggregate(session)
