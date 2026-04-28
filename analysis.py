import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import r2_score, mean_absolute_error
from sklearn.feature_selection import SelectKBest, f_regression, RFE
import sqlite3

def get_predictions(df=None):
    if df is None:
        conn = sqlite3.connect('mushroom_client.db')
        df = pd.read_sql("SELECT ts, temp FROM sensors", conn)
        conn.close()
        
    df['ts'] = pd.to_datetime(df['ts'])
    
    # --- 1. FEATURE ENGINEERING ---
    df['hour'] = df['ts'].dt.hour
    df['day_of_week'] = df['ts'].dt.dayofweek
    df['day_of_month'] = df['ts'].dt.day
    df['is_weekend'] = df['day_of_week'].isin([5, 6]).astype(int)
    
    features = ['hour', 'day_of_week', 'day_of_month', 'is_weekend']
    X = df[features]
    y = df['temp']
    
    # --- 2. HYBRID FEATURE SELECTION ---
    # Phase 1: Filter Method (Select K Best - Keep top 3)
    filter_selector = SelectKBest(score_func=f_regression, k=3)
    X_filtered = filter_selector.fit_transform(X, y)
    
    # Get the names of the features that survived the filter
    filter_support = filter_selector.get_support()
    filtered_features = [features[i] for i in range(len(features)) if filter_support[i]]
    
    # Phase 2: Wrapper Method (RFE with Random Forest - Keep top 2)
    rf_estimator = RandomForestRegressor(n_estimators=20, random_state=42)
    wrapper_selector = RFE(estimator=rf_estimator, n_features_to_select=2)
    X_final = wrapper_selector.fit_transform(X_filtered, y)
    
    # Get the names of the completely final selected features
    wrapper_support = wrapper_selector.get_support()
    final_selected_features = [filtered_features[i] for i in range(len(filtered_features)) if wrapper_support[i]]
    
    # --- 3. TRAIN FINAL MODEL ---
    model = RandomForestRegressor(n_estimators=50, random_state=42).fit(X_final, y)
    
    # Evaluate Model Performance on Training Data
    train_preds = model.predict(X_final)
    r2 = r2_score(y, train_preds)
    mae = mean_absolute_error(y, train_preds)
    
    # --- 4. PREDICT NEXT 7 DAYS (168 HOURS) ---
    last_ts = df['ts'].max()
    future_times = [last_ts + pd.Timedelta(hours=i) for i in range(1, 169)]
    future = pd.DataFrame({'ts': future_times})
    
    # Apply the same Feature Engineering to the future data
    future['hour'] = future['ts'].dt.hour
    future['day_of_week'] = future['ts'].dt.dayofweek
    future['day_of_month'] = future['ts'].dt.day
    future['is_weekend'] = future['day_of_week'].isin([5, 6]).astype(int)
    
    # Select only the features that survived the Hybrid Selection
    future_X_final = future[final_selected_features]
    preds = model.predict(future_X_final)
    
    return preds, r2, mae

def predict_harvest_date(plant_date_str):
    import datetime
    plant_date = datetime.datetime.strptime(plant_date_str, "%Y-%m-%d").date()
    early_harvest = plant_date + datetime.timedelta(days=21)
    late_harvest = plant_date + datetime.timedelta(days=28)
    
    return f"{early_harvest.strftime('%b %d, %Y')} to {late_harvest.strftime('%b %d, %Y')}"