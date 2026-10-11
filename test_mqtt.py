import json
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

MQTT_HOST = "broker.hivemq.com"
MQTT_PORT = 1883
MQTT_TOPIC = "ptit/iot2/B23DCCN140/dat-a73f/telemetry"

received_count = 0

def on_connect(client, userdata, flags, reason_code, properties):
    print(f"Ket noi MQTT: {reason_code}", flush=True)

    if reason_code == 0:
        client.subscribe(MQTT_TOPIC)
        print(f"Dang subscribe: {MQTT_TOPIC}", flush=True)
    else:
        print("Ket noi khong thanh cong", flush=True)

def on_subscribe(client, userdata, mid, reason_codes, properties):
    print(f"Broker phan hoi subscribe: {reason_codes}", flush=True)

def on_message(client, userdata, message):
    global received_count

    try:
        data = json.loads(message.payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        print(f"Du lieu khong phai JSON hop le: {error}", flush=True)
        return

    if not isinstance(data, dict):
        print("JSON phai la mot object", flush=True)
        return

    received_count += 1
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    print("\n" + "=" * 50, flush=True)
    print(f"Ban tin thu: {received_count}", flush=True)
    print(f"Thoi gian nhan UTC: {now}", flush=True)
    print(f"Topic: {message.topic}", flush=True)
    print(f"Thiet bi: {data.get('device_id')}", flush=True)
    print(f"Boot ID: {data.get('boot_id')}", flush=True)
    print(f"Sequence: {data.get('sequence')}", flush=True)
    print(f"Nhiet do: {data.get('temperature')} °C", flush=True)
    print(f"Do am: {data.get('humidity')} %", flush=True)
    print(json.dumps(data, ensure_ascii=False, indent=2), flush=True)

client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
client.on_connect = on_connect
client.on_subscribe = on_subscribe
client.on_message = on_message
client.reconnect_delay_set(min_delay=1, max_delay=30)

try:
    client.connect(MQTT_HOST, MQTT_PORT, 60)
    client.loop_forever()
except KeyboardInterrupt:
    print("\nDa dung chuong trinh", flush=True)
finally:
    client.disconnect()