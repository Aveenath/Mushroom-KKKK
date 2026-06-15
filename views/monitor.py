import streamlit as st
import pandas as pd
import datetime
import requests
import plotly.express as px
from analysis import get_predictions_multi
from utils import get_db_connection, get_local_now, db_read_sql
from sync_live_data import fetch_live_data, sync_to_db


@st.cache_data(ttl=60)
def sync_live_sensor_data():
    df = fetch_live_data()
    return sync_to_db(df)


def show():
    st.title("📊 Monitoring & AI Forecasting")

    try:
        new_count = sync_live_sensor_data()
        if new_count > 0:
            st.toast(f"🔄 Synced {new_count} new reading(s) from SmartSense", icon="✅")
    except Exception:
        st.caption("⚠️ Could not reach SmartSense live data — showing last saved readings.")

    conn = get_db_connection()
    df = db_read_sql("""
    SELECT ts, temp, humidity, co2 FROM (
        SELECT ts, temp, humidity, co2 FROM sensors ORDER BY ts DESC LIMIT 40000
    ) sub ORDER BY ts ASC
    """, conn)
    conn.close()

    st.subheader("🏠 Internal Farm Sensors")
    col1, col2, col3 = st.columns(3)
    latest = df.iloc[-1]

    if latest['temp'] >= 29:
        st.error(f"🚨 **CRITICAL ALERT:** Internal temperature is {latest['temp']}°C (Exceeds 29°C)! Activate cooling systems immediately.")
    if latest['humidity'] <= 75:
        st.warning(f"⚠️ **WARNING:** Internal humidity has dropped to {latest['humidity']}% (Below 75%). Misting recommended.")

    temp_status = "🟢 Normal" if 24 <= latest['temp'] <= 28 else ("🟡 Warning" if latest['temp'] < 30 else "🔴 Critical")
    hum_status  = "🟢 Normal" if 80 <= latest['humidity'] <= 90 else ("🟡 Warning" if latest['humidity'] >= 75 else "🔴 Critical")
    co2_status  = "🟢 Normal" if latest['co2'] < 1000 else ("🟡 Warning" if latest['co2'] < 1500 else "🔴 Critical")

    col1.metric("Internal Temp",     f"{latest['temp']}°C",      temp_status)
    col2.metric("Internal Humidity", f"{latest['humidity']}%",   hum_status)
    col3.metric("Internal CO2",      f"{latest['co2']} ppm",     co2_status)

    st.markdown("---")
    st.subheader("🌦️ Outside Weather (Live & Forecast)")
    st.markdown("**Farm Location:** Perak (Kuala Kangsar), MY")

    lat, lon = 4.7730, 100.9410
    try:
        weather_url = (
            f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
            f"&current=temperature_2m,relative_humidity_2m&hourly=temperature_2m"
            f"&timezone=Asia%2FSingapore&forecast_days=2"
        )
        res = requests.get(weather_url, headers={'User-Agent': 'MushroomFarm-App/1.0'}, timeout=5).json()

        curr_temp = res['current']['temperature_2m']
        curr_hum  = res['current']['relative_humidity_2m']

        w_col1, w_col2 = st.columns(2)
        w_col1.metric("Outside Temp",     f"{curr_temp}°C")
        w_col2.metric("Outside Humidity", f"{curr_hum}%")

        df_weather = pd.DataFrame({'Time': res['hourly']['time'], 'Outside Temp (°C)': res['hourly']['temperature_2m']})
        df_weather['Time'] = pd.to_datetime(df_weather['Time'])
        current_hour = get_local_now().replace(minute=0, second=0, microsecond=0)
        df_weather = df_weather[df_weather['Time'] >= current_hour].head(24)

        max_pred = df_weather['Outside Temp (°C)'].max()
        if max_pred >= 33:
            st.warning(f"⚠️ **OUTSIDE HEATWAVE ALERT:** Forecast predicts peak outside temperatures hitting {max_pred:.1f}°C in the next 24 hours.")

        fig_weather = px.line(df_weather, x='Time', y='Outside Temp (°C)',
                              title="24-Hour Outside Weather Prediction (Kuala Kangsar)",
                              line_shape='spline', render_mode='svg')
        fig_weather.update_traces(line_color='#00BFFF')
        st.plotly_chart(fig_weather, use_container_width=True)

    except Exception:
        st.warning("⚠️ **Network Blocked:** Cannot connect to the outside weather satellite right now.")

    st.markdown("---")
    st.subheader("🔮 7-Day Predictive Forecast")
    st.write("Uses pre-trained AI models to forecast Temperature, Humidity, and CO2 for the next 7 days, based on live synced sensor data.")
    st.caption(f"📡 Using {len(df)} live readings — latest: {df['ts'].iloc[-1]}")
    uploaded_csv = st.file_uploader("📂 Upload Sensor CSV (Optional — overrides live data)", type=['csv'])

    if st.button("🔄 Run AI Forecast", type="primary"):
        with st.spinner('Loading AI models and generating forecast...'):
            df_forecast = df.copy()

            if uploaded_csv is not None:
                try:
                    df_raw = pd.read_csv(uploaded_csv)
                    col_lower = {c: str(c).lower() for c in df_raw.columns}
                    ts_col   = next((c for c, l in col_lower.items() if 'timestamp' in l or 'ts' == l), None)
                    temp_col = next((c for c, l in col_lower.items() if 'temp' in l), None)
                    hum_col  = next((c for c, l in col_lower.items() if 'rh' in l or 'humid' in l), None)
                    co2_col  = next((c for c, l in col_lower.items() if 'co2' in l), None)

                    if ts_col and temp_col:
                        keep = {ts_col: 'ts', temp_col: 'temp'}
                        if hum_col: keep[hum_col] = 'humidity'
                        if co2_col: keep[co2_col] = 'co2'
                        df_forecast = df_raw[list(keep.keys())].rename(columns=keep)
                        df_forecast['temp'] = pd.to_numeric(df_forecast['temp'], errors='coerce')
                        df_forecast = df_forecast.dropna(subset=['temp'])
                        st.success("Successfully loaded data from uploaded CSV!")
                    else:
                        st.error("Uploaded CSV must contain Timestamp and Temperature columns.")
                        df_forecast = None
                except Exception as e:
                    st.error(f"Error reading CSV: {e}")
                    df_forecast = None

            if df_forecast is not None and not df_forecast.empty:
                try:
                    multi = get_predictions_multi(df_forecast)
                    future_times = [get_local_now() + datetime.timedelta(hours=i) for i in range(1, 169)]

                    # --- Metrics row ---
                    labels = {'temp': '🌡️ Temperature', 'humidity': '💧 Humidity', 'co2': '🌿 CO2'}
                    units  = {'temp': '°C',             'humidity': '%',            'co2': 'ppm'}
                    metric_cols = st.columns(len(multi))
                    for i, (target, data) in enumerate(multi.items()):
                        with metric_cols[i]:
                            st.metric(f"{labels[target]} R²", f"{data['r2'] * 100:.1f}%")
                            st.metric(f"{labels[target]} MAE", f"±{data['mae']:.2f} {units[target]}")

                    # --- Charts in tabs ---
                    colors  = {'temp': '#FF4B4B', 'humidity': '#4B9EFF', 'co2': '#4BFF91'}
                    y_axis  = {
                        'temp':     'Predicted Temp (°C)',
                        'humidity': 'Predicted Humidity (%)',
                        'co2':      'Predicted CO2 (ppm)'
                    }
                    titles  = {
                        'temp':     'Temperature Forecast — Next 7 Days',
                        'humidity': 'Humidity Forecast — Next 7 Days',
                        'co2':      'CO2 Level Forecast — Next 7 Days'
                    }

                    tab_list = st.tabs([labels[t] for t in multi.keys()])
                    for tab, (target, data) in zip(tab_list, multi.items()):
                        with tab:
                            forecast_df = pd.DataFrame({
                                'Time': future_times,
                                y_axis[target]: data['predictions']
                            })
                            fig = px.line(forecast_df, x='Time', y=y_axis[target],
                                          title=titles[target],
                                          line_shape='spline', render_mode='svg')
                            fig.update_traces(line_color=colors[target])
                            st.plotly_chart(fig, use_container_width=True)

                except Exception as e:
                    st.error(f"Could not generate forecast: {e}")
            else:
                st.error("No data available to generate forecast.")
