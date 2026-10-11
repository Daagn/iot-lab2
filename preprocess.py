import json
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

load_dotenv(Path(__file__).with_name(".env"))

INFLUX_URL = os.getenv("INFLUX_URL", "http://localhost:8086")
INFLUX_ORG = os.environ["INFLUX_ORG"]
INFLUX_TOKEN = os.environ["INFLUX_TOKEN"]
RAW_BUCKET = os.getenv("INFLUX_BUCKET_RAW", "iot_raw")
PROCESSED_BUCKET = os.getenv(
    "INFLUX_BUCKET_PROCESSED",
    "iot_processed"
)

COLUMNS = ["temperature", "humidity"]
RUN_ID = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")

def detect_outliers(series):
    valid = series.dropna()
    mask = pd.Series(False, index=series.index)

    if len(valid) < 8:
        return mask

    q1 = valid.quantile(0.25)
    q3 = valid.quantile(0.75)
    iqr = q3 - q1

    if iqr <= 0:
        return mask

    lower = q1 - 1.5 * iqr
    upper = q3 + 1.5 * iqr

    return ((series < lower) | (series > upper)).fillna(False)

def fill_short_gaps(series, max_bins=2):
    missing = series.isna()
    groups = missing.ne(missing.shift()).cumsum()
    gap_sizes = missing.groupby(groups).transform("sum")

    candidate = series.interpolate(
        method="time",
        limit_area="inside"
    )

    can_fill = missing & (gap_sizes <= max_bins)
    return series.where(~can_fill, candidate)

def normalize(series):
    valid = series.dropna()

    if valid.empty:
        return series.copy()

    minimum = valid.min()
    maximum = valid.max()

    if maximum == minimum:
        return series.where(series.isna(), 0.0)

    return (series - minimum) / (maximum - minimum)

def process_device(frame):
    frame = frame.sort_values("_time").copy()

    frame = frame.drop_duplicates(
        subset=["device_id", "boot_id", "sequence"],
        keep="first"
    )

    frame = frame.set_index("_time")

    values = frame[COLUMNS].apply(
        pd.to_numeric,
        errors="coerce"
    )

    values = values.replace([np.inf, -np.inf], np.nan)

    flags = pd.DataFrame(index=values.index)

    for column in COLUMNS:
        flags[column] = detect_outliers(values[column])

    cleaned = values.mask(flags)

    result = cleaned.resample("10s").mean()

    result["sample_count"] = (
        values.resample("10s").size().astype(int)
    )

    result["outlier_count"] = (
        flags.any(axis=1)
        .astype(int)
        .resample("10s")
        .sum()
        .astype(int)
    )

    before_fill = result[COLUMNS].isna()

    for column in COLUMNS:
        result[column] = fill_short_gaps(result[column])

    filled = before_fill & result[COLUMNS].notna()

    result["is_imputed"] = filled.any(axis=1)
    result["is_valid"] = result[COLUMNS].notna().all(axis=1)

    for column in COLUMNS:
        result[f"{column}_rolling_mean"] = (
            result[column]
            .rolling(window=6, min_periods=6)
            .mean()
        )

        result[f"{column}_delta"] = result[column].diff()

        result[f"{column}_normalized"] = normalize(
            result[column]
        )

    return result, len(frame), int(flags.any(axis=1).sum())

def make_points(device_id, frame):
    points = []

    for timestamp, row in frame.iterrows():
        point = (
            Point("sensor_processed")
            .tag("device_id", str(device_id))
            .tag("run_id", RUN_ID)
            .field("sample_count", int(row["sample_count"]))
            .field("outlier_count", int(row["outlier_count"]))
            .field("is_imputed", bool(row["is_imputed"]))
            .field("is_valid", bool(row["is_valid"]))
            .time(timestamp.to_pydatetime(), WritePrecision.MS)
        )

        for column in COLUMNS:
            fields = [
                column,
                f"{column}_rolling_mean",
                f"{column}_delta",
                f"{column}_normalized"
            ]

            for field in fields:
                value = row[field]

                if pd.notna(value) and np.isfinite(value):
                    point.field(field, float(value))

        points.append(point)

    return points

def main():
    end = pd.Timestamp.now(tz="UTC").floor("10s")
    start = end - pd.Timedelta(hours=3)

    query = f'''
from(bucket: {json.dumps(RAW_BUCKET)})
  |> range(
    start: time(v: "{start.isoformat()}"),
    stop: time(v: "{end.isoformat()}")
  )
  |> filter(fn: (r) => r._measurement == "sensor_raw")
  |> pivot(
    rowKey: ["_time"],
    columnKey: ["_field"],
    valueColumn: "_value"
  )
'''

    with InfluxDBClient(
        url=INFLUX_URL,
        token=INFLUX_TOKEN,
        org=INFLUX_ORG,
        timeout=30000
    ) as client:
        data = client.query_api().query_data_frame(query)

        if isinstance(data, list):
            frames = [frame for frame in data if not frame.empty]
            data = (
                pd.concat(frames, ignore_index=True)
                if frames
                else pd.DataFrame()
            )

        if data.empty:
            print("Khong co du lieu trong 30 phut gan nhat.")
            return

        required = {
            "_time",
            "device_id",
            "boot_id",
            "sequence",
            "temperature",
            "humidity"
        }

        missing = required - set(data.columns)

        if missing:
            raise ValueError(f"Thieu cot trong database: {missing}")

        data["_time"] = pd.to_datetime(data["_time"], utc=True)

        print(f"RUN_ID: {RUN_ID}")
        print(f"Khoang doc: {start} -> {end}")

        total_points = 0

        with client.write_api(write_options=SYNCHRONOUS) as writer:
            for device_id, frame in data.groupby("device_id"):
                result, raw_count, outlier_count = process_device(frame)
                points = make_points(device_id, result)

                for offset in range(0, len(points), 500):
                    writer.write(
                        bucket=PROCESSED_BUCKET,
                        org=INFLUX_ORG,
                        record=points[offset:offset + 500]
                    )

                total_points += len(points)

                print(f"\nThiet bi: {device_id}")
                print(f"So mau goc sau loai trung: {raw_count}")
                print(f"So mau co ngoai le: {outlier_count}")
                print(f"So o 10 giay: {len(result)}")
                print(
                    "So o duoc noi suy:",
                    int(result["is_imputed"].sum())
                )
                print(
                    "So o van thieu du lieu:",
                    int((~result["is_valid"]).sum())
                )
                print(result.tail(5).to_string())

        print(f"\nDONE: da ghi {total_points} diem vao {PROCESSED_BUCKET}")
        print(f"RUN_ID: {RUN_ID}")

if __name__ == "__main__":
    main()