import streamlit as st
import pandas as pd
import time
from PIL import Image
from ultralytics import YOLO
from utils import get_db_connection, get_local_now, db_read_sql

try:
    import av
    from streamlit_webrtc import VideoProcessorBase, webrtc_streamer
    WEBRTC_AVAILABLE = True
except ImportError:
    WEBRTC_AVAILABLE = False


@st.cache_resource
def load_model():
    return YOLO("mushroom_yolo.pt")


if WEBRTC_AVAILABLE:
    class MushroomVideoTransformer(VideoProcessorBase):
        def __init__(self):
            self.model = load_model()
            self.names = self.model.names
            self.last_counts = {"young": 0, "ready": 0, "overripe": 0, "total": 0}
            self._frame_count = 0
            self._last_annotated = None

        def recv(self, frame):
            self._frame_count += 1
            image = frame.to_ndarray(format="bgr24")

            # Run inference every 3rd frame only to reduce lag
            if self._frame_count % 3 == 0:
                results = self.model.predict(source=image, conf=0.60, imgsz=320, agnostic_nms=True)[0]
                detections = [self.names[int(box.cls[0])].lower() for box in results.boxes]
                self.last_counts = {
                    "young":    detections.count("young"),
                    "ready":    detections.count("ready"),
                    "overripe": detections.count("overripe"),
                    "total":    len(detections),
                }
                self._last_annotated = results.plot()

            out = self._last_annotated if self._last_annotated is not None else image
            return av.VideoFrame.from_ndarray(out, format="bgr24")


def show():
    st.title("🍄 Mushroom Detection")

    try:
        model = load_model()
    except Exception as e:
        st.error(f"Error loading model: {e}. Make sure 'mushroom_yolo.pt' is in the project folder.")
        st.stop()

    options = ["📹 Continuous Stream", "📷 Take Photo", "📂 Upload File"] if WEBRTC_AVAILABLE else ["📷 Take Photo", "📂 Upload File"]
    input_method = st.radio("Choose Input Method", options, horizontal=True)

    source = None
    continuous_mode = input_method == "📹 Continuous Stream"
    webrtc_ctx = None

    if continuous_mode:
        st.info("📹 Continuous Stream: Allow camera access and the app will process webcam frames live.")
        webrtc_ctx = webrtc_streamer(
            key="mushroom-stream",
            video_processor_factory=MushroomVideoTransformer,
            media_stream_constraints={"video": True, "audio": False},
        )
    elif input_method == "📷 Take Photo":
        st.info("📸 Take one photo for mushroom detection.")
        source = st.camera_input("Take Photo", label_visibility="collapsed")
    else:
        st.info("📁 Upload an image file (JPG, PNG) for mushroom detection.")
        source = st.file_uploader("Upload Image", type=["jpg", "png", "jpeg"])

    col_main, col_side = st.columns([2, 1])

    # Persist last stream counts across reruns (survives STOP)
    if 'stream_counts' not in st.session_state:
        st.session_state.stream_counts = {"young": 0, "ready": 0, "overripe": 0, "total": 0}

    with col_main:
        if continuous_mode:
            stream_playing = webrtc_ctx and getattr(webrtc_ctx.state, 'playing', False)

            # Always save counts when processor exists — captures the last frame before STOP
            if webrtc_ctx and webrtc_ctx.video_processor:
                live = webrtc_ctx.video_processor.last_counts
                # Only overwrite if something was actually detected (don't wipe a good result with zeros)
                if live.get("total", 0) > 0 or stream_playing:
                    st.session_state.stream_counts = live

            counts     = st.session_state.stream_counts
            c_young    = counts.get("young", 0)
            c_ready    = counts.get("ready", 0)
            c_overripe = counts.get("overripe", 0)
            total      = counts.get("total", 0)

            st.markdown("### 📊 Live Inventory Metrics")
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("🌱 Young", c_young)
            m2.metric("✅ Ready", c_ready)
            m3.metric("⚠️ Overripe", c_overripe)
            m4.metric("📦 Total Clusters", total)

            st.markdown("### 📋 Smart Harvest Guidance")
            if not stream_playing and total == 0:
                st.info("▶️ Press **START** above and allow camera access to begin detection.")
            else:
                if c_ready > 0:
                    st.success(f"✂️ **HARVEST NOW:** {c_ready} clusters are ready for market.")
                if c_overripe > 0:
                    st.error(f"🚨 **URGENT:** {c_overripe} clusters are overripe. Remove immediately to prevent spore release.")
                if c_young > 0:
                    st.info(f"🕒 **STATUS:** {c_young} clusters are currently in growth phase.")
                if total == 0 and stream_playing:
                    st.warning("🔎 **NO DETECTION YET:** Point the camera at the mushroom bed and wait a moment.")

            # Auto-refresh every 1 second while stream is active
            if stream_playing:
                time.sleep(1)
                st.rerun()

        elif source:
            image = Image.open(source).convert("RGB")
            with st.spinner("Analyzing..."):
                results = model.predict(source=image, conf=0.60, imgsz=1024, agnostic_nms=True)[0]

            detections = [model.names[int(box.cls[0])].lower() for box in results.boxes]
            c_young    = detections.count("young")
            c_ready    = detections.count("ready")
            c_overripe = detections.count("overripe")

            st.markdown("### 📊 Live Inventory Metrics")
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("🌱 Young", c_young)
            m2.metric("✅ Ready", c_ready)
            m3.metric("⚠️ Overripe", c_overripe)
            m4.metric("📦 Total Clusters", len(detections))

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
            st.image(results.plot(), use_container_width=True)

            ts_now = get_local_now().strftime("%Y-%m-%d %H:%M:%S")
            fname = source.name if hasattr(source, 'name') else "Capture"

            if st.session_state.last_processed_file != fname:
                conn_log = get_db_connection()
                conn_log.execute(
                    "INSERT INTO ai_harvest_logs (timestamp, filename, young, ready, old, total_clusters, username) VALUES (?,?,?,?,?,?,?)",
                    (ts_now, fname, c_young, c_ready, c_overripe, len(detections), st.session_state.username)
                )
                conn_log.commit()
                conn_log.close()
                st.session_state.last_processed_file = fname
                st.toast(f"Saved to Database: {fname}", icon="✅")

    with col_side:
        st.subheader("📋 Persistent Harvest Log")
        conn_view = get_db_connection()
        df_log = db_read_sql(
            "SELECT * FROM ai_harvest_logs WHERE username = ? ORDER BY timestamp DESC",
            conn_view, params=(st.session_state.username,)
        )
        conn_view.close()

        if not df_log.empty:
            st.dataframe(df_log[["timestamp", "ready", "total_clusters"]].head(10), hide_index=True)
            st.write(f"**Total ready historically:** {df_log['ready'].sum()}")

            csv = df_log.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="📥 Download Full Report",
                data=csv,
                file_name=f"harvest_history_{get_local_now().date()}.csv",
                mime="text/csv",
                use_container_width=True
            )

            if st.button("🗑️ Delete Database Records", use_container_width=True, type="primary"):
                conn_del = get_db_connection()
                conn_del.execute("DELETE FROM ai_harvest_logs WHERE username = ?", (st.session_state.username,))
                conn_del.commit()
                conn_del.close()
                st.session_state.last_processed_file = None
                st.rerun()
        else:
            st.info("No scans recorded yet. Use the scanner to start logging.")

    st.divider()
    st.caption("Teammate AI UI | YOLO11 | Maturity Model")
