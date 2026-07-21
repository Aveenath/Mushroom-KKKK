"""
Trainable harvest-date predictor (LightGBM), with auto-retraining.

This is a heavier, more accurate sibling to the EMA-based adaptive
baseline in groq_advisor.py. That system is a simple running average
(good default, works from day one). This module is a real trained
regression model that learns patterns from sensor conditions + harvest
history — but it needs a reasonable number of samples before it's
trustworthy, so it's designed to fall back gracefully when data is thin.

═══════════════════════════════════════════════════════════════════════
WHAT DATA THIS NEEDS (all already in your DB):
  - planting_records: planted_date, harvest_count, last_harvest_date, cycle
  - harvest_history:  harvest_number, harvest_date, cycle (per block)
  - sensors:          temp, humidity, co2, ts  (hourly/periodic readings)

TARGET (y): observed_days = harvest_date - reference_date
  where reference_date = planted_date (first harvest) or the previous
  harvest_date (reharvest).

FEATURES (X): engineered from sensor readings across [reference_date, harvest_date]
  - is_first_harvest, harvest_number, cycle
  - avg_temp, avg_humidity, avg_co2
  - temp_min, temp_max, humidity_min, humidity_max
  - temp_std, humidity_std      (volatility — how stable conditions were)
  - stress_ratio                (fraction of days that breached thresholds)
  - days_with_sensor_data       (data coverage / confidence signal)
  - prior_observed_days         (0 for first harvest; else how long the
                                  previous cycle for this block took)
═══════════════════════════════════════════════════════════════════════
"""

import os
import json
import datetime
import joblib
import numpy as np
import pandas as pd
from pathlib import Path

from utils import get_db_connection
from groq_advisor import STRESS_HUMIDITY_THRESHOLD, STRESS_TEMP_THRESHOLD

MODEL_PATH        = Path(__file__).parent / "harvest_predictor.pkl"
METADATA_PATH     = Path(__file__).parent / "harvest_predictor_meta.json"
RETRAIN_STATE_KEY = "harvest_predictor_retrain_state"

# Colab-trained snapshot (from your Excel historical data). If present,
# this gets merged with whatever's in the live DB every time training
# runs — so your original dataset never has to be inserted into
# harvest_history/planting_records to keep contributing to the model.
BASELINE_DATA_PATH = Path(__file__).parent / "baseline_training_data.csv"

# Don't even attempt training below this — too few samples to trust.
MIN_SAMPLES_TO_TRAIN = 15

# Auto-retrain once this many NEW harvests have accumulated since the
# last training run (avoids retraining on every single harvest).
RETRAIN_EVERY_N_NEW_SAMPLES = 5

FEATURE_COLUMNS = [
    "is_first_harvest", "harvest_number", "cycle",
    "avg_temp", "avg_humidity", "avg_co2",
    "temp_min", "temp_max", "humidity_min", "humidity_max",
    "temp_std", "humidity_std",
    "stress_ratio", "days_with_sensor_data",
    "prior_observed_days",
]


# ─────────────────────────────────────────────────────────────────────────
# 1. BUILD TRAINING DATA
# ─────────────────────────────────────────────────────────────────────────

def _fetch_sensor_window(conn, start_date, end_date):
    """Daily-averaged sensor readings between two dates (inclusive)."""
    try:
        cur = conn.execute(
            """
            SELECT
                strftime('%Y-%m-%d', ts) AS day,
                ROUND(AVG(temp),     1) AS temp,
                ROUND(AVG(humidity), 1) AS humidity,
                ROUND(AVG(co2),      0) AS co2
            FROM sensors
            WHERE ts >= ? AND ts < ?
            GROUP BY day
            ORDER BY day ASC
            """,
            (start_date.isoformat(), (end_date + datetime.timedelta(days=1)).isoformat())
        )
        return cur.fetchall()  # (day, temp, humidity, co2)
    except Exception:
        return []


def _engineer_window_features(rows):
    """Turn a list of daily (day, temp, hum, co2) rows into summary features."""
    if not rows:
        return {
            "avg_temp": np.nan, "avg_humidity": np.nan, "avg_co2": np.nan,
            "temp_min": np.nan, "temp_max": np.nan,
            "humidity_min": np.nan, "humidity_max": np.nan,
            "temp_std": np.nan, "humidity_std": np.nan,
            "stress_ratio": np.nan, "days_with_sensor_data": 0,
        }

    temps = [r[1] for r in rows if r[1] is not None]
    hums  = [r[2] for r in rows if r[2] is not None]
    co2s  = [r[3] for r in rows if r[3] is not None]

    stress_days = sum(
        1 for (_, t, h, _c) in rows
        if (h is not None and h < STRESS_HUMIDITY_THRESHOLD)
        or (t is not None and t > STRESS_TEMP_THRESHOLD)
    )

    return {
        "avg_temp":     round(np.mean(temps), 2) if temps else np.nan,
        "avg_humidity": round(np.mean(hums), 2) if hums else np.nan,
        "avg_co2":      round(np.mean(co2s), 1) if co2s else np.nan,
        "temp_min":     min(temps) if temps else np.nan,
        "temp_max":     max(temps) if temps else np.nan,
        "humidity_min": min(hums) if hums else np.nan,
        "humidity_max": max(hums) if hums else np.nan,
        "temp_std":     round(np.std(temps), 2) if len(temps) > 1 else 0.0,
        "humidity_std": round(np.std(hums), 2) if len(hums) > 1 else 0.0,
        "stress_ratio": round(stress_days / len(rows), 3),
        "days_with_sensor_data": len(rows),
    }


def load_baseline_training_data():
    """
    Load the Colab-trained baseline snapshot, if it's been uploaded.
    Returns an empty DataFrame with the right columns if the file
    doesn't exist yet — safe to concat with either way.
    """
    empty = pd.DataFrame(columns=FEATURE_COLUMNS + ["observed_days", "block_id", "source"])
    if not BASELINE_DATA_PATH.exists():
        return empty
    try:
        df = pd.read_csv(BASELINE_DATA_PATH)
        missing = set(FEATURE_COLUMNS + ["observed_days"]) - set(df.columns)
        if missing:
            # Don't silently train on a malformed baseline file — better
            # to surface the problem than quietly drop your old data.
            raise ValueError(
                f"baseline_training_data.csv is missing required column(s): {missing}. "
                f"Regenerate it from Colab using the same feature schema."
            )
        if "block_id" not in df.columns:
            df["block_id"] = "unknown"
        df["source"] = "baseline"
        return df
    except Exception as exc:
        print(f"[harvest_predictor] WARNING: could not load baseline_training_data.csv: {exc}")
        return empty


def build_training_dataframe(username=None, conn=None, include_baseline=True):
    """
    Walks harvest_history (oldest first per block/cycle) exactly like
    learn_from_harvest_history() does, but instead of feeding a running
    average, it builds one training row per harvest with engineered
    sensor features + the actual observed_days as the label.

    If include_baseline=True (default), also merges in
    baseline_training_data.csv — the Colab-trained snapshot of your
    original Excel data — so old and new data are combined for every
    training run, even though the old rows were never inserted into
    harvest_history.
    """
    if conn is None:
        conn = get_db_connection()
        close_conn = True
    else:
        close_conn = False

    try:
        planted_query = "SELECT block_id, cycle, planted_date, username FROM planting_records"
        params = ()
        if username:
            planted_query += " WHERE username = ?"
            params = (username,)
        planted_rows = conn.execute(planted_query, params).fetchall()
        planted_map = {(str(b), int(c or 1)): p for b, c, p, *_ in planted_rows}

        harvest_query = (
            "SELECT block_id, cycle, harvest_number, harvest_date, username "
            "FROM harvest_history"
        )
        h_params = ()
        if username:
            harvest_query += " WHERE username = ?"
            h_params = (username,)
        harvest_query += " ORDER BY block_id, cycle, harvest_number ASC"
        harvest_rows = conn.execute(harvest_query, h_params).fetchall()

        records = []
        last_date_by_key = {}
        prior_days_by_key = {}

        for block_id, cycle, harvest_number, harvest_date, _user in harvest_rows:
            key = (str(block_id), int(cycle or 1))
            try:
                current = datetime.date.fromisoformat(str(harvest_date))
                h_num = int(harvest_number)
            except Exception:
                continue

            if h_num == 1:
                planted_str = planted_map.get(key)
                if not planted_str:
                    last_date_by_key[key] = current
                    continue
                try:
                    reference = datetime.date.fromisoformat(str(planted_str))
                except Exception:
                    last_date_by_key[key] = current
                    continue
                is_first = True
            else:
                reference = last_date_by_key.get(key)
                if reference is None:
                    last_date_by_key[key] = current
                    continue
                is_first = False

            observed_days = (current - reference).days
            if observed_days <= 0:
                last_date_by_key[key] = current
                continue

            sensor_rows = _fetch_sensor_window(conn, reference, current)
            feats = _engineer_window_features(sensor_rows)
            feats["is_first_harvest"]    = 1 if is_first else 0
            feats["harvest_number"]      = h_num
            feats["cycle"]               = int(cycle or 1)
            feats["prior_observed_days"] = prior_days_by_key.get(key, 0)
            feats["observed_days"]       = observed_days
            feats["block_id"]            = block_id
            feats["source"]              = "live"

            records.append(feats)
            last_date_by_key[key] = current
            prior_days_by_key[key] = observed_days

        db_df = pd.DataFrame(records)

        if not include_baseline:
            return db_df

        baseline_df = load_baseline_training_data()
        if baseline_df.empty:
            return db_df
        if db_df.empty:
            return baseline_df

        combined = pd.concat([baseline_df, db_df], ignore_index=True, sort=False)
        return combined
    finally:
        if close_conn:
            conn.close()


# ─────────────────────────────────────────────────────────────────────────
# 2. TRAIN
# ─────────────────────────────────────────────────────────────────────────

def train_harvest_predictor(df=None, username=None):
    """
    Trains a LightGBM regressor on observed_days. Returns a dict with
    status, metrics, and sample count — never raises; safe to call from
    the app directly.
    """
    import lightgbm as lgb
    from sklearn.model_selection import KFold
    from sklearn.metrics import mean_absolute_error

    if df is None:
        df = build_training_dataframe(username=username)

    n = len(df)
    if n < MIN_SAMPLES_TO_TRAIN:
        return {
            "trained": False,
            "reason": f"Only {n} sample(s) available — need at least "
                      f"{MIN_SAMPLES_TO_TRAIN} before training a reliable model. "
                      f"The adaptive baseline (EMA) in groq_advisor.py will keep "
                      f"being used until then.",
            "n_samples": n,
        }

    X = df[FEATURE_COLUMNS].copy()
    y = df["observed_days"].copy()

    # Simple median-fill for any missing sensor windows (e.g. gaps in
    # logging) rather than dropping rows — every harvest is valuable
    # when the dataset is still small.
    X = X.fillna(X.median(numeric_only=True))

    # k-fold cross-validation for an honest MAE estimate even on a small
    # dataset (better than a single train/test split when n is small).
    k = min(5, n)
    kf = KFold(n_splits=k, shuffle=True, random_state=42)
    fold_maes = []

    for train_idx, test_idx in kf.split(X):
        X_train, X_test = X.iloc[train_idx], X.iloc[test_idx]
        y_train, y_test = y.iloc[train_idx], y.iloc[test_idx]

        fold_model = lgb.LGBMRegressor(
            n_estimators=200,
            learning_rate=0.05,
            max_depth=4,
            num_leaves=15,
            min_child_samples=max(1, n // 10),
            verbose=-1,
        )
        fold_model.fit(X_train, y_train)
        preds = fold_model.predict(X_test)
        fold_maes.append(mean_absolute_error(y_test, preds))

    cv_mae = round(float(np.mean(fold_maes)), 2)

    # Final model trained on ALL available data (cross-validation above
    # was only to measure how good it is — the deployed model uses
    # everything).
    final_model = lgb.LGBMRegressor(
        n_estimators=200,
        learning_rate=0.05,
        max_depth=4,
        num_leaves=15,
        min_child_samples=max(1, n // 10),
        verbose=-1,
    )
    final_model.fit(X, y)

    joblib.dump({"model": final_model, "feature_columns": FEATURE_COLUMNS}, MODEL_PATH)

    metadata = {
        "trained_at":  datetime.datetime.utcnow().isoformat(),
        "n_samples":   n,
        "n_baseline_samples": int((df["source"] == "baseline").sum()) if "source" in df.columns else 0,
        "n_live_samples":     int((df["source"] == "live").sum()) if "source" in df.columns else n,
        "cv_mae_days": cv_mae,
        "feature_columns": FEATURE_COLUMNS,
    }
    with open(METADATA_PATH, "w") as f:
        json.dump(metadata, f, indent=2)

    return {"trained": True, "n_samples": n, "cv_mae_days": cv_mae}


def load_predictor():
    """Load the trained model, or None if it doesn't exist yet."""
    if not MODEL_PATH.exists():
        return None
    try:
        bundle = joblib.load(MODEL_PATH)
        return bundle["model"], bundle["feature_columns"]
    except Exception:
        return None


def load_predictor_metadata():
    if not METADATA_PATH.exists():
        return None
    try:
        with open(METADATA_PATH) as f:
            return json.load(f)
    except Exception:
        return None


def predict_observed_days(feature_dict):
    """
    Predict how many days a cycle will take, given engineered features
    (same shape as one row from build_training_dataframe, minus the
    label). Returns None if no trained model exists yet — caller should
    fall back to the EMA baseline in that case.
    """
    loaded = load_predictor()
    if loaded is None:
        return None
    model, feature_columns = loaded

    row = pd.DataFrame([{col: feature_dict.get(col, np.nan) for col in feature_columns}])
    row = row.fillna(row.median(numeric_only=True)).fillna(0)
    pred = float(model.predict(row)[0])
    return round(pred, 1)


# ─────────────────────────────────────────────────────────────────────────
# 3. AUTO-RETRAIN TRIGGER
# ─────────────────────────────────────────────────────────────────────────

def _get_retrain_state(conn):
    try:
        row = conn.execute(
            "SELECT value FROM app_state WHERE key = ?", (RETRAIN_STATE_KEY,)
        ).fetchone()
        if row is None:
            return {"last_trained_sample_count": 0}
        return json.loads(row[0])
    except Exception:
        return {"last_trained_sample_count": 0}


def _save_retrain_state(conn, state):
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS app_state (key TEXT PRIMARY KEY, value TEXT)"
        )
    except Exception:
        pass
    conn.execute(
        "INSERT INTO app_state (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (RETRAIN_STATE_KEY, json.dumps(state))
    )
    conn.commit()


def maybe_retrain(username=None, conn=None, force=False):
    """
    Call this after every new harvest is recorded (e.g. right after
    learn_from_harvest_history() in planting.py). It only actually
    retrains when:
      - there's never been a trained model before, OR
      - at least RETRAIN_EVERY_N_NEW_SAMPLES new harvests have arrived
        since the last training run, OR
      - force=True (e.g. a manual "Retrain Now" button)

    This keeps retraining cheap: it won't refit the model on every
    single harvest once you have a lot of history, only every few.
    """
    if conn is None:
        conn = get_db_connection()
        close_conn = True
    else:
        close_conn = False

    try:
        df = build_training_dataframe(username=username, conn=conn)
        current_n = len(df)

        state = _get_retrain_state(conn)
        last_n = int(state.get("last_trained_sample_count", 0))

        should_retrain = force or (current_n - last_n) >= RETRAIN_EVERY_N_NEW_SAMPLES or (
            last_n == 0 and current_n >= MIN_SAMPLES_TO_TRAIN
        )

        if not should_retrain:
            return {
                "trained": False,
                "reason": f"Only {current_n - last_n} new sample(s) since last training "
                          f"(threshold is {RETRAIN_EVERY_N_NEW_SAMPLES}). Skipping retrain.",
                "n_samples": current_n,
            }

        result = train_harvest_predictor(df=df)
        if result.get("trained"):
            _save_retrain_state(conn, {"last_trained_sample_count": current_n})
        return result
    finally:
        if close_conn:
            conn.close()
