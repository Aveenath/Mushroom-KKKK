import paho.mqtt.client as mqtt
import sqlite3
import json
import datetime

# ==========================================
# 🛑 FILL IN YOUR MQTT DETAILS BELOW 🛑
# ==========================================
BROKER_ADDRESS = "FILL_IN_BROKER_IP_OR_URL"  # e.g., "192.168.1.100" or "mqtt.didikhub.com"
PORT = 1883                                  # Usually 1883 for non-TLS, 8883 for TLS
TOPIC = "FILL_IN_TOPIC_HERE"                 # e.g., "farm/sensors/live"
USERNAME = ""                                # Leave empty if no username is required
PASSWORD = ""                                # Leave empty if no password is required
# ==========================================

DB_NAME = "mushroom_client.db"

def get_db_connection():
    return sqlite3.connect(DB_NAME)

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print(f"✅ Successfully connected to MQTT Broker: {BROKER_ADDRESS}")
        client.subscribe(TOPIC)
        print(f"📡 Subscribed to topic: {TOPIC}")
        print("⏳ Waiting for sensor data...")
    else:
        print(f"❌ Failed to connect, return code {rc}")

def on_message(client, userdata, msg):
    try:
        payload = msg.payload.decode("utf-8")
        print(f"[{datetime.datetime.now()}] 📩 Received Message: {payload}")
        
        # Parse the JSON payload from the sensor
        # Example expected JSON: {"temp": 28.5, "humidity": 80.2, "co2": 450}
        data = json.loads(payload)
        
        temp = data.get("temp")
        humidity = data.get("humidity", 0) # Fallback to 0 if not provided
        co2 = data.get("co2", 0)           # Fallback to 0 if not provided
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        if temp is not None:
            conn = get_db_connection()
            # We insert into 'sensors' table. 
            # Note: The original table format was: id, device, co2, temp, humidity, ts, ip, created
            # We will insert NULL or defaults for columns we don't have.
            conn.execute(
                "INSERT INTO sensors (device, co2, temp, humidity, ts, created) VALUES (?, ?, ?, ?, ?, ?)",
                ("MQTT_Sensor", co2, temp, humidity, ts, ts)
            )
            conn.commit()
            conn.close()
            print(f"💾 Saved to Database -> Temp: {temp}°C, Humidity: {humidity}%")
        else:
            print("⚠️ Ignoring message: No 'temp' key found in JSON.")
            
    except json.JSONDecodeError:
        print("⚠️ Error: Received message is not valid JSON.")
    except Exception as e:
        print(f"❌ Database/Processing Error: {e}")

if __name__ == "__main__":
    print("🚀 Starting Mushroom Farm OS - MQTT Background Listener...")
    
    if BROKER_ADDRESS == "FILL_IN_BROKER_IP_OR_URL":
        print("🛑 STOP: You must fill in the BROKER_ADDRESS, PORT, and TOPIC variables inside this script first!")
        exit()
        
    client = mqtt.Client()
    
    if USERNAME and PASSWORD:
        client.username_pw_set(USERNAME, PASSWORD)
        
    client.on_connect = on_connect
    client.on_message = on_message
    
    try:
        client.connect(BROKER_ADDRESS, PORT, 60)
        client.loop_forever()
    except Exception as e:
        print(f"❌ Critical Error: Could not connect to broker ({e})")
