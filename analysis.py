import pandas as pd
import os
import sqlite3
import joblib


def _prepare_df(df):
    df = df.copy()
    df['ts'] = pd.to_datetime(df['ts'])
    df['hour']         = df['ts'].dt.hour
    df['day_of_week']  = df['ts'].dt.dayofweek
    df['day_of_month'] = df['ts'].dt.day
    df['is_weekend']   = df['day_of_week'].isin([5, 6]).astype(int)
    return df


def _load_or_train(target, df, features):
    pkl_path = f'model_{target}.pkl'
    if os.path.exists(pkl_path):
        return joblib.load(pkl_path)

    # Fallback: train from scratch if .pkl not found
    from sklearn.ensemble import RandomForestRegressor
    from sklearn.feature_selection import SelectKBest, f_regression, RFE
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import r2_score, mean_absolute_error

    subset = df[features + [target]].dropna()
    X = subset[features]
    y = subset[target]

    filter_sel = SelectKBest(score_func=f_regression, k=min(3, len(features)))
    X_filtered = filter_sel.fit_transform(X, y)
    filtered_feats = [features[i] for i in range(len(features)) if filter_sel.get_support()[i]]

    rfe_sel = RFE(
        estimator=RandomForestRegressor(n_estimators=20, random_state=42),
        n_features_to_select=min(2, len(filtered_feats))
    )
    X_final = rfe_sel.fit_transform(X_filtered, y)
    final_feats = [filtered_feats[i] for i in range(len(filtered_feats)) if rfe_sel.get_support()[i]]

    X_train, X_test, y_train, y_test = train_test_split(X_final, y, test_size=0.2, random_state=42)
    model = RandomForestRegressor(n_estimators=50, random_state=42)
    model.fit(X_train, y_train)

    return {
        'model': model,
        'filter_selector': filter_sel,
        'rfe_selector': rfe_sel,
        'features': features,
        'final_features': final_feats,
        'r2': r2_score(y_test, model.predict(X_test)),
        'mae': mean_absolute_error(y_test, model.predict(X_test))
    }


def _predict_future(bundle, last_ts, hours=168):
    features   = bundle['features']
    filter_sel = bundle['filter_selector']
    rfe_sel    = bundle['rfe_selector']
    model      = bundle['model']

    future_times = [last_ts + pd.Timedelta(hours=i) for i in range(1, hours + 1)]
    future = pd.DataFrame({'ts': future_times})
    future['hour']         = future['ts'].dt.hour
    future['day_of_week']  = future['ts'].dt.dayofweek
    future['day_of_month'] = future['ts'].dt.day
    future['is_weekend']   = future['day_of_week'].isin([5, 6]).astype(int)

    X_f     = filter_sel.transform(future[features])
    X_final = rfe_sel.transform(X_f)
    return model.predict(X_final)


def get_predictions(df=None):
    """Temperature-only forecast — keeps monitor.py working unchanged."""
    if df is None:
        conn = sqlite3.connect('mushroom_client.db')
        df = pd.read_sql("SELECT ts, temp FROM sensors", conn)
        conn.close()

    features = ['hour', 'day_of_week', 'day_of_month', 'is_weekend']
    df = _prepare_df(df)
    bundle = _load_or_train('temp', df, features)
    preds  = _predict_future(bundle, df['ts'].max())
    return preds, bundle['r2'], bundle['mae']


def get_predictions_multi(df=None):
    """Forecast for temp, humidity, and co2 — used by expanded monitor charts."""
    if df is None:
        conn = sqlite3.connect('mushroom_client.db')
        df = pd.read_sql("SELECT ts, temp, humidity, co2 FROM sensors", conn)
        conn.close()

    features = ['hour', 'day_of_week', 'day_of_month', 'is_weekend']
    df = _prepare_df(df)
    last_ts = df['ts'].max()

    result = {}
    for target in ['temp', 'humidity', 'co2']:
        pkl_path = f'model_{target}.pkl'
        if os.path.exists(pkl_path):
            # Pre-trained model exists — load directly, only needs last_ts
            bundle = joblib.load(pkl_path)
            result[target] = {
                'predictions': _predict_future(bundle, last_ts),
                'r2': bundle['r2'],
                'mae': bundle['mae']
            }
        elif target in df.columns:
            # No pkl — train from scratch using df columns
            bundle = _load_or_train(target, df, features)
            result[target] = {
                'predictions': _predict_future(bundle, last_ts),
                'r2': bundle['r2'],
                'mae': bundle['mae']
            }
    return result


def predict_harvest_date(plant_date_str):
    import datetime
    plant_date   = datetime.datetime.strptime(plant_date_str, "%Y-%m-%d").date()
    early_harvest = plant_date + datetime.timedelta(days=21)
    late_harvest  = plant_date + datetime.timedelta(days=28)
    return f"{early_harvest.strftime('%b %d, %Y')} to {late_harvest.strftime('%b %d, %Y')}"
