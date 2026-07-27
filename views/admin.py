import streamlit as st
import bcrypt
import re
import pandas as pd
import plotly.express as px
from utils import get_db_connection, db_read_sql
from translations import t


def show():
    if st.session_state.get('role') != 'majikan':
        st.error("Access denied.")
        st.stop()

    st.title(t('admin_title'))
    st.caption(t('admin_caption'))

    conn = get_db_connection()

    # ── LOAD DATA ─────────────────────────────────────────────────────────────
    users_df = db_read_sql("SELECT username, role FROM users", conn)
    blocks_df = db_read_sql(
        "SELECT block_id, username, species, planted_date, predicted_harvest, retired "
        "FROM planting_records ORDER BY planted_date DESC",
        conn
    )
    harvests_df = db_read_sql(
        "SELECT block_id, username, harvest_date, weight_kg FROM harvest_history ORDER BY harvest_date DESC",
        conn
    )
    conn.close()

    workers_list = (
        users_df[users_df['role'] != 'majikan']['username'].tolist()
        if not users_df.empty else []
    )

    # ── SUMMARY METRICS ───────────────────────────────────────────────────────
    total_users    = len(workers_list)
    total_blocks   = len(blocks_df[blocks_df['retired'] != 1]) if not blocks_df.empty else 0
    total_harvests = len(harvests_df)
    total_weight   = harvests_df['weight_kg'].dropna().sum() if not harvests_df.empty else 0.0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric(t('admin_total_workers'),  total_users)
    c2.metric(t('admin_active_blocks'),  total_blocks)
    c3.metric(t('admin_total_harvests'), total_harvests)
    c4.metric(t('admin_total_weight'),   f"{total_weight:.2f}")

    st.markdown("---")

    # ── REGISTERED WORKERS ────────────────────────────────────────────────────
    st.subheader(t('admin_workers'))
    if not workers_list:
        st.info(t('admin_no_workers'))
    else:
        display_users = pd.DataFrame({'Username': workers_list})
        if not blocks_df.empty:
            active = blocks_df[blocks_df['retired'] != 1]
            block_counts = active.groupby('username').size().reset_index(name='Active Blocks')
            display_users = display_users.merge(
                block_counts, left_on='Username', right_on='username', how='left'
            ).drop(columns='username')
            display_users['Active Blocks'] = display_users['Active Blocks'].fillna(0).astype(int)
        st.dataframe(display_users, use_container_width=True, hide_index=True)

        # ── Remove Worker ──────────────────────────────────────────────────────
        with st.expander(t('admin_remove'), expanded=False):
            worker_to_remove = st.selectbox(t('admin_select_remove'), workers_list, key="remove_worker_select")
            st.warning(t('admin_warn_remove', w=worker_to_remove))
            confirm_remove = st.checkbox(t('admin_confirm_cb', w=worker_to_remove))
            if st.button(t('admin_confirm_btn'), type="primary", disabled=not confirm_remove):
                conn_del = get_db_connection()
                conn_del.execute("DELETE FROM users WHERE username = ?", (worker_to_remove,))
                conn_del.execute("DELETE FROM planting_records WHERE username = ?", (worker_to_remove,))
                conn_del.execute("DELETE FROM harvest_history WHERE username = ?", (worker_to_remove,))
                conn_del.execute("DELETE FROM situation_reports WHERE username = ?", (worker_to_remove,))
                conn_del.commit()
                conn_del.close()
                st.success(t('admin_removed', w=worker_to_remove))
                st.rerun()

        # ── Reset Worker Password ──────────────────────────────────────────────
        with st.expander(t('admin_reset_pw'), expanded=False):
            reset_worker = st.selectbox(t('admin_reset_select'), workers_list, key="reset_pw_worker")
            new_pw  = st.text_input(t('admin_reset_new_pw'),    type="password", key="reset_pw_new")
            conf_pw = st.text_input(t('admin_reset_confirm_pw'), type="password", key="reset_pw_conf")
            if st.button(t('admin_reset_btn'), key="reset_pw_btn"):
                pw_errors = []
                if new_pw != conf_pw:
                    pw_errors.append(t('admin_reset_mismatch'))
                elif (len(new_pw) < 8
                      or not re.search(r'[A-Z]', new_pw)
                      or not re.search(r'[a-z]', new_pw)
                      or not re.search(r'\d', new_pw)
                      or not re.search(r'[!@#$%^&*()_+\-=\[\]{};\':"\\|,.<>\/?]', new_pw)):
                    pw_errors.append(t('admin_reset_weak'))
                if pw_errors:
                    for e in pw_errors:
                        st.error(e)
                else:
                    new_hash = bcrypt.hashpw(new_pw.encode(), bcrypt.gensalt()).decode()
                    conn_pw = get_db_connection()
                    conn_pw.execute("UPDATE users SET password = ? WHERE username = ?", (new_hash, reset_worker))
                    conn_pw.commit()
                    conn_pw.close()
                    st.success(t('admin_reset_success', w=reset_worker))

    st.markdown("---")

    # ── WORKER PRODUCTIVITY CHART ─────────────────────────────────────────────
    st.subheader(t('admin_productivity'))
    if harvests_df.empty:
        st.info(t('admin_productivity_no_data'))
    else:
        prod_df = harvests_df.copy()
        prod_df['weight_kg'] = pd.to_numeric(prod_df['weight_kg'], errors='coerce').fillna(0)
        prod_summary = prod_df.groupby('username').agg(
            Total_kg=('weight_kg', 'sum'),
            Harvests=('block_id', 'count')
        ).reset_index()
        prod_summary.columns = ['Worker', 'Total (kg)', 'Harvests']
        prod_summary = prod_summary.sort_values('Total (kg)', ascending=False)

        col_chart, col_table = st.columns([2, 1])
        with col_chart:
            fig = px.bar(
                prod_summary, x='Worker', y='Total (kg)',
                color='Worker', title='Harvest Weight per Worker',
                labels={'Total (kg)': 'Total Harvest (kg)'}
            )
            fig.update_layout(showlegend=False)
            st.plotly_chart(fig, use_container_width=True)
        with col_table:
            st.dataframe(prod_summary, use_container_width=True, hide_index=True)

    st.markdown("---")

    # ── OVERDUE BLOCKS ────────────────────────────────────────────────────────
    st.subheader(t('admin_overdue'))
    if blocks_df.empty:
        st.success(t('admin_overdue_none'))
    else:
        active_b = blocks_df[blocks_df['retired'] != 1].copy()
        active_b['predicted_dt'] = pd.to_datetime(active_b['predicted_harvest'], errors='coerce')
        today = pd.Timestamp.today().normalize()
        overdue_b = active_b[active_b['predicted_dt'] < today].copy()
        if overdue_b.empty:
            st.success(t('admin_overdue_none'))
        else:
            overdue_b['Days Overdue'] = (today - overdue_b['predicted_dt']).dt.days.astype(int)
            overdue_b = overdue_b.sort_values('Days Overdue', ascending=False)
            disp_od = overdue_b[['block_id', 'username', 'species', 'predicted_harvest', 'Days Overdue']].copy()
            disp_od.columns = ['Block ID', 'Worker', 'Species', 'Was Due', 'Days Overdue']
            st.warning(t('admin_overdue_warn', n=len(disp_od)))
            st.dataframe(disp_od, use_container_width=True, hide_index=True)

    st.markdown("---")

    # ── ALL ACTIVE BLOCKS ─────────────────────────────────────────────────────
    st.subheader(t('admin_all_blocks'))
    if not blocks_df.empty:
        active_blocks = blocks_df[blocks_df['retired'] != 1].copy()
        if active_blocks.empty:
            st.info(t('admin_no_blocks'))
        else:
            worker_options = [t('admin_all_workers')] + sorted(active_blocks['username'].unique().tolist())
            selected_worker = st.selectbox(t('admin_filter'), worker_options, key="blocks_worker_filter")
            if selected_worker != t('admin_all_workers'):
                active_blocks = active_blocks[active_blocks['username'] == selected_worker]
            active_blocks = active_blocks[['block_id', 'username', 'species', 'planted_date', 'predicted_harvest']]
            active_blocks.columns = ['Block ID', 'Worker', 'Species', 'Planted Date', 'Predicted Harvest']
            st.dataframe(active_blocks, use_container_width=True, hide_index=True)
    else:
        st.info(t('admin_no_planting'))
