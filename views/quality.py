import streamlit as st
import pandas as pd
import plotly.express as px
import re
import base64
from utils import get_db_connection, db_read_sql


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
    st.title("📈 Quality & Disease Analysis")

    conn = get_db_connection()
    reports_df = db_read_sql(
        "SELECT date, block_id, section_id, status, quality, disease_noted, notes, photo FROM situation_reports WHERE username = ? ORDER BY date DESC",
        conn, params=(st.session_state.username,)
    )
    conn.close()

    if reports_df.empty:
        st.info("No reports found. Start recording in the 'Record Situation' tab.")
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
    st.subheader("📊 Executive Summary")
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
    st.subheader("📝 Complete Log Repository")

    export_cols = [c for c in reports_df.columns if c != 'photo']
    csv = reports_df[export_cols].to_csv(index=False).encode('utf-8')
    st.download_button("📥 Export Full Logs to Excel/CSV", data=csv,
                       file_name="mushroom_farm_reports.csv", mime="text/csv")

    view_mode = st.radio("View table by:", ["Block", "Section"], horizontal=True)

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

    rows_to_delete = edited_df[edited_df["Delete?"] == True]
    if not rows_to_delete.empty:
        st.warning(f"You have selected {len(rows_to_delete)} log(s) for deletion.")
        if st.button("🚨 Confirm Delete Selected Logs"):
            conn = get_db_connection()
            for dt in rows_to_delete['Date & Time']:
                conn.execute("DELETE FROM situation_reports WHERE date = ? AND username = ?",
                             (dt, st.session_state.username))
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
                    (new_status, new_quality, disease_val, new_notes, selected_dt, st.session_state.username)
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
                img_bytes = base64.b64decode(sel_photo_row['photo'])
                block_lbl = sel_photo_row.get('Block', '-')
                sec_lbl   = sel_photo_row.get('Section', '-')
                label     = f"{sel_photo_dt} | Block: {block_lbl} | Section: {sec_lbl}"
                st.image(img_bytes, caption=label, use_container_width=True)
            except Exception as e:
                st.error(f"Could not load photo: {e}")
