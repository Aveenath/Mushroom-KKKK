"""
Background misting alert script.
Run by GitHub Actions every 30 minutes.
Queries Turso directly via HTTP REST API (no libsql_client needed),
asks Groq if misting is needed, sends Telegram notification.
"""
import os
import json
import requests
import datetime
from pathlib import Path
from dotenv import load_dotenv
from groq import Groq

DATA_STALE_HOURS = 2   # alert if Turso data is older than this

load_dotenv(dotenv_path=Path(__file__).parent / ".env")


def query_turso(sql):
    """Query Turso database directly via HTTP REST API."""
    raw_url   = os.environ["TURSO_DATABASE_URL"]
    auth_token = os.environ["TURSO_AUTH_TOKEN"]

    # Normalise URL: libsql:// → https://
    base_url = raw_url.replace("libsql://", "https://").rstrip("/")
    endpoint = f"{base_url}/v2/pipeline"

    payload = {
        "requests": [
            {"type": "execute", "stmt": {"sql": sql}},
            {"type": "close"}
        ]
    }
    headers = {
        "Authorization": f"Bearer {auth_token}",
        "Content-Type": "application/json"
    }
    resp = requests.post(endpoint, json=payload, headers=headers, timeout=15)
    resp.raise_for_status()

    result = resp.json()["results"][0]["response"]["result"]
    cols = [c["name"] for c in result["cols"]]
    rows = [dict(zip(cols, [v["value"] for v in row])) for row in result["rows"]]
    return rows


def send_telegram(message):
    token   = os.environ["TELEGRAM_BOT_TOKEN"].strip()
    chat_id = os.environ["TELEGRAM_CHAT_ID"].strip()
    url     = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(url, data={"chat_id": chat_id, "text": message, "parse_mode": "HTML"}, timeout=10)
    print(f"Telegram response: {resp.status_code} {resp.text}")


def ask_groq(temp, humidity, co2):
    client = Groq(api_key=os.environ["GROQ_API_KEY"])
    prompt = f"""You are an oyster mushroom farm controller. Based on current sensor readings, decide if misting is needed.

Current readings:
- Temperature : {temp}°C  (optimal: 25–28°C, max: 30°C)
- Humidity    : {humidity}%  (optimal: 80–90%, min: 80%)
- CO2         : {co2} ppm  (max: 800 ppm)
- Time        : {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC

Rules:
- If humidity < 80%: MIST ON immediately
- If humidity 80–85%: MIST ON (approaching low end)
- If humidity > 90%: MIST OFF (too wet)
- If temp > 30°C: MIST ON to cool down
- Otherwise: MIST OFF

Reply in this exact format (2 lines only):
ACTION: MIST ON or MIST OFF
REASON: one short sentence why"""

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
        max_tokens=80
    )
    return response.choices[0].message.content.strip()


def main():
    rows = query_turso("SELECT temp, humidity, co2, ts FROM sensors ORDER BY ts DESC LIMIT 1")
    if not rows:
        print("No sensor data found.")
        return

    row      = rows[0]
    temp     = row["temp"]
    humidity = row["humidity"]
    co2      = row["co2"]
    ts       = row["ts"]
    print(f"Latest reading: Temp={temp}°C, Humidity={humidity}%, CO2={co2}ppm @ {ts}")

    # Check data freshness — warn if Turso hasn't been updated recently
    try:
        ts_dt = datetime.datetime.fromisoformat(str(ts).replace("Z", ""))
        age_hours = (datetime.datetime.utcnow() - ts_dt).total_seconds() / 3600
        if age_hours > DATA_STALE_HOURS:
            stale_msg = (
                f"⚠️ <b>Sensor Data Stale</b>\n\n"
                f"Last reading was <b>{age_hours:.1f} hours ago</b> ({ts}).\n"
                f"Live sync only runs when the Streamlit app is open.\n"
                f"Please check the app or sensor connection."
            )
            send_telegram(stale_msg)
            print(f"Stale data alert sent ({age_hours:.1f}h old). Skipping misting check.")
            return
    except Exception as e:
        print(f"Could not parse timestamp '{ts}': {e}")

    # Check if action is needed before calling Groq
    humidity = float(humidity)
    temp     = float(temp)

    needs_mist_on  = humidity < 80.0 or temp > 30.0
    needs_mist_off = humidity > 90.0

    if not needs_mist_on and not needs_mist_off:
        print(f"Humidity {humidity}% and Temp {temp}°C are within normal range. No alert sent.")
        return

    # Determine action from thresholds directly (Groq is advisory only)
    if needs_mist_on:
        status = "MIST ON"
        emoji  = "💧"
    else:
        status = "MIST OFF"
        emoji  = "✅"

    # Ask Groq for a human-readable reason — but don't let a Groq failure block the alert
    reason_text = "Sensor thresholds breached — check misting system."
    try:
        groq_reply = ask_groq(temp, humidity, co2)
        print(f"Groq says:\n{groq_reply}")
        lines       = groq_reply.splitlines()
        reason_line = next((l for l in lines if l.startswith("REASON:")), "")
        if reason_line:
            reason_text = reason_line.replace("REASON:", "").strip()
    except Exception as e:
        print(f"Groq unavailable ({e}) — sending alert with sensor data only.")

    message = (
        f"{emoji} <b>Mushroom Farm Misting Alert</b>\n\n"
        f"🕐 Time: {(datetime.datetime.utcnow() + datetime.timedelta(hours=8)).strftime('%Y-%m-%d %H:%M')} MYT\n"
        f"🌡️ Temp: {temp}°C\n"
        f"💧 Humidity: {humidity}%\n"
        f"🌿 CO2: {co2} ppm\n\n"
        f"<b>Action: {status}</b>\n"
        f"Reason: {reason_text}"
    )

    send_telegram(message)
    print(f"Telegram alert sent: {status}")


if __name__ == "__main__":
    main()
