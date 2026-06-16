import pandas as pd
import numpy as np
import os
import joblib
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


def _engineer_on_rolling(df, RPH):
    df = df.copy()
    for t in ['temp', 'humidity', 'co2']:
        if t not in df.columns:
            continue
        for label, shift in [('1h',  RPH),    ('2h',  RPH*2),
                              ('3h',  RPH*3),  ('6h',  RPH*6),
                              ('12h', RPH*12), ('1d',  RPH*24),
                              ('2d',  RPH*48), ('3d',  RPH*72),
                              ('7d',  RPH*168)]:
            df[f'{t}_lag_{label}'] = df[t].shift(shift)

        for label, w in [('1h',  RPH),   ('3h',  RPH*3),
                         ('6h',  RPH*6), ('12h', RPH*12), ('24h', RPH*24)]:
            df[f'{t}_rolling_{label}']     = df[t].rolling(w, min_periods=1).mean()
            df[f'{t}_rolling_std_{label}'] = df[t].rolling(w, min_periods=1).std()
            df[f'{t}_rolling_min_{label}'] = df[t].rolling(w, min_periods=1).min()
            df[f'{t}_rolling_max_{label}'] = df[t].rolling(w, min_periods=1).max()

        df[f'{t}_diff_1h'] = df[t].diff(RPH)
        df[f'{t}_diff_6h'] = df[t].diff(RPH * 6)
        df[f'{t}_diff_1d'] = df[t].diff(RPH * 24)
        df[f'{t}_accel']   = df[t].diff(RPH).diff(RPH)

    if {'temp', 'humidity'}.issubset(df.columns):
        df['heat_index']          = df['temp'] * df['humidity'] / 100
        df['temp_humidity_ratio'] = df['temp'] / (df['humidity'] + 1)
        df['vpd'] = (1 - df['humidity'] / 100) * 0.6108 * np.exp(
                        17.27 * df['temp'] / (df['temp'] + 237.3))

    if {'temp', 'co2'}.issubset(df.columns):
        df['temp_co2_interaction'] = df['temp'] * df['co2'] / 1000

    if {'humidity', 'co2'}.issubset(df.columns):
        df['humidity_co2_ratio'] = df['humidity'] / (df['co2'] + 1) * 100

    return df


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


def _predict_future(bundle, history_df, hours=42, step_hours=4):
    model             = bundle['model']
    selector          = bundle['selector']
    safe_features     = bundle['safe_features']
    selected_features = bundle['selected_features']
    RPH               = bundle.get('RPH', 1)
    target            = bundle['target']

    max_lag_rows = RPH * 168 + 10
    working = history_df[['ts', 'temp', 'humidity', 'co2']].copy()
    working = working.tail(max_lag_rows).reset_index(drop=True)
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
    for col in ["temp", "humidity", "co2"]:
        if col in history_df.columns:
            target_std[col] = history_df[col].std() * 0.05

    predictions = []
    for h in range(1, hours + 1):
        next_ts = last_ts + pd.Timedelta(hours=h * step_hours)

        row = pd.DataFrame({'ts': [next_ts]})
        row = _add_temporal(row)
        for col in ['temp', 'humidity', 'co2']:
            row[col] = working[col].iloc[-1]

        extended = pd.concat([working, row], ignore_index=True)
        engineered = _engineer_on_rolling(extended, RPH)

        if engineered.empty:
            predictions.append(np.nan)
            continue

        last_row = engineered.iloc[[-1]].copy()
        for m in [f for f in safe_features if f not in last_row.columns]:
            last_row[m] = 0.0

        try:
            X_input = last_row[selected_features].values
            pred    = model.predict(X_input)[0]
        except Exception:
            pred = working[target].iloc[-RPH:].mean()

        if target in clamp:
            lo, hi = clamp[target]
            pred = float(np.clip(pred, lo, hi))

        if target in target_std and target_std[target] > 0:
            pred += float(np.random.normal(0, target_std[target]))
            if target in clamp:
                pred = float(np.clip(pred, clamp[target][0], clamp[target][1]))

        predictions.append(pred)
        extended.at[extended.index[-1], target] = pred
        working = extended.tail(max_lag_rows).reset_index(drop=True)

    return np.array(predictions)


# ─────────────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────────────

def get_predictions(df=None):
    """Temperature-only forecast — backward compatible with monitor.py."""
    if df is None:
        conn = get_db_connection()
        # FIX: use db_read_sql instead of pd.read_sql_query
        df = db_read_sql("SELECT ts, temp, humidity, co2 FROM sensors", conn)
        conn.close()

    bundle  = joblib.load('model_temp.pkl') if os.path.exists('model_temp.pkl') \
              else _fallback_train('temp', df)
    r2, mae = _get_metrics(bundle)
    preds   = _predict_future(bundle, df, hours=168)
    return preds, r2, mae


def get_predictions_multi(df=None):
    if df is None:
        conn = get_db_connection()
        # FIX: use db_read_sql instead of pd.read_sql_query
        df = db_read_sql("SELECT ts, temp, humidity, co2 FROM sensors", conn)
        conn.close()

    for col in ['temp', 'humidity', 'co2']:
        if col in df.columns:
            df[col] = df[col].astype(float)

    result = {}
    for target in ['temp', 'humidity', 'co2']:
        pkl_path = f'model_{target}.pkl'
        bundle   = joblib.load(pkl_path) if os.path.exists(pkl_path) \
                   else (_fallback_train(target, df) if target in df.columns else None)
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