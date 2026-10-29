# =====================================================================
# PlantPal - STAP 5: I2C-sensoren + MCP3008 (SPI) uitlezen en versturen
#
# Nieuw in stap 5:
#   - De MCP3008 (analoog-digitaal-omzetter) op de SPI-bus.
#     De Pico kan zelf maar beperkt analoge spanningen meten; de MCP3008
#     meet 8 kanalen en geeft een getal terug via SPI.
#       kanaal 0 (CH0): LDR (lichtgevoelige weerstand) -> "ldr" in %
#       kanaal 1 (CH1): potmeter (draaiknop)          -> "drempel" in %
#     "drempel" = de droogtedrempel: onder dit bodemvocht krijgt de plant
#     straks water (stap 9). Met de draaiknop stel je hem in.
#
# Uit stap 2:
#   - De Pico leest 3 sensoren op dezelfde I2C-bus:
#       BH1750   (adres 0x23)  -> licht in lux
#       BMP280   (adres 0x77 of 0x76) -> temperatuur en luchtdruk
#       Seesaw   (adres 0x36)  -> bodemvocht (Adafruit STEMMA soil sensor)
#   - En de ultrasone sensor HC-SR04 (GEEN I2C, maar digitale pinnen
#     trigger + echo) -> afstand tot het water = waterpeil reservoir
#   - Alle metingen gaan samen in EEN bericht (JSON) naar het topic
#     plantpal/sensors
#   - Een sensor die (nog) niet aangesloten is, wordt overgeslagen.
#     Je kunt dus testen met 1, 2 of 3 sensoren.
#
# Wat blijft hetzelfde?
#   - wifi en MQTT zoals in stap 1
#   - het commando plantpal/cmd/led ("aan"/"uit") voor de LED op de Pico
#
# Nodige libraries in CIRCUITPY/lib:
#   adafruit_minimqtt (map), adafruit_connection_manager.mpy,
#   adafruit_ticks.mpy, adafruit_bh1750.mpy, adafruit_bmp280.mpy,
#   adafruit_seesaw (map), adafruit_bus_device (map), adafruit_hcsr04.mpy,
#   adafruit_mcp3xxx (map)                         <- nieuw in stap 5
# =====================================================================

import os
import time
import json         # om metingen om te zetten naar JSON-tekst
import board
import busio        # voor de I2C-bus en de SPI-bus
import digitalio
import wifi
import socketpool
import adafruit_minimqtt.adafruit_minimqtt as MQTT


# ---------------------------------------------------------------------
# 1. Instellingen
# ---------------------------------------------------------------------
WIFI_SSID = os.getenv("CIRCUITPY_WIFI_SSID")
WIFI_PASS = os.getenv("CIRCUITPY_WIFI_PASSWORD")
BROKER = os.getenv("MQTT_BROKER")
PORT = int(os.getenv("MQTT_PORT", 1883))
MQTT_USER = os.getenv("MQTT_USER") or None
MQTT_PASS = os.getenv("MQTT_PASS") or None

TOPIC_SENSORS = "plantpal/sensors"   # Pico -> Pi : alle metingen
TOPIC_LED = "plantpal/cmd/led"       # Pi -> Pico : LED aan/uit (test)
TOPIC_STATUS = "plantpal/status"     # online/offline

INTERVAL = 5   # om de hoeveel seconden er gemeten en verstuurd wordt

# I2C-pinnen op de Pico. Gebruiken jullie andere pinnen in de
# aansluitgids? Pas dan alleen deze twee regels aan.
I2C_SDA = board.GP4    # fysieke pin 6
I2C_SCL = board.GP5    # fysieke pin 7

# Kalibratie bodemvochtsensor (ruwe waarde van de Seesaw).
# Ongeveer 300 = droog (in de lucht), ongeveer 1000 = nat.
# Deze getallen meet je straks zelf en vul je hier in (zie uitleg).
VOCHT_DROOG = 300
VOCHT_NAT = 1000

# Is de HC-SR04 al aangesloten? Zet dan op True.
# Zolang hij op False staat, slaat de code de ultrasone sensor over.
HCSR04_AANGESLOTEN = False

# Pinnen van de ultrasone sensor HC-SR04 (digitaal, geen I2C).
# De ECHO-pin gaat via een spanningsdeler naar de Pico (5 V -> 3,3 V)!
HCSR04_TRIG = board.GP20   # fysieke pin 26
HCSR04_ECHO = board.GP21   # fysieke pin 27

# Kalibratie reservoir: afstand van de sensor tot het water in cm.
# Meet ze zelf bij een LEEG en een VOL reservoir en vul ze hier in.
RESERVOIR_LEEG_CM = 15.0
RESERVOIR_VOL_CM = 3.0     # HC-SR04 meet niet betrouwbaar onder ~2 cm

# SPI-pinnen voor de MCP3008 (SPI0 van de Pico).
# Later hangt ook het OLED-scherm op dezelfde SCK/MOSI, met een eigen CS.
SPI_SCK = board.GP18    # fysieke pin 24 -> MCP3008 pin 13 (CLK)
SPI_MOSI = board.GP19   # fysieke pin 25 -> MCP3008 pin 11 (DIN)
SPI_MISO = board.GP16   # fysieke pin 21 -> MCP3008 pin 12 (DOUT)
MCP_CS = board.GP17     # fysieke pin 22 -> MCP3008 pin 10 (CS)


# ---------------------------------------------------------------------
# 2. LED op de Pico (zoals in stap 1)
# ---------------------------------------------------------------------
led = digitalio.DigitalInOut(board.LED)
led.direction = digitalio.Direction.OUTPUT
led.value = False


# ---------------------------------------------------------------------
# 3. De I2C-bus starten en kijken welke sensoren er hangen
# ---------------------------------------------------------------------
i2c = busio.I2C(I2C_SCL, I2C_SDA)   # let op: eerst SCL, dan SDA

# Scan: welke adressen antwoorden er op de bus?
while not i2c.try_lock():           # de bus "reserveren"
    pass
gevonden = [hex(adres) for adres in i2c.scan()]
i2c.unlock()                        # de bus weer vrijgeven
print("I2C-adressen gevonden:", gevonden)
# Verwacht: 0x23 (BH1750), 0x77 of 0x76 (BMP280), 0x36 (bodemvocht)


def start_sensor(naam, maak):
    # Probeert een sensor te starten. Lukt het niet (niet aangesloten,
    # verkeerd adres, library ontbreekt), dan geeft het None terug en
    # draait de rest van het programma gewoon verder.
    try:
        sensor = maak()
        print("OK   ", naam)
        return sensor
    except Exception as fout:
        print("NIET ", naam, "->", fout)
        return None


def maak_bh1750():
    import adafruit_bh1750
    return adafruit_bh1750.BH1750(i2c)


def maak_bmp280():
    import adafruit_bmp280
    # Adafruit-borden gebruiken 0x77, goedkope borden vaak 0x76
    if "0x76" in gevonden:
        return adafruit_bmp280.Adafruit_BMP280_I2C(i2c, address=0x76)
    return adafruit_bmp280.Adafruit_BMP280_I2C(i2c, address=0x77)


def maak_seesaw():
    from adafruit_seesaw.seesaw import Seesaw
    return Seesaw(i2c, addr=0x36)


def maak_hcsr04():
    import adafruit_hcsr04
    return adafruit_hcsr04.HCSR04(trigger_pin=HCSR04_TRIG, echo_pin=HCSR04_ECHO)


lichtsensor = start_sensor("BH1750 (licht)", maak_bh1750)
tempsensor = start_sensor("BMP280 (temperatuur/druk)", maak_bmp280)
vochtsensor = start_sensor("Seesaw (bodemvocht)", maak_seesaw)
def maak_mcp3008():
    # De SPI-bus starten: klok (SCK), data naar de chip (MOSI) en
    # data van de chip terug (MISO).
    import adafruit_mcp3xxx.mcp3008 as MCP
    from adafruit_mcp3xxx.analog_in import AnalogIn
    spi = busio.SPI(clock=SPI_SCK, MOSI=SPI_MOSI, MISO=SPI_MISO)
    # CS ("chip select"): met deze pin zegt de Pico "MCP3008, ik praat
    # nu tegen jou". Elk SPI-toestel krijgt zijn eigen CS-pin.
    cs = digitalio.DigitalInOut(MCP_CS)
    mcp = MCP.MCP3008(spi, cs)
    # De kanalen van de chip: P0 = CH0 (pin 1), P1 = CH1 (pin 2)
    kanaal_ldr = AnalogIn(mcp, getattr(MCP, "P0", 0))
    kanaal_pot = AnalogIn(mcp, getattr(MCP, "P1", 1))
    return (kanaal_ldr, kanaal_pot)


# Let op: SPI heeft geen adres zoals I2C. Hangt de MCP3008 niet (goed)
# aan de Pico, dan merkt de code dat niet, maar lees je steeds 0 of 100 %.
adc = start_sensor("MCP3008 (LDR + potmeter)", maak_mcp3008)


def naar_procent_adc(kanaal):
    # .value loopt van 0 (0 V) tot 65535 (3,3 V); omzetten naar 0-100 %.
    return kanaal.value / 65535 * 100


if HCSR04_AANGESLOTEN:
    sonar = start_sensor("HC-SR04 (waterpeil)", maak_hcsr04)
else:
    sonar = None
    print("UIT  ", "HC-SR04 (waterpeil) - staat uit in de instellingen")


def naar_procent(ruw):
    # Zet de ruwe vochtwaarde om naar 0-100 %, begrensd tussen 0 en 100.
    procent = (ruw - VOCHT_DROOG) / (VOCHT_NAT - VOCHT_DROOG) * 100
    return max(0, min(100, procent))


def waterpeil_procent(afstand):
    # Hoe kleiner de afstand tot het water, hoe voller het reservoir.
    procent = (RESERVOIR_LEEG_CM - afstand) / (RESERVOIR_LEEG_CM - RESERVOIR_VOL_CM) * 100
    return max(0, min(100, procent))


def meet_alles():
    # Leest elke aanwezige sensor en geeft een woordenboek (dict) terug,
    # bv. {"licht": 412.5, "temperatuur": 21.3, ...}
    meting = {}
    if lichtsensor:
        try:
            meting["licht"] = round(lichtsensor.lux, 1)
        except Exception as fout:
            print("Fout bij BH1750:", fout)
    if tempsensor:
        try:
            meting["temperatuur"] = round(tempsensor.temperature, 1)
            meting["luchtdruk"] = round(tempsensor.pressure, 1)
        except Exception as fout:
            print("Fout bij BMP280:", fout)
    if vochtsensor:
        try:
            ruw = vochtsensor.moisture_read()
            meting["bodemvocht_ruw"] = ruw
            meting["bodemvocht"] = round(naar_procent(ruw), 1)
        except Exception as fout:
            print("Fout bij bodemvochtsensor:", fout)
    if adc:
        try:
            kanaal_ldr, kanaal_pot = adc
            meting["ldr"] = round(naar_procent_adc(kanaal_ldr), 1)
            meting["drempel"] = round(naar_procent_adc(kanaal_pot), 1)
        except Exception as fout:
            print("Fout bij MCP3008:", fout)
    if sonar:
        try:
            afstand = sonar.distance        # in cm
            meting["afstand_water_cm"] = round(afstand, 1)
            meting["waterpeil"] = round(waterpeil_procent(afstand), 1)
        except RuntimeError:
            # Geen echo ontvangen (bv. niets binnen bereik, losse draad)
            print("HC-SR04: geen echo ontvangen")
    return meting


# ---------------------------------------------------------------------
# 4. Wifi
# ---------------------------------------------------------------------
print("Verbinden met wifi:", WIFI_SSID)
wifi.radio.connect(WIFI_SSID, WIFI_PASS)
print("Verbonden! IP-adres van de Pico:", wifi.radio.ipv4_address)
pool = socketpool.SocketPool(wifi.radio)


# ---------------------------------------------------------------------
# 5. MQTT (zoals in stap 1)
# ---------------------------------------------------------------------
def bij_verbinden(client, userdata, flags, rc):
    print("Verbonden met de MQTT-broker op", BROKER)
    client.publish(TOPIC_STATUS, "online", retain=True)
    client.subscribe(TOPIC_LED)


def bij_bericht(client, topic, bericht):
    print("Ontvangen op", topic, ":", bericht)
    if topic == TOPIC_LED:
        led.value = (bericht == "aan")


client = MQTT.MQTT(
    broker=BROKER,
    port=PORT,
    username=MQTT_USER,
    password=MQTT_PASS,
    client_id="plantpal-pico",
    socket_pool=pool,
)
client.will_set(TOPIC_STATUS, "offline", retain=True)
client.on_connect = bij_verbinden
client.on_message = bij_bericht
client.connect()


# ---------------------------------------------------------------------
# 6. Hoofdlus: meten en versturen
# ---------------------------------------------------------------------
laatst_verstuurd = time.monotonic() - INTERVAL   # meteen een eerste meting

while True:
    client.loop(timeout=1)

    if time.monotonic() - laatst_verstuurd >= INTERVAL:
        meting = meet_alles()
        tekst = json.dumps(meting)          # dict -> JSON-tekst
        client.publish(TOPIC_SENSORS, tekst)
        print("Verstuurd:", tekst)
        laatst_verstuurd = time.monotonic()
