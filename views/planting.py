import streamlit as st
import pandas as pd
import datetime
import re
from utils import get_db_connection, get_local_now, db_read_sql


def _validate_and_normalize(block_id):
    raw = block_id.strip()
    if not raw.startswith('B'):
        return None, "Block ID must start with uppercase 'B' (e.g. B1, B001). Lowercase 'b' is not allowed."
    match = re.match(r'^B(\d+)$', raw)
    if not match:
        return None, "Invalid format. Use B followed by a number only (e.g. B1, B099, B244)."
    number = int(match.group(1))
    if number < 1 or number > 244:
        return None, f"Block number must be between 1 and 244. Got: {number}."
    return f"B{number}", None


def _get_next_harvest(planted_date_str, harvest_count, last_harvest_date_str):
    planted = datetime.date.fromisoformat(planted_date_str)
    if harvest_count == 0:
        return planted + datetime.timedelta(days=14)
    last = datetime.date.fromisoformat(last_harvest_date_str)
    return last + datetime.timedelta(days=15)


def _get_status(next_harvest_date):
    today = get_local_now().date()
    days_left = (next_harvest_date - today).days
    if days_left < 0:
        return f"Overdue ({abs(days_left)}d ago)", days_left
    elif days_left == 0:
        return "Harvest NOW", 0
    elif days_left == 1:
        return "Tomorrow", 1
    elif days_left <= 3:
        return "Soon", days_left
    else:
        return f"In {days_left} days", days_left


def show():
    st.title("🌱 Harvest Schedule Manager")

    # Add new columns to existing table if not yet present
    conn = get_db_connection()
    for alter_sql in [
        "ALTER TABLE planting_records ADD COLUMN harvest_count INTEGER DEFAULT 0",
        "ALTER TABLE planting_records ADD COLUMN last_harvest_date TEXT",
        "ALTER TABLE planting_records ADD COLUMN retired INTEGER DEFAULT 0",
    ]:
        try:
            conn.execute(alter_sql)
            conn.commit()
        except Exception:
            pass
    conn.close()

    # --- SEARCH ---
    st.subheader("🔍 Search Block")
    search_id = st.text_input("Enter Block ID (e.g. B1, B001, B244)")
    if search_id.strip():
        norm_id, err = _validate_and_normalize(search_id)
        if err:
            st.error(err)
        else:
            conn = get_db_connection()
            # FIX: use db_read_sql instead of pd.read_sql
            result = db_read_sql(
                "SELECT * FROM planting_records WHERE block_id = ? AND username = ?",
                conn, params=(norm_id, st.session_state.username)
            )
            conn.close()
            if result.empty:
                st.warning(f"No record found for Block **{norm_id}**.")
            else:
                row = result.iloc[0]
                hc = int(row.get('harvest_count') or 0)
                lhd = row.get('last_harvest_date') or None
                retired = int(row.get('retired') or 0)
                if retired:
                    st.error(f"Block **{row['block_id']}** has been retired after {hc} harvest(s).")
                else:
                    next_date = _get_next_harvest(row['planted_date'], hc, lhd)
                    status_label, _ = _get_status(next_date)
                    st.success(f"Block **{row['block_id']}** found!")
                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric("Species", row['species'])
                    c2.metric("Planted", row['planted_date'])
                    c3.metric("Total Harvests Done", hc)
                    c4.metric("Next Harvest Date", str(next_date))
                    st.info(f"Status: {status_label}  |  Next interval: {14 if hc == 0 else 15} days")

    # ── AI HARVEST ADVISOR ────────────────────────────────────────────────────
    st.markdown("---")
    st.subheader("🤖 AI Harvest Advisor")

    if st.button("🔮 Get AI Recommendation", type="primary"):
        with st.spinner("Getting AI harvest recommendations..."):
            from groq_advisor import get_harvest_advice
            result, error = get_harvest_advice(st.session_state.username)

        if error:
            st.error(f"❌ {error}")

        elif result:
            st.success("✅ AI Analysis Complete!")

            if result.get("blocks"):
                st.markdown("#### 🍄 Harvest Predictions per Block")
                cat_map = {
                    "HARVEST_TODAY": "🔴",
                    "HARVEST_WEEK":  "🟡",
                    "MONITOR":       "🟢",
                    "WAIT":          "⬛",
                }
                blocks_df = pd.DataFrame(result["blocks"])
                blocks_df["Status"] = blocks_df["category"].map(
                    lambda c: f"{cat_map.get(c, '❓')} {c.replace('_', ' ')}"
                )

                # FIX: use db_read_sql instead of pd.read_sql
                conn = get_db_connection()
                db_records = db_read_sql(
                    "SELECT block_id, planted_date, harvest_count, last_harvest_date "
                    "FROM planting_records WHERE username = ? AND (retired = 0 OR retired IS NULL)",
                    conn, params=(st.session_state.username,)
                )
                conn.close()

                def _official_date(block_id):
                    row = db_records[db_records['block_id'] == block_id]
                    if row.empty:
                        return "-"
                    r = row.iloc[0]
                    hc = int(r.get('harvest_count') or 0)
                    lhd = r.get('last_harvest_date') or None
                    return str(_get_next_harvest(r['planted_date'], hc, lhd))

                blocks_df["official_date"] = blocks_df["block_id"].apply(_official_date)

                display_cols = ["block_id", "days_planted", "official_date",
                                "est_harvest_date", "days_until_harvest", "Status", "reason"]
                display_cols = [c for c in display_cols if c in blocks_df.columns]
                st.dataframe(
                    blocks_df[display_cols].rename(columns={
                        "block_id":           "Block",
                        "days_planted":       "Days Grown",
                        "official_date":      "📅 Official Date",
                        "est_harvest_date":   "🤖 AI Adjusted Date",
                        "days_until_harvest": "Days Until Harvest",
                        "reason":             "Reason",
                    }),
                    use_container_width=True,
                    hide_index=True
                )
                st.caption(
                    "ℹ️ **📅 Official Date** = fixed schedule based on planting/harvest records. "
                    "**🤖 AI Adjusted Date** = estimate only, adjusted for current sensor conditions "
                    "(CO2, humidity). AI dates do **not** affect your official schedule."
                )
                col1, col2, col3, col4 = st.columns(4)
                counts = blocks_df["category"].value_counts()
                col1.metric("🔴 Harvest Today", counts.get("HARVEST_TODAY", 0))
                col2.metric("🟡 This Week",     counts.get("HARVEST_WEEK", 0))
                col3.metric("🟢 Monitor",        counts.get("MONITOR", 0))
                col4.metric("⬛ Wait",           counts.get("WAIT", 0))
            else:
                st.info("No block predictions returned.")

            if result.get("advice"):
                st.markdown("#### 💡 Environment Advice")
                st.info(result["advice"])

            if not result.get("blocks") and result.get("raw"):
                st.markdown("#### Raw AI Response")
                st.markdown(result["raw"])

    # --- RECORD NEW BLOCK ---
    st.markdown("---")
    st.subheader("➕ Record New Block")
    with st.form("planting_form"):
        st.caption("For multiple blocks with same planting date, separate IDs with comma e.g. B1, B2, B3")
        block_id = st.text_input("Block ID(s) (B1 – B244, uppercase B only)")
        species = st.selectbox("Mushroom Species", ["Oyster Mushroom"])
        planted_date = st.date_input("Planting Date", get_local_now().date())
        notes = st.text_area("Initial Conditions / Notes")

        if st.form_submit_button("Record Block"):
            if not block_id.strip():
                st.error("Please enter at least one Block ID.")
            else:
                raw_ids = [b.strip() for b in block_id.split(",") if b.strip()]
                success_list, error_list = [], []

                for raw in raw_ids:
                    clean_id, err = _validate_and_normalize(raw)
                    if err:
                        error_list.append(f"{raw}: {err}")
                        continue
                    conn = get_db_connection()
                    # FIX: check duplicate using execute/fetchone (no pd.read_sql needed)
                    duplicate = conn.execute(
                        "SELECT block_id FROM planting_records WHERE block_id = ? AND username = ?",
                        (clean_id, st.session_state.username)
                    ).fetchone()
                    if duplicate:
                        error_list.append(f"{clean_id}: already exists")
                        conn.close()
                        continue
                    planted_str = planted_date.strftime("%Y-%m-%d")
                    first_harvest = (planted_date + datetime.timedelta(days=14)).strftime("%Y-%m-%d")
                    conn.execute(
                        "INSERT INTO planting_records (block_id, species, planted_date, notes, predicted_harvest, username, harvest_count, last_harvest_date, retired) VALUES (?,?,?,?,?,?,?,?,?)",
                        (clean_id, species, planted_str, notes, first_harvest, st.session_state.username, 0, None, 0)
                    )
                    conn.commit()
                    conn.close()
                    success_list.append(clean_id)

                if success_list:
                    st.success(f"✅ Recorded: {', '.join(success_list)} — First harvest in 14 days!")
                if error_list:
                    for e in error_list:
                        st.error(e)
                if success_list:
                    st.rerun()

    st.markdown("---")

    # --- MARK AS HARVESTED / RETIRE ---
    st.subheader("✅ Mark Block as Harvested")
    conn = get_db_connection()
    # FIX: use db_read_sql instead of pd.read_sql
    active_blocks_df = db_read_sql(
        "SELECT block_id FROM planting_records WHERE username = ? AND (retired = 0 OR retired IS NULL) ORDER BY block_id",
        conn, params=(st.session_state.username,)
    )
    conn.close()

    if not active_blocks_df.empty:
        with st.form("mark_harvested_form"):
            selected_blocks = st.multiselect(
                "Select Block(s) to Harvest",
                active_blocks_df['block_id'].tolist(),
                placeholder="Select one or more blocks..."
            )
            actual_harvest_date = st.date_input("Actual Harvest Date", get_local_now().date())
            retire_block = st.checkbox("Retire all selected blocks after this harvest")

            if st.form_submit_button("✅ Confirm"):
                if not selected_blocks:
                    st.warning("Please select at least one block.")
                else:
                    success_list, error_list = [], []
                    conn = get_db_connection()
                    for selected_block in selected_blocks:
                        try:
                            # FIX: use db_read_sql instead of pd.read_sql
                            # FIX: removed rowid — UPDATE by block_id + username instead
                            row_df = db_read_sql(
                                "SELECT * FROM planting_records WHERE block_id = ? AND username = ? LIMIT 1",
                                conn, params=(selected_block, st.session_state.username)
                            )
                            if row_df.empty:
                                error_list.append(selected_block)
                                continue
                            row = row_df.iloc[0]
                            new_hc = int(row.get('harvest_count') or 0) + 1
                            # FIX: UPDATE by block_id + username — no rowid needed
                            conn.execute(
                                "UPDATE planting_records SET harvest_count = ?, last_harvest_date = ?, retired = ? WHERE block_id = ? AND username = ?",
                                (new_hc, actual_harvest_date.strftime("%Y-%m-%d"), 1 if retire_block else 0, selected_block, st.session_state.username)
                            )
                            success_list.append(f"{selected_block} (#{new_hc})")
                        except Exception as e:
                            error_list.append(selected_block)
                    conn.commit()
                    conn.close()

                    if success_list:
                        if retire_block:
                            st.success(f"✅ Retired: {', '.join(success_list)}")
                        else:
                            st.success(f"✅ Harvested: {', '.join(success_list)} — Next harvest in 15 days!")
                    if error_list:
                        st.error(f"❌ Failed: {', '.join(error_list)}")
                    st.rerun()
    else:
        st.info("No active blocks. All blocks are retired or none recorded yet.")

    st.markdown("---")

    # --- FULL SCHEDULE TABLE ---
    st.subheader("📋 Full Harvest Schedule")
    conn = get_db_connection()
    try:
        # FIX: use db_read_sql instead of pd.read_sql
        df_all = db_read_sql(
            "SELECT * FROM planting_records WHERE username = ? ORDER BY block_id",
            conn, params=(st.session_state.username,)
        )
    except Exception:
        df_all = pd.DataFrame()
    finally:
        conn.close()

    if not df_all.empty:
        schedule_rows = []
        for _, row in df_all.iterrows():
            hc = int(row.get('harvest_count') or 0)
            lhd = row.get('last_harvest_date') or None
            retired = int(row.get('retired') or 0)
            if retired:
                schedule_rows.append({
                    'Block ID': row['block_id'], 'Species': row['species'],
                    'Planted': row['planted_date'], 'Harvests Done': hc,
                    'Last Harvest': lhd or '-', 'Next Harvest': '-',
                    'Days Left': 9999, 'Status': 'Retired',
                })
            else:
                next_date = _get_next_harvest(row['planted_date'], hc, lhd)
                status_label, days_left = _get_status(next_date)
                schedule_rows.append({
                    'Block ID': row['block_id'], 'Species': row['species'],
                    'Planted': row['planted_date'], 'Harvests Done': hc,
                    'Last Harvest': lhd or '-', 'Next Harvest': str(next_date),
                    'Days Left': days_left, 'Status': status_label,
                })

        schedule_df = pd.DataFrame(schedule_rows).sort_values('Days Left')

        csv_export = schedule_df.drop(columns=['Days Left']).to_csv(index=False).encode('utf-8')
        st.download_button("📥 Export Schedule to CSV", data=csv_export, file_name="harvest_schedule.csv", mime="text/csv")

        schedule_df.insert(len(schedule_df.columns), "Delete?", False)

        active_df = schedule_df[schedule_df['Status'] != 'Retired']
        tab_all, tab_today, tab_week, tab_retired = st.tabs(["All Active", "🔴 Harvest Today / Overdue", "🟡 This Week", "⬛ Retired"])

        def _render_table(df, key):
            return st.data_editor(
                df.drop(columns=['Days Left']),
                column_config={"Delete?": st.column_config.CheckboxColumn("🗑️ Delete?", default=False)},
                disabled=["Block ID", "Species", "Planted", "Harvests Done", "Last Harvest", "Next Harvest", "Status"],
                hide_index=True,
                use_container_width=True,
                height=350,
                key=key
            )

        with tab_all:
            edited_all = _render_table(active_df.copy(), key="tbl_all")
        with tab_today:
            today_df = active_df[active_df['Days Left'] <= 0].copy()
            if today_df.empty:
                st.success("No blocks overdue or due today.")
            else:
                _render_table(today_df, key="tbl_today")
        with tab_week:
            week_df = active_df[active_df['Days Left'].between(1, 7)].copy()
            if week_df.empty:
                st.info("No blocks due in the next 7 days.")
            else:
                _render_table(week_df, key="tbl_week")
        with tab_retired:
            ret_df = schedule_df[schedule_df['Status'] == 'Retired'].copy()
            if ret_df.empty:
                st.info("No retired blocks yet.")
            else:
                _render_table(ret_df, key="tbl_retired")

        rows_to_delete = edited_all[edited_all["Delete?"] == True]
        if not rows_to_delete.empty:
            st.warning(f"{len(rows_to_delete)} block(s) selected for deletion.")
            if st.button("🚨 Confirm Delete Selected Blocks"):
                conn_del = get_db_connection()
                for blk in rows_to_delete['Block ID']:
                    conn_del.execute("DELETE FROM planting_records WHERE block_id = ? AND username = ?",
                                     (blk, st.session_state.username))
                conn_del.commit()
                conn_del.close()
                st.success("Deleted successfully!")
                st.rerun()
    else:
        st.info("No planting records found. Add a block above to get started.")