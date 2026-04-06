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
    
    # Predict next 12 hours
    last_hour = df['ts'].max().hour
    future = pd.DataFrame({'hour': [(last_hour + i) % 24 for i in range(1, 13)]})
    preds = model.predict(future)
    
    return preds