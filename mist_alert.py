"""
Background alert script — run by GitHub Actions every 30 minutes.
Sends: misting alerts, harvest-due alerts, stale data warnings.
Throttled: same alert won't repeat until condition resets or 2 hours pass.
"""
import os
import datetime
import requests
from pathlib import Path
from dotenv import load_dotenv
from groq import Groq

DATA_STALE_HOURS  = 1.0  # warn if sensor data older than 1 hour
REMIND_HOURS      = 2    # re-send same alert after this many hours

load_dotenv(dotenv_path=Path(__file__).parent / ".env")


# ── Turso helpers ──────────────────────────────────────────────────────────────

def _turso_arg(value):
    if value is None:
        return {"type": "null"}
    if isinstance(value, int):
        return {"type": "integer", "value": value}
    if isinstance(value, float):
        return {"type": "float", "value": value}
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


def execute_turso(sql, params=None):
    query_turso(sql, params)


# ── Telegram ───────────────────────────────────────────────────────────────────

def send_telegram(message):
    token   = os.environ["TELEGRAM_BOT_TOKEN"].strip()
    chat_id = os.environ["TELEGRAM_CHAT_ID"].strip()
    url     = f"https://api.telegram.org/bot{token}/sendMessage"
    resp    = requests.post(url, data={"chat_id": chat_id, "text": message, "parse_mode": "HTML"}, timeout=10)
    print(f"Telegram response: {resp.status_code} {resp.text}")


# ── Groq ───────────────────────────────────────────────────────────────────────

def ask_groq(temp, humidity, co2):
    client = Groq(api_key=os.environ["GROQ_API_KEY"])
    prompt = f"""You are an oyster mushroom farm controller.
Current readings:
- Temperature : {temp}°C  (optimal: 25–28°C, max: 30°C)
- Humidity    : {humidity}%  (optimal: 80–90%, min: 80%)
- CO2         : {co2} ppm  (max: 800 ppm)

Rules:
- humidity < 80%: MIST ON
- humidity > 90%: MIST OFF
- temp > 30°C: MIST ON
- Otherwise: no action needed

Reply in exactly 2 lines:
ACTION: MIST ON or MIST OFF
REASON: one short sentence why"""
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
        max_tokens=80,
    )
    return response.choices[0].message.content.strip()


# ── Alert state (throttle) ─────────────────────────────────────────────────────

def _init_alert_table():
    execute_turso(
        "CREATE TABLE IF NOT EXISTS alert_state "
        "(alert_type TEXT PRIMARY KEY, last_status TEXT, last_sent TEXT)"
    )


def _get_state(alert_type):
    rows = query_turso(
        "SELECT last_status, last_sent FROM alert_state WHERE alert_type = ?",
        [alert_type]
    )
    return rows[0] if rows else None


def _set_state(alert_type, status):
    now_str = datetime.datetime.utcnow().isoformat()
    execute_turso(
        "INSERT INTO alert_state (alert_type, last_status, last_sent) VALUES (?, ?, ?) "
        "ON CONFLICT(alert_type) DO UPDATE SET last_status = excluded.last_status, last_sent = excluded.last_sent",
        [alert_type, status, now_str]
    )


def _should_send(current_status, state):
    """
    Returns (send: bool, reason: str).
    Sends when: no prior alert, status changed, or REMIND_HOURS passed with same status.
    """
    if state is None:
        return True, "first alert"
    if state["last_status"] != current_status:
        return True, "condition changed"
    hours_ago = 0.0
    try:
        last_dt   = datetime.datetime.fromisoformat(str(state["last_sent"]))
        hours_ago = (datetime.datetime.utcnow() - last_dt).total_seconds() / 3600
        if hours_ago >= REMIND_HOURS:
            return True, f"reminder ({hours_ago:.1f}h since last alert)"
    except Exception:
        return True, "could not parse last_sent"
    return False, f"throttled — same status '{current_status}' sent {hours_ago:.1f}h ago"


# ── Harvest due check ──────────────────────────────────────────────────────────

def _get_harvest_due():
    rows  = query_turso(
        "SELECT block_id, planted_date, harvest_count, last_harvest_date "
        "FROM planting_records WHERE retired = 0 OR retired IS NULL"
    )
    today    = datetime.date.today()
    due_today, overdue = [], []
    for r in rows:
        try:
            hc  = int(r.get("harvest_count") or 0)
            lhd = r.get("last_harvest_date")
            if hc == 0:
                next_date = datetime.date.fromisoformat(r["planted_date"]) + datetime.timedelta(days=14)
            else:
                next_date = datetime.date.fromisoformat(lhd) + datetime.timedelta(days=15)
            diff = (today - next_date).days
            if diff == 0:
                due_today.append((r["block_id"], next_date))
            elif diff > 0:
                overdue.append((r["block_id"], next_date, diff))
        except Exception:
            pass
    return due_today, overdue


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    _init_alert_table()
    now_myt = (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).strftime("%Y-%m-%d %H:%M")

    # ── 1. Sensor freshness ────────────────────────────────────────────────────
    rows = query_turso("SELECT temp, humidity, co2, ts FROM sensors ORDER BY ts DESC LIMIT 1")
    if not rows:
        print("No sensor data found.")
        return

    row      = rows[0]
    temp     = float(row["temp"])
    humidity = float(row["humidity"])
    co2      = row["co2"]
    ts       = row["ts"]
    print(f"Latest: Temp={temp}°C  Humidity={humidity}%  CO2={co2}ppm  @ {ts}")

    try:
        ts_dt     = datetime.datetime.fromisoformat(str(ts).replace("Z", ""))
        age_hours = (datetime.datetime.utcnow() - ts_dt).total_seconds() / 3600
        if age_hours > DATA_STALE_HOURS:
            age_label  = f"{int(age_hours * 60)} min" if age_hours < 1 else f"{age_hours:.1f}h"
            send_it, r = _should_send("STALE", _get_state("stale"))
            if send_it:
                send_telegram(
                    f"⚠️ <b>Sensor Data Stale</b>\n\n"
                    f"Last reading was <b>{age_label} ago</b>.\n"
                    f"Auto-sync may have failed. Please check the sensor connection."
                )
                _set_state("stale", "STALE")
                print(f"Stale alert sent ({age_label} old). {r}")
            else:
                print(f"Stale alert throttled. {r}")
            return
    except Exception as e:
        print(f"Could not parse timestamp: {e}")

    _set_state("stale", "FRESH")

    # ── 2. Misting alert ───────────────────────────────────────────────────────
    needs_on  = humidity < 80.0 or temp > 30.0
    needs_off = humidity > 90.0

    if needs_on or needs_off:
        status = "MIST ON" if needs_on else "MIST OFF"
        emoji  = "💧" if needs_on else "✅"

        send_it, reason = _should_send(status, _get_state("misting"))
        print(f"Misting: {status} | Send={send_it} | {reason}")

        if send_it:
            reason_text = "Sensor thresholds breached — check misting system."
            try:
                groq_reply  = ask_groq(temp, humidity, co2)
                reason_line = next((l for l in groq_reply.splitlines() if l.startswith("REASON:")), "")
                if reason_line:
                    reason_text = reason_line.replace("REASON:", "").strip()
            except Exception as e:
                print(f"Groq unavailable ({e})")

            send_telegram(
                f"{emoji} <b>Mushroom Farm Misting Alert</b>\n\n"
                f"🕐 Time: {now_myt} MYT\n"
                f"🌡️ Temp: {temp}°C\n"
                f"💧 Humidity: {humidity}%\n"
                f"🌿 CO2: {co2} ppm\n\n"
                f"<b>Action: {status}</b>\n"
                f"Reason: {reason_text}"
            )
            _set_state("misting", status)
    else:
        print(f"Humidity {humidity}% and Temp {temp}°C within normal range. No mist alert.")
        _set_state("misting", "NORMAL")

    # ── 3. Harvest due alert ───────────────────────────────────────────────────
    try:
        due_today, overdue = _get_harvest_due()
        lines = []
        for block_id, date in due_today:
            lines.append(f"  🟢 <b>{block_id}</b> — due TODAY ({date})")
        for block_id, date, days in overdue:
            lines.append(f"  🔴 <b>{block_id}</b> — overdue by {days} day(s) (was {date})")

        if lines:
            harvest_key      = ",".join(sorted([b for b, _ in due_today] + [b for b, *_ in overdue]))
            send_it, reason  = _should_send(harvest_key, _get_state("harvest"))
            print(f"Harvest: {len(lines)} block(s) due | Send={send_it} | {reason}")
            if send_it:
                send_telegram(
                    f"🍄 <b>Harvest Due Alert</b>\n\n"
                    f"🕐 {now_myt} MYT\n\n"
                    + "\n".join(lines) +
                    "\n\nPlease harvest these blocks as soon as possible."
                )
                _set_state("harvest", harvest_key)
        else:
            print("No blocks due for harvest today.")
            _set_state("harvest", "NONE")
    except Exception as e:
        print(f"Harvest check failed: {e}")


if __name__ == "__main__":
    main()
