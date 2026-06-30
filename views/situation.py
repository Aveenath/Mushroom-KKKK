import streamlit as st
import re
import os
import cloudinary
import cloudinary.uploader
from utils import get_db_connection, get_local_now

MAX_PHOTO_BYTES = 5 * 1024 * 1024  # 5 MB limit


def _upload_photo(photo_bytes):
    cloudinary.config(
        cloud_name=os.environ.get("CLOUDINARY_CLOUD_NAME"),
        api_key=os.environ.get("CLOUDINARY_API_KEY"),
        api_secret=os.environ.get("CLOUDINARY_API_SECRET"),
        secure=True,
    )
    result = cloudinary.uploader.upload(photo_bytes, folder="mushroom_farm", resource_type="image")
    return result["secure_url"]

SECTION_OPTIONS = [f"{letter}{i}{j}" for letter in "AB" for i in range(1, 4) for j in range(1, 4)]  # A11–B33


def _normalize_block(block_id):
    raw = block_id.strip()
    if not raw.startswith('B'):
        return None, "Block ID must start with uppercase 'B' (e.g. B1, B099). Lowercase 'b' is not allowed."
    match = re.match(r'^B(\d+)$', raw)
    if not match:
        return None, "Invalid format. Use B followed by a number only (e.g. B1, B099, B244)."
    number = int(match.group(1))
    if number < 1 or number > 244:
        return None, f"Block number must be between 1 and 244. Got: {number}."
    return f"B{number}", None


def show():
    st.title("📝 Record Daily Situation")

    # --- Choose mode OUTSIDE form so it re-renders dynamically ---
    record_by = st.radio("Record by:", ["Block", "Section"], horizontal=True)

    st.markdown("")

    # --- Photo uploader OUTSIDE form so preview works ---
    uploaded_photo = st.file_uploader(
        "📷 Disease / Condition Photo (optional — max 5 MB)",
        type=["jpg", "jpeg", "png"],
        help="Attach a photo of the mushroom block condition or disease."
    )
    photo_url = None
    if uploaded_photo is not None:
        photo_bytes = uploaded_photo.read()
        if len(photo_bytes) > MAX_PHOTO_BYTES:
            st.error("Photo is too large. Please upload a photo under 5 MB.")
            uploaded_photo = None
        else:
            st.image(photo_bytes, caption="Photo preview", width=300)
            with st.spinner("Uploading photo..."):
                try:
                    photo_url = _upload_photo(photo_bytes)
                    st.success("Photo uploaded ✅")
                except Exception as e:
                    st.warning(f"Photo upload failed — report will be saved without photo. ({e})")

    st.markdown("")

    with st.form("situation_form"):
        # --- Row 1: Date & Time ---
        col_date, col_time = st.columns(2)
        with col_date:
            date = st.date_input("Report Date", get_local_now().date())
        with col_time:
            time = st.time_input("Report Time", get_local_now().time())

        # --- Row 2: Block OR Section (not both) ---
        if record_by == "Block":
            selected_block = st.text_input("Block ID", placeholder="e.g. B1, B099, B244")
            selected_section = "-"
        else:
            selected_section = st.selectbox("Section (A11–B33)", SECTION_OPTIONS)
            selected_block = "-"

        # --- Situation ---
        status = st.selectbox("Current Situation", ["Normal", "Harvesting", "Disease Detected", "Maintenance"])

        # --- Quality ---
        st.markdown("**Mushroom Quality**")
        quality = st.radio(
            "Mushroom Quality",
            options=["🔴 Bad", "🟡 Normal", "🟢 Good"],
            index=1,
            horizontal=True,
            label_visibility="collapsed"
        )

        # --- Disease ---
        disease = st.text_input("Disease Name (leave blank if none)", placeholder="e.g. Trichoderma, Neurospora")

        # --- Notes ---
        notes = st.text_area("Detailed Notes", placeholder="Describe conditions, observations, or actions taken...")

        if st.form_submit_button("💾 Save Report", use_container_width=True, type="primary"):
            report_datetime = f"{date} {time.strftime('%H:%M')}"
            clean_quality = quality.split(" ", 1)[1]
            disease_val = disease.strip() if disease.strip() else "None"

            if record_by == "Block":
                if not selected_block.strip():
                    st.error("Please enter a Block ID.")
                else:
                    block_ref, err = _normalize_block(selected_block.strip().upper())
                    if err:
                        st.error(err)
                    else:
                        conn = get_db_connection()
                        conn.execute(
                            "INSERT INTO situation_reports (date, status, disease_noted, quality, notes, username, block_id, section_id, photo) VALUES (?,?,?,?,?,?,?,?,?)",
                            (report_datetime, status, disease_val, clean_quality, notes,
                             st.session_state.username, block_ref, "-", photo_url)
                        )
                        conn.commit()
                        conn.close()
                        st.success(f"Report saved — Block **{block_ref}** | Quality: {quality}")
            else:
                conn = get_db_connection()
                conn.execute(
                    "INSERT INTO situation_reports (date, status, disease_noted, quality, notes, username, block_id, section_id, photo) VALUES (?,?,?,?,?,?,?,?,?)",
                    (report_datetime, status, disease_val, clean_quality, notes,
                     st.session_state.username, "-", selected_section, photo_url)
                )
                conn.commit()
                conn.close()
                st.success(f"Report saved — Section **{selected_section}** | Quality: {quality}")
