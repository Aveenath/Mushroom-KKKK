import os
import json
import datetime
import streamlit as st
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
    stress_ratio (and, when a trained model exists, the full feature set)
    that drives the harvest-date prediction.
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
            """SELECT block_id, cycle, planted_date, harvest_count, last_harvest_date
               FROM planting_records
               WHERE username = ? AND (retired = 0 OR retired IS NULL)
               ORDER BY block_id""",
            (username,)
        )
        return cur.fetchall()
    except Exception:
        return []


def _build_prior_days_map(conn, username, blocks):
    """
    For each active block, prior_observed_days = how long THIS block's most
    recently COMPLETED interval took (harvest N-1 -> N, or planted ->
    harvest 1). This is one of the trained model's features — it lets the
    model see whether this specific block has personally been running fast
    or slow, not just farm-wide averages. Returns {(block_id, cycle): days}.
    """
    prior = {}
    try:
        harvest_rows = conn.execute(
            "SELECT block_id, cycle, harvest_number, harvest_date FROM harvest_history "
            "WHERE username = ? ORDER BY block_id, cycle, harvest_number ASC",
            (username,)
        ).fetchall()
    except Exception:
        harvest_rows = []

    by_key = {}
    for block_id, cycle, h_num, h_date in harvest_rows:
        by_key.setdefault((str(block_id), int(cycle or 1)), []).append((int(h_num), h_date))

    for block_id, cycle, planted_date, harvest_count, _last in blocks:
        key = (str(block_id), int(cycle or 1))
        hc = int(harvest_count or 0)
        if hc == 0:
            prior[key] = 0
            continue
        seq = sorted(by_key.get(key, []))
        reference_str = planted_date if hc == 1 else next(
            (d for n, d in seq if n == hc - 1), None
        )
        current_str = next((d for n, d in seq if n == hc), None)
        if not reference_str or not current_str:
            prior[key] = 0
            continue
        try:
            ref = datetime.date.fromisoformat(str(reference_str))
            cur_d = datetime.date.fromisoformat(str(current_str))
            prior[key] = max((cur_d - ref).days, 0)
        except Exception:
            prior[key] = 0

    return prior


def _window_rows(daily_series, start_date, end_date):
    """Daily sensor rows falling within [start_date, end_date] inclusive."""
    return [
        row for row in daily_series
        if start_date.isoformat() <= row[0] <= end_date.isoformat()
    ]


def _compute_stress_ratio(window):
    """
    Fraction of days in `window` where conditions look like the
    "kering"/stress pattern seen in this farm's own history (low humidity
    + high temp). Returns (stress_ratio, days_with_data, avg_temp,
    avg_humidity, avg_co2). Feeds the fixed-baseline fallback below —
    unlike the 24h summary, this looks at the whole cycle so far.
    """
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
    Fixed-baseline fallback, used only when no trained model exists yet
    (or the model call fails for any reason). No sensor data for this
    cycle -> historical median. Otherwise interpolate between best-case
    and worst-case durations seen in real history, based on how stressed
    the environment has been.
    """
    if stress_ratio is None:
        return base
    return round(min_d + stress_ratio * (max_d - min_d), 1)


def _try_ml_days(is_first, harvest_number, cycle, window, prior_days):
    """
    Attempt a prediction from the trained harvest_predictor.py model, using
    the same engineered features it was trained on. Returns None — never
    raises — if no model has been trained yet, or on any failure; caller
    falls back to the fixed baseline in that case. This is what lets every
    new harvest recorded in Turso actually change future predictions,
    instead of just sitting in the table unused.
    """
    try:
        from harvest_predictor import predict_observed_days, _engineer_window_features
    except Exception:
        return None
    try:
        feats = _engineer_window_features(window)
        feats["is_first_harvest"]    = 1 if is_first else 0
        feats["harvest_number"]      = harvest_number
        feats["cycle"]               = cycle
        feats["prior_observed_days"] = prior_days
        return predict_observed_days(feats)
    except Exception:
        return None


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


def _compute_blocks(blocks, today, daily_series, prior_days_map=None):
    """
    All harvest-date math happens here, in plain Python — Groq never
    touches these numbers, it only explains them in plain language, which
    keeps predictions reproducible. For each block this now tries the
    trained harvest_predictor.py model FIRST (if one exists), and only
    falls back to the fixed empirical baseline when no model is trained
    yet or the model call fails for any reason.
    """
    prior_days_map = prior_days_map or {}
    result = []

    for block_id, cycle, planted_date, harvest_count, last_harvest_date in blocks:
        hc = int(harvest_count or 0)
        cycle = int(cycle or 1)

        try:
            planted = datetime.date.fromisoformat(planted_date)
        except Exception:
            planted = today

        if hc == 0:
            reference   = planted
            base, lo, hi = FIRST_HARVEST_BASE, FIRST_HARVEST_MIN, FIRST_HARVEST_MAX
            ref_label   = "since planting"
            is_first    = True
        else:
            try:
                reference = datetime.date.fromisoformat(last_harvest_date)
            except Exception:
                reference = today
            base, lo, hi = REHARVEST_BASE, REHARVEST_MIN, REHARVEST_MAX
            ref_label   = "since last harvest"
            is_first    = False

        days_elapsed = (today - reference).days

        window = _window_rows(daily_series, reference, today)
        stress_ratio, days_with_data, avg_temp, avg_hum, avg_co2 = _compute_stress_ratio(window)

        prior_days = prior_days_map.get((str(block_id), cycle), 0)
        ml_days = _try_ml_days(is_first, hc + 1, cycle, window, prior_days)

        if ml_days is not None:
            target_days = ml_days
            source = "model"
        else:
            target_days = _predict_target_days(base, lo, hi, stress_ratio)
            source = "baseline"

        days_until_harvest = round(target_days - days_elapsed, 1)
        est_harvest_date = reference + datetime.timedelta(days=round(target_days))

        # Historical best/worst-case dates, for an honest range (not fake precision)
        est_range_low  = reference + datetime.timedelta(days=lo)
        est_range_high = reference + datetime.timedelta(days=hi)
        est_range = f"{est_range_low} to {est_range_high}"

        category = _categorize(days_until_harvest)

        if source == "model":
            # The trained model produced target_days directly from engineered
            # sensor + history features — it did NOT use the base/lo/hi
            # baseline formula below, so the reason text must describe the
            # model's own basis instead of borrowing the baseline's wording.
            if stress_ratio is None:
                reason = (
                    f"Predicted by the trained harvest model for this block "
                    f"({ref_label}) — no sensor history available for this "
                    f"window, so the model relied on the block's harvest-number, "
                    f"cycle, and prior-interval features."
                )
            else:
                pct = round(stress_ratio * 100)
                reason = (
                    f"Predicted by the trained harvest model for this block "
                    f"({ref_label}), using {days_with_data}d of sensor data "
                    f"(avg {avg_temp}°C / {avg_hum}%, {pct}% stress days) plus "
                    f"this block's own harvest history."
                )
        elif stress_ratio is None:
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
            "prediction_source":  source,
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


def compute_harvest_predictions(username):
    """
    Recomputes every active block's harvest-date estimate from sensor
    stress data (and the trained LightGBM model, if one exists) — pure
    Python, no Groq call, no API key required. This is safe to run on
    every page load for a silent auto-refresh of predicted_harvest.
    Returns (computed_blocks, error).
    """
    conn = get_db_connection()
    try:
        blocks = _fetch_blocks(conn, username)
        if not blocks:
            return [], None

        today = datetime.date.today()
        ref_dates = []
        for _bid, _cycle, planted_date, harvest_count, last_harvest_date in blocks:
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
        prior_days_map = _build_prior_days_map(conn, username, blocks)
    finally:
        conn.close()

    computed_blocks = _compute_blocks(blocks, today, daily_series, prior_days_map)
    return computed_blocks, None


@st.cache_data(ttl=300)
def refresh_predicted_dates_cached(username):
    """
    Cached wrapper around refresh_predicted_dates. The underlying function
    does a full sensor/DB scan per active block, and without this it would
    re-run that scan on every single Streamlit rerun (which fires on every
    widget interaction, not just page loads). This limits it to at most
    once every 5 minutes per user — predictions stay just as "auto-updating"
    from the user's point of view, just without redundant repeat work.
    """
    return refresh_predicted_dates(username)


def refresh_predicted_dates(username):
    """
    Silent auto-refresh: recompute predicted_harvest for every active
    block from current sensor data and save it — no Groq call. Meant to
    be called at the top of any page (planting.py, report.py) so the
    saved date is never far behind current sensor readings, without
    requiring a manual "Get AI Recommendation" click each time.
    """
    computed_blocks, err = compute_harvest_predictions(username)
    if err or not computed_blocks:
        return False
    conn = get_db_connection()
    try:
        for b in computed_blocks:
            conn.execute(
                "UPDATE planting_records SET predicted_harvest = ? "
                "WHERE block_id = ? AND username = ? AND (retired = 0 OR retired IS NULL)",
                (b["est_harvest_date"], b["block_id"], username)
            )
        conn.commit()
    finally:
        conn.close()
    return True


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
        # the stress_ratio / feature engineering for the harvest-date
        # prediction, not the current-conditions summary shown to the user.
        ref_dates = []
        for _bid, _cycle, planted_date, harvest_count, last_harvest_date in blocks:
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

        prior_days_map = _build_prior_days_map(conn, username, blocks)
    finally:
        conn.close()

    sensor_text, flags = _build_sensor_summary(history_24h, latest)

    # ── All block-level harvest math happens in Python ──────────────────────
    computed_blocks = _compute_blocks(blocks, today, daily_series, prior_days_map)

    counts = {}
    for b in computed_blocks:
        counts[b["category"]] = counts.get(b["category"], 0) + 1
    stressed = [b for b in computed_blocks if (b["stress_ratio"] or 0) > 0.3]
    model_used = any(b.get("prediction_source") == "model" for b in computed_blocks)
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
        "model_used":      model_used,
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