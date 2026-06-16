import os
import json
import datetime
from pathlib import Path
from dotenv import load_dotenv
from groq import Groq
from utils import get_db_connection, db_read_sql

load_dotenv(dotenv_path=Path(__file__).parent / ".env")


def get_harvest_advice(username):
    """
    Pull latest sensor reading + planting records, send to Groq for
    harvest recommendations. Returns (advice_dict, error).

    advice_dict keys:
        blocks : list of {block_id, days_planted, est_harvest_date,
                          days_until_harvest, category, reason}
        advice : str  (environment adjustment tips)
        raw    : str  (full Groq response, fallback display)
    """
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return None, "GROQ_API_KEY not found in .env file."

    # FIX: use get_db_connection() instead of sqlite3.connect(DB_PATH)
    conn = get_db_connection()

    # ── 1. Sensor history — hourly averages over last 24 hours ───────────────
    try:
        history_cur = conn.execute("""
            SELECT
                ROUND(AVG(temp), 1)     AS temp,
                ROUND(AVG(humidity), 1) AS humidity,
                ROUND(AVG(co2), 0)      AS co2,
                strftime('%Y-%m-%d %H:00', ts) AS hour_bucket
            FROM sensors
            WHERE ts >= datetime('now', '-24 hours')
            GROUP BY hour_bucket
            ORDER BY hour_bucket DESC
            LIMIT 24
        """)
        history_rows = history_cur.fetchall()
        latest = history_rows[0] if history_rows else None
    except Exception:
        history_rows = []
        latest = None

    # ── 2. Active planting records ────────────────────────────────────────────
    try:
        blocks_cur = conn.execute(
            """SELECT block_id, planted_date, harvest_count, last_harvest_date
               FROM planting_records
               WHERE username = ? AND (retired = 0 OR retired IS NULL)
               ORDER BY block_id""",
            (username,)
        )
        blocks = blocks_cur.fetchall()
    except Exception:
        blocks = []
    finally:
        # FIX: close after both queries are done (was closing too early before)
        conn.close()

    if not blocks:
        return None, "No active blocks found. Please record planting data first."

    today = datetime.date.today()

    # ── 3. Sensor summary with trend (hourly context) ────────────────────────
    if latest:
        temp, humidity, co2, ts = latest
        n = len(history_rows)

        ref_6h = history_rows[min(6, n - 1)]

        def _trend(val, ref):
            diff = val - ref
            if abs(diff) < 0.5: return "stable ➡️"
            return "rising 📈" if diff > 0 else "falling 📉"

        co2_trend  = _trend(co2,      ref_6h[2])
        hum_trend  = _trend(humidity, ref_6h[1])
        temp_trend = _trend(temp,     ref_6h[0])

        co2_bad_streak = sum(1 for r in history_rows if r[2] > 800)
        hum_bad_streak = sum(1 for r in history_rows if r[1] < 80)

        co2_peak  = max(r[2] for r in history_rows)
        hum_min   = min(r[1] for r in history_rows)
        temp_peak = max(r[0] for r in history_rows)

        history_lines = ["Hour (avg)           | Temp  | Humidity | CO2"]
        for i, (t, h, c, bucket) in enumerate(history_rows):
            if i % 3 == 0 or i == n - 1:
                history_lines.append(f"  {bucket} | {t}°C | {h}% | {c} ppm")
        history_text = "\n".join(history_lines)

        sensor_text = (
            f"Latest hourly average (hour ending {ts}):\n"
            f"  Temperature : {temp}°C  (vs 6h ago: {ref_6h[0]}°C, trend: {temp_trend})"
            f"  | 24h peak: {temp_peak}°C\n"
            f"  Humidity    : {humidity}%  (vs 6h ago: {ref_6h[1]}%, trend: {hum_trend})"
            f"  | 24h low: {hum_min}%"
            f"  | {hum_bad_streak}/{n} hours below 80%\n"
            f"  CO2         : {co2} ppm  (vs 6h ago: {ref_6h[2]} ppm, trend: {co2_trend})"
            f"  | 24h peak: {co2_peak} ppm"
            f"  | {co2_bad_streak}/{n} hours above 800 ppm\n\n"
            f"Hourly history (last 24h, sampled every ~3h):\n{history_text}"
        )
    else:
        temp = humidity = co2 = None
        co2_bad_streak = hum_bad_streak = 0
        sensor_text = "No sensor data available."

    # ── 4. Block summary ──────────────────────────────────────────────────────
    block_lines = []
    for block_id, planted_date, harvest_count, last_harvest_date in blocks:
        hc = int(harvest_count or 0)

        try:
            planted      = datetime.date.fromisoformat(planted_date)
            days_planted = (today - planted).days
        except Exception:
            days_planted = 0

        if hc == 0:
            target_days  = 14
            days_elapsed = days_planted
            reference    = "since planting"
        else:
            target_days  = 15
            try:
                last         = datetime.date.fromisoformat(last_harvest_date)
                days_elapsed = (today - last).days
            except Exception:
                days_elapsed = 0
            reference    = "since last harvest"

        days_remaining = target_days - days_elapsed

        if last_harvest_date and hc > 0:
            block_lines.append(
                f"{block_id}: planted {days_planted}d ago | "
                f"{hc} harvest(s) done | {days_elapsed}d elapsed {reference} | "
                f"target={target_days}d | days_until_harvest={days_remaining}"
            )
        else:
            block_lines.append(
                f"{block_id}: planted {days_planted}d ago | not yet harvested | "
                f"{days_elapsed}d elapsed {reference} | "
                f"target={target_days}d | days_until_harvest={days_remaining}"
            )

    blocks_text = "\n".join(block_lines)

    # ── 5. Prompt ─────────────────────────────────────────────────────────────
    prompt = f"""You are an expert grey oyster mushroom farm advisor.

Today: {today}

=== CURRENT SENSOR READINGS ===
{sensor_text}

=== ACTIVE BLOCKS ({len(blocks)} total) ===
{blocks_text}

Each block already has "days_until_harvest" pre-calculated for you based on:
- First harvest target : 14 days after planting
- Re-harvest target    : 15 days after last harvest

=== GROW FACTS ===
- Optimal : temp 25–30°C, humidity 80–90%, CO2 < 800 ppm

SENSOR ADJUSTMENTS — apply only if conditions are clearly out of range:
- If CO2 > 800 ppm  → subtract 1 from days_until_harvest (high CO2 accelerates flushing)
- If humidity < 80% → add 1 to days_until_harvest (low humidity slows growth)
- If both conditions apply simultaneously → apply both (net 0 if they cancel out)
- If conditions are within optimal range → no adjustment needed
Use the hourly trend and 24h context above to support your reason, but keep the
adjustment simple: only ±1 day per condition. Do not invent larger adjustments.
Show the adjusted value in days_until_harvest, not the original.

=== YOUR TASKS ===

TASK 1 — For each block, use the provided "days_until_harvest" value as your
starting point. Adjust it slightly ONLY if sensor conditions are not optimal.
Then compute est_harvest_date = today ({today}) + adjusted days_until_harvest.
If days_until_harvest is 0 or negative, the block is ready now.

TASK 2 — CATEGORIZE each block using the ADJUSTED days_until_harvest:
- HARVEST_TODAY : days_until_harvest <= 0  (overdue or ready now)
- HARVEST_WEEK  : days_until_harvest 1–7
- MONITOR       : days_until_harvest 8–14
- WAIT          : days_until_harvest > 14

TASK 3 — Give 2–3 sentences of practical environment advice
based on the current sensor readings.

=== RESPONSE FORMAT ===
Respond in valid JSON only. No text outside the JSON.

{{
  "blocks": [
    {{
      "block_id": "B1",
      "days_planted": 0,
      "est_harvest_date": "YYYY-MM-DD",
      "days_until_harvest": 0,
      "category": "WAIT",
      "reason": "one sentence explanation"
    }}
  ],
  "advice": "environment adjustment advice here"
}}"""

    # ── 6. Groq API call ──────────────────────────────────────────────────────
    try:
        client   = Groq(api_key=api_key)
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=1000,
            response_format={"type": "json_object"}
        )
        raw_text = response.choices[0].message.content
        result        = json.loads(raw_text)
        result["raw"] = raw_text
        result["co2_bad_streak"] = co2_bad_streak
        result["hum_bad_streak"] = hum_bad_streak
        result["latest_temp"]    = temp
        result["latest_humidity"]= humidity
        result["latest_co2"]     = co2
        return result, None

    except json.JSONDecodeError:
        return {"raw": raw_text, "blocks": [], "advice": ""}, None
    except Exception as e:
        return None, f"Groq API error: {str(e)}"