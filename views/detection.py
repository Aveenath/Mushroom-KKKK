import datetime
import streamlit as st
import pandas as pd
from PIL import Image
from ultralytics import YOLO
from utils import get_db_connection, get_local_now, db_read_sql
from cloudinary_utils import upload_pil_image, upload_numpy_image


@st.cache_resource
def load_model():
    return YOLO("mushroom_yolo.pt")


def _build_section_options():
    sections = []
    for rack in ["A", "B"]:
        for row in [1, 2, 3]:
            for col in [1, 2, 3]:
                sections.append(f"{rack}{row}{col}")
    return sections


def _safe(text):
    return str(text).encode('latin-1', errors='ignore').decode('latin-1')


def _build_log_pdf(df_log, username):
    from fpdf import FPDF
    pdf = FPDF(orientation='L')
    pdf.set_auto_page_break(auto=True, margin=10)
    pdf.add_page()
    pdf.set_margins(10, 10, 10)

    pdf.set_font("Helvetica", "B", 15)
    pdf.cell(0, 10, _safe("AI Detection Harvest Log"), ln=True)
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 5, _safe(f"User: {username}  |  Generated: {datetime.date.today()}"), ln=True)
    pdf.ln(3)

    headers = ["Timestamp", "Section", "Young", "Ready", "Overripe", "Total", "Analyzed URL"]
    widths  = [40,           18,        15,      15,      18,         15,      136]

    pdf.set_fill_color(76, 175, 80)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 7)
    for h, w in zip(headers, widths):
        pdf.cell(w, 7, _safe(h), border=1, fill=True)
    pdf.ln()

    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "", 6)
    left_margin = 10
    row_h = 6
    for i, (_, row) in enumerate(df_log.iterrows()):
        fill_color = (245, 245, 245) if i % 2 == 0 else (255, 255, 255)
        pdf.set_fill_color(*fill_color)

        anal = str(row.get("analyzed_url") or "-")
        if anal in ("None", "nan") or anal.strip() == "":
            anal = "-"

        vals = [
            str(row.get("timestamp", ""))[:20],
            str(row.get("section_id", "-"))[:8],
            str(int(row.get("young", 0))),
            str(int(row.get("ready", 0))),
            str(int(row.get("overripe", 0))),
            str(int(row.get("total_clusters", 0))),
        ]

        for val, w in zip(vals, widths[:-1]):
            pdf.cell(w, row_h, _safe(val), border=1, fill=True)

        y_before = pdf.get_y()
        pdf.multi_cell(widths[-1], row_h, _safe(anal), border=1, fill=True, align="L")
        pdf.set_xy(left_margin, max(pdf.get_y(), y_before + row_h))

    pdf.ln(5)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(0, 6, _safe(f"Total Scans   : {len(df_log)}"), ln=True)
    pdf.cell(0, 6, _safe(f"Total Ready   : {int(df_log['ready'].sum())}"), ln=True)
    pdf.cell(0, 6, _safe(f"Total Overripe: {int(df_log['overripe'].sum())}"), ln=True)

    return bytes(pdf.output())


def show():
    st.title("🍄 AI Mushroom Detection System")

    # ── Session state init ─────────────────────────────────────────
    if "last_processed_file" not in st.session_state:
        st.session_state.last_processed_file = None
    if "dialog_row" not in st.session_state:
        st.session_state.dialog_row = None
    if "input_key" not in st.session_state:
        st.session_state.input_key = 0
    if "last_section" not in st.session_state:
        st.session_state.last_section = None

    try:
        model = load_model()
    except Exception as e:
        st.error(f"Error loading model: {e}. Make sure 'mushroom_yolo.pt' is in the project folder.")
        st.stop()

    # ── Section Selector ───────────────────────────────────────────
    st.markdown("### 📍 Select Mushroom Block")
    section_options = _build_section_options()
    selected_section = st.selectbox(
        "Block",
        options=section_options,
        index=0,
        key="sel_section"
    )
    st.info(f"📍 Selected Mushroom Block: **{selected_section}**")

    if st.session_state.last_section != selected_section:
        st.session_state.last_section = selected_section
        st.session_state.last_processed_file = None
        st.session_state.input_key += 1
        st.rerun()

    # ── Input Method ───────────────────────────────────────────────
    input_method = st.radio(
        "Choose Input Method",
        ["📷 Take Mushroom Photo", "📂 Upload Mushroom Photo"],
        horizontal=True
    )

    if input_method == "📷 Take Photo":
        st.info("📸 Take one photo for mushroom detection.")
        source = st.camera_input("Take Photo", label_visibility="collapsed", key=f"cam_{st.session_state.input_key}")
    else:
        st.info("📁 Upload an image file (JPG, PNG) for mushroom detection.")
        source = st.file_uploader("Upload Image", type=["jpg", "png", "jpeg"], key=f"upload_{st.session_state.input_key}")

    col_main, col_side = st.columns([2, 1])

    with col_main:
        if source:
            image = Image.open(source).convert("RGB")
            with st.spinner("Analyzing..."):
                results = model.predict(
                    source=image,
                    conf=0.4,
                    imgsz=640,
                    agnostic_nms=True
                )[0]

            detections = [model.names[int(box.cls[0])].lower() for box in results.boxes]
            c_young    = detections.count("young")
            c_ready    = detections.count("ready")
            c_overripe = detections.count("overripe")

            st.markdown("### 📊 Live Inventory Metrics")
            m1, m2, m3, m4, m5 = st.columns(5)
            m1.metric("📍 Section",  selected_section)
            m2.metric("🌱 Young",    c_young)
            m3.metric("✅ Ready",    c_ready)
            m4.metric("⚠️ Overripe", c_overripe)
            m5.metric("📦 Total",    len(detections))

            st.markdown("### 📋 Smart Harvest Guidance")
            if c_ready > 0:
                st.success(f"✂️ **HARVEST NOW:** {c_ready} clusters are ready for market.")
            if c_overripe > 0:
                st.error(f"🚨 **URGENT:** {c_overripe} clusters are overripe. Remove immediately to prevent spore release.")
            if c_young > 0:
                st.info(f"🕒 **STATUS:** {c_young} clusters are currently in growth phase.")
            if not detections:
                st.warning("🔎 **NO DETECTION:** No mushrooms identified. Check lighting or camera focus.")

            st.markdown("---")
            st.subheader("🖥️ Vision Analysis")
            analyzed_img = results.plot()
            st.image(analyzed_img, use_container_width=True)

            fname = source.name if hasattr(source, 'name') else "Capture"

            if st.session_state.last_processed_file != fname:
                original_url = None
                analyzed_url = None

                with st.spinner("☁️ Uploading images to Cloudinary..."):
                    scan_id = get_local_now().strftime("%Y%m%d_%H%M%S")
                    try:
                        original_url = upload_pil_image(image, f"original_{scan_id}")
                        analyzed_url = upload_numpy_image(analyzed_img, f"analyzed_{scan_id}")
                    except Exception as e:
                        st.warning(f"Image upload failed: {e}. Scan saved without images.")

                try:
                    conn_log = get_db_connection()
                    conn_log.execute(
                        """INSERT INTO ai_harvest_logs
                           (timestamp, filename, section_id, young, ready, overripe,
                            total_clusters, username, original_url, analyzed_url)
                           VALUES (?,?,?,?,?,?,?,?,?,?)""",
                        (get_local_now().strftime("%Y-%m-%d %H:%M:%S"), fname,
                         selected_section, c_young, c_ready, c_overripe,
                         len(detections), st.session_state.username,
                         original_url, analyzed_url)
                    )
                    conn_log.commit()
                    st.toast(f"Saved — Section {selected_section}: {fname}", icon="✅")
                except Exception as e:
                    st.warning(f"Failed to save scan log: {e}")
                finally:
                    conn_log.close()

                st.session_state.last_processed_file = fname

    # ── Harvest Log ────────────────────────────────────────────────
    with col_side:
        st.subheader("📋 Harvest Log")

        conn_view = get_db_connection()
        df_log = db_read_sql(
            "SELECT * FROM ai_harvest_logs WHERE username = ? ORDER BY timestamp DESC",
            conn_view, params=(st.session_state.username,)
        )
        conn_view.close()

        # ── Dialog ────────────────────────────────────────────────
        @st.dialog("🖼️ Scan Images", width="large")
        def show_image_dialog(row):
            st.markdown(f"**🕐 Scan Time: {row['timestamp']}**")
            st.markdown(
                f"📍 **Section:** {row.get('section_id', '-')} &nbsp;|&nbsp; "
                f"🍄 **Total:** {int(row.get('total_clusters') or 0)} clusters &nbsp;|&nbsp; "
                f"✅ **Ready:** {int(row.get('ready') or 0)}"
            )
            st.divider()
            col_orig, col_anal = st.columns(2)
            with col_orig:
                st.markdown("##### 📷 Original Photo")
                if row.get("original_url"):
                    st.image(row["original_url"], use_container_width=True)
                    st.link_button("View Original", row["original_url"], use_container_width=True)
                else:
                    st.info("No image available.")
            with col_anal:
                st.markdown("##### 🔬 AI Analysis")
                if row.get("analyzed_url"):
                    st.image(row["analyzed_url"], use_container_width=True)
                    st.link_button("View Analysis", row["analyzed_url"], use_container_width=True)
                else:
                    st.info("No image available.")

        if st.session_state.dialog_row is not None:
            show_image_dialog(st.session_state.dialog_row)
            st.session_state.dialog_row = None

        if not df_log.empty:
            h1, h2, h3, h4, h5 = st.columns([2.5, 1.2, 1.2, 1.2, 1.0])
            h1.markdown("**Time**")
            h2.markdown("**Sec**")
            h3.markdown("**Ready**")
            h4.markdown("**Total**")
            h5.markdown("**View**")
            st.markdown("<hr style='margin:2px 0 6px 0'>", unsafe_allow_html=True)

            for i, (_, row) in enumerate(df_log.head(10).iterrows()):
                c1, c2, c3, c4, c5 = st.columns([2.5, 1.2, 1.2, 1.2, 1.0])
                c1.caption(row["timestamp"])
                c2.write(str(row.get("section_id", "-")))
                c3.write(int(row.get("ready") or 0))
                c4.write(int(row.get("total_clusters") or 0))
                if c5.button("🔍", key=f"view_{i}", use_container_width=True):
                    st.session_state.dialog_row = row.to_dict()
                    st.rerun()
                st.markdown("<hr style='margin:2px 0'>", unsafe_allow_html=True)

            st.divider()
            st.write(f"**Total Scans:** {len(df_log)}")
            st.write(f"**Total Ready Historically:** {int(df_log['ready'].sum())}")

            csv = df_log.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="📥 Download Full Report (CSV)",
                data=csv,
                file_name=f"harvest_log_{get_local_now().date()}.csv",
                mime="text/csv",
                use_container_width=True,
            )

            log_pdf = _build_log_pdf(df_log, st.session_state.username)
            st.download_button(
                label="📄 Download Full Report (PDF)",
                data=log_pdf,
                file_name=f"harvest_log_{get_local_now().date()}.pdf",
                mime="application/pdf",
                use_container_width=True,
            )

            if st.button("🗑️ Delete Database Records", use_container_width=True, type="primary"):
                conn_del = get_db_connection()
                conn_del.execute(
                    "DELETE FROM ai_harvest_logs WHERE username = ?",
                    (st.session_state.username,)
                )
                conn_del.commit()
                conn_del.close()
                st.session_state.last_processed_file = None
                st.rerun()
        else:
            st.info("No scans recorded yet. Use the scanner to start logging.")