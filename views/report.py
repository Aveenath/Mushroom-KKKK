import streamlit as st
import pandas as pd
import datetime
import re
from utils import get_db_connection, get_local_now, db_read_sql


def _safe(text):
    return str(text).encode('latin-1', errors='ignore').decode('latin-1')


def _build_schedule_pdf(schedule_df):
    from fpdf import FPDF
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=10)
    pdf.add_page()
    pdf.set_margins(10, 10, 10)

    pdf.set_font("Helvetica", "B", 15)
    pdf.cell(0, 10, _safe("Full Harvest Schedule"), ln=True)
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 5, _safe(f"Generated: {datetime.date.today()}"), ln=True)
    pdf.ln(4)

    df = schedule_df.drop(columns=['Days Left'])
    headers = list(df.columns)
    # Block ID, Cycle, Planted, Harvests Done, Last Harvest, Next Harvest, Status
    widths  = [25, 15, 28, 30, 28, 28, 36]

    pdf.set_fill_color(76, 175, 80)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 8)
    for h, w in zip(headers, widths):
        pdf.cell(w, 7, _safe(str(h)), border=1, fill=True)
    pdf.ln()

    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "", 8)
    for i, (_, row) in enumerate(df.iterrows()):
        pdf.set_fill_color(245, 245, 245) if i % 2 == 0 else pdf.set_fill_color(255, 255, 255)
        for val, w in zip(row, widths):
            pdf.cell(w, 6, _safe(str(val)[:18]), border=1, fill=True)
        pdf.ln()

    return bytes(pdf.output())


def _build_harvest_pdf(norm_id, cycle, species, planted_date, display_rows, sit_df, raw_weights):
    from fpdf import FPDF
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=10)
    pdf.add_page()
    pdf.set_margins(10, 10, 10)

    pdf.set_font("Helvetica", "B", 15)
    pdf.cell(0, 10, _safe(f"Block {norm_id} - Cycle {cycle} Report"), ln=True)
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 5, _safe(f"Species: {species}  |  Planted: {planted_date}  |  Generated: {datetime.date.today()}"), ln=True)
    pdf.ln(3)

    if raw_weights:
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(0, 6, _safe(
            f"Yield: Total {sum(raw_weights):.2f} kg  |  Avg {sum(raw_weights)/len(raw_weights):.2f} kg  |  Best {max(raw_weights):.2f} kg"
        ), ln=True)
        pdf.ln(2)

    # Harvest history table
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 7, "Harvest History", ln=True)

    h_headers = ["#", "Predicted Date", "Actual Date", "Weight (kg)"]
    h_widths  = [58, 44, 44, 44]

    pdf.set_fill_color(76, 175, 80)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 8)
    for h, w in zip(h_headers, h_widths):
        pdf.cell(w, 7, _safe(h), border=1, fill=True)
    pdf.ln()

    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "", 8)
    for i, r in enumerate(display_rows):
        pdf.set_fill_color(245, 245, 245) if i % 2 == 0 else pdf.set_fill_color(255, 255, 255)
        vals = [
            str(r.get("#", ""))[:32],
            str(r.get("Predicted Date", ""))[:15],
            str(r.get("Actual Date", ""))[:15],
            str(r.get("Weight (kg)", "")),
        ]
        for val, w in zip(vals, h_widths):
            pdf.cell(w, 6, _safe(val), border=1, fill=True)
        pdf.ln()

    if not sit_df.empty:
        pdf.ln(5)
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(0, 7, "Situation Reports", ln=True)

        s_headers = ["Date", "Status", "Disease", "Quality", "Notes"]
        s_widths  = [38, 30, 30, 20, 72]

        pdf.set_fill_color(76, 175, 80)
        pdf.set_text_color(255, 255, 255)
        pdf.set_font("Helvetica", "B", 8)
        for h, w in zip(s_headers, s_widths):
            pdf.cell(w, 7, _safe(h), border=1, fill=True)
        pdf.ln()

        pdf.set_text_color(0, 0, 0)
        pdf.set_font("Helvetica", "", 7)
        for i, (_, row) in enumerate(sit_df.iterrows()):
            pdf.set_fill_color(245, 245, 245) if i % 2 == 0 else pdf.set_fill_color(255, 255, 255)
            vals = [
                str(row.get("Date", ""))[:22],
                str(row.get("Status", ""))[:18],
                str(row.get("Disease", ""))[:18],
                str(row.get("Quality", ""))[:10],
                str(row.get("Notes", "") or "")[:45],
            ]
            for val, w in zip(vals, s_widths):
                pdf.cell(w, 6, _safe(val), border=1, fill=True)
            pdf.ln()

    return bytes(pdf.output())


def _validate_and_normalize(block_id):
    raw = block_id.strip()
    if not raw.startswith('B'):
        return None, "Block ID must start with uppercase 'B' (e.g. B1, B001)."
    match = re.match(r'^B(\d+)$', raw)
    if not match:
        return None, "Invalid format. Use B followed by a number only."
    number = int(match.group(1))
    if number < 1 or number > 244:
        return None, f"Block number must be between 1 and 244."
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


def _calc_predicted_dates(planted_date_str, history_df, total_harvests):
    planted  = datetime.date.fromisoformat(planted_date_str)
    predicted = []
    for i in range(1, total_harvests + 1):
        if i == 1:
            pred = planted + datetime.timedelta(days=14)
        else:
            prev_h = history_df[history_df['harvest_number'].astype(int) == (i - 1)]
            if not prev_h.empty:
                prev_actual = datetime.date.fromisoformat(str(prev_h.iloc[0]['harvest_date']))
                pred = prev_actual + datetime.timedelta(days=15)
            else:
                pred = predicted[-1] + datetime.timedelta(days=15)
        predicted.append(pred)
    return predicted


def _build_schedule(username):
    conn = get_db_connection()
    try:
        df_all = db_read_sql(
            "SELECT * FROM planting_records WHERE username = ? ORDER BY block_id",
            conn, params=(username,)
        )
    finally:
        conn.close()
    if df_all.empty:
        return pd.DataFrame()
    rows = []
    for _, row in df_all.iterrows():
        hc      = int(row.get('harvest_count') or 0)
        lhd     = row.get('last_harvest_date') or None
        retired = int(row.get('retired') or 0)
        cycle   = int(row.get('cycle') or 1)
        if retired:
            rows.append({'Block ID': row['block_id'], 'Cycle': cycle, 'Planted': row['planted_date'],
                         'Harvests Done': hc, 'Last Harvest': lhd or '-',
                         'Next Harvest': '-', 'Days Left': 9999, 'Status': 'Retired'})
        else:
            next_date            = _get_next_harvest(row['planted_date'], hc, lhd)
            status_label, days_left = _get_status(next_date)
            rows.append({'Block ID': row['block_id'], 'Cycle': cycle, 'Planted': row['planted_date'],
                         'Harvests Done': hc, 'Last Harvest': lhd or '-',
                         'Next Harvest': str(next_date), 'Days Left': days_left, 'Status': status_label})
    return pd.DataFrame(rows).sort_values('Days Left')


def show():
    st.title("📊 Generate Report")

    # ── SECTION 1: Block Harvest Detail ───────────────────────────────────────
    st.subheader("🔍 Block Harvest Detail")
    search_id = st.text_input("Enter Block ID (e.g. B1, B5)")

    if search_id.strip():
        norm_id, err = _validate_and_normalize(search_id)
        if err:
            st.error(err)
        else:
            conn = get_db_connection()
            all_cycles = db_read_sql(
                "SELECT * FROM planting_records WHERE block_id = ? AND username = ? ORDER BY cycle ASC",
                conn, params=(norm_id, st.session_state.username)
            )
            conn.close()

            if all_cycles.empty:
                st.warning(f"No record found for Block **{norm_id}**.")
            else:
                # Cycle selector
                cycle_options = []
                for _, c in all_cycles.iterrows():
                    cyc     = int(c.get('cycle') or 1)
                    retired = int(c.get('retired') or 0)
                    label   = f"Cycle {cyc} — Planted {c['planted_date']} {'(Retired)' if retired else '(Active)'}"
                    cycle_options.append(label)

                selected_label = st.radio("Select Cycle", cycle_options, horizontal=True) if len(cycle_options) > 1 else cycle_options[0]
                selected_idx   = cycle_options.index(selected_label)
                row            = all_cycles.iloc[selected_idx]
                hc             = int(row.get('harvest_count') or 0)
                lhd            = row.get('last_harvest_date') or None
                retired        = int(row.get('retired') or 0)
                cycle          = int(row.get('cycle') or 1)

                st.success(f"Block **{norm_id}** | Cycle {cycle} | {row['species']} | Planted: {row['planted_date']} | Total Harvests: {hc}")

                # Fetch harvest history for this block + cycle
                conn_h = get_db_connection()
                history_df = db_read_sql(
                    "SELECT harvest_number, harvest_date, weight_kg FROM harvest_history "
                    "WHERE block_id = ? AND username = ? AND cycle = ? ORDER BY harvest_number ASC",
                    conn_h, params=(norm_id, st.session_state.username, cycle)
                )
                conn_h.close()

                # Fetch situation reports for this block
                conn_s = get_db_connection()
                sit_df = db_read_sql(
                    "SELECT date, status, disease_noted, quality, notes FROM situation_reports "
                    "WHERE block_id = ? AND username = ? ORDER BY date ASC",
                    conn_s, params=(norm_id, st.session_state.username)
                )
                conn_s.close()

                recorded_nums   = set(history_df['harvest_number'].astype(int).tolist()) if not history_df.empty else set()
                predicted_dates = _calc_predicted_dates(row['planted_date'], history_df, hc) if hc > 0 else []

                # ── Full Report Table ──────────────────────────────────────────
                st.markdown("#### 📅 Harvest History")
                display_rows = [{"#": "🌱 Planted", "Predicted Date": "—", "Actual Date": row['planted_date'], "Weight (kg)": "—"}]

                raw_weights = []
                for i in range(1, hc + 1):
                    predicted_str = str(predicted_dates[i - 1]) if i <= len(predicted_dates) else "—"
                    if i in recorded_nums:
                        h        = history_df[history_df['harvest_number'].astype(int) == i].iloc[0]
                        w        = h['weight_kg']
                        w_val    = round(float(w), 2) if (w is not None and pd.notna(w)) else None
                        if w_val is not None:
                            raw_weights.append(w_val)
                        display_rows.append({
                            "#":              f"Harvest #{i}",
                            "Predicted Date": predicted_str,
                            "Actual Date":    str(h['harvest_date']),
                            "Weight (kg)":    f"{w_val:.2f}" if w_val is not None else "—",
                        })
                    else:
                        actual = lhd if (i == hc and lhd) else "Not recorded"
                        display_rows.append({
                            "#":              f"Harvest #{i}",
                            "Predicted Date": predicted_str,
                            "Actual Date":    actual,
                            "Weight (kg)":    "—",
                        })

                if not retired:
                    next_date     = _get_next_harvest(row['planted_date'], hc, lhd)
                    status_label, _ = _get_status(next_date)
                    next_predicted = str(next_date)
                    display_rows.append({
                        "#":              f"🔜 Harvest #{hc + 1} (Upcoming)",
                        "Predicted Date": next_predicted,
                        "Actual Date":    "—",
                        "Weight (kg)":    "—",
                    })

                report_df = pd.DataFrame(display_rows)
                st.dataframe(report_df, hide_index=True, use_container_width=True)

                # ── Weight Summary ─────────────────────────────────────────────
                if raw_weights:
                    st.markdown("#### 📈 Yield Summary")
                    s1, s2, s3 = st.columns(3)
                    s1.metric("Total Yield",        f"{sum(raw_weights):.2f} kg")
                    s2.metric("Average / Harvest",  f"{sum(raw_weights)/len(raw_weights):.2f} kg")
                    s3.metric("Best Harvest",        f"{max(raw_weights):.2f} kg")

                # ── Situation Reports ──────────────────────────────────────────
                if not sit_df.empty:
                    st.markdown("#### 📋 Situation Reports (this block)")
                    sit_df = sit_df.rename(columns={
                        "date": "Date", "status": "Status",
                        "disease_noted": "Disease", "quality": "Quality", "notes": "Notes"
                    })
                    st.dataframe(sit_df, hide_index=True, use_container_width=True)

                # ── Export ─────────────────────────────────────────────────────
                st.markdown("---")
                export_df = report_df.copy()
                if not sit_df.empty:
                    export_df = pd.concat([
                        export_df,
                        pd.DataFrame([{"#": ""}]),
                        pd.DataFrame([{"#": "--- SITUATION REPORTS ---"}]),
                        sit_df.rename(columns={"Date": "#"}),
                    ], ignore_index=True)

                exp_col1, exp_col2 = st.columns(2)
                with exp_col1:
                    csv = export_df.to_csv(index=False).encode('utf-8')
                    st.download_button(
                        f"📥 Export CSV — Block {norm_id} Cycle {cycle}",
                        data=csv,
                        file_name=f"report_{norm_id}_cycle{cycle}_{get_local_now().date()}.csv",
                        mime="text/csv",
                        use_container_width=True,
                    )
                with exp_col2:
                    pdf_bytes = _build_harvest_pdf(
                        norm_id, cycle, str(row['species']), str(row['planted_date']),
                        display_rows, sit_df, raw_weights
                    )
                    st.download_button(
                        f"📄 Export PDF — Block {norm_id} Cycle {cycle}",
                        data=pdf_bytes,
                        file_name=f"report_{norm_id}_cycle{cycle}_{get_local_now().date()}.pdf",
                        mime="application/pdf",
                        use_container_width=True,
                    )

                # ── Edit Harvest Weight ────────────────────────────────────────
                if hc > 0:
                    with st.expander("✏️ Edit Harvest Weight", expanded=False):
                        st.caption("Correct a wrong weight entry for any harvest.")
                        edit_harvest_num = st.selectbox(
                            "Select Harvest #",
                            options=list(range(1, hc + 1)),
                            format_func=lambda x: f"Harvest #{x}",
                            key=f"edit_harvest_num_{norm_id}_{cycle}"
                        )
                        existing_weight = None
                        if not history_df.empty:
                            match = history_df[history_df['harvest_number'].astype(int) == edit_harvest_num]
                            if not match.empty:
                                w = match.iloc[0]['weight_kg']
                                existing_weight = round(float(w), 2) if (w is not None and pd.notna(w)) else None
                        new_weight = st.number_input(
                            "New Weight (kg)",
                            min_value=0.0, step=0.1, format="%.2f",
                            value=existing_weight if existing_weight is not None else 0.0,
                            key=f"edit_weight_{norm_id}_{cycle}"
                        )
                        if st.button("💾 Save Weight", key=f"save_weight_{norm_id}_{cycle}"):
                            weight_val = float(new_weight) if new_weight > 0 else None
                            conn_edit = get_db_connection()
                            conn_edit.execute(
                                "UPDATE harvest_history SET weight_kg = ? "
                                "WHERE block_id = ? AND username = ? AND cycle = ? AND harvest_number = ?",
                                (weight_val, norm_id, st.session_state.username, cycle, edit_harvest_num)
                            )
                            conn_edit.commit()
                            conn_edit.close()
                            st.success(f"✅ Harvest #{edit_harvest_num} weight updated to {new_weight:.2f} kg.")
                            st.rerun()

                # ── Delete Cycle ───────────────────────────────────────────────
                with st.expander("🗑️ Delete this Cycle", expanded=False):
                    st.warning(
                        f"This will permanently delete **Cycle {cycle}** of Block **{norm_id}** "
                        f"and all its harvest history. This cannot be undone."
                    )
                    confirm = st.checkbox(f"Yes, I want to delete Block {norm_id} Cycle {cycle}")
                    if st.button("🗑️ Confirm Delete", type="primary", disabled=not confirm):
                        conn_del = get_db_connection()
                        conn_del.execute(
                            "DELETE FROM planting_records WHERE block_id = ? AND username = ? AND cycle = ?",
                            (norm_id, st.session_state.username, cycle)
                        )
                        conn_del.execute(
                            "DELETE FROM harvest_history WHERE block_id = ? AND username = ? AND cycle = ?",
                            (norm_id, st.session_state.username, cycle)
                        )
                        conn_del.commit()
                        conn_del.close()
                        st.success(f"✅ Block {norm_id} Cycle {cycle} deleted.")
                        st.rerun()

    # ── SECTION 2: Full Harvest Schedule ──────────────────────────────────────
    st.markdown("---")
    st.subheader("📋 Full Harvest Schedule")

    if st.button("📂 View Full Schedule", type="primary"):
        st.session_state['show_schedule'] = not st.session_state.get('show_schedule', False)

    if st.session_state.get('show_schedule', False):
        schedule_df = _build_schedule(st.session_state.username)

        if schedule_df.empty:
            st.info("No planting records found.")
        else:
            active_df  = schedule_df[schedule_df['Status'] != 'Retired'].copy()
            retired_df = schedule_df[schedule_df['Status'] == 'Retired'].copy()

            sch_col1, sch_col2 = st.columns(2)
            with sch_col1:
                csv_export = schedule_df.drop(columns=['Days Left']).to_csv(index=False).encode('utf-8')
                st.download_button(
                    "📥 Export Full Schedule (CSV)",
                    data=csv_export,
                    file_name=f"harvest_schedule_{get_local_now().date()}.csv",
                    mime="text/csv",
                    use_container_width=True,
                )
            with sch_col2:
                sch_pdf = _build_schedule_pdf(schedule_df)
                st.download_button(
                    "📄 Export Full Schedule (PDF)",
                    data=sch_pdf,
                    file_name=f"harvest_schedule_{get_local_now().date()}.pdf",
                    mime="application/pdf",
                    use_container_width=True,
                )

            tab_active, tab_overdue, tab_week, tab_retired = st.tabs([
                "All Active", "🔴 Overdue / Today", "🟡 This Week", "⬛ Retired"
            ])

            with tab_active:
                if active_df.empty:
                    st.info("No active blocks.")
                else:
                    st.dataframe(active_df.drop(columns=['Days Left']), hide_index=True, use_container_width=True)

            with tab_overdue:
                today_df = active_df[active_df['Days Left'] <= 0]
                if today_df.empty:
                    st.success("No blocks overdue or due today.")
                else:
                    st.dataframe(today_df.drop(columns=['Days Left']), hide_index=True, use_container_width=True)

            with tab_week:
                week_df = active_df[active_df['Days Left'].between(1, 7)]
                if week_df.empty:
                    st.info("No blocks due this week.")
                else:
                    st.dataframe(week_df.drop(columns=['Days Left']), hide_index=True, use_container_width=True)

            with tab_retired:
                if retired_df.empty:
                    st.info("No retired blocks.")
                else:
                    st.dataframe(retired_df.drop(columns=['Days Left']), hide_index=True, use_container_width=True)
