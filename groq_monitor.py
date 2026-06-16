import os
import json
import sqlite3
import datetime
from pathlib import Path
from dotenv import load_dotenv
from groq import Groq

load_dotenv(dotenv_path=Path(__file__).parent / ".env")

DB_PATH = "mushroom_client.db"


def get_monitor_advice(username=None):
    """
    Pull latest sensor reading, send to Groq for equipment recommendations.
    Returns (advice_dict, error).

    advice_dict keys:
        mist      : dict {status: "ON"/"OFF", reason: str}
        summary   : str  (overall environment summary)
        raw       : str  (full Groq response, fallback display)
    """
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return None, "GROQ_API_KEY not found in .env file."

    conn = sqlite3.connect(DB_PATH)

    # ── 1. Sensor reading — 15-minute average for stable equipment decisions ──
    # Single-minute readings can flicker; averaging last 15 rows gives a
    # more stable signal for mist ON/OFF control.
    try:
        rows = conn.execute(
            "SELECT temp, humidity, co2 FROM sensors ORDER BY ts DESC LIMIT 15"
        ).fetchall()
        if rows:
            temp     = round(sum(r[0] for r in rows) / len(rows), 1)
            humidity = round(sum(r[1] for r in rows) / len(rows), 1)
            co2      = round(sum(r[2] for r in rows) / len(rows), 0)
            ts_row   = conn.execute(
                "SELECT ts FROM sensors ORDER BY ts DESC LIMIT 1"
            ).fetchone()
            ts = ts_row[0] if ts_row else "unknown"
            latest = (temp, humidity, co2, ts)
        else:
            latest = None
    except Exception:
        latest = None
    finally:
        conn.close()

    if not latest:
        return None, "No sensor data available."

    temp, humidity, co2, ts = latest
    today = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    sensor_text = (
        f"Temperature : {temp}°C\n"
        f"Humidity    : {humidity}%\n"
        f"CO2         : {co2} ppm\n"
        f"Averaged at : {ts} (15-minute average)"
    )

    temp_status  = "CRITICAL (above 30°C)" if float(temp) > 30 else "NORMAL"
    humid_status = "LOW (below 80%)" if float(humidity) < 80 else ("HIGH (above 90%)" if float(humidity) > 90 else "NORMAL (80-90%)")
    co2_status   = "HIGH (above 800 ppm)" if float(co2) > 800 else "NORMAL"

    mist_trigger = float(humidity) < 80
    mist_off     = float(humidity) >= 90

    prompt = f"""You are an expert grey oyster mushroom farm advisor responsible for equipment control.

Current time: {today}

=== CURRENT SENSOR READINGS ===
{sensor_text}

=== SENSOR STATUS EVALUATION ===
Temperature : {temp_status}
Humidity    : {humid_status}
CO2         : {co2_status}

=== EQUIPMENT RULES FOR GREY OYSTER MUSHROOM ===
Optimal conditions: temp 25–30°C, humidity 80–90%, CO2 ≤ 800 ppm

MIST rules:
- Turn ON  if: humidity < 80%
- Turn OFF if: humidity ≥ 90% (hysteresis range 80–90% prevents flickering)
- Keep current state if: humidity is between 80–90%
Mist ON trigger: {mist_trigger}
Mist OFF trigger: {mist_off}

=== YOUR TASKS ===
TASK 1 — Based on the rules and current readings, decide:
- MIST: should it be ON or OFF right now?

TASK 2 — Give a one-sentence reason for each decision.

TASK 3 — Write a 2-sentence overall environment summary for the farmer.

=== RESPONSE FORMAT ===
Respond in valid JSON only. No text outside the JSON.

{{
  "mist": {{
    "status": "OFF",
    "reason": "one sentence explanation"
  }},
  "summary": "overall environment summary here"
}}"""

    # ── 3. Groq API call ──────────────────────────────────────────────────────
    try:
        client   = Groq(api_key=api_key)
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=500,
            response_format={"type": "json_object"}
        )
        raw_text      = response.choices[0].message.content
        result        = json.loads(raw_text)
        result["raw"] = raw_text
        return result, None

    except json.JSONDecodeError:
        return {"raw": raw_text, "mist": {}, "summary": ""}, None
    except Exception as e:
        return None, f"Groq API error: {str(e)}"