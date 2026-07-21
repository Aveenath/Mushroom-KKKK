import pandas as pd
import numpy as np
import os
import joblib
import functools
from utils import get_db_connection, db_read_sql


def _add_temporal(df):
    df = df.copy()
    df['ts']           = pd.to_datetime(df['ts'])
    df['hour']         = df['ts'].dt.hour
    df['day_of_week']  = df['ts'].dt.dayofweek
    df['day_of_month'] = df['ts'].dt.day
    df['month']        = df['ts'].dt.month
    df['is_weekend']   = df['day_of_week'].isin([5, 6]).astype(int)
    df['hour_sin']     = np.sin(2 * np.pi * df['ts'].dt.hour / 24)
    df['hour_cos']     = np.cos(2 * np.pi * df['ts'].dt.hour / 24)
    df['dow_sin']      = np.sin(2 * np.pi * df['ts'].dt.dayofweek / 7)
    df['dow_cos']      = np.cos(2 * np.pi * df['ts'].dt.dayofweek / 7)
    df['month_sin']    = np.sin(2 * np.pi * df['ts'].dt.month / 12)
    df['month_cos']    = np.cos(2 * np.pi * df['ts'].dt.month / 12)
    return df


def _get_metrics(bundle):
    if 'metrics' in bundle:
        return bundle['metrics'].get('r2', 0.0), bundle['metrics'].get('mae', 0.0)
    return bundle.get('r2', 0.0), bundle.get('mae', 0.0)


@functools.lru_cache(maxsize=8)
def _load_model_cached(pkl_path):
    """
    Cache loaded model bundles in-process so repeated forecast runs (e.g. the
    user clicking 'Run AI Forecast' multiple times in a session) don't re-read
    and re-unpickle the .pkl files from disk every time.
    """
    return joblib.load(pkl_path)


def _load_model(target, df):
    pkl_path = f'model_{target}.pkl'
    if os.path.exists(pkl_path):
        return _load_model_cached(os.path.abspath(pkl_path))
    if target in df.columns:
        return _fallback_train(target, df)
    return None


def _fallback_train(target, df):
    from sklearn.ensemble import RandomForestRegressor
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import r2_score, mean_absolute_error
    from sklearn.feature_selection import SelectKBest, f_regression

    df = _add_temporal(df)
    features = ['hour', 'day_of_week', 'day_of_month', 'month', 'is_weekend',
                'hour_sin', 'hour_cos', 'dow_sin', 'dow_cos']
    features = [f for f in features if f in df.columns]

    subset = df[features + [target]].dropna()
    X, y   = subset[features], subset[target]
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, shuffle=False)

    model = RandomForestRegressor(n_estimators=100, random_state=42, n_jobs=-1)
    model.fit(X_train, y_train)

    selector = SelectKBest(f_regression, k=len(features))
    selector.fit(X_train, y_train)

    return {
        'model':             model,
        'selector':          selector,
        'selected_features': features,
        'safe_features':     features,
        'all_features':      features,
        'target':            target,
        'RPH':               1,
        'metrics': {
            'r2':  r2_score(y_test, model.predict(X_test)),
            'mae': mean_absolute_error(y_test, model.predict(X_test)),
        },
    }


# ─────────────────────────────────────────────────────────────────────
# FAST last-row-only feature engineering
#
# The original implementation ran pandas .rolling()/.shift() over the ENTIRE
# working history (up to RPH*168 rows) at every one of the 42 forecast steps,
# for each of the 3 targets — 126 full-dataframe recomputations just to read
# off a single trailing value. Rolling windows only ever look back 24h max,
# and lag features are just fixed-offset lookups, so all of this can be
# computed directly from numpy arrays in O(window) instead of O(n).
# This is the main fix for slow forecast generation.
# ─────────────────────────────────────────────────────────────────────
def _last_row_features(values, RPH):
    """
    values: dict of column -> 1D numpy array of the full working history
            (including the newly appended placeholder row).
    Returns a dict of engineered feature name -> value for just the last row,
    matching the column names produced by the original _engineer_on_rolling().
    """
    feats = {}
    for t in ('temp', 'humidity', 'co2'):
        if t not in values:
            continue
        arr = values[t]
        n = len(arr)

        def at(offset, arr=arr, n=n):
            idx = n - 1 - offset
            return arr[idx] if idx >= 0 else np.nan

        for label, shift in [('1h', RPH), ('2h', RPH * 2), ('3h', RPH * 3),
                              ('6h', RPH * 6), ('12h', RPH * 12), ('1d', RPH * 24),
                              ('2d', RPH * 48), ('3d', RPH * 72), ('7d', RPH * 168)]:
            feats[f'{t}_lag_{label}'] = at(shift)

        for label, w in [('1h', RPH), ('3h', RPH * 3), ('6h', RPH * 6),
                          ('12h', RPH * 12), ('24h', RPH * 24)]:
            window = arr[max(0, n - w):n]
            feats[f'{t}_rolling_{label}']     = window.mean()
            feats[f'{t}_rolling_std_{label}'] = window.std(ddof=1) if len(window) > 1 else np.nan
            feats[f'{t}_rolling_min_{label}'] = window.min()
            feats[f'{t}_rolling_max_{label}'] = window.max()

        cur = arr[-1]
        feats[f'{t}_diff_1h'] = cur - at(RPH)
        feats[f'{t}_diff_6h'] = cur - at(RPH * 6)
        feats[f'{t}_diff_1d'] = cur - at(RPH * 24)
        d1 = cur - at(RPH)
        d2 = at(RPH) - at(RPH * 2)
        feats[f'{t}_accel'] = d1 - d2

    tv = values.get('temp')
    hv = values.get('humidity')
    cv = values.get('co2')
    if tv is not None and hv is not None:
        t_, h_ = tv[-1], hv[-1]
        feats['heat_index']          = t_ * h_ / 100
        feats['temp_humidity_ratio'] = t_ / (h_ + 1)
        feats['vpd'] = (1 - h_ / 100) * 0.6108 * np.exp(17.27 * t_ / (t_ + 237.3))
    if tv is not None and cv is not None:
        feats['temp_co2_interaction'] = tv[-1] * cv[-1] / 1000
    if hv is not None and cv is not None:
        feats['humidity_co2_ratio'] = hv[-1] / (cv[-1] + 1) * 100

    return feats


def _predict_future(bundle, history_df, hours=42, step_hours=4):
    model             = bundle['model']
    safe_features     = bundle['safe_features']
    selected_features = bundle['selected_features']
    RPH               = bundle.get('RPH', 1)
    target            = bundle['target']

    max_lag_rows = RPH * 168 + 10
    working = history_df[['ts', 'temp', 'humidity', 'co2']].tail(max_lag_rows).reset_index(drop=True)
    working = _add_temporal(working)
    last_ts = working['ts'].max()

    clamp = {}
    for col in ['temp', 'humidity', 'co2']:
        if col not in history_df.columns:
            continue
        lo = history_df[col].quantile(0.02)
        hi = history_df[col].quantile(0.98)
        if col == 'humidity':
            hi = min(hi, 95.0)
        clamp[col] = (lo, hi)

    target_std = {}
    for col in ('temp', 'humidity', 'co2'):
        if col in history_df.columns:
            target_std[col] = history_df[col].std() * 0.05

    # Plain numpy buffers, extended in place each step — avoids the repeated
    # pd.concat()+tail() full-frame copy the original did on every iteration.
    values = {c: working[c].to_numpy(dtype=float).copy() for c in ('temp', 'humidity', 'co2')}

    predictions = []
    for h in range(1, hours + 1):
        next_ts = last_ts + pd.Timedelta(hours=h * step_hours)
        row_t = _add_temporal(pd.DataFrame({'ts': [next_ts]})).iloc[0]

        # carry the last known value forward for all 3 series (same behavior
        # as the original: non-target series stay flat, target gets predicted)
        for c in ('temp', 'humidity', 'co2'):
            values[c] = np.append(values[c], values[c][-1])

        feats = _last_row_features(values, RPH)
        for tc in ('hour', 'day_of_week', 'day_of_month', 'month', 'is_weekend',
                   'hour_sin', 'hour_cos', 'dow_sin', 'dow_cos', 'month_sin', 'month_cos'):
            feats[tc] = row_t[tc]

        for m in safe_features:
            if m not in feats:
                feats[m] = 0.0

        try:
            X_input = np.array([[feats[f] for f in selected_features]])
            pred = model.predict(X_input)[0]
        except Exception:
            pred = values[target][-1 - RPH:-1].mean()

        if target in clamp:
            lo, hi = clamp[target]
            pred = float(np.clip(pred, lo, hi))

        if target in target_std and target_std[target] > 0:
            pred += float(np.random.normal(0, target_std[target]))
            if target in clamp:
                pred = float(np.clip(pred, clamp[target][0], clamp[target][1]))

        predictions.append(pred)
        values[target][-1] = pred

        # keep buffers bounded, same as the original tail(max_lag_rows)
        for c in ('temp', 'humidity', 'co2'):
            if len(values[c]) > max_lag_rows:
                values[c] = values[c][-max_lag_rows:]

    return np.array(predictions)


# ─────────────────────────────────────────────────────────────────────
# PUBLIC API (unchanged signatures — drop-in replacement)
# ─────────────────────────────────────────────────────────────────────

def get_predictions(df=None):
    """Temperature-only forecast — backward compatible with monitor.py."""
    if df is None:
        conn = get_db_connection()
        df = db_read_sql("SELECT ts, temp, humidity, co2 FROM sensors", conn)
        conn.close()

    bundle  = _load_model('temp', df)
    r2, mae = _get_metrics(bundle)
    preds   = _predict_future(bundle, df, hours=168)
    return preds, r2, mae


def get_predictions_multi(df=None):
    if df is None:
        conn = get_db_connection()
        df = db_read_sql("SELECT ts, temp, humidity, co2 FROM sensors", conn)
        conn.close()

    for col in ('temp', 'humidity', 'co2'):
        if col in df.columns:
            df[col] = df[col].astype(float)

    result = {}
    for target in ('temp', 'humidity', 'co2'):
        bundle = _load_model(target, df)
        if bundle is None:
            continue

        r2, mae = _get_metrics(bundle)
        preds   = _predict_future(bundle, df, hours=42, step_hours=4)
        result[target] = {'predictions': preds, 'r2': r2, 'mae': mae}

    return result


def predict_harvest_date(plant_date_str):
    import datetime
    plant_date    = datetime.datetime.strptime(plant_date_str, "%Y-%m-%d").date()
    early_harvest = plant_date + datetime.timedelta(days=21)
    late_harvest  = plant_date + datetime.timedelta(days=28)
    return (f"{early_harvest.strftime('%b %d, %Y')} "
            f"to {late_harvest.strftime('%b %d, %Y')}")