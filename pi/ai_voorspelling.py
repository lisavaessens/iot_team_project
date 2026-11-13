#!/usr/bin/env python3
"""
PlantPal - STAP 12: AI-voorspelling "wanneer heeft de plant dorst?"
===================================================================

Wat doet dit script?
  1. Het haalt de bodemvochtmetingen van de laatste uren uit InfluxDB.
  2. Het zoekt het moment waarop de plant voor het laatst water kreeg
     (een plotse stijging van het bodemvocht) en gebruikt alleen de
     metingen daarna: vanaf dan droogt de bodem gewoon uit.
  3. Een machine-learningmodel (lineaire regressie uit scikit-learn) leert
     uit die metingen hoe snel de bodem uitdroogt (procent per uur).
  4. Met dat model voorspelt het hoeveel uur het nog duurt voor het
     bodemvocht onder de droogtedrempel zakt (= de plant krijgt dorst).
  5. Het resultaat gaat
       - naar InfluxDB (measurement "plantpal", tag bron=ai) -> Grafana
       - via MQTT naar plantpal/ai (bv. voor het OLED van de Pico)

Dit herhaalt zich elke 5 minuten.

Gebruik:
    cd ~/iot_team_project/pi
    ~/env/bin/python3 ai_voorspelling.py            # elke 5 min (Ctrl+C = stop)
    ~/env/bin/python3 ai_voorspelling.py --eenmalig # 1 keer, toont alles
    ~/env/bin/python3 ai_voorspelling.py --demo     # test met nepgegevens,
                                                    # zonder InfluxDB/MQTT
"""

# Hier importeren we de modules die we nodig hebben.
import json                             # resultaat als JSON versturen/tonen
import random                           # ruis toevoegen aan de nepgegevens (demo)
import sys                              # opties zoals --demo lezen en stoppen bij fouten
import time                             # huidige tijd en wachten tussen voorspellingen
from datetime import datetime, timezone # tijd mooi tonen in de logboeken
from pathlib import Path                # om config.env te vinden

import numpy as np                      # rekenen met lijsten getallen (matrices)

# Hier proberen we scikit-learn te laden (de machine-learningbibliotheek).
# Lukt dat niet, dan rekenen we verderop zelf de rechte uit met numpy.
try:
    from sklearn.linear_model import LinearRegression
except ImportError:                      # zonder scikit-learn: zelfde wiskunde
    LinearRegression = None

# ---------------------------------------------------------------------------
# Instellingen
# ---------------------------------------------------------------------------
# Hier staan alle instellingen van het AI-model op één plek.
INTERVAL_S = 300              # elke 5 minuten opnieuw voorspellen
VENSTER_UUR = 24              # hoeveel uur geschiedenis we bekijken
MIN_METINGEN = 6              # minstens zoveel punten (van 5 min) nodig
MIN_TIJDSPANNE_UUR = 0.5      # en minstens een half uur aan metingen
WATER_SPRONG = 8              # stijging (procentpunt) = er kwam water bij
DREMPEL_STANDAARD = 30        # als de potmeter (drempel) ontbreekt of 0 is
GEEN_DROGING = -0.05          # trager dan dit (%/uur) = droogt niet echt op
TOPIC_AI = "plantpal/ai"      # MQTT-topic waarop we de voorspelling versturen


# ---------------------------------------------------------------------------
# 1. Het AI-gedeelte: leren hoe snel de bodem uitdroogt
# ---------------------------------------------------------------------------
# Hier zoeken we de laatste keer dat de plant water kreeg.
# Water geven zie je als een plotse sprong omhoog in het bodemvocht.
# Alleen de metingen ná die sprong zijn bruikbaar om het uitdrogen te leren.
def sinds_laatste_water(punten):
    """punten = lijst (tijd_in_s, bodemvocht). Geeft alleen de punten na de
    laatste plotse stijging terug (= na de laatste keer water geven)."""
    start = 0
    for i in range(1, len(punten)):
        # Is het bodemvocht t.o.v. de vorige meting minstens WATER_SPRONG gestegen?
        if punten[i][1] - punten[i - 1][1] >= WATER_SPRONG:
            start = i
    return punten[start:]


# Hier gebeurt het eigenlijke machine learning:
# we trainen een lineaire regressie (een rechte lijn door de metingen)
# en gebruiken die lijn om te voorspellen wanneer de drempel bereikt wordt.
def leer_en_voorspel(punten, drempel, nu=None):
    """Traint het model op (tijd -> bodemvocht) en voorspelt wanneer het
    bodemvocht de drempel bereikt. Geeft een dict met het resultaat."""
    nu = nu or time.time()
    # Metingen op tijd sorteren en alleen die van na de laatste watergift houden.
    punten = sinds_laatste_water(sorted(punten))
    # Te weinig metingen? Dan is een voorspelling niet betrouwbaar.
    if len(punten) < MIN_METINGEN:
        return {"status": "te_weinig_gegevens", "aantal_metingen": len(punten),
                "advies": "Nog te weinig metingen om te voorspellen"}
    # De metingen moeten ook over een lang genoeg tijdsvak verspreid zijn.
    tijdspanne = (punten[-1][0] - punten[0][0]) / 3600
    if tijdspanne < MIN_TIJDSPANNE_UUR:
        return {"status": "te_weinig_gegevens", "aantal_metingen": len(punten),
                "advies": "Nog te weinig metingen om te voorspellen"}

    # Hier maken we de trainingsgegevens voor het model.
    # X = tijd in uren t.o.v. nu (negatief = verleden), y = bodemvocht in %
    X = np.array([[(t - nu) / 3600] for t, _ in punten])
    y = np.array([v for _, v in punten])

    if LinearRegression is not None:
        # Hier trainen we het model met scikit-learn.
        model = LinearRegression().fit(X, y)
        snelheid = float(model.coef_[0])                 # % per uur
        huidig = float(model.predict([[0.0]])[0])        # % nu volgens model
        betrouwbaarheid = float(model.score(X, y))       # R2: 0..1
    else:
        # Zonder scikit-learn: dezelfde rechte lijn berekenen met numpy (polyfit),
        # en de betrouwbaarheid (R²) zelf uitrekenen.
        snelheid, huidig = np.polyfit(X[:, 0], y, 1)
        voorspeld = snelheid * X[:, 0] + huidig
        rest = ((y - voorspeld) ** 2).sum()
        totaal = ((y - y.mean()) ** 2).sum() or 1
        betrouwbaarheid = float(1 - rest / totaal)
        snelheid, huidig = float(snelheid), float(huidig)

    # Hier stoppen we de uitkomst van het model in een dictionary.
    resultaat = {"status": "ok", "aantal_metingen": len(punten),
                 "droogsnelheid": round(snelheid, 3),
                 "bodemvocht_model": round(huidig, 1),
                 "drempel": round(drempel, 1),
                 "betrouwbaarheid": round(max(0.0, betrouwbaarheid), 2)}

    # Hier bepalen we het advies.
    if huidig <= drempel:
        # Het bodemvocht zit nu al onder de drempel.
        resultaat.update(uren_tot_dorst=0.0, advies="Nu water geven")
    elif snelheid >= GEEN_DROGING:
        # De lijn daalt (bijna) niet: de bodem droogt niet op.
        resultaat.update(uren_tot_dorst=-1.0,
                         advies="Bodem droogt niet op: geen water nodig")
    else:
        # Hier rekenen we uit hoeveel uur het nog duurt tot de drempel:
        # (verschil tot de drempel) gedeeld door (daling per uur).
        uren = (drempel - huidig) / snelheid
        resultaat["uren_tot_dorst"] = round(uren, 1)
        if uren < 1:
            resultaat["advies"] = "Binnen het uur dorst"
        elif uren < 24:
            resultaat["advies"] = f"Dorst over ongeveer {uren:.0f} uur"
        else:
            resultaat["advies"] = f"Dorst over ongeveer {uren / 24:.1f} dagen"
    return resultaat


# ---------------------------------------------------------------------------
# 2. Demo: nepgegevens om te zien of het model werkt (geen Pi nodig)
# ---------------------------------------------------------------------------
# Hier maken we 12 uur aan nepmetingen (elke 5 minuten) waarvan we het juiste
# antwoord al kennen. Zo kunnen we controleren of het model goed rekent.
def demo():
    print("DEMO met nepgegevens: de bodem droogt 1,5 % per uur uit,")
    print("na water geven 6 uur geleden (van 35 % naar 70 %).\n")
    nu = time.time()
    punten = []
    for i in range(12 * 12, 0, -1):                     # 12 uur, elke 5 min
        t = nu - i * 300
        uren_geleden = i * 300 / 3600
        if uren_geleden > 6:
            v = 35 + (uren_geleden - 6) * -1.5 + 20     # oude, droge periode
        else:
            v = 70 - (6 - uren_geleden) * 1.5           # na het water geven
        # Een beetje willekeurige ruis toevoegen, zoals bij een echte sensor.
        punten.append((t, v + random.uniform(-0.8, 0.8)))
    resultaat = leer_en_voorspel(punten, drempel=40, nu=nu)
    print(json.dumps(resultaat, indent=2, ensure_ascii=False))
    print("\nVerwacht: droogsnelheid ongeveer -1.5 en dorst over ongeveer 14 uur"
          "\n(nu ongeveer 61 %, drempel 40 %: 21 / 1,5 = 14 uur).")


# ---------------------------------------------------------------------------
# 3. Verbinding met InfluxDB en MQTT
# ---------------------------------------------------------------------------
# Hier lezen we config.env in (regels NAAM=waarde), net zoals in de andere scripts.
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


# Hier maken we de verbindingen met InfluxDB (lezen + schrijven) en met MQTT.
# De imports staan hier binnenin, zodat --demo ook werkt zonder die bibliotheken.
def start_verbindingen():
    import paho.mqtt.client as mqtt
    from influxdb_client import InfluxDBClient
    from influxdb_client.client.write_api import SYNCHRONOUS

    cfg = lees_config(Path(__file__).resolve().parent / "config.env")
    # Verbinding met InfluxDB.
    influx = InfluxDBClient(url=cfg.get("INFLUX_URL", "http://localhost:8086"),
                            token=cfg.get("INFLUX_TOKEN", ""),
                            org=cfg.get("INFLUX_ORG", "PlantPal"))
    # Verbinding met de MQTT-broker (draait op de achtergrond).
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="plantpal-ai")
    if cfg.get("MQTT_USER"):
        client.username_pw_set(cfg["MQTT_USER"], cfg.get("MQTT_PASS"))
    client.connect_async(cfg.get("MQTT_HOST", "localhost"), int(cfg.get("MQTT_PORT", "1883")))
    client.loop_start()
    return cfg, influx, influx.query_api(), influx.write_api(write_options=SYNCHRONOUS), client


# Hier halen we de metingen van de laatste 24 uur uit InfluxDB met een Flux-query:
#   - alleen metingen van de Pico (bron = pico),
#   - alleen de velden bodemvocht en drempel (potmeter),
#   - gemiddeld per 5 minuten, zodat we niet te veel punten hebben.
def haal_metingen(query_api, cfg):
    bucket, org = cfg.get("INFLUX_BUCKET", "plantpal_metingen"), cfg.get("INFLUX_ORG", "PlantPal")
    flux = f'''
from(bucket: "{bucket}")
  |> range(start: -{VENSTER_UUR}h)
  |> filter(fn: (r) => r._measurement == "plantpal" and r.bron == "pico")
  |> filter(fn: (r) => r._field == "bodemvocht" or r._field == "drempel")
  |> aggregateWindow(every: 5m, fn: mean, createEmpty: false)'''
    vocht, drempels = [], []
    # Hier lopen we door het antwoord van InfluxDB en verdelen we de waarden
    # over twee lijsten: bodemvocht en drempel, telkens als (tijd, waarde).
    for tabel in query_api.query(flux, org=org):
        for r in tabel.records:
            if r.get_value() is None:
                continue
            if r.get_field() == "bodemvocht":
                vocht.append((r.get_time().timestamp(), float(r.get_value())))
            else:
                drempels.append((r.get_time().timestamp(), float(r.get_value())))
    drempel = max(drempels)[1] if drempels else 0       # meest recente drempel
    # Geen (bruikbare) drempel gevonden? Dan nemen we de standaardwaarde.
    if drempel <= 0:
        drempel = DREMPEL_STANDAARD
    return vocht, drempel


# Hier bewaren we de voorspelling:
#   - in InfluxDB met tag bron=ai (zodat Grafana ze kan tonen),
#   - via MQTT op plantpal/ai (retain=True: nieuwe abonnees krijgen meteen het laatste advies).
def bewaar(resultaat, schrijver, client, cfg):
    from influxdb_client import Point
    punt = Point("plantpal").tag("bron", "ai")
    for naam in ("uren_tot_dorst", "droogsnelheid", "betrouwbaarheid", "drempel"):
        if naam in resultaat:
            punt = punt.field(naam, float(resultaat[naam]))
    punt = punt.field("advies", resultaat["advies"])
    schrijver.write(bucket=cfg.get("INFLUX_BUCKET", "plantpal_metingen"),
                    org=cfg.get("INFLUX_ORG", "PlantPal"), record=punt)
    client.publish(TOPIC_AI, json.dumps(resultaat, ensure_ascii=False), retain=True)


# ---------------------------------------------------------------------------
# 4. Hoofdprogramma
# ---------------------------------------------------------------------------
# Hier begint het programma. Afhankelijk van de optie doen we een demo,
# één voorspelling (--eenmalig) of elke 5 minuten een voorspelling.
def main():
    if "--demo" in sys.argv:
        demo()
        return
    eenmalig = "--eenmalig" in sys.argv
    cfg, influx, query_api, schrijver, client = start_verbindingen()
    print("PlantPal AI gestart" + (" (eenmalig)" if eenmalig else
          f", voorspelt elke {INTERVAL_S // 60} minuten"), flush=True)
    try:
        # De hoofdlus: metingen ophalen -> model trainen -> resultaat bewaren -> wachten.
        while True:
            try:
                vocht, drempel = haal_metingen(query_api, cfg)
                resultaat = leer_en_voorspel(vocht, drempel)
                bewaar(resultaat, schrijver, client, cfg)
                tijd = datetime.now(timezone.utc).astimezone().strftime("%H:%M")
                print(f"[{tijd}] {resultaat['advies']}  ->  "
                      f"{json.dumps(resultaat, ensure_ascii=False)}", flush=True)
            except Exception as fout:                   # niet crashen
                print(f"FOUT: {fout}", flush=True)
            if eenmalig:
                time.sleep(1)                           # MQTT-bericht laten vertrekken
                break
            time.sleep(INTERVAL_S)                      # 5 minuten wachten
    except KeyboardInterrupt:
        print("\nGestopt.")
    finally:
        # Bij het stoppen: verbindingen netjes afsluiten.
        client.loop_stop()
        influx.close()


# Dit zorgt dat main() alleen loopt als je het script zelf start
# (en niet als een ander script het importeert).
if __name__ == "__main__":
    main()
