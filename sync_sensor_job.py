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


def query_turso(sql):
    raw_url    = os.environ["TURSO_DATABASE_URL"]
    auth_token = os.environ["TURSO_AUTH_TOKEN"]
    base_url   = raw_url.replace("libsql://", "https://").rstrip("/")
    endpoint   = f"{base_url}/v2/pipeline"
    payload = {"requests": [{"type": "execute", "stmt": {"sql": sql}}, {"type": "close"}]}
    headers = {"Authorization": f"Bearer {auth_token}", "Content-Type": "application/json"}
    resp    = requests.post(endpoint, json=payload, headers=headers, timeout=15)
    if not resp.ok:
        print(f"Turso error {resp.status_code}: {resp.text[:300]}")
        resp.raise_for_status()
    result = resp.json()["results"][0]["response"]["result"]
    cols   = [c["name"] for c in result["cols"]]
    return [dict(zip(cols, [v["value"] for v in row])) for row in result["rows"]]


def _esc(v):
    """Safely escape a value for direct SQL — data comes from our own API, not user input."""
    if v is None or str(v).strip() == "":
        return "NULL"
    return "'" + str(v).replace("'", "''") + "'"


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

    today = datetime.date.today().isoformat()
    existing = query_turso(
        f"SELECT id FROM sensors WHERE ts >= '{today} 00:00:00'"
    )
    existing_ids = set()
    for r in existing:
        try:
            existing_ids.add(str(int(float(str(r["id"])))))
        except (ValueError, TypeError):
            pass

    inserted, failed = 0, 0
    for r in rows:
        try:
            row_id = str(int(float(str(r["id"]))))
        except (ValueError, TypeError):
            continue
        if row_id in existing_ids:
            continue
        try:
            query_turso(
                f"INSERT OR IGNORE INTO sensors (id, device, co2, temp, humidity, ts, ip, created) "
                f"VALUES ({_esc(r['id'])}, {_esc(r['device'])}, {_esc(r['co2'])}, {_esc(r['temp'])}, "
                f"{_esc(r['humidity'])}, {_esc(r['ts'])}, {_esc(r['ip'])}, {_esc(r['created'])})"
            )
            inserted += 1
        except Exception as e:
            print(f"Skip row {r.get('id')}: {e}")
            failed += 1

    print(f"Inserted {inserted} new row(s) into Turso. Failed: {failed}.")

    # ── Archive: delete sensor rows older than 90 days ─────────────────────────
    cutoff = (datetime.date.today() - datetime.timedelta(days=90)).isoformat()
    try:
        query_turso(f"DELETE FROM sensors WHERE ts < '{cutoff} 00:00:00'")
        print(f"Archived: deleted sensor rows older than {cutoff}.")
    except Exception as e:
        print(f"Archive step failed: {e}")


if __name__ == "__main__":
    main()
