import streamlit as st
import pandas as pd
import datetime
from utils import get_db_connection, get_local_now, db_read_sql


def _get_next_harvest(planted_date_str, harvest_count, last_harvest_date_str):
    planted = datetime.date.fromisoformat(planted_date_str)
    if harvest_count == 0:
        return planted + datetime.timedelta(days=14)
    return datetime.date.fromisoformat(last_harvest_date_str) + datetime.timedelta(days=15)


def show():
    st.title(f"🍄 Welcome, {st.session_state.username}!")
    today = get_local_now().date()
    st.caption(f"Today: {today.strftime('%A, %d %B %Y')}")
    st.markdown("---")

    conn = get_db_connection()
    planting_df = db_read_sql(
        "SELECT * FROM planting_records WHERE username = ? ORDER BY block_id",
        conn, params=(st.session_state.username,)
    )
    sensor_df = db_read_sql(
        "SELECT temp, humidity, co2, ts FROM sensors ORDER BY ts DESC LIMIT 1", conn
    )
    conn.close()

    # ── Top metrics row ────────────────────────────────────────────────────────
    active_df  = planting_df[planting_df['retired'].astype(str).isin(['0', ''])] if not planting_df.empty else pd.DataFrame()
    retired_df = planting_df[planting_df['retired'].astype(str) == '1'] if not planting_df.empty else pd.DataFrame()

    overdue_blocks, due_today_blocks = [], []
    for _, row in active_df.iterrows():
        try:
            hc  = int(row.get('harvest_count') or 0)
            lhd = row.get('last_harvest_date') or None
            if hc > 0 and not lhd:
                continue
            next_date = _get_next_harvest(row['planted_date'], hc, lhd)

            pred_col = row.get('predicted_harvest')
            if pred_col is not None and pd.notna(pred_col) and str(pred_col).strip():
                try:
                    next_date = datetime.date.fromisoformat(str(pred_col))
                except Exception:
                    pass

            diff = (today - next_date).days
            if diff > 0:
                overdue_blocks.append((row['block_id'], next_date, diff))
            elif diff == 0:
                due_today_blocks.append((row['block_id'], next_date))
        except Exception:
            pass

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("🌱 Active Blocks",   len(active_df))
    m2.metric("⬛ Retired Blocks",  len(retired_df))
    m3.metric("🔴 Overdue Harvest", len(overdue_blocks))
    m4.metric("🟢 Due Today",       len(due_today_blocks))

    st.markdown("---")

    col_left, col_right = st.columns(2)

    # ── Harvest alerts ─────────────────────────────────────────────────────────
    with col_left:
        st.subheader("🍄 Harvest Status")

        upcoming = []
        for _, row in active_df.iterrows():
            try:
                hc  = int(row.get('harvest_count') or 0)
                lhd = row.get('last_harvest_date') or None
                if hc > 0 and not lhd:
                    continue
                next_date = _get_next_harvest(row['planted_date'], hc, lhd)

                pred_col = row.get('predicted_harvest')
                if pred_col is not None and pd.notna(pred_col) and str(pred_col).strip():
                    try:
                        next_date = datetime.date.fromisoformat(str(pred_col))
                    except Exception:
                        pass

                diff = (next_date - today).days
                if 1 <= diff <= 7:
                    upcoming.append((row['block_id'], next_date, diff))
            except Exception:
                pass
        upcoming = sorted(upcoming, key=lambda x: x[2])

        LIMIT = 10
        tab_overdue, tab_today, tab_week = st.tabs([
            f"🔴 Overdue ({len(overdue_blocks)})",
            f"✅ Due Today ({len(due_today_blocks)})",
            f"📅 This Week ({len(upcoming)})",
        ])

        with tab_overdue:
            if overdue_blocks:
                with st.container(height=300):
                    for block_id, date, days in overdue_blocks[:LIMIT]:
                        st.error(f"**{block_id}** — overdue by **{days} day(s)** (was due {date})")
                if len(overdue_blocks) > LIMIT:
                    st.caption(f"Showing {LIMIT} of {len(overdue_blocks)} overdue blocks.")
            else:
                st.success("No overdue blocks.")

        with tab_today:
            if due_today_blocks:
                with st.container(height=300):
                    for block_id, date in due_today_blocks[:LIMIT]:
                        st.success(f"**{block_id}** — due **TODAY** ({date})")
                if len(due_today_blocks) > LIMIT:
                    st.caption(f"Showing {LIMIT} of {len(due_today_blocks)} blocks due today.")
            else:
                st.info("No blocks due today.")

        with tab_week:
            if upcoming:
                with st.container(height=300):
                    for block_id, date, days in upcoming[:LIMIT]:
                        st.info(f"**{block_id}** — in {days} day(s) ({date})")
                if len(upcoming) > LIMIT:
                    st.caption(f"Showing {LIMIT} of {len(upcoming)} blocks due this week.")
            else:
                st.info("No blocks due in the next 7 days.")

    # ── Live sensor snapshot ───────────────────────────────────────────────────
    with col_right:
        st.subheader("📡 Live Sensor Snapshot")
        if not sensor_df.empty:
            latest = sensor_df.iloc[0]
            temp_val = float(latest['temp'])
            hum_val  = float(latest['humidity'])
            co2_val  = float(latest['co2'])

            s1, s2, s3 = st.columns(3)
            s1.metric("Temp",     f"{temp_val}°C",    "🟢 OK" if 25 <= temp_val <= 28 else ("🟡 Warn" if temp_val <= 30 else "🔴 High"))
            s2.metric("Humidity", f"{hum_val}%",      "🟢 OK" if 80 <= hum_val <= 90 else ("🟡 Warn" if hum_val >= 75 else "🔴 Low"))
            s3.metric("CO2",      f"{co2_val:.0f}ppm","🟢 OK" if co2_val < 800 else ("🟡 Warn" if co2_val < 1000 else "🔴 High"))

            try:
                ts_myt = pd.to_datetime(latest['ts']) + pd.Timedelta(hours=8)
                st.caption(f"Last sync: {ts_myt.strftime('%Y-%m-%d %H:%M')} MYT")
            except Exception:
                st.caption(f"Last sync: {latest['ts']}")

            if hum_val < 80:
                st.error("💧 Humidity low — turn misting ON")
            elif hum_val > 90:
                st.warning("✅ Humidity high — turn misting OFF")
            if temp_val > 30:
                st.error("🌡️ Temperature too high — check ventilation")
        else:
            st.info("No sensor data. Open Live Monitor to sync.")


    st.markdown("---")
    st.caption("Use the sidebar to navigate. Go to **Live Monitor** to sync fresh sensor data.")