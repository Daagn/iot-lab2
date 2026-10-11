import json
import os
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv
from influxdb_client import InfluxDBClient

load_dotenv(Path(__file__).with_name(".env"))

st.set_page_config(
    page_title="IoT Sensor Monitoring",
    page_icon="📊",
    layout="wide"
)

INFLUX_URL = os.getenv("INFLUX_URL", "http://localhost:8086")
INFLUX_ORG = os.getenv("INFLUX_ORG")
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN")
RAW_BUCKET = os.getenv("INFLUX_BUCKET_RAW", "iot_raw")
PROCESSED_BUCKET = os.getenv(
    "INFLUX_BUCKET_PROCESSED",
    "iot_processed"
)

if not INFLUX_ORG or not INFLUX_TOKEN:
    st.error("Thiếu INFLUX_ORG hoặc INFLUX_TOKEN trong .env.")
    st.stop()

st.title("IoT Sensor Monitoring")
st.caption("ESP32 → MQTT → Python → InfluxDB → Streamlit")

minutes = st.sidebar.selectbox(
    "Khoảng thời gian gần nhất",
    [15, 30, 60, 180],
    index=1
)

auto_refresh = st.sidebar.checkbox(
    "Tự động làm mới mỗi 5 giây",
    value=True
)

st.sidebar.button("Làm mới ngay")

st.sidebar.info(
    "Dữ liệu gốc cập nhật khi collector đang chạy. "
    "Dữ liệu xử lý chỉ có đợt mới khi bạn chạy preprocess.py."
)


def read_data(client, bucket, measurement, start, stop):
    query = f'''
from(bucket: {json.dumps(bucket)})
  |> range(
    start: time(v: "{start.isoformat()}"),
    stop: time(v: "{stop.isoformat()}")
  )
  |> filter(fn: (r) =>
    r._measurement == {json.dumps(measurement)}
  )
  |> pivot(
    rowKey: ["_time"],
    columnKey: ["_field"],
    valueColumn: "_value"
  )
'''

    data = client.query_api().query_data_frame(query)

    if isinstance(data, list):
        frames = [frame for frame in data if not frame.empty]
        data = (
            pd.concat(frames, ignore_index=True)
            if frames
            else pd.DataFrame()
        )

    if not data.empty:
        data["_time"] = pd.to_datetime(data["_time"], utc=True)
        data = data.sort_values("_time")

    return data


def draw_chart(raw, processed, field, title, unit):
    figure = go.Figure()

    if field in raw.columns:
        figure.add_trace(go.Scatter(
            x=raw["_time"].dt.tz_convert("Asia/Ho_Chi_Minh"),
            y=raw[field],
            mode="lines+markers",
            name="Dữ liệu gốc",
            connectgaps=False
        ))

    if not processed.empty and field in processed.columns:
        figure.add_trace(go.Scatter(
            x=processed["_time"].dt.tz_convert("Asia/Ho_Chi_Minh"),
            y=processed[field],
            mode="lines+markers",
            name="Sau xử lý",
            connectgaps=False
        ))

    rolling = f"{field}_rolling_mean"

    if not processed.empty and rolling in processed.columns:
        figure.add_trace(go.Scatter(
            x=processed["_time"].dt.tz_convert("Asia/Ho_Chi_Minh"),
            y=processed[rolling],
            mode="lines",
            name="Trung bình trượt 1 phút",
            connectgaps=False
        ))

    figure.update_layout(
        title=title,
        xaxis_title="Thời gian Việt Nam",
        yaxis_title=unit,
        hovermode="x unified",
        legend=dict(orientation="h", y=1.12),
        height=400
    )

    st.plotly_chart(figure, use_container_width=True)


def display_table(frame):
    table = frame.drop(
        columns=["result", "table", "_start", "_stop", "_measurement"],
        errors="ignore"
    ).copy()

    table["_time"] = table["_time"].dt.tz_convert(
        "Asia/Ho_Chi_Minh"
    )

    st.dataframe(
        table.sort_values("_time", ascending=False),
        use_container_width=True,
        hide_index=True
    )


@st.fragment(run_every="5s" if auto_refresh else None)
def dashboard():
    stop = pd.Timestamp.now(tz="UTC")
    start = stop - pd.Timedelta(minutes=minutes)

    try:
        with InfluxDBClient(
            url=INFLUX_URL,
            token=INFLUX_TOKEN,
            org=INFLUX_ORG,
            timeout=30000
        ) as client:
            raw = read_data(
                client, RAW_BUCKET, "sensor_raw", start, stop
            )
            processed = read_data(
                client, PROCESSED_BUCKET, "sensor_processed", start, stop
            )
    except Exception as error:
        st.error(f"Không đọc được InfluxDB: {error}")
        return

    if raw.empty:
        st.warning(
            "Không có dữ liệu gốc trong khoảng thời gian đã chọn. "
            "Kiểm tra Wokwi, collector.py hoặc chọn khoảng rộng hơn."
        )
        return

    device = st.selectbox(
        "Thiết bị",
        sorted(raw["device_id"].dropna().unique()),
        key="device"
    )

    raw = raw[raw["device_id"] == device].copy()

    selected = pd.DataFrame()

    if not processed.empty:
        processed = processed[
            processed["device_id"] == device
        ].copy()

    if not processed.empty and "run_id" in processed.columns:
        runs = sorted(
            processed["run_id"].dropna().unique(),
            reverse=True
        )

        choice = st.selectbox(
            "Đợt xử lý",
            ["Tự chọn đợt mới nhất"] + runs,
            key="processing_run"
        )

        run_id = runs[0] if choice == "Tự chọn đợt mới nhất" else choice

        selected = processed[
            processed["run_id"] == run_id
        ].copy()

        st.caption(f"RUN_ID đang hiển thị: {run_id}")
    else:
        st.info(
            "Chưa có dữ liệu xử lý trong khoảng này. "
            "Chạy python preprocess.py ở terminal khác."
        )

    latest = raw.iloc[-1]
    age_seconds = (
        pd.Timestamp.now(tz="UTC") - latest["_time"]
    ).total_seconds()

    col1, col2, col3, col4 = st.columns(4)

    col1.metric("Nhiệt độ mới nhất", f"{latest['temperature']:.2f} °C")
    col2.metric("Độ ẩm mới nhất", f"{latest['humidity']:.2f} %RH")
    col3.metric("Số mẫu gốc trong khoảng", len(raw))
    col4.metric("Tuổi mẫu mới nhất", f"{age_seconds:.1f} s")

    if age_seconds > 30:
        st.warning("Mẫu mới nhất đã hơn 30 giây. Kiểm tra luồng thu thập.")
    elif age_seconds < 0:
        st.warning("Thời gian thiết bị đang đi trước máy chủ.")

    draw_chart(raw, selected, "temperature", "Nhiệt độ", "°C")
    draw_chart(raw, selected, "humidity", "Độ ẩm", "%RH")

    st.subheader("Độ trễ từ thiết bị đến collector")

    if "latency_ms" in raw.columns:
        latency = pd.to_numeric(raw["latency_ms"], errors="coerce")
        valid_latency = latency.dropna()

        if not valid_latency.empty:
            col1, col2, col3 = st.columns(3)
            col1.metric("Trung bình", f"{valid_latency.mean():.1f} ms")
            col2.metric("P95", f"{valid_latency.quantile(0.95):.1f} ms")
            col3.metric("Lớn nhất", f"{valid_latency.max():.1f} ms")

            draw_chart(
                raw, pd.DataFrame(), "latency_ms",
                "Độ trễ nhận bản tin", "ms"
            )

            if (valid_latency < 0).any():
                st.warning(
                    "Có độ trễ âm. Kiểm tra đồng bộ thời gian "
                    "trước khi đánh giá thống kê độ trễ."
                )

    st.caption(
        "latency_ms = thời gian collector nhận − timestamp thiết bị. "
        "Chỉ số này chưa bao gồm thời gian ghi database "
        "và thời gian trình duyệt hiển thị."
    )

    if not selected.empty:
        st.subheader("Chất lượng dữ liệu sau xử lý")
        col1, col2, col3 = st.columns(3)

        col1.metric(
            "Mẫu có ngoại lệ",
            int(selected["outlier_count"].fillna(0).sum())
        )
        col2.metric(
            "Ô 10 giây được nội suy",
            int(selected["is_imputed"].eq(True).sum())
        )
        col3.metric(
            "Ô 10 giây còn thiếu dữ liệu",
            int(selected["is_valid"].eq(False).sum())
        )

    with st.expander("Bảng dữ liệu gốc"):
        display_table(raw)

    if not selected.empty:
        with st.expander("Bảng dữ liệu sau xử lý"):
            display_table(selected)

    refreshed = pd.Timestamp.now(tz="Asia/Ho_Chi_Minh")
    st.caption(
        f"Lần đọc dữ liệu gần nhất: {refreshed:%d/%m/%Y %H:%M:%S}"
    )


dashboard()