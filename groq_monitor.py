import os
import json
import datetime
from pathlib import Path
from dotenv import load_dotenv
from groq import Groq
from utils import get_db_connection

load_dotenv(dotenv_path=Path(__file__).parent / ".env")

# ── Thresholds (single source of truth) ───────────────────────────────────────
TEMP_MIN        = 25.0
TEMP_OPTIMAL    = 28.0   # true optimal upper bound for fruiting
TEMP_MAX        = 30.0   # stress threshold
HUMIDITY_MIN    = 80.0
HUMIDITY_MAX    = 90.0
CO2_MAX         = 800.0


def _fetch_latest_reading(conn):
    """
    Return the single most recent sensor reading.
    Returns (temp, humidity, co2, latest_ts, 1) or None.
    """
    try:
        cur = conn.execute(
            """
            SELECT
                ROUND(temp,     1) AS temp,
                ROUND(humidity, 1) AS humidity,
                ROUND(co2,      0) AS co2,
                ts                 AS latest_ts,
                1                  AS row_count
            FROM sensors
            ORDER BY ts DESC
            LIMIT 1
            """
        )
        row = cur.fetchone()
        if row and row[0] is not None:
            return row
        return None
    except Exception:
        return None


def _evaluate_conditions(temp, humidity, co2):
    """
    Return a dict of plain status flags and labels.
    Single source of truth for all condition checks.
    """
    temp_f     = float(temp)
    humidity_f = float(humidity)
    co2_f      = float(co2)

    # Temperature bands
    if temp_f > TEMP_MAX:
        temp_status = f"HIGH — above {TEMP_MAX}°C (heat stress)"
        temp_flag   = "HIGH"
    elif temp_f > TEMP_OPTIMAL:
        temp_status = f"WARM — above optimal {TEMP_OPTIMAL}°C (acceptable but monitor)"
        temp_flag   = "WARM"
    elif temp_f < TEMP_MIN:
        temp_status = f"LOW — below {TEMP_MIN}°C (growth slowdown)"
        temp_flag   = "LOW"
    else:
        temp_status = f"OPTIMAL ({TEMP_MIN}–{TEMP_OPTIMAL}°C)"
        temp_flag   = "OPTIMAL"

    # Humidity bands
    if humidity_f < HUMIDITY_MIN:
        humidity_status = f"LOW — below {HUMIDITY_MIN}% (misting needed)"
        humidity_flag   = "LOW"
    elif humidity_f > HUMIDITY_MAX:
        humidity_status = f"HIGH — above {HUMIDITY_MAX}% (misting not needed)"
        humidity_flag   = "HIGH"
    else:
        humidity_status = f"OPTIMAL ({HUMIDITY_MIN}–{HUMIDITY_MAX}%)"
        humidity_flag   = "OPTIMAL"

    # CO2
    if co2_f > CO2_MAX:
        co2_status = f"HIGH — above {CO2_MAX:.0f} ppm (increase ventilation)"
        co2_flag   = "HIGH"
    else:
        co2_status = f"NORMAL — below {CO2_MAX:.0f} ppm"
        co2_flag   = "NORMAL"

    # Mist pre-evaluation (passed to model for transparency)
    if humidity_f < HUMIDITY_MIN:
        mist_suggestion = "ON"
        mist_reason     = f"Humidity ({humidity}%) is below {HUMIDITY_MIN}% — misting required"
    elif humidity_f > HUMIDITY_MAX:
        mist_suggestion = "OFF"
        mist_reason     = f"Humidity ({humidity}%) is above {HUMIDITY_MAX}% — misting not needed"
    else:
        mist_suggestion = "MAINTAIN"
        mist_reason     = f"Humidity ({humidity}%) is within optimal range — no change needed"

    return {
        "temp_flag":        temp_flag,
        "temp_status":      temp_status,
        "humidity_flag":    humidity_flag,
        "humidity_status":  humidity_status,
        "co2_flag":         co2_flag,
        "co2_status":       co2_status,
        "mist_suggestion":  mist_suggestion,
        "mist_reason":      mist_reason,
    }


def _build_prompt(temp, humidity, co2, latest_ts, conditions):
    """Assemble the full Groq prompt."""
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    return f"""You are an expert grey oyster mushroom farm advisor responsible for equipment recommendations.

Current time   : {now}
Latest reading : {latest_ts}

=== CURRENT SENSOR READINGS (latest reading) ===
  Temperature : {temp}°C
  Humidity    : {humidity}%
  CO2         : {co2} ppm

=== SENSOR STATUS ===
  Temperature : {conditions['temp_status']}
  Humidity    : {conditions['humidity_status']}
  CO2         : {conditions['co2_status']}

=== GROW FACTS — GREY OYSTER MUSHROOM ===
  Optimal conditions:
    Temperature : {TEMP_MIN}–{TEMP_OPTIMAL}°C  (above {TEMP_MAX}°C = heat stress)
    Humidity    : {HUMIDITY_MIN}–{HUMIDITY_MAX}%
    CO2         : < {CO2_MAX:.0f} ppm

=== MIST EQUIPMENT — RULES & EFFECTS ===
  Primary effect   : raises humidity directly
  Secondary effect : slight temperature reduction via evaporative cooling
  CO2 effect       : negligible

  Decision rules (hysteresis prevents flickering):
    Turn ON      if humidity < {HUMIDITY_MIN}%
    Turn OFF     if humidity > {HUMIDITY_MAX}%
    MAINTAIN     if humidity is between {HUMIDITY_MIN}–{HUMIDITY_MAX}%

  Pre-evaluated suggestion : {conditions['mist_suggestion']}
  Pre-evaluated reason     : {conditions['mist_reason']}

=== YOUR TASKS ===

TASK 1 — Decide mist status: ON, OFF, or MAINTAIN.
  Use the pre-evaluated suggestion above as your answer unless you have a
  specific reason to override it based on trends or combined sensor context.

TASK 2 — Write one clear sentence explaining the mist decision.
  Reference the actual sensor value (e.g. "Humidity is at 76%...").

TASK 3 — Write a 1–3 short simple sentences for a farmer summary.
  Keep it simple and direct — no long explanations.
  Mention the actual sensor values and what action to take if needed.
  If temperature is WARM or HIGH, mention it and suggest ventilation.
  If CO2 is HIGH, mention it and suggest opening vents or a fan.
  If mist has a useful secondary cooling effect in current conditions, mention it.

=== CRITICAL RESPONSE RULES ===
- "summary" MUST be a plain flowing paragraph — NOT bullet points, NOT a list, NOT nested JSON.
- Write it as connected sentences, not isolated observations.
- Example: "The current environment is within acceptable conditions for grey oyster mushroom growth,
  with humidity at 84.2% and CO2 at 636 ppm both in normal range. However, temperature at 29.7°C
  is slightly warm — ensure ventilation is running to prevent heat stress. The farmer can expect
  steady growth under these conditions."

=== RESPONSE FORMAT ===
Respond in valid JSON only. No text outside the JSON.

{{
  "mist": {{
    "status": "ON",
    "reason": "one sentence referencing the actual humidity value"
  }},
  "summary": "flowing paragraph summary here"
}}"""


def get_monitor_advice(username=None):
    """
    Pull the latest sensor reading, send to Groq for equipment recommendations.

    Returns (advice_dict, error_str).

    advice_dict keys:
        mist    : dict  {{status: "ON"/"OFF"/"MAINTAIN", reason: str}}
        summary : str   (overall environment summary)
        raw     : str   (full Groq JSON response)
    """
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return None, "GROQ_API_KEY not found in .env file."

    conn = get_db_connection()
    try:
        reading = _fetch_latest_reading(conn)
    finally:
        conn.close()

    if not reading:
        return None, "No sensor data available."

    temp, humidity, co2, latest_ts, _ = reading
    conditions = _evaluate_conditions(temp, humidity, co2)
    prompt     = _build_prompt(temp, humidity, co2, latest_ts, conditions)

    # ── Groq API call ──────────────────────────────────────────────────────────
    try:
        client   = Groq(api_key=api_key)
        response = client.chat.completions.create(
            model           = "llama-3.3-70b-versatile",
            messages        = [{"role": "user", "content": prompt}],
            temperature     = 0.3,
            max_tokens      = 600,
            response_format = {"type": "json_object"},
        )
        raw_text = response.choices[0].message.content
        result   = json.loads(raw_text)

    except json.JSONDecodeError:
        return {"raw": raw_text, "mist": {}, "summary": ""}, None
    except Exception as e:
        return None, f"Groq API error: {str(e)}"

    result["raw"] = raw_text
    return result, None