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
        from groq_advisor import refresh_predicted_dates
        refresh_predicted_dates(st.session_state.username)
    except Exception:
        pass

    # ── SEARCH ────────────────────────────────────────────────────────────────
    st.subheader("🔍 Search Block")
    search_id = st.text_input("Enter Block ID (e.g. B1, B001, B244)")
    if search_id.strip():
        norm_id, err = _validate_and_normalize(search_id)
        if err:
            st.error(err)
        else:
            conn = get_db_connection()
            all_cycles_df = db_read_sql(
                "SELECT * FROM planting_records WHERE block_id = ? AND username = ? ORDER BY cycle DESC",
                conn, params=(norm_id, st.session_state.username)
            )
            conn.close()
            if all_cycles_df.empty:
                st.warning(f"No record found for Block **{norm_id}**.")
            else:
                active_df = all_cycles_df[all_cycles_df['retired'].astype(int) == 0]
                row = active_df.iloc[0] if not active_df.empty else all_cycles_df.iloc[0]
                hc      = int(row.get('harvest_count') or 0)
                lhd     = row.get('last_harvest_date') or None
                retired = int(row.get('retired') or 0)
                cycle   = int(row.get('cycle') or 1)
                total_cycles = len(all_cycles_df)

                st.success(f"Block **{row['block_id']}** found! — Cycle {cycle} of {total_cycles}")
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Species",            row['species'])
                c2.metric("Planted",             row['planted_date'])
                c3.metric("Total Harvests Done", hc)

                if retired:
                    c4.metric("Status", "Retired")
                    st.error(f"Block **{row['block_id']}** (Cycle {cycle}) has been retired after {hc} harvest(s). You can now replant it.")
                else:
                    next_date       = _get_next_harvest(row['planted_date'], hc, lhd)
                    status_label, _ = _get_status(next_date)
                    c4.metric("Next Harvest Date", str(next_date))
                    st.info(f"Status: {status_label}  |  Next interval: {14 if hc == 0 else 15} days")

                st.caption("💡 For full harvest history and weight details, go to **Generate Report** in the sidebar.")

    # ── AI HARVEST ADVISOR ────────────────────────────────────────────────────
    st.markdown("---")
    st.subheader("🤖 AI Harvest Advisor")

    LANG_OPTIONS = ["English", "BM", "Mandarin", "Tamil", "Japanese"]
    if "groq_lang" not in st.session_state:
        st.session_state.groq_lang = "English"
    st.session_state.groq_lang = st.selectbox(
        "🌐 AI Response Language",
        LANG_OPTIONS,
        index=LANG_OPTIONS.index(st.session_state.groq_lang),
        key="lang_planting"
    )

    # ── Trained predictor status + manual retrain ─────────────────────────
    # The predictor is optional and self-contained: if harvest_predictor.py
    # isn't importable yet, or nothing's been trained, everything above
    # still works off the fixed baseline in groq_advisor.py.
    try:
        from harvest_predictor import load_predictor_metadata, MIN_SAMPLES_TO_TRAIN, build_training_dataframe
        predictor_meta = load_predictor_metadata()
        current_sample_count = len(build_training_dataframe(st.session_state.username))
        if predictor_meta:
            baseline_n = predictor_meta.get('n_baseline_samples', 0)
            live_n = predictor_meta.get('n_live_samples', predictor_meta['n_samples'])
            st.caption(
                f"🎯 Trained harvest predictor: {predictor_meta['n_samples']} samples "
                f"({baseline_n} from Colab baseline + {live_n} from live harvests), "
                f"cross-validated MAE {predictor_meta['cv_mae_days']}d "
                f"(last trained {predictor_meta['trained_at'][:10]})"
            )
        else:
            st.caption(
                f"🎯 Trained harvest predictor: not trained yet "
                f"({current_sample_count}/{MIN_SAMPLES_TO_TRAIN} live harvests recorded) "
                f"— using the fixed baseline until then."
            )
        if st.button("🔁 Retrain Predictor Now", help="Force a retrain regardless of the auto-retrain threshold"):
            from harvest_predictor import maybe_retrain
            with st.spinner("Training model..."):
                result = maybe_retrain(st.session_state.username, force=True)
            if result.get("trained"):
                st.success(f"✅ Trained on {result['n_samples']} samples — CV MAE: {result['cv_mae_days']} days.")
            else:
                st.warning(result.get("reason", "Could not train yet."))
    except Exception:
        st.caption("🎯 Trained harvest predictor: not available yet.")
        st.exception(e)   # TEMPORARY

    if st.button("🔮 Get AI Recommendation", type="primary"):
        with st.spinner("Getting AI harvest recommendations..."):
            from groq_advisor import get_harvest_advice
            ai_result, ai_error = get_harvest_advice(st.session_state.username, lang=st.session_state.groq_lang)

        if ai_error:
            st.error(f"❌ {ai_error}")

        elif ai_result:
            st.success("✅ AI Analysis Complete!")

            if ai_result.get("model_used"):
                st.caption("🧠 These predictions came from the trained harvest model.")
            else:
                st.caption("📐 Using the fixed baseline — not enough live harvests recorded yet to train the model.")

            if ai_result.get("blocks"):
                st.markdown("#### 🍄 Harvest Predictions per Block")
                cat_map = {
                    "HARVEST_TODAY": "🔴",
                    "HARVEST_WEEK":  "🟡",
                    "MONITOR":       "🟢",
                    "WAIT":          "⬛",
                }
                blocks_df = pd.DataFrame(ai_result["blocks"])
                blocks_df["Status"] = blocks_df["category"].map(
                    lambda c: f"{cat_map.get(c, '❓')} {c.replace('_', ' ')}"
                )

                # ── Save the AI-adjusted date into predicted_harvest ─────────
                # Overwrites the existing column (no new columns needed) so
                # report.py just reads predicted_harvest like it always has.
                # This is a point-in-time save: it updates now, and stays
                # until the next time this button is clicked — it does not
                # silently drift as new sensor readings come in.
                conn_save = get_db_connection()
                for _, brow in blocks_df.iterrows():
                    conn_save.execute(
                        "UPDATE planting_records SET predicted_harvest = ? "
                        "WHERE block_id = ? AND username = ? AND (retired = 0 OR retired IS NULL)",
                        (brow.get("est_harvest_date"), brow["block_id"], st.session_state.username)
                    )
                conn_save.commit()
                conn_save.close()
                st.caption("💾 predicted_harvest updated with today's AI-adjusted dates.")

                display_cols = ["block_id", "est_harvest_date", "Status", "reason"]
                display_cols = [c for c in display_cols if c in blocks_df.columns]

                st.dataframe(
                    blocks_df[display_cols].rename(columns={
                        "block_id":           "Block",
                        "est_harvest_date":   "🤖 Predicted Harvest Date",
                        "reason":             "Reason",
                    }),
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "Reason": st.column_config.TextColumn("Reason", width="large"),
                    }
                )
            else:
                st.info("No block predictions returned.")

            if ai_result.get("advice"):
                st.markdown("#### 💡 Environment Advice")
                advice = ai_result["advice"]
                if isinstance(advice, list):
                    advice = " ".join(str(a) for a in advice)
                clean = str(advice).lstrip("•").strip()
                st.info(clean)

            if not ai_result.get("blocks") and ai_result.get("raw"):
                st.markdown("#### Raw AI Response")
                st.markdown(ai_result["raw"])

    # ── RECORD NEW BLOCK ──────────────────────────────────────────────────────
    st.markdown("---")
    st.subheader("➕ Record New Block")

    if st.session_state.get('_record_block_success'):
        st.success(st.session_state.pop('_record_block_success'))
    if st.session_state.get('_record_block_errors'):
        for e in st.session_state.pop('_record_block_errors'):
            st.error(e)

    with st.form("planting_form"):
        st.caption("For multiple blocks with same planting date, separate IDs with comma e.g. B1, B2, B3")
        block_id    = st.text_input("Block ID(s) (B1 – B244, uppercase B only)")
        species     = st.selectbox("Mushroom Species", ["Oyster Mushroom"])
        planted_date = st.date_input("Planting Date", get_local_now().date())
        notes       = st.text_area("Initial Conditions / Notes")

        if st.form_submit_button("Record Block"):
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
    st.subheader("✅ Mark Block as Harvested")
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
                "Select Block(s) to Harvest",
                active_blocks_df['block_id'].tolist(),
                placeholder="Select one or more blocks..."
            )
            actual_harvest_date = st.date_input("Actual Harvest Date", get_local_now().date())
            harvest_weight      = st.number_input("Total Harvest Weight (kg)", min_value=0.0, step=0.1, format="%.2f")
            retire_block        = st.checkbox("Retire all selected blocks after this harvest")

            if st.form_submit_button("✅ Confirm"):
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
        st.info("No active blocks. All blocks are retired or none recorded yet.")

    st.caption("💡 To view the full harvest schedule and block reports, go to **Generate Report** in the sidebar.")