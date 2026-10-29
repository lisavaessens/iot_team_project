#!/usr/bin/env python3
"""
PlantPal - STAP 3: brug van MQTT naar InfluxDB
==============================================

Wat doet dit script?
  1. Het luistert op de MQTT-broker (Mosquitto) naar het topic
     plantpal/sensors, waarop de Pico elke 5 seconden zijn metingen stuurt.
  2. Elk bericht is JSON, bv.
        {"licht": 412.5, "temperatuur": 21.8, "luchtdruk": 1031.2,
         "bodemvocht_ruw": 368, "bodemvocht": 9.7}
  3. Het zet die waarden om naar een "punt" voor InfluxDB en schrijft het
     in de bucket (standaard: plantpal_metingen).

Zo komt elke meting van de Pico automatisch in de database terecht,
waar Grafana en het AI-model ze later uitlezen.

Instellingen komen uit config.env (in dezelfde map), bv.:
    MQTT_PORT=1883
    MQTT_USER=plantpal
    MQTT_PASS=...
    INFLUX_URL=http://localhost:8086
    INFLUX_ORG=PlantPal
    INFLUX_BUCKET=plantpal_metingen
    INFLUX_TOKEN=...

Gebruik:
    cd ~/iot_team_project/pi
    source ~/env/bin/activate
    python3 mqtt_naar_influx.py          (stoppen met Ctrl+C)

Later laat installeer_bridge.sh dit script automatisch op de achtergrond
draaien, ook na een herstart van de Pi.
"""

import json
import os
import sys
from pathlib import Path

import paho.mqtt.client as mqtt
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS


# ---------------------------------------------------------------------------
# 1. Instellingen lezen uit config.env
# ---------------------------------------------------------------------------
MAP = Path(__file__).resolve().parent      # de map waarin dit script staat
CONFIG_BESTAND = MAP / "config.env"


def lees_config(pad):
    """Leest regels als NAAM=waarde uit config.env en geeft een dict terug.
    Lege regels en regels die met # beginnen, worden overgeslagen."""
    if not pad.exists():
        sys.exit(f"FOUT: {pad} bestaat niet. Voer eerst setup_pi.sh uit.")
    instellingen = {}
    for regel in pad.read_text().splitlines():
        regel = regel.strip()
        if not regel or regel.startswith("#") or "=" not in regel:
            continue
        naam, waarde = regel.split("=", 1)
        # aanhalingstekens rond de waarde weghalen: "abc" -> abc
        instellingen[naam.strip()] = waarde.strip().strip('"').strip("'")
    return instellingen


cfg = lees_config(CONFIG_BESTAND)

MQTT_HOST = cfg.get("MQTT_HOST", "localhost")   # de broker draait op deze Pi
MQTT_PORT = int(cfg.get("MQTT_PORT", "1883"))
MQTT_USER = cfg.get("MQTT_USER") or None
MQTT_PASS = cfg.get("MQTT_PASS") or None
MQTT_TOPIC = "plantpal/sensors"

INFLUX_URL = cfg.get("INFLUX_URL", "http://localhost:8086")
INFLUX_ORG = cfg.get("INFLUX_ORG", "PlantPal")
INFLUX_BUCKET = cfg.get("INFLUX_BUCKET", "plantpal_metingen")
# Token: eerst uit config.env, anders uit de omgevingsvariabele INFLUXDB_TOKEN
INFLUX_TOKEN = cfg.get("INFLUX_TOKEN") or os.environ.get("INFLUXDB_TOKEN", "")

MEASUREMENT = "plantpal"   # de "tabelnaam" in InfluxDB

if not INFLUX_TOKEN:
    sys.exit("FOUT: geen InfluxDB-token. Zet INFLUX_TOKEN=... in config.env.")


# ---------------------------------------------------------------------------
# 2. Verbinden met InfluxDB
# ---------------------------------------------------------------------------
influx = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
schrijver = influx.write_api(write_options=SYNCHRONOUS)

if influx.ping():
    print(f"InfluxDB bereikbaar op {INFLUX_URL} (org '{INFLUX_ORG}', bucket '{INFLUX_BUCKET}')")
else:
    print(f"LET OP: InfluxDB antwoordt niet op {INFLUX_URL}. Ik probeer het toch verder.")


def bericht_naar_punt(data):
    """Zet een dict met metingen om naar een InfluxDB-punt.
    Getallen worden altijd als kommagetal (float) opgeslagen: InfluxDB
    weigert een veld dat de ene keer 368 (int) en de andere keer 368.0
    (float) is."""
    punt = Point(MEASUREMENT).tag("bron", "pico")
    aantal = 0
    for naam, waarde in data.items():
        if isinstance(waarde, bool):            # True/False -> 1.0/0.0
            punt = punt.field(naam, float(waarde))
        elif isinstance(waarde, (int, float)):
            punt = punt.field(naam, float(waarde))
        elif isinstance(waarde, str):           # bv. later "stemming": "dorst"
            punt = punt.field(naam, waarde)
        else:
            continue                            # lijsten e.d. overslaan
        aantal += 1
    return punt, aantal


# ---------------------------------------------------------------------------
# 3. MQTT: functies die paho automatisch oproept
# ---------------------------------------------------------------------------
def bij_verbinden(client, userdata, flags, reason_code, properties):
    if reason_code.is_failure:
        print(f"MQTT: verbinden mislukt: {reason_code}")
        return
    print(f"MQTT: verbonden met {MQTT_HOST}:{MQTT_PORT}, ik luister op {MQTT_TOPIC}")
    # Inschrijven in bij_verbinden: na een herverbinding gebeurt het opnieuw.
    client.subscribe(MQTT_TOPIC)


def bij_verbreken(client, userdata, flags, reason_code, properties):
    print(f"MQTT: verbinding verbroken ({reason_code}), ik probeer opnieuw...")


def bij_bericht(client, userdata, msg):
    tekst = msg.payload.decode(errors="replace")
    try:
        data = json.loads(tekst)
    except ValueError:
        print(f"Overgeslagen (geen geldige JSON): {tekst}")
        return
    if not isinstance(data, dict):
        print(f"Overgeslagen (geen JSON-object): {tekst}")
        return

    punt, aantal = bericht_naar_punt(data)
    if aantal == 0:
        print(f"Overgeslagen (geen metingen): {tekst}")
        return

    try:
        schrijver.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=punt)
        print(f"Opgeslagen: {tekst}")
    except Exception as fout:
        # bv. InfluxDB staat uit of de token klopt niet: niet crashen
        print(f"FOUT bij schrijven naar InfluxDB: {fout}")


# ---------------------------------------------------------------------------
# 4. Starten
# ---------------------------------------------------------------------------
client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="plantpal-bridge")
if MQTT_USER:
    client.username_pw_set(MQTT_USER, MQTT_PASS)
client.on_connect = bij_verbinden
client.on_disconnect = bij_verbreken
client.on_message = bij_bericht

# Na een verbroken verbinding: na 1 s opnieuw proberen, oplopend tot max. 30 s
client.reconnect_delay_set(min_delay=1, max_delay=30)

print(f"MQTT: verbinden met {MQTT_HOST}:{MQTT_PORT} ...")
# connect_async + retry_first_connection: werkt ook als Mosquitto bij het
# opstarten van de Pi nog niet klaar is.
client.connect_async(MQTT_HOST, MQTT_PORT, keepalive=60)

try:
    client.loop_forever(retry_first_connection=True)
except KeyboardInterrupt:
    print("\nGestopt.")
finally:
    client.disconnect()
    influx.close()
