import streamlit as st
import pandas as pd
import plotly.express as px
import re
import base64
import datetime
from utils import get_db_connection, db_read_sql
from translations import t


def _safe(text):
    return str(text).encode('latin-1', errors='ignore').decode('latin-1')


def _quality_pdf(df, view_label):
    from fpdf import FPDF
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=10)
    pdf.add_page()
    pdf.set_margins(10, 10, 10)

    pdf.set_font("Helvetica", "B", 15)
    pdf.cell(0, 10, _safe("Quality & Disease Analysis Report"), ln=True)
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 5, _safe(f"View: {view_label}  |  Generated: {datetime.date.today()}"), ln=True)
    pdf.ln(4)

    headers = ["Date & Time", view_label, "Situation", "Quality", "Disease", "Notes"]
    id_col  = "Block" if view_label == "Block" else "Section"
    widths  = [38, 18, 28, 18, 28, 60]

    pdf.set_fill_color(76, 175, 80)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 8)
    for h, w in zip(headers, widths):
        pdf.cell(w, 7, _safe(h), border=1, fill=True)
    pdf.ln()

    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "", 7)
    for i, (_, row) in enumerate(df.iterrows()):
        pdf.set_fill_color(245, 245, 245) if i % 2 == 0 else pdf.set_fill_color(255, 255, 255)
        vals = [
            str(row.get("Date & Time", ""))[:22],
            str(row.get(id_col, ""))[:12],
            str(row.get("Situation", ""))[:18],
            str(row.get("Quality", ""))[:10],
            str(row.get("Disease", ""))[:18],
            str(row.get("Notes", "") or "")[:38],
        ]
        for val, w in zip(vals, widths):
            pdf.cell(w, 6, _safe(val), border=1, fill=True)
        pdf.ln()

    return bytes(pdf.output())


def _clean_status(val):
    return re.sub(r'\s*\[.+?\]', '', str(val)).strip()


def _parse_block(row):
    if row.get('block_id') and row['block_id'] not in [None, '-', '']:
        return row['block_id']
    match = re.search(r'\[(.+?)\]', str(row.get('status', '')))
    return match.group(1) if match else '-'


def _parse_section(row):
    val = row.get('section_id', '-')
    return val if val not in [None, ''] else '-'


def show():
    st.title(t('qa_title'))

    # Admin user selector
    if st.session_state.get('role') == 'majikan':
        conn_u = get_db_connection()
        users_df = db_read_sql(
            "SELECT username FROM users WHERE role != 'majikan' ORDER BY username", conn_u
        )
        conn_u.close()
        if users_df.empty:
            st.info(t('admin_no_workers'))
            return
        view_username = st.selectbox(
            t('qa_viewing'), users_df['username'].tolist(), key="qa_user_select"
        )
        st.markdown("---")
    else:
        view_username = st.session_state.username

    conn = get_db_connection()
    reports_df = db_read_sql(
        "SELECT date, block_id, section_id, status, quality, disease_noted, notes, photo FROM situation_reports WHERE username = ? ORDER BY date DESC",
        conn, params=(view_username,)
    )
    conn.close()

    if reports_df.empty:
        st.info(t('qa_no_reports'))
        return

    # Clean columns
    reports_df['block_id'] = reports_df.apply(_parse_block, axis=1)
    reports_df['section_id'] = reports_df.apply(_parse_section, axis=1)
    reports_df['status'] = reports_df['status'].apply(_clean_status)
    reports_df.rename(columns={
        "date": "Date & Time", "block_id": "Block", "section_id": "Section",
        "status": "Situation", "quality": "Quality",
        "disease_noted": "Disease", "notes": "Notes"
    }, inplace=True)

    # --- CHARTS (always visible, no toggle) ---
    st.subheader(t('qa_exec'))
    col1, col2 = st.columns(2)
    with col1:
        fig_pie = px.pie(reports_df, names='Quality', title='Overall Harvest Quality', hole=0.3)
        st.plotly_chart(fig_pie, use_container_width=True)
    with col2:
        disease_df = reports_df[~reports_df['Disease'].astype(str).str.lower().isin(['none', 'null', ''])]
        if not disease_df.empty:
            fig_bar = px.histogram(disease_df, x='Disease', title='Reported Diseases Frequency', color='Disease')
            st.plotly_chart(fig_bar, use_container_width=True)
        else:
            st.success("🎉 No diseases recorded. Excellent farm health!")

    # --- TABLE with toggle ---
    st.markdown("---")
    st.subheader(t('qa_log'))

    export_cols = [c for c in reports_df.columns if c != 'photo']
    dl_col1, dl_col2 = st.columns(2)
    with dl_col1:
        csv = reports_df[export_cols].to_csv(index=False).encode('utf-8')
        st.download_button(t('qa_export_csv'), data=csv,
                           file_name="mushroom_farm_reports.csv", mime="text/csv",
                           use_container_width=True)

    view_mode = st.radio(t('qa_view_by'), ["Block", "Section"], horizontal=True)

    if view_mode == "Block":
        display_df = reports_df[reports_df['Block'] != '-'][
            ['Date & Time', 'Block', 'Situation', 'Quality', 'Disease', 'Notes']
        ].copy()
        if display_df.empty:
            st.info("No block data recorded yet.")
            return
    else:
        # Only show rows that have a section recorded
        display_df = reports_df[reports_df['Section'] != '-'][
            ['Date & Time', 'Section', 'Situation', 'Quality', 'Disease', 'Notes']
        ].copy()
        if display_df.empty:
            st.info("No section data recorded yet.")
            return

    display_df.insert(len(display_df.columns), "Delete?", False)
    edited_df = st.data_editor(
        display_df,
        column_config={"Delete?": st.column_config.CheckboxColumn("🗑️ Delete?", default=False)},
        disabled=[c for c in display_df.columns if c != "Delete?"],
        hide_index=True,
        use_container_width=True,
        height=400
    )

    with dl_col2:
        pdf_df = display_df[[c for c in display_df.columns if c != "Delete?"]].copy()
        pdf_bytes = _quality_pdf(pdf_df, view_mode)
        st.download_button(
            t('qa_export_pdf'),
            data=pdf_bytes,
            file_name=f"quality_report_{datetime.date.today()}.pdf",
            mime="application/pdf",
            use_container_width=True,
        )

    rows_to_delete = edited_df[edited_df["Delete?"] == True]
    if not rows_to_delete.empty:
        st.warning(f"You have selected {len(rows_to_delete)} log(s) for deletion.")
        if st.button("🚨 Confirm Delete Selected Logs"):
            conn = get_db_connection()
            for dt in rows_to_delete['Date & Time']:
                conn.execute("DELETE FROM situation_reports WHERE date = ? AND username = ?",
                             (dt, view_username))
            conn.commit()
            conn.close()
            st.success("Logs successfully deleted!")
            st.rerun()

    # ── Edit a report ──────────────────────────────────────────────────────────
    st.markdown("---")
    with st.expander("✏️ Edit a Report", expanded=False):
        st.caption("Select a report by Block / Section to correct mistakes.")
        id_col = "Block" if view_mode == "Block" else "Section"
        if not display_df.empty:
            label_map = {
                f"{row[id_col]} — {row['Date & Time']}": row['Date & Time']
                for _, row in display_df.iterrows()
            }
            sel_label   = st.selectbox("Select Report", list(label_map.keys()))
            selected_dt = label_map[sel_label]
            sel_row     = display_df[display_df['Date & Time'] == selected_dt].iloc[0]

            new_status  = st.selectbox("Situation", ["Normal", "Harvesting", "Disease Detected", "Maintenance"],
                                       index=["Normal", "Harvesting", "Disease Detected", "Maintenance"].index(sel_row.get("Situation", "Normal"))
                                       if sel_row.get("Situation", "Normal") in ["Normal", "Harvesting", "Disease Detected", "Maintenance"] else 0)
            new_quality = st.radio("Quality", ["Bad", "Normal", "Good"],
                                   index=["Bad", "Normal", "Good"].index(sel_row.get("Quality", "Normal"))
                                   if sel_row.get("Quality", "Normal") in ["Bad", "Normal", "Good"] else 1,
                                   horizontal=True)
            new_disease = st.text_input("Disease (leave blank if none)", value="" if sel_row.get("Disease") in ["None", "null", None] else str(sel_row.get("Disease", "")))
            new_notes   = st.text_area("Notes", value=str(sel_row.get("Notes", "") or ""))

            if st.button("💾 Save Changes"):
                disease_val = new_disease.strip() if new_disease.strip() else "None"
                conn = get_db_connection()
                conn.execute(
                    "UPDATE situation_reports SET status = ?, quality = ?, disease_noted = ?, notes = ? "
                    "WHERE date = ? AND username = ?",
                    (new_status, new_quality, disease_val, new_notes, selected_dt, view_username)
                )
                conn.commit()
                conn.close()
                st.success("Report updated.")
                st.rerun()

    # ── View photos ────────────────────────────────────────────────────────────
    photo_rows = reports_df[reports_df['photo'].notna() & (reports_df['photo'].astype(str) != '') & (reports_df['photo'].astype(str) != 'None')] if 'photo' in reports_df.columns else pd.DataFrame()
    if not photo_rows.empty:
        st.markdown("---")
        with st.expander("📷 View Report Photos", expanded=False):
            st.caption("Select a report to view its attached photo.")
            photo_id_col = "Block" if view_mode == "Block" else "Section"
            photo_label_map = {
                f"{row[photo_id_col]} — {row['Date & Time']}": row['Date & Time']
                for _, row in photo_rows.iterrows()
            }
            sel_photo_label = st.selectbox("Select Report", list(photo_label_map.keys()), key="photo_select")
            sel_photo_dt    = photo_label_map[sel_photo_label]
            sel_photo_row   = photo_rows[photo_rows['Date & Time'] == sel_photo_dt].iloc[0]
            try:
                photo_val = str(sel_photo_row['photo'])
                block_lbl = sel_photo_row.get('Block', '-')
                sec_lbl   = sel_photo_row.get('Section', '-')
                label     = f"{sel_photo_dt} | Block: {block_lbl} | Section: {sec_lbl}"
                if photo_val.startswith("http"):
                    st.image(photo_val, caption=label, use_container_width=True)
                else:
                    st.image(base64.b64decode(photo_val), caption=label, use_container_width=True)
            except Exception as e:
                st.error(f"Could not load photo: {e}")
