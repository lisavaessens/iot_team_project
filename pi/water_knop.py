#!/usr/bin/env python3
"""
PlantPal - STAP 10b: de knop "Water geven" voor het dashboard
=============================================================

Grafana kan zelf geen MQTT-berichten versturen. Daarom draait op de Pi dit
kleine webservertje (poort 8080). Klik je in Grafana op "Water geven",
dan opent de browser een adres zoals

    http://rpilv.local:8080/water?seconden=3

en dit script stuurt dan via MQTT het bericht
    topic   plantpal/cmd/water
    inhoud  {"seconden": 3}
naar de Pico. De Pico geeft water (tenzij het reservoir leeg is).

Adressen:
    /                      een pagina met knoppen (2, 3 en 5 seconden)
    /water?seconden=3      geeft 3 seconden water (1 tot 10 s toegestaan)

Instellingen (MQTT-login) komen uit config.env in dezelfde map.

Gebruik:
    cd ~/iot_team_project/pi
    ~/env/bin/python3 water_knop.py        (stoppen met Ctrl+C)
Later laat installeer_waterknop.sh dit automatisch op de achtergrond draaien.
"""

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import paho.mqtt.client as mqtt

POORT = 8080
TOPIC_WATER = "plantpal/cmd/water"
MIN_SECONDEN, MAX_SECONDEN = 1, 10      # zelfde grens als op de Pico


# ---------------------------------------------------------------------------
# 1. Instellingen uit config.env
# ---------------------------------------------------------------------------
CONFIG_BESTAND = Path(__file__).resolve().parent / "config.env"


def lees_config(pad):
    if not pad.exists():
        sys.exit(f"FOUT: {pad} bestaat niet.")
    instellingen = {}
    for regel in pad.read_text().splitlines():
        regel = regel.strip()
        if not regel or regel.startswith("#") or "=" not in regel:
            continue
        naam, waarde = regel.split("=", 1)
        instellingen[naam.strip()] = waarde.strip().strip('"').strip("'")
    return instellingen


cfg = lees_config(CONFIG_BESTAND)
MQTT_HOST = cfg.get("MQTT_HOST", "localhost")
MQTT_PORT = int(cfg.get("MQTT_PORT", "1883"))
MQTT_USER = cfg.get("MQTT_USER") or None
MQTT_PASS = cfg.get("MQTT_PASS") or None


# ---------------------------------------------------------------------------
# 2. MQTT-verbinding (blijft open; paho verbindt zelf opnieuw indien nodig)
# ---------------------------------------------------------------------------
client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="plantpal-waterknop")
if MQTT_USER:
    client.username_pw_set(MQTT_USER, MQTT_PASS)
client.reconnect_delay_set(min_delay=1, max_delay=30)
client.connect_async(MQTT_HOST, MQTT_PORT, keepalive=60)
client.loop_start()


# ---------------------------------------------------------------------------
# 3. De webpagina's
# ---------------------------------------------------------------------------
STIJL = """<style>
body{font-family:system-ui,sans-serif;background:#111217;color:#e6e6e6;
     display:flex;flex-direction:column;align-items:center;padding:24px}
a.knop{display:inline-block;margin:8px;padding:14px 22px;border-radius:8px;
     background:#1f60c4;color:#fff;text-decoration:none;font-size:18px}
p{font-size:18px}
</style>"""


def pagina(titel, inhoud):
    return (f"<!doctype html><html lang='nl'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{titel}</title>{STIJL}</head><body>{inhoud}</body></html>")


KNOPPEN = ("<p>Hoeveel water?</p>"
           "<div><a class='knop' href='/water?seconden=2'>2 s</a>"
           "<a class='knop' href='/water?seconden=3'>3 s</a>"
           "<a class='knop' href='/water?seconden=5'>5 s</a></div>")


class Afhandelaar(BaseHTTPRequestHandler):
    def stuur(self, code, html):
        data = html.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        adres = urlparse(self.path)
        if adres.path == "/":
            self.stuur(200, pagina("PlantPal water", "<h1>PlantPal</h1>" + KNOPPEN))
        elif adres.path == "/water":
            try:
                seconden = float(parse_qs(adres.query).get("seconden", ["3"])[0])
            except ValueError:
                seconden = 3
            seconden = max(MIN_SECONDEN, min(MAX_SECONDEN, seconden))
            if not client.is_connected():
                self.stuur(503, pagina("Fout", "<h1>Geen verbinding met MQTT</h1>"
                                       "<p>Draait Mosquitto? Probeer opnieuw.</p>"))
                return
            client.publish(TOPIC_WATER, json.dumps({"seconden": seconden}))
            print(f"Water gevraagd: {seconden} s", flush=True)
            self.stuur(200, pagina("Water verstuurd",
                f"<h1>Verstuurd: {seconden:g} s water</h1>"
                "<p>De Pico geeft water, tenzij het reservoir leeg is "
                "(kijk op het OLED of in Grafana).</p>"
                "<p>Je mag dit tabblad sluiten.</p>" + KNOPPEN))
        else:
            self.stuur(404, pagina("Niet gevonden", "<h1>Niet gevonden</h1>"))

    def log_message(self, formaat, *args):
        pass    # geen lijn per bezoek in het logboek


print(f"Waterknop luistert op poort {POORT}; MQTT naar {MQTT_HOST}:{MQTT_PORT}", flush=True)
server = ThreadingHTTPServer(("0.0.0.0", POORT), Afhandelaar)
try:
    server.serve_forever()
except KeyboardInterrupt:
    print("\nGestopt.")
finally:
    client.loop_stop()
    client.disconnect()
