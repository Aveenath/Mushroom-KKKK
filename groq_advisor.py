import os
import json
import datetime
from pathlib import Path
from dotenv import load_dotenv
from groq import Groq
from utils import get_db_connection

load_dotenv(dotenv_path=Path(__file__).parent / ".env")

# ── Thresholds (single source of truth) ───────────────────────────────────────
TEMP_MIN            = 25.0
TEMP_OPTIMAL        = 28.0
TEMP_MAX            = 30.0
HUMIDITY_MIN        = 80.0
HUMIDITY_OPT        = 85.0
HUMIDITY_MAX        = 90.0
CO2_MAX             = 800.0
FIRST_HARVEST_DAYS  = 14
REHARVEST_DAYS      = 15


def _trend_label(current, reference, threshold=0.5):
    diff = current - reference
    if abs(diff) < threshold:
        return "stable ➡️"
    return "rising 📈" if diff > 0 else "falling 📉"


def _fetch_sensor_history(conn):
    try:
        cur = conn.execute("""
            SELECT
                ROUND(AVG(temp),     1) AS temp,
                ROUND(AVG(humidity), 1) AS humidity,
                ROUND(AVG(co2),      0) AS co2,
                strftime('%Y-%m-%d %H:00', ts) AS hour_bucket
            FROM sensors
            WHERE ts >= datetime('now', '-24 hours')
            GROUP BY hour_bucket
            ORDER BY hour_bucket DESC
            LIMIT 24
        """)
        rows = cur.fetchall()
        return rows, (rows[0] if rows else None)
    except Exception:
        return [], None


def _fetch_blocks(conn, username):
    try:
        cur = conn.execute(
            """SELECT block_id, planted_date, harvest_count, last_harvest_date
               FROM planting_records
               WHERE username = ? AND (retired = 0 OR retired IS NULL)
               ORDER BY block_id""",
            (username,)
        )
        return cur.fetchall()
    except Exception:
        return []


def _build_sensor_summary(history_rows, latest):
    if not latest:
        return "No sensor data available.", {
            "co2_high": False, "humidity_low": False,
            "temp_high": False, "temp_low": False,
            "co2_bad_streak": 0, "hum_bad_streak": 0,
            "temp": None, "humidity": None, "co2": None,
        }

    temp, humidity, co2, ts = latest
    n = len(history_rows)
    ref_6h = history_rows[min(6, n - 1)]

    co2_trend  = _trend_label(co2,      ref_6h[2], threshold=20)
    hum_trend  = _trend_label(humidity, ref_6h[1], threshold=2)
    temp_trend = _trend_label(temp,     ref_6h[0], threshold=0.5)

    co2_bad_streak = sum(1 for r in history_rows if r[2] > CO2_MAX)
    hum_bad_streak = sum(1 for r in history_rows if r[1] < HUMIDITY_MIN)

    co2_peak   = max(r[2] for r in history_rows)
    hum_min    = min(r[1] for r in history_rows)
    temp_peak  = max(r[0] for r in history_rows)
    temp_min24 = min(r[0] for r in history_rows)

    history_lines = ["Hour (avg)           | Temp  | Humidity | CO2"]
    for i, (t, h, c, bucket) in enumerate(history_rows):
        if i % 3 == 0 or i == n - 1:
            history_lines.append(f"  {bucket} | {t}°C | {h}% | {c} ppm")

    sensor_text = (
        f"Latest hourly average (hour ending {ts}):\n"
        f"  Temperature : {temp}°C  ({temp_trend} vs 6h ago: {ref_6h[0]}°C)"
        f"  | 24h range: {temp_min24}–{temp_peak}°C\n"
        f"  Humidity    : {humidity}%  ({hum_trend} vs 6h ago: {ref_6h[1]}%)"
        f"  | 24h low: {hum_min}%"
        f"  | {hum_bad_streak}/{n} hours below {HUMIDITY_MIN}%\n"
        f"  CO2         : {co2} ppm  ({co2_trend} vs 6h ago: {ref_6h[2]} ppm)"
        f"  | 24h peak: {co2_peak} ppm"
        f"  | {co2_bad_streak}/{n} hours above {CO2_MAX} ppm\n\n"
        f"Hourly history (last 24h, sampled every ~3h):\n"
        + "\n".join(history_lines)
    )

    flags = {
        "co2_high":       float(co2)      > CO2_MAX,
        "humidity_low":   float(humidity) < HUMIDITY_MIN,
        "humidity_high":  float(humidity) > HUMIDITY_MAX,
        "temp_high":      float(temp)     > TEMP_MAX,
        "temp_low":       float(temp)     < TEMP_MIN,
        "co2_bad_streak": co2_bad_streak,
        "hum_bad_streak": hum_bad_streak,
        "temp":     temp,
        "humidity": humidity,
        "co2":      co2,
    }
    return sensor_text, flags


def _categorize(days):
    if days <= 0:
        return "HARVEST_TODAY"
    elif days <= 7:
        return "HARVEST_WEEK"
    elif days <= 14:
        return "MONITOR"
    else:
        return "WAIT"


def _compute_blocks(blocks, today, flags):
    """
    Pure Python block calculation — no AI involved.
    Returns list of block dicts ready for display.
    """
    result = []

    co2_adj = -1 if flags["co2_high"]     else 0
    hum_adj = +1 if flags["humidity_low"] else 0
    total_adj = co2_adj + hum_adj

    for block_id, planted_date, harvest_count, last_harvest_date in blocks:
        hc = int(harvest_count or 0)

        try:
            planted      = datetime.date.fromisoformat(planted_date)
            days_planted = (today - planted).days
        except Exception:
            days_planted = 0

        if hc == 0:
            target_days  = FIRST_HARVEST_DAYS
            days_elapsed = days_planted
            reference    = "since planting"
        else:
            target_days = REHARVEST_DAYS
            try:
                last         = datetime.date.fromisoformat(last_harvest_date)
                days_elapsed = (today - last).days
            except Exception:
                days_elapsed = 0
            reference = "since last harvest"

        base_days_remaining = target_days - days_elapsed
        adj_days_remaining  = base_days_remaining + total_adj
        est_harvest_date    = today + datetime.timedelta(days=max(adj_days_remaining, 0))
        category            = _categorize(adj_days_remaining)

        if total_adj == 0 and not flags["temp_high"]:
            reason = "No adjustment needed, all conditions are within optimal range."
        else:
            parts = []
            if hum_adj:
                parts.append(f"humidity at {flags['humidity']}% is below optimal, harvest delayed by 1 day")
            if co2_adj:
                parts.append(f"CO2 at {flags['co2']} ppm is high, harvest brought forward by 1 day")
            if flags["temp_high"]:
                parts.append(f"temperature at {flags['temp']}°C is above optimal, ensure ventilation")
            reason = ". ".join(p.capitalize() for p in parts) + "."

        result.append({
            "block_id":           block_id,
            "days_planted":       days_planted,
            "est_harvest_date":   str(est_harvest_date),
            "days_until_harvest": adj_days_remaining,
            "category":           category,
            "reason":             reason,
        })

    return result


def _build_advice_prompt(today, sensor_text, flags):
    """Prompt ONLY for the environment advice paragraph — no block math."""
    return f"""You are an expert grey oyster mushroom farm advisor.

Today: {today}

=== CURRENT SENSOR READINGS ===
{sensor_text}

=== GROW FACTS — GREY OYSTER MUSHROOM ===
Optimal conditions: temp {TEMP_MIN}–{TEMP_MAX}°C | humidity {HUMIDITY_MIN}–{HUMIDITY_MAX}% | CO2 < {CO2_MAX:.0f} ppm

=== YOUR TASK ===
Write 1-3 short simple sentences for a farmer summary. 
Keep it simple and direct — no long explanations.
Mention the actual sensor values and what action to take if needed.

=== CRITICAL RESPONSE RULES ===
- "advice" MUST be a plain flowing paragraph — NOT bullet points, NOT a list.
- Write connected sentences, not isolated observations.

=== RESPONSE FORMAT ===
Respond in valid JSON only. No text outside the JSON.

{{
  "advice": "flowing paragraph environment advice here"
}}"""


def get_harvest_advice(username):
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return None, "GROQ_API_KEY not found in .env file."

    conn = get_db_connection()
    try:
        history_rows, latest = _fetch_sensor_history(conn)
        blocks = _fetch_blocks(conn, username)
    finally:
        conn.close()

    if not blocks:
        return None, "No active blocks found. Please record planting data first."

    today = datetime.date.today()

    sensor_text, flags = _build_sensor_summary(history_rows, latest)

    # ── Block calculations done entirely in Python ─────────────────────────────
    computed_blocks = _compute_blocks(blocks, today, flags)

    # ── Groq only for the advice paragraph ────────────────────────────────────
    try:
        client  = Groq(api_key=api_key)
        prompt  = _build_advice_prompt(today, sensor_text, flags)
        response = client.chat.completions.create(
            model           = "llama-3.3-70b-versatile",
            messages        = [{"role": "user", "content": prompt}],
            temperature     = 0.3,
            max_tokens      = 400,
            response_format = {"type": "json_object"},
        )
        raw_text = response.choices[0].message.content
        ai_result = json.loads(raw_text)
        advice = ai_result.get("advice", "")

    except Exception:
        advice = (
            f"Current humidity is {flags['humidity']}% and temperature is {flags['temp']}°C. "
            f"{'Humidity is below optimal — consider misting. ' if flags['humidity_low'] else ''}"
            f"{'Temperature is above optimal — ensure ventilation is running. ' if flags['temp_high'] else ''}"
            f"{'CO2 is elevated — open vents or run a fan. ' if flags['co2_high'] else ''}"
        )
        raw_text = ""

    result = {
        "blocks":          computed_blocks,
        "advice":          advice,
        "raw":             raw_text,
        "co2_bad_streak":  flags["co2_bad_streak"],
        "hum_bad_streak":  flags["hum_bad_streak"],
        "latest_temp":     flags["temp"],
        "latest_humidity": flags["humidity"],
        "latest_co2":      flags["co2"],
    }

    return result, None