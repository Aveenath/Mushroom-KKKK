import os
import json
import datetime
from pathlib import Path
from dotenv import load_dotenv
from groq import Groq
from utils import get_db_connection

load_dotenv(dotenv_path=Path(__file__).parent / ".env")

# ── Optimal environment thresholds ─────────────────────────────────────────
TEMP_MIN     = 25.0
TEMP_OPTIMAL = 28.0
TEMP_MAX     = 30.0
HUMIDITY_MIN = 80.0
HUMIDITY_OPT = 85.0
HUMIDITY_MAX = 90.0
CO2_MAX      = 800.0


FIRST_HARVEST_BASE = 14
FIRST_HARVEST_MIN  = 12
FIRST_HARVEST_MAX  = 15

REHARVEST_BASE = 10
REHARVEST_MIN  = 7
REHARVEST_MAX  = 15

STRESS_HUMIDITY_THRESHOLD = 70.0
STRESS_TEMP_THRESHOLD     = 31.0


def _trend_label(current, reference, threshold=0.5):
    diff = current - reference
    if abs(diff) < threshold:
        return "stable ➡️"
    return "rising 📈" if diff > 0 else "falling 📉"


def _fetch_sensor_history_24h(conn):
    """Latest 24h, hourly — used for the 'current conditions' summary."""
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


def _fetch_daily_sensor_series(conn, start_date_str):
    """
    Daily averages from start_date_str to now. This is what lets us look
    back across an entire growth cycle (which can be anywhere from 5 to 15
    days) instead of only the last 24 hours — used to compute the
    stress_ratio that drives the harvest-date prediction.
    """
    try:
        cur = conn.execute("""
            SELECT
                strftime('%Y-%m-%d', ts) AS day,
                ROUND(AVG(temp),     1) AS temp,
                ROUND(AVG(humidity), 1) AS humidity,
                ROUND(AVG(co2),      0) AS co2
            FROM sensors
            WHERE ts >= ?
            GROUP BY day
            ORDER BY day ASC
        """, (start_date_str,))
        return cur.fetchall()  # (day, temp, humidity, co2)
    except Exception:
        return []


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


def _compute_stress_ratio(daily_series, start_date, end_date):
    """
    Fraction of days, within [start_date, end_date], where conditions look
    like the "kering"/stress pattern seen in this farm's own history
    (low humidity + high temp). Returns (stress_ratio, days_with_data,
    avg_temp, avg_humidity, avg_co2) for that window. This is what feeds
    _predict_target_days() — the prediction engine, unlike the 24h summary
    below, looks at the whole cycle so far rather than just today.
    """
    window = [
        row for row in daily_series
        if start_date.isoformat() <= row[0] <= end_date.isoformat()
    ]
    if not window:
        return None, 0, None, None, None

    stress_days = sum(
        1 for (_, temp, hum, _co2) in window
        if (hum is not None and hum < STRESS_HUMIDITY_THRESHOLD)
        or (temp is not None and temp > STRESS_TEMP_THRESHOLD)
    )
    n = len(window)
    avg_temp = round(sum(r[1] for r in window) / n, 1)
    avg_hum  = round(sum(r[2] for r in window) / n, 1)
    avg_co2  = round(sum(r[3] for r in window) / n, 0)
    return stress_days / n, n, avg_temp, avg_hum, avg_co2


def _predict_target_days(base, min_d, max_d, stress_ratio):
    """
    No sensor data for this cycle yet -> fall back to the historical median.
    Otherwise interpolate between the best-case and worst-case durations
    seen in real history, based on how stressed the environment has been.
    """
    if stress_ratio is None:
        return base
    return round(min_d + stress_ratio * (max_d - min_d), 1)


def _categorize(days):
    if days <= 0:
        return "HARVEST_TODAY"
    elif days <= 7:
        return "HARVEST_WEEK"
    elif days <= 14:
        return "MONITOR"
    else:
        return "WAIT"


def _build_sensor_summary(history_rows, latest):
    """
    Richer 24h "current conditions" summary: trend vs 6h ago, 24h peak/low
    for each metric, and a count of how many of the last 24 hourly readings
    breached CO2/humidity thresholds — plus a sampled hourly table that
    gets handed to Groq so the advice paragraph can reference real numbers
    instead of guessing.
    """
    if not latest:
        return "No sensor data available.", {
            "co2_high": False, "humidity_low": False, "humidity_high": False,
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


def _compute_blocks(blocks, today, daily_series):
    """
    All harvest-date math happens here, in plain Python, using the
    empirical baselines above and the cycle-long stress_ratio (not just
    today's snapshot). Groq never touches these numbers — it only gets
    asked to explain them in plain language. This keeps predictions
    reproducible: the same inputs always give the same date.
    """
    result = []

    for block_id, planted_date, harvest_count, last_harvest_date in blocks:
        hc = int(harvest_count or 0)

        try:
            planted = datetime.date.fromisoformat(planted_date)
        except Exception:
            planted = today

        if hc == 0:
            reference   = planted
            base, lo, hi = FIRST_HARVEST_BASE, FIRST_HARVEST_MIN, FIRST_HARVEST_MAX
            ref_label   = "since planting"
        else:
            try:
                reference = datetime.date.fromisoformat(last_harvest_date)
            except Exception:
                reference = today
            base, lo, hi = REHARVEST_BASE, REHARVEST_MIN, REHARVEST_MAX
            ref_label   = "since last harvest"

        days_elapsed = (today - reference).days

        stress_ratio, days_with_data, avg_temp, avg_hum, avg_co2 = \
            _compute_stress_ratio(daily_series, reference, today)

        target_days = _predict_target_days(base, lo, hi, stress_ratio)
        days_until_harvest = round(target_days - days_elapsed, 1)
        est_harvest_date = reference + datetime.timedelta(days=round(target_days))

        # Historical best/worst-case dates, for an honest range (not fake precision)
        est_range_low  = reference + datetime.timedelta(days=lo)
        est_range_high = reference + datetime.timedelta(days=hi)
        est_range = f"{est_range_low} to {est_range_high}"

        category = _categorize(days_until_harvest)

        if stress_ratio is None:
            reason = (
                f"No sensor history {ref_label} yet — using this farm's historical "
                f"median of {base} days."
            )
        elif stress_ratio == 0:
            reason = (
                f"Conditions {ref_label} ({days_with_data}d of data) stayed within the "
                f"good range (avg {avg_temp}°C / {avg_hum}%) — tracking toward the "
                f"faster end of this farm's historical range ({lo}-{hi}d)."
            )
        else:
            pct = round(stress_ratio * 100)
            reason = (
                f"{pct}% of days {ref_label} showed stress conditions "
                f"(avg {avg_temp}°C / {avg_hum}% humidity, vs. optimal "
                f"{TEMP_MIN}-{TEMP_MAX}°C / {HUMIDITY_MIN}-{HUMIDITY_MAX}%) — "
                f"pushed toward the slower end of this farm's historical range ({lo}-{hi}d)."
            )

        result.append({
            "block_id":           block_id,
            "days_planted":       (today - planted).days,
            "days_elapsed":       days_elapsed,
            "reference_point":    ref_label,
            "est_harvest_date":   str(est_harvest_date),
            "est_range":          est_range,
            "days_until_harvest": days_until_harvest,
            "stress_ratio":       round(stress_ratio, 2) if stress_ratio is not None else None,
            "category":           category,
            "reason":             reason,
        })

    return result


def _build_advice_prompt(today, sensor_text, flags, block_summary, lang="English"):
    """
    Groq is only asked to write the explanation paragraph. It receives
    already-computed signals (not raw sensor tables to reason over), so it
    can't invent or contradict the numbers Python already worked out.
    """
    return f"""You are an expert grey oyster mushroom farm advisor.

IMPORTANT: Respond entirely in {lang}. All text in the JSON values must be in {lang}.

Today: {today}

=== CURRENT SENSOR READINGS ===
{sensor_text}

=== COMPUTED HARVEST SIGNAL SUMMARY (already calculated, do not recalculate) ===
{block_summary}

=== GROW FACTS — GREY OYSTER MUSHROOM ===
Optimal conditions: temp {TEMP_MIN}-{TEMP_MAX}°C | humidity {HUMIDITY_MIN}-{HUMIDITY_MAX}% | CO2 < {CO2_MAX:.0f} ppm

=== YOUR TASK ===
Write 1-3 short simple sentences summarizing current conditions and what action
to take if needed. Use the computed signal summary to explain WHY blocks are
running fast/slow if relevant — do not invent your own day counts. You may
reference the 24h streaks/peaks/lows in the sensor readings if relevant.

=== CRITICAL RESPONSE RULES ===
- "advice" MUST be a plain flowing paragraph — NOT bullet points, NOT a list.
- Do not state specific harvest dates yourself; those come from the app, not you.

=== RESPONSE FORMAT ===
Respond in valid JSON only. No text outside the JSON.

{{
  "advice": "flowing paragraph environment advice here"
}}"""


def get_harvest_advice(username, lang="English"):
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return None, "GROQ_API_KEY not found in .env file."

    conn = get_db_connection()
    try:
        history_24h, latest = _fetch_sensor_history_24h(conn)
        blocks = _fetch_blocks(conn, username)

        if not blocks:
            return None, "No active blocks found. Please record planting data first."

        today = datetime.date.today()

        # Fetch daily sensor history covering every active block's cycle,
        # starting from the earliest reference date among them, in ONE query.
        # This is separate from the 24h history above — it's what drives
        # the stress_ratio / harvest-date prediction, not the current-
        # conditions summary shown to the user.
        ref_dates = []
        for _bid, planted_date, harvest_count, last_harvest_date in blocks:
            hc = int(harvest_count or 0)
            try:
                ref_dates.append(
                    datetime.date.fromisoformat(last_harvest_date) if hc else
                    datetime.date.fromisoformat(planted_date)
                )
            except Exception:
                pass
        earliest = min(ref_dates) if ref_dates else today
        daily_series = _fetch_daily_sensor_series(conn, earliest.isoformat())
    finally:
        conn.close()

    sensor_text, flags = _build_sensor_summary(history_24h, latest)

    # ── All block-level harvest math happens in Python ──────────────────────
    computed_blocks = _compute_blocks(blocks, today, daily_series)

    counts = {}
    for b in computed_blocks:
        counts[b["category"]] = counts.get(b["category"], 0) + 1
    stressed = [b for b in computed_blocks if (b["stress_ratio"] or 0) > 0.3]
    block_summary = (
        f"{len(computed_blocks)} active blocks. "
        f"Status counts: {counts}. "
        f"{len(stressed)} block(s) showing significant stress days this cycle."
    )

    # ── Groq only writes the explanation paragraph ──────────────────────────
    try:
        client = Groq(api_key=api_key)
        prompt = _build_advice_prompt(today, sensor_text, flags, block_summary, lang=lang)
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


_ALIASES = {
    "FALLBACK_AVG_FIRST_HARVEST": "FIRST_HARVEST_BASE",
    "FALLBACK_FIRST_HARVEST":     "FIRST_HARVEST_BASE",
    "AVG_FIRST_HARVEST_DAYS":     "FIRST_HARVEST_BASE",
    "FIRST_HARVEST_DAYS":         "FIRST_HARVEST_BASE",
    "FALLBACK_AVG_REHARVEST":     "REHARVEST_BASE",
    "FALLBACK_REHARVEST":         "REHARVEST_BASE",
    "AVG_REHARVEST_DAYS":         "REHARVEST_BASE",
    "REHARVEST_DAYS":             "REHARVEST_BASE",
    "MIN_FIRST_HARVEST_DAYS":     "FIRST_HARVEST_MIN",
    "MAX_FIRST_HARVEST_DAYS":     "FIRST_HARVEST_MAX",
    "MIN_REHARVEST_DAYS":         "REHARVEST_MIN",
    "MAX_REHARVEST_DAYS":         "REHARVEST_MAX",
}


def __getattr__(name):
    target = _ALIASES.get(name)
    if target is not None:
        print(f"[groq_advisor] NOTE: '{name}' was imported but not defined here — "
              f"using {target}={globals()[target]} instead. "
              f"Run: findstr /n \"from groq_advisor import\" views\\planting.py "
              f"to find the real import list and fix this properly.")
        return globals()[target]
    raise AttributeError(
        f"module 'groq_advisor' has no attribute '{name}'. "
        f"This name isn't recognized even as an alias — please share the exact "
        f"'from groq_advisor import (...)' block from your planting.py so it can be added."
    )