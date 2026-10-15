import numpy as np
from sklearn.ensemble import ExtraTreesRegressor


NANOSECONDS_PER_HOUR = 60 * 60 * 1_000_000_000


def predictors(record):
    # current delta, theta, gamma and quality, then the ratio from 40 seconds ago
    bands = record["bands"]
    context = np.column_stack([bands[:, 0], bands[:, 1], bands[:, 6], bands[:, 7],
                               record["bad_context"]])
    valid = np.isfinite(context).all(axis=1)
    context[~valid] = np.nan

    prior = np.full(len(record["pope"]), np.nan)
    prior[8:] = record["pope"][:-8]
    return np.column_stack([context, prior])


def archive_arrays(records):
    X = np.concatenate([predictors(record) for record in records])
    y = np.concatenate([record["signed_change"] for record in records])
    times = np.concatenate([record["times"] for record in records])
    valid = np.isfinite(X).all(axis=1) & np.isfinite(y)
    return X, y, times, valid


def fit_archive_model(records):
    records = sorted(records, key=lambda record: record["start"])
    X, y, times, valid = archive_arrays(records)
    n = len(y)
    split = int(0.8*n)

    # first 80% for fitting, last 20% for calibration, with 120s removed on both sides
    positions = np.arange(n)
    fit = valid & (positions < split-24)
    calibration = valid & (positions >= split+24)
    if fit.sum() < 200 or calibration.sum() < 100:
        raise ValueError("Need substantially more valid archive bins for fit and residual calibration")

    # get scaling from the fitting rows only
    center = np.median(X[fit], axis=0)
    scale = 1.4826 * np.median(np.abs(X[fit] - center), axis=0)
    scale = np.maximum(scale, 1e-3)
    X_train = (X[fit] - center) / scale
    X_cal = (X[calibration] - center) / scale
    y_train, y_cal = y[fit], y[calibration]

    model = ExtraTreesRegressor(
        n_estimators=120, min_samples_leaf=10, max_depth=8,
        max_features=1.0, n_jobs=2, random_state=1701,
    )
    model.fit(X_train, y_train)

    # calibration errors set the residual scale and strong-peak threshold
    output = model.predict(X_cal)
    error = y_cal - output
    bias = float(np.mean(error))
    residual_scale = max(float(np.sqrt(np.mean((error - bias)**2))), 1e-6)
    standardized = np.abs(error - bias) / residual_scale
    threshold = float(np.quantile(standardized, 0.95))

    return {
        "model": model,
        "center": center,
        "scale": scale,
        "bias": bias,
        "residual_scale": residual_scale,
        "threshold": threshold,
        "fit_rows": int(fit.sum()),
        "calibration_rows": int(calibration.sum()),
        "archive_files": [record["path"] for record in records],
        "archive_hours": float(sum((r["end"] - r["start"]) / NANOSECONDS_PER_HOUR for r in records)),
        "calibration_time_start": int(times[calibration][0]),
        "calibration_time_end": int(times[calibration][-1]),
    }


def score_record(record, bundle):
    X = predictors(record)
    y = record["signed_change"]
    valid = np.isfinite(X).all(axis=1) & np.isfinite(y)
    prediction = np.full(len(X), np.nan)
    residual = prediction.copy()
    global_score = prediction.copy()

    X_test = (X[valid] - bundle["center"]) / bundle["scale"]
    prediction[valid] = bundle["model"].predict(X_test)
    residual[valid] = y[valid] - prediction[valid] - bundle["bias"]
    magnitude = np.abs(residual[valid]) / bundle["residual_scale"]
    global_score[valid] = magnitude

    return {
        "prediction": prediction,
        "residual": residual,
        "global_score": global_score,
        "valid": valid,
    }
