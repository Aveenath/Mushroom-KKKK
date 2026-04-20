import pandas as pd
import sqlite3

def run_pipeline():
    # 1. Load Data
    df = pd.read_csv('smartsense_readings.csv')
    
    # 2. Clean Data (Structural Standard)
    df.columns = ['id', 'device', 'co2', 'temp', 'humidity', 'ts', 'ip', 'created']
    df['ts'] = pd.to_datetime(df['ts'])
    
    # 2a. Data Quality Checks (Professional Standard)
    initial_len = len(df)
    
    # Remove duplicate sensor readings
    df = df.drop_duplicates()
    
    # Drop rows missing critical sensor data
    df = df.dropna(subset=['co2', 'temp', 'humidity', 'ts'])
    
    # Filter impossible/anomalous sensor readings 
    # (e.g., CO2 must be > 0, Temp in realistic range, Humidity 0-100)
    df = df[
        (df['co2'] > 0) & 
        (df['temp'] > -20) & (df['temp'] < 80) &
        (df['humidity'] >= 0) & (df['humidity'] <= 100)
    ]
    
    print(f"[{'SUCCESS'}] Data Cleaning Complete: Removed {initial_len - len(df)} bad rows.")
    
    # 3. Create SQL Database
    conn = sqlite3.connect('mushroom_client.db')
    df.to_sql('sensors', conn, if_exists='replace', index=False)
    
    # 4. Add Index (Speed for large data)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ts ON sensors(ts)")
    conn.close()
    print("[SUCCESS] Pipeline Complete: mushroom_client.db created.")

if __name__ == "__main__":
    run_pipeline()