"""
PlantPal - schrijft 10 dummy metingen naar InfluxDB.

Gebruik:
    export INFLUXDB_TOKEN="iOOmjzwPBEflWMm6xQueYxPHK7LY-dFpGSE-D0fqObe4iPLahQ7SPyOvipi-f8klTRBNlmcjVidal3UM4AldsA=="
    python3 plantpal_dummy.py
"""
import os
from datetime import datetime, timedelta, timezone

from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

URL = "http://localhost:8086"
ORG = "PlantPal"
BUCKET = "plantpal_metingen"
TOKEN = (os.environ.get("INFLUXDB_TOKEN") or "").strip()

if not TOKEN:
    raise SystemExit("INFLUXDB_TOKEN is niet ingesteld. Doe eerst: export INFLUXDB_TOKEN='...'")

# (bodemvocht %, licht lux, temperatuur °C, afstand tot water cm)
DUMMY = [
    (62, 480, 21.5, 4.2),
    (60, 510, 21.7, 4.4),
    (58, 560, 22.0, 4.7),
    (55, 640, 22.4, 5.1),
    (52, 720, 22.9, 5.6),
    (49, 800, 23.3, 6.0),
    (45, 760, 23.6, 6.5),
    (41, 690, 23.4, 7.1),
    (38, 580, 23.0, 7.6),
    (78, 520, 22.6, 8.3),  # plant net water gegeven: bodem nat, reservoir lager
]

now = datetime.now(timezone.utc)

with InfluxDBClient(url=URL, token=TOKEN, org=ORG) as client:
    write_api = client.write_api(write_options=SYNCHRONOUS)

    for i, (soil, light, temp, dist) in enumerate(DUMMY):
        # elke meting 1 minuut na de vorige, de laatste is "nu"
        ts = now - timedelta(minutes=len(DUMMY) - 1 - i)

        point = (
            Point("plantpal")
            .tag("plant", "monstera")
            .field("soil_moisture", float(soil))
            .field("light", float(light))
            .field("temperature", float(temp))
            .field("water_distance_cm", float(dist))
            .time(ts, WritePrecision.S)
        )
        write_api.write(bucket=BUCKET, org=ORG, record=point)

print(f"{len(DUMMY)} metingen geschreven naar bucket '{BUCKET}'.")