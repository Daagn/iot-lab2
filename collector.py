import json
import math
import os
import time
from collections import OrderedDict
from pathlib import Path

import paho.mqtt.client as mqtt
from dotenv import load_dotenv
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

load_dotenv(Path(__file__).with_name(".env"))

MQTT_HOST = os.getenv("MQTT_HOST", "broker.hivemq.com")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_TOPIC = os.environ["MQTT_TOPIC"]

INFLUX_URL = os.getenv("INFLUX_URL", "http://localhost:8086")
INFLUX_ORG = os.environ["INFLUX_ORG"]
INFLUX_TOKEN = os.environ["INFLUX_TOKEN"]
INFLUX_BUCKET_RAW = os.getenv("INFLUX_BUCKET_RAW", "iot_raw")

influx = InfluxDBClient(
    url=INFLUX_URL,
    token=INFLUX_TOKEN,
    org=INFLUX_ORG,
    timeout=10000
)

write_api = influx.write_api(write_options=SYNCHRONOUS)

seen = OrderedDict()
last_sequences = {}
saved_count = 0

def validate(data, received_at_ms):
    if not isinstance(data, dict):
        raise ValueError("JSON phai la object")

    required = [
        "device_id",
        "boot_id",
        "sequence",
        "timestamp_ms",
        "temperature",
        "humidity"
    ]

    missing = [key for key in required if key not in data]

    if missing:
        raise ValueError(f"Thieu truong: {', '.join(missing)}")

    for key in ["device_id", "boot_id"]:
        if not isinstance(data[key], str) or not data[key].strip():
            raise ValueError(f"{key} phai la chuoi khong rong")

    for key in ["sequence", "timestamp_ms"]:
        if type(data[key]) is not int or data[key] < 0:
            raise ValueError(f"{key} phai la so nguyen khong am")

    for key in ["temperature", "humidity"]:
        value = data[key]

        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError(f"{key} phai la so huu han")

    if not -40 <= data["temperature"] <= 80:
        raise ValueError("Nhiet do ngoai khoang -40 den 80")

    if not 0 <= data["humidity"] <= 100:
        raise ValueError("Do am ngoai khoang 0 den 100")

    oldest_allowed = received_at_ms - 7 * 24 * 60 * 60 * 1000
    newest_allowed = received_at_ms + 60_000

    if not oldest_allowed <= data["timestamp_ms"] <= newest_allowed:
        raise ValueError("Timestamp qua cu hoac lech ve tuong lai")

def on_connect(client, userdata, flags, reason_code, properties):
    print(f"MQTT connect: {reason_code}", flush=True)

    if reason_code == 0:
        client.subscribe(MQTT_TOPIC)
        print(f"Subscribed: {MQTT_TOPIC}", flush=True)

def on_message(client, userdata, message):
    global saved_count

    received_at_ms = time.time_ns() // 1_000_000

    try:
        data = json.loads(message.payload.decode("utf-8"))
        validate(data, received_at_ms)
    except (UnicodeDecodeError, ValueError) as error:
        print(f"INVALID: {error}", flush=True)
        return

    key = (
        data["device_id"],
        data["boot_id"],
        data["sequence"]
    )

    if key in seen:
        print(f"DUPLICATE: {key}", flush=True)
        return

    latency_ms = received_at_ms - data["timestamp_ms"]

    point = (
        Point("sensor_raw")
        .tag("device_id", data["device_id"])
        .field("boot_id", data["boot_id"])
        .field("sequence", data["sequence"])
        .field("temperature", float(data["temperature"]))
        .field("humidity", float(data["humidity"]))
        .field("received_at_ms", received_at_ms)
        .field("latency_ms", latency_ms)
        .time(data["timestamp_ms"], WritePrecision.MS)
    )

    started = time.perf_counter()

    try:
        write_api.write(
            bucket=INFLUX_BUCKET_RAW,
            org=INFLUX_ORG,
            record=point
        )
    except Exception as error:
        print(f"WRITE FAILED: sequence={data['sequence']} | {error}", flush=True)
        return

    write_duration_ms = (time.perf_counter() - started) * 1000
    saved_count += 1

    seen[key] = True

    if len(seen) > 10000:
        seen.popitem(last=False)

    session = (data["device_id"], data["boot_id"])
    previous = last_sequences.get(session)

    if previous is not None and data["sequence"] > previous + 1:
        gap = data["sequence"] - previous - 1
        print(f"SEQUENCE GAP: {gap} ban tin chua thay luu", flush=True)

    if previous is None or data["sequence"] > previous:
        last_sequences[session] = data["sequence"]

    print(
        f"SAVED #{saved_count} | "
        f"seq={data['sequence']} | "
        f"T={data['temperature']:.2f} C | "
        f"H={data['humidity']:.2f} % | "
        f"receive_latency={latency_ms} ms | "
        f"db_write={write_duration_ms:.1f} ms",
        flush=True
    )

    if latency_ms < 0:
        print("CLOCK WARNING: kiem tra dong bo thoi gian", flush=True)

mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message
mqtt_client.reconnect_delay_set(min_delay=1, max_delay=30)

try:
    if not influx.ping():
        raise RuntimeError("InfluxDB chua san sang")

    print("InfluxDB reachable", flush=True)
    mqtt_client.connect(MQTT_HOST, MQTT_PORT, 60)
    mqtt_client.loop_forever()
except KeyboardInterrupt:
    print("\nStopped collector", flush=True)
finally:
    mqtt_client.disconnect()
    write_api.close()
    influx.close()