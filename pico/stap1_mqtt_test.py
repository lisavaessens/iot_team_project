# =====================================================================
# PlantPal - STAP 1: de Pico praat met de Raspberry Pi via MQTT
#
# Wat doet dit programma?
#   1. De Pico maakt verbinding met de wifi.
#   2. De Pico maakt verbinding met de MQTT-broker (Mosquitto) op de Pi.
#   3. Elke 5 seconden stuurt de Pico een testbericht naar de Pi
#      op het topic  plantpal/test
#   4. De Pico luistert naar het topic  plantpal/cmd/led
#      Stuur je "aan" of "uit", dan gaat de LED op de Pico aan of uit.
#
# Zo test je de communicatie in BEIDE richtingen:
#   Pico -> Pi : metingen (later: bodemvocht, licht, ...)
#   Pi -> Pico : commando's (later: "geef water")
#
# Er hoeft nog NIETS op het breadboard te zitten.
#
# Nodige libraries in de map CIRCUITPY/lib:
#   - adafruit_minimqtt            (de hele map)
#   - adafruit_connection_manager.mpy
#   - adafruit_ticks.mpy
# =====================================================================

import os           # om de instellingen uit settings.toml te lezen
import time         # om te wachten (time.sleep) en de tijd te meten
import board        # de namen van de pinnen op de Pico (bv. board.LED)
import digitalio    # om een pin als uitgang (LED) of ingang (knop) te gebruiken
import wifi         # de wifi-chip van de Pico 2 W
import socketpool   # "netwerkkanalen" die MQTT nodig heeft om te praten
import adafruit_minimqtt.adafruit_minimqtt as MQTT   # de MQTT-library


# ---------------------------------------------------------------------
# 1. Instellingen ophalen uit settings.toml
# ---------------------------------------------------------------------
WIFI_SSID = os.getenv("CIRCUITPY_WIFI_SSID")
WIFI_PASS = os.getenv("CIRCUITPY_WIFI_PASSWORD")
BROKER = os.getenv("MQTT_BROKER")
PORT = int(os.getenv("MQTT_PORT", 1883))
# "or None": als de gebruiker leeg is (""), geven we None door = geen login
MQTT_USER = os.getenv("MQTT_USER") or None
MQTT_PASS = os.getenv("MQTT_PASS") or None

# De topics. Zo heten de "kanalen" waarop berichten verstuurd worden.
TOPIC_TEST = "plantpal/test"        # Pico -> Pi
TOPIC_LED = "plantpal/cmd/led"      # Pi -> Pico
TOPIC_STATUS = "plantpal/status"    # online/offline van de Pico

INTERVAL = 5   # om de hoeveel seconden de Pico een bericht stuurt


# ---------------------------------------------------------------------
# 2. De ingebouwde LED klaarzetten (het groene ledje op de Pico zelf)
# ---------------------------------------------------------------------
led = digitalio.DigitalInOut(board.LED)
led.direction = digitalio.Direction.OUTPUT
led.value = False   # False = uit, True = aan


# ---------------------------------------------------------------------
# 3. Verbinden met de wifi
# ---------------------------------------------------------------------
print()
print("Verbinden met wifi:", WIFI_SSID)
wifi.radio.connect(WIFI_SSID, WIFI_PASS)
print("Verbonden! IP-adres van de Pico:", wifi.radio.ipv4_address)

# Een "pool" met netwerkverbindingen. MiniMQTT gebruikt die om via wifi te praten.
pool = socketpool.SocketPool(wifi.radio)


# ---------------------------------------------------------------------
# 4. Functies die MiniMQTT automatisch oproept ("callbacks")
#    Je roept ze zelf nooit op: de library doet dat als er iets gebeurt.
# ---------------------------------------------------------------------
def bij_verbinden(client, userdata, flags, rc):
    # Wordt uitgevoerd zodra de verbinding met de broker gelukt is.
    print("Verbonden met de MQTT-broker op", BROKER)
    # Laat de Pi weten dat de Pico online is. retain=True: de broker onthoudt
    # dit bericht, zodat wie later inschrijft het ook meteen ziet.
    client.publish(TOPIC_STATUS, "online", retain=True)
    # Inschrijven op het commando-topic: vanaf nu ontvangt de Pico die berichten.
    client.subscribe(TOPIC_LED)
    print("Ik luister nu op", TOPIC_LED)


def bij_bericht(client, topic, bericht):
    # Wordt uitgevoerd telkens er een bericht binnenkomt op een topic
    # waarop de Pico ingeschreven is.
    print("Ontvangen op", topic, ":", bericht)
    if topic == TOPIC_LED:
        if bericht == "aan":
            led.value = True
            print("-> LED aan")
        elif bericht == "uit":
            led.value = False
            print("-> LED uit")
        else:
            print("-> onbekend commando, gebruik 'aan' of 'uit'")


# ---------------------------------------------------------------------
# 5. De MQTT-client aanmaken en verbinden
# ---------------------------------------------------------------------
client = MQTT.MQTT(
    broker=BROKER,
    port=PORT,
    username=MQTT_USER,
    password=MQTT_PASS,
    client_id="plantpal-pico",   # unieke naam van dit toestel bij de broker
    socket_pool=pool,
)

# "Last will": valt de Pico weg (stroom uit, wifi weg), dan publiceert
# de broker zelf "offline". Zo weet de Pi dat de Pico niet meer leeft.
client.will_set(TOPIC_STATUS, "offline", retain=True)

client.on_connect = bij_verbinden
client.on_message = bij_bericht

print("Verbinden met MQTT-broker", BROKER, "poort", PORT, "...")
client.connect()


# ---------------------------------------------------------------------
# 6. De hoofdlus: blijft eeuwig draaien
# ---------------------------------------------------------------------
teller = 0
laatst_verstuurd = time.monotonic()   # tijd in seconden sinds de Pico opstartte

while True:
    # loop() kijkt of er berichten binnengekomen zijn (en roept dan
    # bij_bericht op) en houdt de verbinding levend. Wacht max. 1 seconde.
    client.loop(timeout=1)

    # Elke INTERVAL seconden een testbericht sturen.
    # We gebruiken GEEN time.sleep(5): dan zou de Pico 5 seconden "doof" zijn
    # en commando's te laat zien.
    if time.monotonic() - laatst_verstuurd >= INTERVAL:
        teller += 1
        tekst = "hallo van de Pico, bericht " + str(teller)
        client.publish(TOPIC_TEST, tekst)
        print("Verstuurd op", TOPIC_TEST, ":", tekst)
        laatst_verstuurd = time.monotonic()
