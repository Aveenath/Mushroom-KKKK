import os
import datetime
from pathlib import Path
from dotenv import load_dotenv
from groq import Groq
from utils import get_db_connection

# Explicitly load .env from the project root folder
load_dotenv(dotenv_path=Path(__file__).parent / ".env")


def get_harvest_advice(username):
    """Call Groq LLM with farm data, return (advice_text, error_message)."""
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return None, "GROQ_API_KEY not found in .env file."

    # --- Latest sensor reading ---
    conn = get_db_connection()
    try:
        sensor = conn.execute(
            "SELECT temp, humidity, co2 FROM sensors ORDER BY ts DESC LIMIT 1"
        ).fetchone()
    except Exception:
        sensor = None

    # --- Active planting records ---
    try:
        rows = conn.execute(
            """SELECT block_id, planted_date, harvest_count, last_harvest_date
               FROM planting_records
               WHERE username = ? AND (retired = 0 OR retired IS NULL)
               ORDER BY block_id""",
            (username,)
        ).fetchall()
    except Exception:
        rows = []
    finally:
        conn.close()

    if not rows:
        return None, "No active blocks found. Please record planting data first."

    today = datetime.date.today()

    # --- Sensor summary ---
    if sensor:
        temp, humidity, co2 = sensor
        if co2 < 1000:
            co2_note = "Normal"
        elif co2 < 1500:
            co2_note = "Elevated — monitor closely"
        else:
            co2_note = "HIGH — accelerates mushroom growth, harvest earlier"
        sensor_text = (
            f"Temperature: {temp}°C | Humidity: {humidity}% | "
            f"CO2: {co2} ppm ({co2_note})"
        )
    else:
        sensor_text = "Sensor data unavailable"

    # --- Block summary (compact format) ---
    block_lines = []
    for block_id, planted_date, harvest_count, last_harvest_date in rows:
        hc = int(harvest_count or 0)
        if last_harvest_date:
            last = datetime.date.fromisoformat(last_harvest_date)
            days_since = (today - last).days
            block_lines.append(
                f"{block_id}: {hc} harvest(s), last harvested {days_since} days ago"
            )
        else:
            try:
                planted = datetime.date.fromisoformat(planted_date)
                days_since_plant = (today - planted).days
            except Exception:
                days_since_plant = "?"
            block_lines.append(
                f"{block_id}: newly planted {days_since_plant} days ago, not yet harvested"
            )

    blocks_text = "\n".join(block_lines)

    # --- Prompt ---
    prompt = f"""You are an expert oyster mushroom farm advisor. Analyze this data and give clear harvest recommendations.

Today: {today}

Farm Sensor Readings:
{sensor_text}

Key facts:
- Oyster mushroom first harvest: typically 15–21 days after planting
- Re-harvest interval: 7–14 days after last harvest
- CO2 > 1500 ppm accelerates growth — harvest earlier than usual

Active Blocks ({len(rows)} total):
{blocks_text}

Based on days since planting/last harvest and current CO2 level, categorize blocks into:

🔴 HARVEST TODAY (overdue or ready now)
🟡 HARVEST THIS WEEK (due within 7 days)
🟢 MONITOR (approaching harvest in 8–14 days)
⬛ WAIT (needs more time)

List block IDs for each category. Be concise and practical. Max 350 words."""

    # --- Groq API call ---
    try:
        client = Groq(api_key=api_key)
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=600
        )
        return response.choices[0].message.content, None
    except Exception as e:
        return None, f"Groq API error: {str(e)}"
