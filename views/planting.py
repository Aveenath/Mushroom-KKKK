import streamlit as st
import datetime
from utils import get_db_connection, get_local_now, db_read_sql
from translations import t


def _validate_and_normalize(block_id):
    raw = block_id.strip()
    if not raw:
        return None, "Block ID cannot be empty."
    return raw.upper(), None


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
    st.title(t('plant_title'))

    # Add new columns to existing tables if not yet present
    conn = get_db_connection()
    for alter_sql in [
        "ALTER TABLE planting_records ADD COLUMN harvest_count INTEGER DEFAULT 0",
        "ALTER TABLE planting_records ADD COLUMN last_harvest_date TEXT",
        "ALTER TABLE planting_records ADD COLUMN retired INTEGER DEFAULT 0",
        "ALTER TABLE planting_records ADD COLUMN cycle INTEGER DEFAULT 1",
        "ALTER TABLE harvest_history ADD COLUMN weight_kg REAL",
        "ALTER TABLE harvest_history ADD COLUMN cycle INTEGER DEFAULT 1",
        "ALTER TABLE harvest_history ADD COLUMN predicted_date_snapshot TEXT",
    ]:
        try:
            conn.execute(alter_sql)
            conn.commit()
        except Exception:
            pass
    conn.close()

    # Silent auto-refresh: recompute predicted_harvest from current sensor
    # data every time this page loads. No Groq call, no API key needed —
    # only the "Get AI Recommendation" button below calls Groq (for the
    # advice paragraph). This keeps saved dates from going stale between
    # manual clicks.
    try:
        from groq_advisor import refresh_predicted_dates_cached
        refresh_predicted_dates_cached(st.session_state.username)
    except Exception:
        pass

    # ── RECORD NEW BLOCK ──────────────────────────────────────────────────────
    st.subheader(t('plant_new_block'))

    if st.session_state.get('_record_block_success'):
        st.success(st.session_state.pop('_record_block_success'))
    if st.session_state.get('_record_block_errors'):
        for err_msg in st.session_state.pop('_record_block_errors'):
            st.error(err_msg)

    with st.form("planting_form"):
        st.caption(t('plant_caption'))
        block_id    = st.text_input(t('plant_block_id'))
        species     = st.selectbox(t('plant_species'), ["Oyster Mushroom"])
        planted_date = st.date_input(t('plant_date'), get_local_now().date())
        notes       = st.text_area(t('plant_notes'))

        if st.form_submit_button(t('plant_submit')):
            if not block_id.strip():
                st.error("Please enter at least one Block ID.")
            else:
                raw_ids      = [b.strip() for b in block_id.split(",") if b.strip()]
                success_list, error_list = [], []

                for raw in raw_ids:
                    clean_id, err = _validate_and_normalize(raw)
                    if err:
                        error_list.append(f"{raw}: {err}")
                        continue
                    conn   = get_db_connection()
                    # Auto-retire any leftover active cycles before replanting
                    conn.execute(
                        "UPDATE planting_records SET retired = 1 WHERE block_id = ? AND username = ? AND (retired = 0 OR retired IS NULL)",
                        (clean_id, st.session_state.username)
                    )
                    conn.commit()
                    max_cycle_row = conn.execute(
                        "SELECT MAX(cycle) FROM planting_records WHERE block_id = ? AND username = ?",
                        (clean_id, st.session_state.username)
                    ).fetchone()
                    next_cycle    = (max_cycle_row[0] or 0) + 1
                    planted_str   = planted_date.strftime("%Y-%m-%d")
                    first_harvest = (planted_date + datetime.timedelta(days=14)).strftime("%Y-%m-%d")
                    conn.execute(
                        "INSERT INTO planting_records "
                        "(block_id, species, planted_date, notes, predicted_harvest, username, harvest_count, last_harvest_date, retired, cycle) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (clean_id, species, planted_str, notes, first_harvest,
                         st.session_state.username, 0, None, 0, next_cycle)
                    )
                    conn.commit()
                    conn.close()
                    if next_cycle > 1:
                        success_list.append(f"{clean_id} (Cycle {next_cycle} — replanted)")
                    else:
                        success_list.append(clean_id)

                if success_list:
                    st.session_state['_record_block_success'] = f"✅ Recorded: {', '.join(success_list)} — First harvest in 14 days!"
                if error_list:
                    st.session_state['_record_block_errors'] = error_list
                st.rerun()

    st.markdown("---")

    # ── MARK AS HARVESTED / RETIRE ────────────────────────────────────────────
    st.subheader(t('plant_mark'))
    if st.session_state.get('_harvest_success'):
        st.success(st.session_state.pop('_harvest_success'))
    if st.session_state.get('_harvest_error'):
        st.error(st.session_state.pop('_harvest_error'))
    conn = get_db_connection()
    active_blocks_df = db_read_sql(
        "SELECT block_id FROM planting_records "
        "WHERE username = ? AND (retired = 0 OR retired IS NULL) ORDER BY block_id",
        conn, params=(st.session_state.username,)
    )
    conn.close()

    if not active_blocks_df.empty:
        with st.form("mark_harvested_form"):
            selected_blocks     = st.multiselect(
                t('plant_select'),
                active_blocks_df['block_id'].tolist(),
                placeholder="Select one or more blocks..."
            )
            actual_harvest_date = st.date_input(t('plant_harvest_date'), get_local_now().date())
            harvest_weight      = st.number_input(t('plant_weight'), min_value=0.0, step=0.1, format="%.2f")
            retire_block        = st.checkbox(t('plant_retire'))

            if st.form_submit_button(t('plant_confirm')):
                if not selected_blocks:
                    st.warning("Please select at least one block.")
                else:
                    success_list, error_list = [], []
                    conn = get_db_connection()
                    for selected_block in selected_blocks:
                        try:
                            row_df = db_read_sql(
                                "SELECT * FROM planting_records WHERE block_id = ? AND username = ? AND (retired = 0 OR retired IS NULL) LIMIT 1",
                                conn, params=(selected_block, st.session_state.username)
                            )
                            if row_df.empty:
                                error_list.append(selected_block)
                                continue
                            row          = row_df.iloc[0]
                            new_hc       = int(row.get('harvest_count') or 0) + 1
                            current_cycle = int(row.get('cycle') or 1)
                            harvest_date_str = actual_harvest_date.strftime("%Y-%m-%d")

                            # Snapshot whatever predicted_harvest says right
                            # now — before it moves on to the next cycle's
                            # prediction — so report.py can later show what
                            # the AI actually predicted for THIS harvest,
                            # instead of losing it to the next refresh.
                            pred_snapshot = row.get('predicted_harvest')
                            pred_snapshot = str(pred_snapshot) if (pred_snapshot is not None and str(pred_snapshot).strip()) else None

                            conn.execute(
                                "UPDATE planting_records "
                                "SET harvest_count = ?, last_harvest_date = ?, retired = ? "
                                "WHERE block_id = ? AND username = ? AND cycle = ?",
                                (new_hc, harvest_date_str,
                                 1 if retire_block else 0,
                                 selected_block, st.session_state.username, current_cycle)
                            )
                            weight_val = float(harvest_weight) if harvest_weight and harvest_weight > 0 else None
                            conn.execute(
                                "INSERT INTO harvest_history "
                                "(block_id, harvest_number, harvest_date, username, weight_kg, cycle, predicted_date_snapshot) "
                                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                                (selected_block, new_hc, harvest_date_str, st.session_state.username,
                                 weight_val, current_cycle, pred_snapshot)
                            )
                            success_list.append(f"{selected_block} (Cycle {current_cycle} #{new_hc})")
                        except Exception:
                            error_list.append(selected_block)
                    conn.commit()

                    # Auto-retrain: every harvest just written to Turso is
                    # now a new (planted -> harvested) training example.
                    # This checks the threshold and only actually retrains
                    # every RETRAIN_EVERY_N_NEW_SAMPLES harvests — cheap to
                    # call every time, and wrapped so a predictor problem
                    # never blocks harvest recording from completing.
                    if success_list:
                        try:
                            from harvest_predictor import maybe_retrain
                            retrain_result = maybe_retrain(st.session_state.username, conn=conn)
                            if retrain_result.get("trained"):
                                st.toast(
                                    f"🎯 Harvest predictor retrained on "
                                    f"{retrain_result['n_samples']} samples "
                                    f"(MAE: {retrain_result['cv_mae_days']}d)",
                                    icon="🧠"
                                )
                        except Exception:
                            pass  # predictor is optional; never block harvest recording

                    conn.close()

                    if success_list:
                        if retire_block:
                            st.session_state['_harvest_success'] = f"✅ Retired: {', '.join(success_list)}"
                        else:
                            st.session_state['_harvest_success'] = f"✅ Harvested: {', '.join(success_list)} — Next harvest in 15 days!"
                    if error_list:
                        st.session_state['_harvest_error'] = f"❌ Failed: {', '.join(error_list)}"
                    st.rerun()
    else:
        st.info(t('plant_no_active'))

    st.caption(t('plant_hint'))