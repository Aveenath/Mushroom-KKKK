"""
Standalone sensor sync job for GitHub Actions.
Fetches today's readings from SmartSense and inserts new rows into Turso.
Runs independently — no Streamlit app needs to be open.
"""
import os
import csv
import requests
import datetime
from io import StringIO
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent / ".env")

LOGIN_URL = "https://didikhub.com/smartsense/auth/login.php"
DATA_URL  = "https://didikhub.com/smartsense/pages/data.php"

COLUMN_MAP = {
    "ID": "id", "Device": "device", "CO2 ppm": "co2",
    "Temp C": "temp", "RH %": "humidity",
    "Timestamp UTC": "ts", "IP Client": "ip", "Created At": "created",
}


def _turso_arg(value):
    if value is None:
        return {"type": "null"}
    try:
        return {"type": "integer", "value": int(value)}
    except (ValueError, TypeError):
        pass
    try:
        return {"type": "float", "value": float(value)}
    except (ValueError, TypeError):
        pass
    return {"type": "text", "value": str(value)}


def query_turso(sql, params=None):
    raw_url    = os.environ["TURSO_DATABASE_URL"]
    auth_token = os.environ["TURSO_AUTH_TOKEN"]
    base_url   = raw_url.replace("libsql://", "https://").rstrip("/")
    endpoint   = f"{base_url}/v2/pipeline"
    stmt       = {"sql": sql}
    if params:
        stmt["args"] = [_turso_arg(p) for p in params]
    payload = {"requests": [{"type": "execute", "stmt": stmt}, {"type": "close"}]}
    headers = {"Authorization": f"Bearer {auth_token}", "Content-Type": "application/json"}
    resp    = requests.post(endpoint, json=payload, headers=headers, timeout=15)
    resp.raise_for_status()
    result = resp.json()["results"][0]["response"]["result"]
    cols   = [c["name"] for c in result["cols"]]
    return [dict(zip(cols, [v["value"] for v in row])) for row in result["rows"]]


def fetch_smartsense():
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})
    session.post(LOGIN_URL, data={
        "username": os.environ["DIDIKHUB_USERNAME"],
        "password": os.environ["DIDIKHUB_PASSWORD"],
    }, timeout=15)

    today  = datetime.date.today().isoformat()
    params = {"device_id": 1, "date_from": today, "date_to": today, "export": "csv"}
    resp   = session.get(DATA_URL, params=params, timeout=15)
    resp.raise_for_status()

    reader = csv.DictReader(StringIO(resp.text))
    rows   = []
    for r in reader:
        try:
            rows.append({
                "id":       r.get("ID", "").strip(),
                "device":   r.get("Device", "").strip(),
                "co2":      r.get("CO2 ppm", "").strip(),
                "temp":     r.get("Temp C", "").strip(),
                "humidity": r.get("RH %", "").strip(),
                "ts":       r.get("Timestamp UTC", "").strip(),
                "ip":       r.get("IP Client", "").strip(),
                "created":  r.get("Created At", "").strip(),
            })
        except Exception:
            pass
    return rows


def main():
    print(f"Sync started at {datetime.datetime.utcnow().isoformat()} UTC")

    rows = fetch_smartsense()
    print(f"Fetched {len(rows)} row(s) from SmartSense.")
    if not rows:
        print("No data returned.")
        return

    existing = query_turso("SELECT id FROM sensors")
    existing_ids = {str(r["id"]) for r in existing}

    inserted = 0
    for r in rows:
        if str(r["id"]) in existing_ids:
            continue
        query_turso(
            "INSERT INTO sensors (id, device, co2, temp, humidity, ts, ip, created) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [r["id"], r["device"], r["co2"], r["temp"], r["humidity"], r["ts"], r["ip"], r["created"]]
        )
        inserted += 1

    print(f"Inserted {inserted} new row(s) into Turso.")


if __name__ == "__main__":
    main()
