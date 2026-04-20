import pandas as pd
from sklearn.ensemble import RandomForestRegressor
import sqlite3

def get_predictions(df=None):
    if df is None:
        conn = sqlite3.connect('mushroom_client.db')
        df = pd.read_sql("SELECT ts, temp FROM sensors", conn)
        conn.close()
        
    df['ts'] = pd.to_datetime(df['ts'])
    df['hour'] = df['ts'].dt.hour
    
    # Train AI Model
    model = RandomForestRegressor(n_estimators=50).fit(df[['hour']], df['temp'])
    
    # Predict next 7 days (168 hours)
    last_hour = df['ts'].max().hour
    future = pd.DataFrame({'hour': [(last_hour + i) % 24 for i in range(1, 169)]})
    preds = model.predict(future)
    return preds

def predict_harvest_date(plant_date_str):
    import datetime
    plant_date = datetime.datetime.strptime(plant_date_str, "%Y-%m-%d").date()
    early_harvest = plant_date + datetime.timedelta(days=21)
    late_harvest = plant_date + datetime.timedelta(days=28)
    
    return f"{early_harvest.strftime('%b %d, %Y')} to {late_harvest.strftime('%b %d, %Y')}"