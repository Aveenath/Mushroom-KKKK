import pandas as pd
import sqlite3

def run_pipeline():
    # 1. Load Data
    df = pd.read_csv('smartsense_readings.csv')
    
    # 2. Clean Data (Professional Standard)
    df.columns = ['id', 'device', 'co2', 'temp', 'humidity', 'ts', 'ip', 'created']
    df['ts'] = pd.to_datetime(df['ts'])
    
    # 3. Create SQL Database
    conn = sqlite3.connect('mushroom_client.db')
    df.to_sql('sensors', conn, if_exists='replace', index=False)
    
    # 4. Add Index (Speed for large data)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ts ON sensors(ts)")
    conn.close()
    print("✅ Pipeline Complete: mushroom_client.db created.")

if __name__ == "__main__":
    run_pipeline()