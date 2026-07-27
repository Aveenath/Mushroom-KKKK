import streamlit as st
import os
import cloudinary
import cloudinary.uploader
from utils import get_db_connection, get_local_now
from translations import t

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
    if not raw:
        return None, "Block ID cannot be empty."
    return raw.upper(), None


def show():
    st.title(t('sit_title'))

    # --- Photo uploader OUTSIDE form so preview works ---
    uploaded_photo = st.file_uploader(
        t('sit_photo'),
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
            date = st.date_input(t('sit_date'), get_local_now().date())
        with col_time:
            time = st.time_input(t('sit_time'), get_local_now().time())

        # --- Row 2: Block ID ---
        selected_block = st.text_input(t('sit_block_id'), placeholder="e.g. B1, B099, B244")
        selected_section = "-"

        # --- Situation ---
        status = st.selectbox(t('sit_situation'), ["Normal", "Harvesting", "Disease Detected", "Maintenance"])

        # --- Quality ---
        st.markdown(f"**{t('sit_quality')}**")
        quality = st.radio(
            t('sit_quality'),
            options=["🔴 Bad", "🟡 Normal", "🟢 Good"],
            index=1,
            horizontal=True,
            label_visibility="collapsed"
        )

        # --- Disease ---
        disease = st.text_input(t('sit_disease'), placeholder="e.g. Trichoderma, Neurospora")

        # --- Notes ---
        notes = st.text_area(t('sit_notes'), placeholder="Describe conditions, observations, or actions taken...")

        if st.form_submit_button(t('sit_save'), use_container_width=True, type="primary"):
            report_datetime = f"{date} {time.strftime('%H:%M')}"
            clean_quality = quality.split(" ", 1)[1]
            disease_val = disease.strip() if disease.strip() else "None"

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
