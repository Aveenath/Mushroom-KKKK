import os
import requests
import pandas as pd
from io import StringIO
import datetime
from dotenv import load_dotenv
from utils import get_db_connection, db_read_sql, db_to_sql

load_dotenv()

LOGIN_URL = "https://didikhub.com/smartsense/auth/login.php"
DATA_URL = "https://didikhub.com/smartsense/pages/data.php"

CREDENTIALS = {
    "username": os.getenv("DIDIKHUB_USERNAME"),
    "password": os.getenv("DIDIKHUB_PASSWORD")
}

COLUMN_MAP = {
    "ID": "id",
    "Device": "device",
    "CO2 ppm": "co2",
    "Temp C": "temp",
    "RH %": "humidity",
    "Timestamp UTC": "ts",
    "IP Client": "ip",
    "Created At": "created",
}


def fetch_live_data(device_id=1, date_from=None, date_to=None):
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})
    session.post(LOGIN_URL, data=CREDENTIALS, timeout=15)

    if date_to is None:
        date_to = datetime.date.today().isoformat()
    if date_from is None:
        date_from = date_to

    params = {"device_id": device_id, "date_from": date_from, "date_to": date_to, "export": "csv"}
    csv_resp = session.get(DATA_URL, params=params, timeout=15)

    df = pd.read_csv(StringIO(csv_resp.text))
    df = df.rename(columns=COLUMN_MAP)
    df = df[["id", "device", "co2", "temp", "humidity", "ts", "ip", "created"]]
    return df


def sync_to_db(df):
    conn = get_db_connection()
    existing_ids = set(db_read_sql("SELECT id FROM sensors", conn)["id"])
    new_rows = df[~df["id"].isin(existing_ids)]

    if not new_rows.empty:
        db_to_sql(new_rows, "sensors", conn, if_exists="append")

    conn.close()
    return len(new_rows)


if __name__ == "__main__":
    df = fetch_live_data()
    print(f"Fetched {len(df)} row(s) from didikhub for today.")
    count = sync_to_db(df)
    print(f"Inserted {count} new row(s).")
