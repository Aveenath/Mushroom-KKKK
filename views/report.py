import streamlit as st
import pandas as pd
import datetime
import re
from utils import get_db_connection, get_local_now, db_read_sql


def _safe(text):
    return str(text).encode('latin-1', errors='ignore').decode('latin-1')


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


def _build_full_report(username):
    conn = get_db_connection()
    try:
        pr_df = db_read_sql(
            "SELECT block_id, cycle, planted_date, harvest_count, last_harvest_date, retired "
            "FROM planting_records WHERE username = ? ORDER BY block_id, cycle ASC",
            conn, params=(username,)
        )
        hist_df = db_read_sql(
            "SELECT block_id, cycle, harvest_number, harvest_date FROM harvest_history "
            "WHERE username = ? ORDER BY block_id, cycle, harvest_number ASC",
            conn, params=(username,)
        )
        sit_df = db_read_sql(
            "SELECT block_id, status, quality FROM situation_reports "
            "WHERE username = ? ORDER BY date DESC",
            conn, params=(username,)
        )
    finally:
        conn.close()

    if pr_df.empty:
        return pd.DataFrame()

    # Keep only the latest cycle per block
    pr_df = (
        pr_df.sort_values(['block_id', 'cycle'])
             .groupby('block_id', as_index=False)
             .last()
    )

    # Latest situation per block
    latest_sit = {}
    for _, row in sit_df.iterrows():
        bid = str(row['block_id'])
        if bid not in latest_sit:
            quality = str(row.get('quality') or '').strip()
            status  = str(row.get('status')  or '').strip()
            latest_sit[bid] = f"{status} / {quality}" if quality and quality != 'None' else status

    def _clean(v):
        return v if (v is not None and pd.notna(v) and str(v).strip() not in ('', 'nan', 'None', 'NaT')) else None

    rows = []
    for _, pr in pr_df.iterrows():
        hc        = int(pr.get('harvest_count') or 0)
        lhd       = _clean(pr.get('last_harvest_date'))
        retired   = int(pr.get('retired') or 0)
        cycle     = int(pr.get('cycle') or 1)
        bid       = str(pr['block_id'])
        situation = latest_sit.get(bid, '-')

        # Harvest history for this block + cycle
        block_hist = hist_df[
            (hist_df['block_id'].astype(str) == bid) &
            (hist_df['cycle'].astype(str) == str(cycle))
        ].copy() if not hist_df.empty else pd.DataFrame()

        predicted_dates = _calc_predicted_dates(pr['planted_date'], block_hist, hc) if hc > 0 else []

        # One row per completed harvest
        for i in range(1, hc + 1):
            predicted_str = str(predicted_dates[i - 1]) if i <= len(predicted_dates) else '-'
            actual_str = '-'
            if not block_hist.empty:
                match = block_hist[block_hist['harvest_number'].astype(int) == i]
                if not match.empty:
                    actual_str = str(match.iloc[0]['harvest_date'])
            if actual_str == '-' and i == hc and lhd:
                actual_str = str(lhd)

            rows.append({
                'Block ID':               bid,
                'Planted Date':           str(pr['planted_date']),
                'Harvest #':              i,
                'Actual Harvest Date':    actual_str,
                'Predicted Harvest Date': predicted_str,
                'Situation':              situation,
            })

        # Upcoming row for active blocks
        if not retired:
            try:
                next_date = _get_next_harvest(pr['planted_date'], hc, str(lhd) if lhd else None)
                rows.append({
                    'Block ID':               bid,
                    'Planted Date':           str(pr['planted_date']),
                    'Harvest #':              f'{hc + 1} (Upcoming)',
                    'Actual Harvest Date':    '-',
                    'Predicted Harvest Date': str(next_date),
                    'Situation':              situation,
                })
            except Exception:
                pass

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df['_bid']  = df['Block ID'].apply(lambda x: int(re.sub(r'\D', '', x) or 0))
    df['_hnum'] = df['Harvest #'].apply(lambda x: int(re.sub(r'\D', '', str(x).split(' ')[0]) or 0))
    df = df.sort_values(['_bid', '_hnum']).drop(columns=['_bid', '_hnum'])
    return df.reset_index(drop=True)


def show():
    st.title("📊 Generate Report")

    # ── SECTION 1: Block Harvest Detail ───────────────────────────────────────
    st.subheader("🔍 Block Harvest Detail")
    search_id = st.text_input("Enter Block ID (e.g. B1, B5)", key="block_search_input")
    do_search = st.button("🔍 Search")

    if do_search:
        st.session_state['report_search_id'] = search_id.strip()
    elif not search_id.strip():
        st.session_state.pop('report_search_id', None)

    active_search = st.session_state.get('report_search_id', '')

    if active_search:
        norm_id, err = _validate_and_normalize(active_search)
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

    # ── SECTION 2: Full Report ─────────────────────────────────────────────────
    st.markdown("---")
    st.subheader("📋 Full Report")

    if st.button("📊 View Full Report", type="primary"):
        full_df = _build_full_report(st.session_state.username)
        if full_df.empty:
            st.info("No planting records found.")
        else:
            st.caption(f"{len(full_df)} record(s) — sorted by Block ID then Cycle")
            with st.container(height=450):
                st.dataframe(full_df, hide_index=True, use_container_width=True)
            csv_export = full_df.to_csv(index=False).encode('utf-8')
            st.download_button(
                "📥 Export Full Report (CSV)",
                data=csv_export,
                file_name=f"full_report_{get_local_now().date()}.csv",
                mime="text/csv",
            )
