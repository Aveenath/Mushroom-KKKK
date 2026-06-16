import os
import json
import sqlite3
import datetime
from pathlib import Path
from dotenv import load_dotenv
from groq import Groq

load_dotenv(dotenv_path=Path(__file__).parent / ".env")

DB_PATH = "mushroom_client.db"


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

    conn = sqlite3.connect(DB_PATH)

    # ── 1. Latest sensor reading ──────────────────────────────────────────────
    try:
        latest = conn.execute(
            "SELECT temp, humidity, co2, ts FROM sensors ORDER BY ts DESC LIMIT 1"
        ).fetchone()
    except Exception:
        latest = None

    # ── 2. Active planting records ────────────────────────────────────────────
    try:
        blocks = conn.execute(
            """SELECT block_id, planted_date, harvest_count, last_harvest_date
               FROM planting_records
               WHERE username = ? AND (retired = 0 OR retired IS NULL)
               ORDER BY block_id""",
            (username,)
        ).fetchall()
    except Exception:
        blocks = []
    finally:
        conn.close()

    if not blocks:
        return None, "No active blocks found. Please record planting data first."

    today = datetime.date.today()

    # ── 3. Sensor summary ─────────────────────────────────────────────────────
    if latest:
        temp, humidity, co2, ts = latest
        sensor_text = (
            f"Temperature : {temp}°C\n"
            f"Humidity    : {humidity}%\n"
            f"CO2         : {co2} ppm\n"
            f"Recorded at : {ts}"
        )
    else:
        temp = humidity = co2 = None
        sensor_text = "No sensor data available."

    # ── 4. Block summary — pre-calculate days elapsed and days remaining ───────
    block_lines = []
    for block_id, planted_date, harvest_count, last_harvest_date in blocks:
        hc = int(harvest_count or 0)

        try:
            planted      = datetime.date.fromisoformat(planted_date)
            days_planted = (today - planted).days
        except Exception:
            days_planted = 0

        if hc == 0:
            # Not yet harvested — target is 14 days from planting
            target_days  = 14
            days_elapsed = days_planted
            reference    = "since planting"
        else:
            # Already harvested — target is 15 days from last harvest
            target_days  = 15
            try:
                last         = datetime.date.fromisoformat(last_harvest_date)
                days_elapsed = (today - last).days
            except Exception:
                days_elapsed = 0
            reference    = "since last harvest"

        days_remaining = target_days - days_elapsed  # negative = overdue

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
IMPORTANT — MANDATORY SENSOR ADJUSTMENTS (you MUST apply these):
- If CO2 > 800 ppm: you MUST subtract 1 from days_until_harvest. Current CO2 is {co2} ppm → SUBTRACT 1 DAY.
- If humidity < 80%: you MUST add 1 to days_until_harvest. Current humidity is {humidity}%
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
        return result, None

    except json.JSONDecodeError:
        return {"raw": raw_text, "blocks": [], "advice": ""}, None
    except Exception as e:
        return None, f"Groq API error: {str(e)}"