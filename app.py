import streamlit as st
import pandas as pd
import sqlite3
import datetime
import requests
import plotly.express as px
from analysis import get_predictions # This pulls the AI logic from your other file
from ultralytics import YOLO
from PIL import Image
import numpy as np

st.set_page_config(page_title="Mushroom Farm OS", layout="wide")

# --- SESSION STATE ---
if 'last_processed_file' not in st.session_state:
    st.session_state.last_processed_file = None

st.markdown("""
    <style>
    .main { background-color: transparent; }
    [data-testid="stMetricValue"] {
        font-size: 28px;
        color: #4CAF50 !important; /* Professional Mushroom Green */
    }
    /* Simple card styling for metrics */
    [data-testid="stMetric"] {
        background-color: rgba(128, 128, 128, 0.1);
        padding: 10px;
        border-radius: 10px;
        border: 1px solid rgba(128, 128, 128, 0.2);
    }
    </style>
    """, unsafe_allow_html=True)

# --- LOAD MODEL ---
@st.cache_resource
def load_new_model():
    # Ensure 'mushroom_yolo.pt' is in the same folder as app.py
    return YOLO("mushroom_yolo.pt")

def get_db_connection():
    return sqlite3.connect('mushroom_client.db')

# Ensure tables exist
conn = get_db_connection()
conn.execute('''CREATE TABLE IF NOT EXISTS situation_reports 
             (date TEXT, status TEXT, disease_noted TEXT, quality TEXT, notes TEXT)''')
conn.execute('''CREATE TABLE IF NOT EXISTS planting_records
             (block_id TEXT, species TEXT, planted_date TEXT, notes TEXT, predicted_harvest TEXT)''')
conn.execute('''CREATE TABLE IF NOT EXISTS ai_harvest_logs
             (timestamp TEXT, filename TEXT, young INTEGER, ready INTEGER, old INTEGER, total_clusters INTEGER)''')
conn.close()

# --- NAVIGATION ---
page = st.sidebar.radio("Go to:", ["Live Monitor & Forecast", "Record Situation", "Record Planting", "SOP Procedures", "Quality Analysis", "AI Image Detection"])

# --- PAGE 1: MONITOR & FORECAST ---
if page == "Live Monitor & Forecast":
    st.title("📊 Monitoring & AI Forecasting")
    
    conn = get_db_connection()
    df = pd.read_sql("SELECT * FROM sensors ORDER BY ts DESC LIMIT 1440", conn)
    conn.close()
    
    st.subheader("🏠 Internal Farm Sensors")
    # 1. Real-time Stats
    col1, col2, col3 = st.columns(3)
    latest = df.iloc[0]
    
    # FEATURE 2: AUTOMATED RED ALERTS
    if latest['temp'] >= 29:
        st.error(f"🚨 **CRITICAL ALERT:** Internal temperature is {latest['temp']}°C (Exceeds 29°C)! Activate cooling systems immediately.")
    if latest['humidity'] <= 75:
        st.warning(f"⚠️ **WARNING:** Internal humidity has dropped to {latest['humidity']}% (Below 75%). Misting recommended.")
        
    col1.metric("Internal Temp", f"{latest['temp']}°C")
    col2.metric("Internal Humidity", f"{latest['humidity']}%")
    col3.metric("Internal CO2", f"{latest['co2']} ppm")

    # FEATURE 1: HISTORICAL SENSOR CHARTS
    st.markdown("#### 📈 24-Hour Climate History")
    df_history = df.copy()
    df_history['ts'] = pd.to_datetime(df_history['ts'])
    
    tab1, tab2, tab3 = st.tabs(["🌡️ Temperature", "💧 Humidity", "💨 CO2"])
    with tab1:
        fig_temp = px.line(df_history, x='ts', y='temp', title="Internal Temperature Trend")
        fig_temp.update_traces(line_color='#FF4B4B')
        st.plotly_chart(fig_temp, use_container_width=True)
    with tab2:
        fig_hum = px.line(df_history, x='ts', y='humidity', title="Internal Humidity Trend")
        fig_hum.update_traces(line_color='#00BFFF')
        st.plotly_chart(fig_hum, use_container_width=True)
    with tab3:
        fig_co2 = px.line(df_history, x='ts', y='co2', title="Internal CO2 Trend")
        fig_co2.update_traces(line_color='#00FF00')
        st.plotly_chart(fig_co2, use_container_width=True)

    st.markdown("---")
    st.subheader("🌦️ Outside Weather (Live & Forecast)")
    loc_choice = st.selectbox("Select Farm Location:", ["Penang, MY", "Kedah (Sungai Petani), MY", "Perak (Kuala Kangsar), MY"])
    
    # Lat/Lon for the areas
    if loc_choice == "Penang, MY":
        lat, lon = 5.4141, 100.3288 # George Town
    elif loc_choice == "Kedah (Sungai Petani), MY":
        lat, lon = 5.6419, 100.4877 # Sungai Petani
    else:
        lat, lon = 4.7730, 100.9410 # Kuala Kangsar
        
    try:
        # Fetch data from Open-Meteo API
        weather_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,relative_humidity_2m&hourly=temperature_2m&timezone=Asia%2FSingapore&forecast_days=2"
        headers = {'User-Agent': 'MushroomFarm-App/1.0'}
        res = requests.get(weather_url, headers=headers, timeout=5).json()
        
        # Display current weather
        curr_temp = res['current']['temperature_2m']
        curr_hum = res['current']['relative_humidity_2m']
        
        w_col1, w_col2 = st.columns(2)
        w_col1.metric("Outside Temp", f"{curr_temp}°C")
        w_col2.metric("Outside Humidity", f"{curr_hum}%")
        
        # Plot 24-hour future forecast
        times = res['hourly']['time']
        temps = res['hourly']['temperature_2m']
        
        df_weather = pd.DataFrame({'Time': times, 'Outside Temp (°C)': temps})
        df_weather['Time'] = pd.to_datetime(df_weather['Time'])
        
        # Filter from current hour to +24 hrs
        current_hour_naive = datetime.datetime.now().replace(minute=0, second=0, microsecond=0)
        df_weather = df_weather[df_weather['Time'] >= current_hour_naive].head(24)
        
         # AUTOMATED RED ALERTS: External Heatwave Warning
        max_pred = df_weather['Outside Temp (°C)'].max()
        if max_pred >= 33:
            st.warning(f"⚠️ **OUTSIDE HEATWAVE ALERT:** Forecast predicts peak outside temperatures hitting {max_pred:.1f}°C in the next 24 hours.")
        
        fig_weather = px.line(df_weather, x='Time', y='Outside Temp (°C)', 
                              title=f"24-Hour Outside Weather Prediction ({loc_choice})",
                              line_shape='spline', render_mode='svg')
        fig_weather.update_traces(line_color='#00BFFF')
        fig_weather.update_layout(xaxis_title="Time", yaxis_title="Temperature (°C)")
        st.plotly_chart(fig_weather, use_container_width=True)
        
    except Exception as e:
        st.warning("⚠️ **Network Blocked:** Cannot connect to the outside weather satellite right now due to a strict network firewall or VPN. Live outside weather updates are temporarily paused.")

    # 2. The Forecasting Section
    st.markdown("---")
    st.subheader("🔮 7-Day Predictive Forecast")
    st.write("Upload a historical dataset (CSV/Excel) to automatically analyze its patterns and forecast the next 7 days.")
    
    uploaded_forecast = st.file_uploader("Upload Historical Data (CSV or Excel)", type=["csv", "xlsx"])
    if uploaded_forecast is not None:
        if uploaded_forecast.name.endswith('.csv'):
            df_upload = pd.read_csv(uploaded_forecast)
        else:
            df_upload = pd.read_excel(uploaded_forecast)
            
        # Dynamically identify Timestamp and Temperature columns based on typical keywords
        col_lower = {c: str(c).lower() for c in df_upload.columns}
        
        ts_col = next((c for c, l in col_lower.items() if 'time' in l or 'ts' in l or 'date' in l), None)
        temp_col = next((c for c, l in col_lower.items() if 'temp' in l), None)
        
        if not ts_col or not temp_col:
            st.error("Uploaded file must contain a timestamp/time column and a temperature column.")
        else:
            # Rename the found columns to exactly what 'analysis.py' expects
            df_upload = df_upload.rename(columns={ts_col: 'ts', temp_col: 'temp'})
            
            st.success(f"Successfully detected '{ts_col}' as Time and '{temp_col}' as Temp!")
            if st.button("Run AI Analysis on Uploaded Data"):
                with st.spinner('Analyzing uploaded dataset...'):
                    try:
                        # Pass custom dataframe to analysis.py
                        predictions = get_predictions(df_upload) 
                        
                        future_times = [datetime.datetime.now() + datetime.timedelta(hours=i) for i in range(1, 169)]
                        forecast_df = pd.DataFrame({'Time': future_times, 'Predicted Temp (°C)': predictions})
                        
                        fig_forecast = px.line(forecast_df, x='Time', y='Predicted Temp (°C)', 
                                             title="Expected Temperature Trend (Next 7 Days)",
                                             line_shape='spline', render_mode='svg')
                        fig_forecast.update_traces(line_color='#FF4B4B')
                        st.plotly_chart(fig_forecast, use_container_width=True)
                        st.success("Analysis complete based on your custom file!")
                    except Exception as e:
                        st.error(f"Could not generate forecast: {e}")

# --- PAGE 2: RECORD SITUATION (The input part for your client) ---
elif page == "Record Situation":
    st.title("📝 Record Daily Situation")
    with st.form("situation_form"):
        date = st.date_input("Report Date", datetime.date.today())
        time = st.time_input("Report Time", datetime.datetime.now().time())
        status = st.selectbox("Current Situation", ["Normal", "Disease Detected", "Harvesting", "Maintenance"])
        quality = st.radio("Mushroom Quality", options=["Bad", "Normal", "Good"], horizontal=True)
        disease = st.text_input("Disease Name (if any)", "None")
        notes = st.text_area("Detailed Notes")
        
        if st.form_submit_button("Save Report"):
            report_datetime = f"{date} {time.strftime('%H:%M')}"
            conn = get_db_connection()
            conn.execute("INSERT INTO situation_reports VALUES (?,?,?,?,?)", 
                         (report_datetime, status, disease, quality, notes))
            conn.commit()
            conn.close()
            st.success("Situation recorded successfully!")

# --- PAGE 2B: RECORD PLANTING ---
elif page == "Record Planting":
    st.title("🌱 Record Planting & Forecast Harvest")
    st.write("Log new mushroom blocks and instantly get AI-calculated harvest windows.")
    
    # Load predictor logic
    try:
        from analysis import predict_harvest_date
    except ImportError:
        predict_harvest_date = lambda x: "Wait 21-28 days"

    with st.form("planting_form"):
        block_id = st.text_input("Batch / Block ID Number (e.g., OYS-001)")
        species = st.selectbox("Mushroom Species", ["Oyster Mushroom"])
        planted_date = st.date_input("Inoculation/Planting Date", datetime.date.today())
        notes = st.text_area("Initial Conditions / Notes")
        
        if st.form_submit_button("Record & Calculate Harvest"):
            if block_id.strip() == "":
                st.error("Please provide a valid Batch / Block ID.")
            else:
                # 1. AI Rule Predictor
                planted_str = planted_date.strftime("%Y-%m-%d")
                predicted_harvest = predict_harvest_date(planted_str)
                
                # 2. Database Insert
                conn = get_db_connection()
                conn.execute("INSERT INTO planting_records VALUES (?,?,?,?,?)", 
                             (block_id, species, planted_str, notes, predicted_harvest))
                conn.commit()
                conn.close()
                st.success(f"Planting recorded! Expect harvest between **{predicted_harvest}**")
    
    st.markdown("---")
    st.subheader("📋 Current Active Inventory")
    conn = get_db_connection()
    try:
        planting_df = pd.read_sql("SELECT block_id AS 'Block ID', species AS 'Species', planted_date AS 'Planted Date', predicted_harvest AS 'Predicted Harvest Window', notes AS 'Notes' FROM planting_records ORDER BY planted_date DESC", conn)
        if not planting_df.empty:
            # 1. CSV EXPORT
            csv = planting_df.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="📥 Export Inventory to Excel/CSV",
                data=csv,
                file_name="active_inventory.csv",
                mime="text/csv",
            )
            
            # 2. SELECTIVE DELETION
            planting_df.insert(len(planting_df.columns), "Delete?", False)
            
            edited_df = st.data_editor(
                planting_df,
                column_config={
                    "Delete?": st.column_config.CheckboxColumn(
                        "🗑️ Delete?",
                        help="Check this box to delete the row",
                        default=False,
                    )
                },
                disabled=["Block ID", "Species", "Planted Date", "Predicted Harvest Window", "Notes"],
                hide_index=True,
                use_container_width=True
            )

            rows_to_delete = edited_df[edited_df["Delete?"] == True]
            
            if not rows_to_delete.empty:
                st.warning(f"You have selected {len(rows_to_delete)} row(s) for deletion.")
                if st.button("🚨 Confirm Delete Selected Blocks"):
                    conn_delete = get_db_connection()
                    for block in rows_to_delete['Block ID']:
                        conn_delete.execute("DELETE FROM planting_records WHERE block_id = ?", (block,))
                    conn_delete.commit()
                    conn_delete.close()
                    st.success("Blocks deleted successfully!")
                    st.rerun()
        else:
            st.info("No planting records found.")
    except Exception as e:
        st.info("No planting records found.")
    finally:
        conn.close()

# --- PAGE 3: SOP PROCEDURES ---
elif page == "SOP Procedures":
    st.title("📜 Standard Operating Procedures (SOP)")
    st.write("Adhere to these clinical guidelines to ensure maximum yield and minimize contamination risks.")
    
    st.markdown("---")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("🌡️ 1. Environmental Control")
        with st.expander("Temperature Management", expanded=True):
            st.info("**Target:** 24°C - 28°C")
            st.markdown("""
            - **Above 30°C:** Activate exhaust fans to 100% capacity.
            - **Below 22°C:** Reduce ventilation and monitor closely.
            - *Notes: Avoid sudden extreme temperature shocks to prevent pinning abortions.*
            """)
            
        with st.expander("Humidity & Moisture", expanded=True):
            st.info("**Target:** 80% - 90% RH")
            st.markdown("""
            - **Misting:** Spray fine water mist upwards into the air 2-3 times daily.
            - **Warning:** NEVER spray water directly onto the mushroom caps (causes bacterial dark blotch).
            - Keep the floor damp but avoid standing stagnant water puddles.
            """)
            
        with st.expander("CO2 & Air Exchange"):
            st.info("**Target:** < 1000 ppm")
            st.markdown("""
            - High CO2 leads to long stems and small underdeveloped caps. 
            - Ensure cross-flow ventilation is active for at least 30 minutes every 4 hours.
            """)

    with col2:
        st.subheader("🛡️ 2. Disease & Contamination")
        with st.expander("Trichoderma (Green Mold) Protocol", expanded=True):
            st.error("**CRITICAL: Highly Contagious**")
            st.markdown("""
            1. **Identification:** Fluffy white patches turning into forest-green powder.
            2. **Immediate Action:** Remove the contaminated bag without squeezing it.
            3. **Disposal:** Seal in a garbage bag and dispose of it far from the active farm.
            4. **Sanitization:** Spray 70% Isopropyl alcohol in the area where the bag sat.
            """)
            
        with st.expander("Neurospora (Orange Mold)"):
            st.warning("**Fast Spreading Spores**")
            st.markdown("""
            - Orange/Pink powdery mold spread quickly via airborne dust.
            - Carefully isolate out of the facility immediately.
            - Keep facility air-filters clean.
            """)
            
        with st.expander("General Pest Control"):
            st.markdown("""
            - **Fungus Gnats & Flies:** Install yellow sticky insect traps near light panels.
            - Maintain strict hygiene. Ensure all workers deploy footbaths before entering.
            """)

    st.markdown("---")
    st.subheader("✂️ 3. Harvesting Guidelines")
    st.success("Optimal Harvesting Window: Just before the cap edges flatten or begin turning upwards.")
    st.markdown("""
    - **Technique:** Firmly hold the base of the mushroom cluster, gently twist, and pull. Do not cut with a knife as leaving stump remnants promotes rotting.
    - **Hygiene:** Handlers must sanitize hands with 70% alcohol or wear fresh gloves before contact. 
    - **Post-Harvest:** Clean the opening of the block after harvest to encourage secondary fruiting flushes.
    """)

# --- PAGE 4: QUALITY ANALYSIS ---
elif page == "Quality Analysis":
    st.title("📈 Quality & Disease Analysis")
    conn = get_db_connection()
    reports_df = pd.read_sql("SELECT * FROM situation_reports ORDER BY date DESC", conn)
    reports_df.rename(columns={"date": "Date & Time"}, inplace=True)
    conn.close()
    
    if not reports_df.empty:
        # FEATURE 3: MEANINGFUL QUALITY ANALYSIS
        st.subheader("📊 Executive Summary")
        col1, col2 = st.columns(2)
        
        with col1:
            fig_pie = px.pie(reports_df, names='quality', title='Overall Harvest Quality', hole=0.3)
            st.plotly_chart(fig_pie, use_container_width=True)
            
        with col2:
            # Drop null or 'None' values
            disease_df = reports_df[~reports_df['disease_noted'].astype(str).str.lower().isin(['none', 'null', ''])]
            if not disease_df.empty:
                fig_bar = px.histogram(disease_df, x='disease_noted', title='Reported Diseases Frequency', color='disease_noted')
                st.plotly_chart(fig_bar, use_container_width=True)
            else:
                st.success("🎉 No diseases recorded. Excellent farm health!")
                
        st.markdown("---")
        st.subheader("📝 Complete Log Repository")
        
        # FEATURE 5: CSV EXPORT DOWNLOAD
        csv = reports_df.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="📥 Export Full Logs to Excel/CSV",
            data=csv,
            file_name="mushroom_farm_reports.csv",
            mime="text/csv",
        )
        
        # Add a placeholder 'Delete?' column to the dataframe
        reports_df.insert(len(reports_df.columns), "Delete?", False)
        
        # Display as an interactive Data Editor
        edited_df = st.data_editor(
            reports_df,
            column_config={
                "Delete?": st.column_config.CheckboxColumn(
                    "🗑️ Delete?",
                    help="Check this box to delete the row",
                    default=False,
                )
            },
            disabled=["Date & Time", "status", "disease_noted", "quality", "notes"],
            hide_index=True,
            use_container_width=True
        )

        # Detect if any rows were checked for deletion
        rows_to_delete = edited_df[edited_df["Delete?"] == True]
        
        if not rows_to_delete.empty:
            st.warning(f"You have selected {len(rows_to_delete)} log(s) for deletion.")
            if st.button("🚨 Confirm Delete Selected Logs"):
                conn = get_db_connection()
                for dt in rows_to_delete['Date & Time']:
                    conn.execute("DELETE FROM situation_reports WHERE date = ?", (dt,))
                conn.commit()
                conn.close()
                st.success("Logs successfully deleted!")
                st.rerun()
                    
    else:
        st.info("No reports found. Start recording in the 'Record Situation' tab.")

# --- PAGE 5: AI IMAGE DETECTION ---
elif page == "AI Image Detection":
    st.title("🍄 Mushroom Detection")

    try:
        model = load_new_model()
    except Exception as e:
        st.error(f"Error loading model: {e}. Make sure 'mushroom_yolo.pt' is in the project folder.")
        st.stop()

    input_method = st.radio("Choose Input Method", ["📷 Camera Feed", "📂 File Upload"], horizontal=True)
    
    # Custom CSS to restrict camera width and give it a 'Mobile Scanner' feel
    st.markdown("""
        <style>
        [data-testid="stCameraInput"] {
            max-width: 500px !important;
            margin: 0 auto;
            border: 8px solid #2e2e2e;
            border-radius: 24px;
            overflow: hidden;
            box-shadow: 0px 10px 20px rgba(0,0,0,0.3);
        }
        </style>
    """, unsafe_allow_html=True)
    
    source = None
    if input_method == "📷 Camera Feed":
        source = st.camera_input("Scanner")
    else:
        source = st.file_uploader("Upload Image", type=["jpg", "png", "jpeg"])

    col_main, col_side = st.columns([2, 1])

    with col_main:
        if source:
            # Force RGB conversion to prevent YOLO color distortions on Web PNGs
            image = Image.open(source).convert("RGB")
            with st.spinner("Analyzing..."):
                # Restore imgsz=1024 as specified by the training parameters
                results = model.predict(source=image, conf=0.40, imgsz=1024, agnostic_nms=True)[0]
            
            detections = [model.names[int(box.cls[0])].lower() for box in results.boxes]
            
            c_young = detections.count("young")
            c_ready = detections.count("ready") 
            c_old   = detections.count("old")
            
            # --- THE CARDS ---
            st.markdown("### 📊 Live Inventory Metrics")
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("🌱 Young", c_young)
            m2.metric("✅ Ready", c_ready)
            m3.metric("⚠️ Old", c_old)
            m4.metric("📦 Total Clusters", len(detections))

            # --- SMART HARVEST ACTIONS ---
            st.markdown("### 📋 Smart Harvest Guidance")
            if c_ready > 0:
                st.success(f"✂️ **HARVEST NOW:** {c_ready} clusters are ready for market.")
            if c_old > 0:
                st.error(f"🚨 **URGENT:** {c_old} clusters are overmature. Remove to prevent spore release.")
            if c_young > 0:
                st.info(f"🕒 **STATUS:** {c_young} clusters are currently in growth phase.")
            if not detections:
                st.warning("🔎 **NO DETECTION:** No mushrooms identified. Check lighting or camera focus.")

            st.markdown("---")
            st.subheader("🖥️ Vision Analysis")
            # Visual confirmation for the user
            res_plotted = results.plot()
            st.image(res_plotted, use_container_width=True)
            
            # --- LOGGING TO DATABASE ---
            ts_now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            fname = source.name if hasattr(source, 'name') else "Capture"

            if st.session_state.last_processed_file != fname:
                conn_log = get_db_connection()
                conn_log.execute("INSERT INTO ai_harvest_logs (timestamp, filename, young, ready, old, total_clusters) VALUES (?, ?, ?, ?, ?, ?)", 
                                 (ts_now, fname, c_young, c_ready, c_old, len(detections)))
                conn_log.commit()
                conn_log.close()
                st.session_state.last_processed_file = fname
                st.toast(f"Saved to Database: {fname}", icon="✅")

    with col_side:
        st.subheader("📋 Persistent Harvest Log")
        conn_view = get_db_connection()
        df_log = pd.read_sql("SELECT * FROM ai_harvest_logs ORDER BY timestamp DESC", conn_view)
        conn_view.close()
        
        if not df_log.empty:
            st.dataframe(df_log[["timestamp", "ready", "total_clusters"]].head(10), hide_index=True)
            st.write(f"**Total ready historically:** {df_log['ready'].sum()}")
            
            csv = df_log.to_csv(index=False).encode('utf-8')
            st.download_button(label="📥 Download Full Report", data=csv, 
                               file_name=f"harvest_history_{datetime.date.today()}.csv", 
                               mime="text/csv", use_container_width=True)
            
            if st.button("🗑️ Delete Database Records", use_container_width=True, type="primary"):
                conn_del = get_db_connection()
                conn_del.execute("DELETE FROM ai_harvest_logs")
                conn_del.commit()
                conn_del.close()
                st.session_state.last_processed_file = None
                st.rerun()
        else:
            st.info("No scans recorded yet. Use the scanner to start logging.")
            
    st.divider()
    st.caption("Teammate AI UI | YOLO11 | Maturity Model")