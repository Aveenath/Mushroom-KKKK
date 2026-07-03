"""
Background alert script — run by GitHub Actions every 1 hour.
Sends: misting alerts, stale data warnings.
Throttled: same alert won't repeat until condition resets or REMIND_HOURS pass.
"""
import os
import datetime
import requests
from pathlib import Path
from dotenv import load_dotenv
from groq import Groq
from db_common import query_turso, init_state_table, get_state, set_state

DATA_STALE_HOURS  = 1.0  # warn if sensor data older than 1 hour
REMIND_HOURS      = 1.0  # re-send alert every 1 hour if problem not fixed

load_dotenv(dotenv_path=Path(__file__).parent / ".env")


# ── Telegram ───────────────────────────────────────────────────────────────────

def send_telegram(message):
    token   = os.environ["TELEGRAM_BOT_TOKEN"].strip()
    chat_id = os.environ["TELEGRAM_CHAT_ID"].strip()
    url     = f"https://api.telegram.org/bot{token}/sendMessage"
    resp    = requests.post(url, data={"chat_id": chat_id, "text": message, "parse_mode": "HTML"}, timeout=10)
    print(f"Telegram response: {resp.status_code} {resp.text}")


# ── Groq ───────────────────────────────────────────────────────────────────────

def ask_groq(humidity, co2):
    client = Groq(api_key=os.environ["GROQ_API_KEY"])
    prompt = f"""You are an oyster mushroom farm controller.
Current readings:
- Humidity : {humidity}%  (optimal: 80–90%)
- CO2      : {co2} ppm  (max: 800 ppm)

Rules:
- humidity < 80%: MIST ON
- humidity 80–90%: MIST MAINTAIN
- humidity > 90%: MIST OFF

Reply in exactly 2 lines:
ACTION: MIST ON or MIST MAINTAIN or MIST OFF
REASON: one short sentence why"""
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
        max_tokens=80,
    )
    return response.choices[0].message.content.strip()


# ── Alert state (throttle) ─────────────────────────────────────────────────────

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


def _misting_decision(humidity):
    """Returns 'MIST ON', 'MIST MAINTAIN', or 'MIST OFF' based on humidity."""
    if humidity < 80.0:
        return "MIST ON"
    if humidity > 90.0:
        return "MIST OFF"
    return "MIST MAINTAIN"


def _heartbeat_update(state, today_str):
    """
    Tracks how many times the script has run today.
    Returns (new_status_to_save, message_or_None).
    A message is only returned once — on the first run of a new day — summarizing
    yesterday's run count. This is a dead-man's-switch: if cron-job.org or GitHub
    Actions itself stops working, this daily message stops arriving too, which is
    the signal that the whole pipeline (not just a sensor) needs checking.
    """
    if state is None:
        return f"{today_str}|1", None
    prev_date, _, prev_count = state["last_status"].partition("|")
    if prev_date == today_str:
        return f"{today_str}|{int(prev_count) + 1}", None
    message = f"💓 <b>Daily Heartbeat</b>\n\nSystem checked in <b>{prev_count}</b> time(s) on {prev_date}."
    return f"{today_str}|1", message


def _stale_cause_message(sync_state):
    """Explains *why* sensor data is stale: our sync script crashed, or the device is offline."""
    if sync_state and str(sync_state["last_status"]).startswith("ERROR"):
        return f"Sync script crashed: <code>{sync_state['last_status']}</code>"
    return "Sync is running fine, but no new data has arrived — the sensor device itself may be offline."


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
    init_state_table()
    now_myt   = (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).strftime("%Y-%m-%d %H:%M")
    today_myt = now_myt.split(" ")[0]

    new_heartbeat, heartbeat_msg = _heartbeat_update(get_state("heartbeat"), today_myt)
    if heartbeat_msg:
        send_telegram(heartbeat_msg)
    set_state("heartbeat", new_heartbeat)

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
            send_it, r = _should_send("STALE", get_state("stale"))
            if send_it:
                cause_line = _stale_cause_message(get_state("sync"))
                send_telegram(
                    f"⚠️ <b>Sensor Data Stale</b>\n\n"
                    f"Last reading was <b>{age_label} ago</b>.\n"
                    f"{cause_line}"
                )
                set_state("stale", "STALE")
                print(f"Stale alert sent ({age_label} old). {r}")
            else:
                print(f"Stale alert throttled. {r}")
            return
    except Exception as e:
        print(f"Could not parse timestamp: {e}")

    set_state("stale", "FRESH")

    # ── 2. Misting alert ───────────────────────────────────────────────────────
    status = _misting_decision(humidity)

    emoji_map = {"MIST ON": "💧", "MIST MAINTAIN": "✅", "MIST OFF": "🚫"}
    emoji = emoji_map.get(status, "💧")

    send_it, reason = _should_send(status, get_state("misting"))
    print(f"Misting: {status} | Send={send_it} | {reason}")

    if send_it:
        reason_text = "Humidity threshold triggered — check misting system."
        try:
            groq_reply  = ask_groq(humidity, co2)
            reason_line = next((l for l in groq_reply.splitlines() if l.startswith("REASON:")), "")
            if reason_line:
                reason_text = reason_line.replace("REASON:", "").strip()
        except Exception as e:
            print(f"Groq unavailable ({e})")

        send_telegram(
            f"{emoji} <b>Mushroom Farm Misting Alert</b>\n\n"
            f"🕐 Time: {now_myt} MYT\n"
            f"💧 Humidity: {humidity}%\n"
            f"🌿 CO2: {co2} ppm\n\n"
            f"<b>Action: {status}</b>\n"
            f"Reason: {reason_text}"
        )
        set_state("misting", status)
    else:
        print(f"Humidity {humidity}% — status '{status}', throttled.")

    # ── 3. Harvest due alert (disabled) ───────────────────────────────────────
    # Uncomment below to re-enable harvest notifications
    # try:
    #     due_today, overdue = _get_harvest_due()
    #     ...
    # except Exception as e:
    #     print(f"Harvest check failed: {e}")
    pass


if __name__ == "__main__":
    main()
