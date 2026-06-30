"""
Shared Turso helpers for the standalone GitHub Actions scripts
(mist_alert.py, sync_sensor_job.py). One copy, so a fix here fixes both.
"""
import os
import datetime
import requests


def _esc(v):
    """Safely escape a value for direct SQL — data comes from our own API, not user input."""
    if v is None or str(v).strip() == "":
        return "NULL"
    return "'" + str(v).replace("'", "''") + "'"


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
    return [dict(zip(cols, [v.get("value") for v in row])) for row in result["rows"]]


# ── Shared alert/status state (used to remember things between runs) ───────────

def init_state_table():
    query_turso(
        "CREATE TABLE IF NOT EXISTS alert_state "
        "(alert_type TEXT PRIMARY KEY, last_status TEXT, last_sent TEXT)"
    )


def get_state(alert_type):
    rows = query_turso(
        f"SELECT last_status, last_sent FROM alert_state WHERE alert_type = {_esc(alert_type)}"
    )
    return rows[0] if rows else None


def set_state(alert_type, status):
    now_str = datetime.datetime.utcnow().isoformat()
    query_turso(
        f"INSERT INTO alert_state (alert_type, last_status, last_sent) VALUES ({_esc(alert_type)}, {_esc(status)}, {_esc(now_str)}) "
        f"ON CONFLICT(alert_type) DO UPDATE SET last_status = excluded.last_status, last_sent = excluded.last_sent"
    )
