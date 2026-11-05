# =====================================================================
# PlantPal - STAP 9: + pomp via relais = de loop is gesloten!
#
# Versie 13: veiliger waterpeil.
#   - De HC-SR04 meet 5 keer en neemt de middelste waarde.
#   - Is het waterpeil ONBEKEND (sensor meet niet), dan pompt de Pico
#     niet ("Waterpeil onbekend!" op het OLED).
#
# Versie 10: meerdere wifi-netwerken (bv. hotspot op school + thuis).
#   De Pico probeert ze na elkaar; per netwerk staat in settings.toml ook
#   het IP-adres van de Pi in dat netwerk.
#   AUTOMATISCH_WATER staat op False zolang de pomp er niet is.
#
# Versie 9b: het OLED blijft altijd iets tonen.
#   - Bovenaan staat een teller (#1, #2, ...) die bij elke meting stijgt:
#     verandert hij, dan werkt de Pico nog.
#   - Na elke klik van het relais en om de 30 s (OLED_HERSTART) wordt het
#     scherm opnieuw opgestart. De klik kan het schermpje storen.
#
# Nieuw in stap 9:
#   - Het relais schakelt de waterpomp (een DC-motor) aan en uit.
#   - Water geven gebeurt op 3 manieren ("bron"):
#       automatisch : bodemvocht < drempel (stemming "dorst")
#       knop        : iemand drukt op de drukknop
#       dashboard   : de Pi stuurt {"seconden": 3} naar plantpal/cmd/water
#   - Veiligheid:
#       * geen water als het reservoir bijna leeg is (pomp mag niet
#         droog draaien)
#       * na automatisch water geven eerst WACHTTIJD_NA_WATER seconden
#         wachten (het water moet in de grond trekken)
#       * nooit langer dan MAX_WATER_SECONDEN pompen
#   - Elke keer water geven -> bericht naar plantpal/event/water
#   - Bij het opstarten: relais-test (je hoort 2 klikjes).
#
# Uit stap 8:
#   - De HC-SR04 staat AAN (HCSR04_AANGESLOTEN = True). Hij meet de
#     afstand tot het water en zet die om naar "waterpeil" in %.
#   - Zakt het waterpeil onder 10 %, dan wordt de stemming
#     "reservoir_leeg": RGB paars, rode LED aan, "Vul mijn water bij!".
#     In stap 9 zorgt dit er ook voor dat de pomp niet droog draait.
#
# Uit stap 7:
#   - RGB-LED met PWM: de KLEUR toont de stemming van de plant, de
#     HELDERHEID past zich aan het licht in de kamer aan (LDR):
#     in een donkere kamer brandt de LED zachter.
#   - Rode LED (digitale uitgang): brandt als de plant aandacht nodig
#     heeft (dorst of reservoir leeg).
#   - Drukknop (digitale ingang): bij een druk meet de Pico meteen,
#     toont "Knop!" op het OLED en stuurt een bericht naar
#     plantpal/event/knop. In stap 9 geeft de knop water.
#   - Bij het opstarten doet de Pico een LED-test: rood, dan de RGB in
#     rood, groen en blauw. Zo zie je meteen of alles goed zit.
#
# Uit stap 6:
#   - Het OLED-scherm (128x64, SSD1306) op een EIGEN SPI-bus (SPI1).
#     Het toont de "stemming" van de plant en de belangrijkste waarden.
#     Waarom een eigen bus? Sommige OLED's hebben geen CS-pin; die zouden
#     anders ook "luisteren" naar de berichten voor de MCP3008.
#   - De stemming wordt berekend uit de metingen en ook meegestuurd via
#     MQTT (veld "stemming"), zodat ze ook in InfluxDB/Grafana staat.
#
# Uit stap 5:
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
#   adafruit_mcp3xxx (map),
#   adafruit_displayio_ssd1306.mpy, adafruit_display_text (map)
#   (stap 7 heeft geen nieuwe libraries nodig: pwmio en keypad zijn ingebouwd)
# =====================================================================

import os
import time
import json         # om metingen om te zetten naar JSON-tekst
import board
import busio        # voor de I2C-bus en de SPI-bus
import digitalio
import pwmio        # PWM: een pin heel snel aan/uit schakelen = dimmen
import keypad       # leest knoppen op de achtergrond (mist geen druk)
import wifi
import socketpool
import adafruit_minimqtt.adafruit_minimqtt as MQTT


# ---------------------------------------------------------------------
# 1. Instellingen
# ---------------------------------------------------------------------
# Meerdere netwerken: de Pico probeert ze na elkaar (zie settings.toml).
# Per netwerk: naam (SSID), wachtwoord en het IP-adres van de Pi in DAT
# netwerk (de Pi krijgt thuis een ander adres dan op de hotspot).
NETWERKEN = []
for nr in ("", "_2", "_3"):
    if nr == "":
        ssid = os.getenv("CIRCUITPY_WIFI_SSID")
        wachtwoord = os.getenv("CIRCUITPY_WIFI_PASSWORD")
    else:
        ssid = os.getenv("WIFI_SSID" + nr)
        wachtwoord = os.getenv("WIFI_PASSWORD" + nr)
    broker = os.getenv("MQTT_BROKER" + nr)
    if ssid and broker:
        NETWERKEN.append((ssid, wachtwoord, broker))
WIFI_SSID = None    # wordt ingevuld zodra de verbinding lukt
BROKER = None
PORT = int(os.getenv("MQTT_PORT", 1883))
MQTT_USER = os.getenv("MQTT_USER") or None
MQTT_PASS = os.getenv("MQTT_PASS") or None

TOPIC_SENSORS = "plantpal/sensors"   # Pico -> Pi : alle metingen
TOPIC_LED = "plantpal/cmd/led"       # Pi -> Pico : LED aan/uit (test)
TOPIC_STATUS = "plantpal/status"     # online/offline
TOPIC_KNOP = "plantpal/event/knop"   # Pico -> Pi : de drukknop is ingedrukt
TOPIC_CMD_WATER = "plantpal/cmd/water"      # Pi -> Pico : {"seconden": 3}
TOPIC_EVENT_WATER = "plantpal/event/water"  # Pico -> Pi : er is water gegeven

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
HCSR04_AANGESLOTEN = True

# Pinnen van de ultrasone sensor HC-SR04 (digitaal, geen I2C).
# De ECHO-pin gaat via een spanningsdeler naar de Pico (5 V -> 3,3 V)!
HCSR04_TRIG = board.GP20   # fysieke pin 26
HCSR04_ECHO = board.GP21   # fysieke pin 27

# Kalibratie reservoir: afstand van de sensor tot het water in cm.
# Meet ze zelf bij een LEEG en een VOL reservoir en vul ze hier in.
RESERVOIR_LEEG_CM = 7.0
RESERVOIR_VOL_CM = 3.0     # HC-SR04 meet niet betrouwbaar onder ~2 cm

# SPI-pinnen voor de MCP3008 (SPI0 van de Pico).
# Later hangt ook het OLED-scherm op dezelfde SCK/MOSI, met een eigen CS.
SPI_SCK = board.GP18    # fysieke pin 24 -> MCP3008 pin 13 (CLK)
SPI_MOSI = board.GP19   # fysieke pin 25 -> MCP3008 pin 11 (DIN)
SPI_MISO = board.GP16   # fysieke pin 21 -> MCP3008 pin 12 (DOUT)
MCP_CS = board.GP17     # fysieke pin 22 -> MCP3008 pin 10 (CS)

# OLED-scherm op SPI1 (een tweede, aparte SPI-bus van de Pico).
OLED_SCK = board.GP10   # fysieke pin 14 -> OLED "SCL" (= klok, ook D0/CLK)
OLED_MOSI = board.GP11  # fysieke pin 15 -> OLED "SDA" (= data, ook D1/DATA)
OLED_DC = board.GP14    # fysieke pin 19 -> OLED "DC"
OLED_RST = board.GP15   # fysieke pin 20 -> OLED "RES" / "RST"
# Onze OLED heeft GEEN CS-pin (6 pinnen: GND VCC SCL SDA RES DC).
# Heeft een OLED wel een CS-pin, sluit die dan aan op GP13 (pin 17)
# en zet hier:  OLED_CS = board.GP13
OLED_CS = None
# Blijft het scherm zwart terwijl de bedrading klopt? Zet dan True.
# (Sommige OLED's zonder CS-pin hebben "SPI-modus 3" nodig.)
OLED_MODUS3 = False

# RGB-LED (PWM) - elke kleur via een weerstand van 220 ohm
RGB_ROOD = board.GP6    # fysieke pin 9
RGB_GROEN = board.GP7   # fysieke pin 10
RGB_BLAUW = board.GP8   # fysieke pin 11
# Gemeenschappelijk pootje (het langste) aan GND = "common cathode" -> False.
# Brandt de LED net omgekeerd (aan als hij uit moet)? Dan is het een
# "common anode": langste pootje aan 3,3 V en hier True zetten.
RGB_COMMON_ANODE = False

# Rode status-LED - via een weerstand van 220 ohm
LED_ROOD = board.GP9    # fysieke pin 12

# Drukknop: tussen deze pin en GND (geen weerstand nodig: de Pico heeft
# een ingebouwde "pull-up"-weerstand die we in de code aanzetten)
KNOP = board.GP12       # fysieke pin 16

# Relais voor de pomp
RELAIS_PIN = board.GP22    # fysieke pin 29 -> "IN" van de relaismodule
# De meeste relaismodules schakelen AAN als IN laag (0 V) is: "actief laag".
# Klikt jouw relais net omgekeerd (aan als het uit moet)? Zet dan False.
RELAIS_ACTIEF_LAAG = True

# Water geven
AUTOMATISCH_WATER = False  # False = alleen water via knop of dashboard
WATER_SECONDEN = 3         # zo lang pompt de pomp per beurt
MAX_WATER_SECONDEN = 10    # nooit langer, ook niet als het dashboard het vraagt
WACHTTIJD_NA_WATER = 60    # seconden wachten na automatisch water geven
                           # (om te testen 60 s; in het echt bv. 1800 = 30 min)
# OLED om de zoveel seconden opnieuw opstarten. De klik van het relais
# kan het schermpje "in de war" brengen (het blijft dan leeg of hangt);
# een herstart lost dat op. Ook na elke pompbeurt gebeurt dit.
OLED_HERSTART = 30
MIN_WATERPEIL = 10         # onder dit waterpeil (%) pompt de pomp NIET

# Dimmen volgens de LDR (waarden in %, zoals "ldr" in Mu).
# Kijk in Mu wat "ldr" is bij normaal licht en met je vinger erop,
# en pas deze twee getallen eventueel aan.
LDR_DONKER = 20         # bij deze ldr-waarde of lager: zachtste stand
LDR_LICHT = 80          # bij deze ldr-waarde of hoger: volle sterkte
MIN_HELDERHEID = 0.03   # zachtste stand (3 %), zodat de LED nooit uit gaat

# Kleur per stemming (rood, groen, blauw), elk van 0 tot 1
KLEUREN = {
    "blij": (0, 1, 0),             # groen
    "dorst": (1, 0, 0),            # rood
    "te_warm": (1, 0.3, 0),        # oranje
    "slapen": (0, 0, 1),           # blauw
    "reservoir_leeg": (1, 0, 1),   # paars
}

# Grenzen voor de stemming van de plant
TE_WARM_C = 30          # boven deze temperatuur: "Te warm!"
DONKER_LUX = 5          # onder dit licht: de plant "slaapt"


# ---------------------------------------------------------------------
# 2. LED op de Pico (zoals in stap 1)
# ---------------------------------------------------------------------
led = digitalio.DigitalInOut(board.LED)
led.direction = digitalio.Direction.OUTPUT
led.value = False


# ---------------------------------------------------------------------
# 3. De I2C-bus starten en kijken welke sensoren er hangen
# ---------------------------------------------------------------------
try:
    i2c = busio.I2C(I2C_SCL, I2C_SDA)   # let op: eerst SCL, dan SDA
    # Scan: welke adressen antwoorden er op de bus?
    while not i2c.try_lock():           # de bus "reserveren"
        pass
    gevonden = [hex(adres) for adres in i2c.scan()]
    i2c.unlock()                        # de bus weer vrijgeven
except RuntimeError as fout:
    # "No pull up found on SDA or SCL": geen enkele I2C-sensor bereikbaar
    # (kabel los, geen 3,3 V, of SDA/SCL op de verkeerde pin).
    # De rest van PlantPal draait gewoon verder, zonder I2C-sensoren.
    print("I2C WERKT NIET:", fout)
    print("  -> kijk SDA (GP4, pin 6), SCL (GP5, pin 7), 3,3 V en GND na")
    i2c = None
    gevonden = []
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
        afstand = meet_afstand()
        if afstand is None:
            print("HC-SR04: geen betrouwbare meting (kijk VCC = 5 V, TRIG, ECHO en de deler na)")
        else:
            meting["afstand_water_cm"] = round(afstand, 1)
            meting["waterpeil"] = round(waterpeil_procent(afstand), 1)
    return meting


def meet_afstand():
    # Meet 5 keer en neemt de MIDDELSTE waarde (mediaan). Zo tellen een
    # mislukte meting of een echo van de wand niet mee.
    # Geeft None als er minder dan 3 goede metingen zijn.
    waarden = []
    for _ in range(5):
        try:
            afstand = sonar.distance
            if 2 <= afstand <= 400:          # HC-SR04 meet tussen 2 en 400 cm
                waarden.append(afstand)
        except RuntimeError:
            pass                             # geen echo: deze meting overslaan
        time.sleep(0.06)                     # echo laten uitsterven
    if len(waarden) < 3:
        return None
    waarden.sort()
    return waarden[len(waarden) // 2]


def bepaal_stemming(meting):
    # Kiest de stemming van de plant. De volgorde is belangrijk:
    # de eerste regel die klopt, wint.
    vocht = meting.get("bodemvocht")
    drempel = meting.get("drempel")
    if meting.get("waterpeil") is not None and meting["waterpeil"] < 10:
        return "reservoir_leeg"
    if vocht is not None and drempel is not None and vocht < drempel:
        return "dorst"
    if meting.get("temperatuur", 0) > TE_WARM_C:
        return "te_warm"
    if meting.get("licht", 1000) < DONKER_LUX:
        return "slapen"
    return "blij"


# Wat de plant op het scherm "zegt" bij elke stemming
ZINNEN = {
    "blij": "Ik ben blij :)",
    "dorst": "Ik heb dorst!",
    "te_warm": "Pfff, te warm!",
    "slapen": "Zzz... slaapwel",
    "reservoir_leeg": "Vul mijn water bij!",
}


# ---------------------------------------------------------------------
# 3b. Het OLED-scherm
# ---------------------------------------------------------------------
oled_spi = None       # de SPI-bus van het scherm (bewaard om te herstarten)
oled_groep = None     # de tekstregels (blijven bewaard bij een herstart)
oled_regels = None


def maak_oled():
    # Start het scherm (of start het opnieuw). De tekst blijft bewaard.
    global oled_spi, oled_groep, oled_regels
    import displayio
    import terminalio                        # het ingebouwde lettertype
    from adafruit_display_text import label
    import adafruit_displayio_ssd1306
    try:
        from fourwire import FourWire        # CircuitPython 9 en nieuwer
    except ImportError:
        from displayio import FourWire       # oudere CircuitPython

    displayio.release_displays()             # oud scherm loslaten
    if oled_spi is not None:
        try:
            oled_spi.deinit()                # oude SPI-bus vrijgeven
        except Exception:
            pass
    oled_spi = busio.SPI(clock=OLED_SCK, MOSI=OLED_MOSI)
    # FourWire = SPI + de extra pinnen DC (commando/data), CS en RESET
    modus = 1 if OLED_MODUS3 else 0
    bus = FourWire(oled_spi, command=OLED_DC, chip_select=OLED_CS,
                   reset=OLED_RST, baudrate=1000000,
                   polarity=modus, phase=modus)
    scherm = adafruit_displayio_ssd1306.SSD1306(bus, width=128, height=64)

    if oled_groep is None:
        # De eerste keer: 5 tekstregels onder elkaar (y = pixels vanaf boven)
        oled_groep = displayio.Group()
        oled_regels = []
        for y in (5, 19, 33, 45, 57):
            regel = label.Label(terminalio.FONT, text="", color=0xFFFFFF, x=0, y=y)
            oled_groep.append(regel)
            oled_regels.append(regel)
        oled_regels[0].text = "==== PlantPal ===="
        oled_regels[1].text = "Opstarten..."
    scherm.root_group = oled_groep
    return oled_regels


def herstart_oled():
    # Het scherm opnieuw opstarten, bv. na een klik van het relais.
    global oled
    try:
        oled = maak_oled()
    except Exception as fout:
        print("OLED herstarten mislukt:", fout)
        oled = None


oled = start_sensor("OLED-scherm", maak_oled)


# ---------------------------------------------------------------------
# 3c. LEDs en drukknop
# ---------------------------------------------------------------------
# PWM-uitgangen voor de 3 kleuren. frequency=1000: 1000 keer per seconde
# aan/uit, te snel voor je oog -> je ziet een gedimde LED.
rgb = [pwmio.PWMOut(pin, frequency=1000, duty_cycle=0)
       for pin in (RGB_ROOD, RGB_GROEN, RGB_BLAUW)]

rode_led = digitalio.DigitalInOut(LED_ROOD)
rode_led.direction = digitalio.Direction.OUTPUT
rode_led.value = False

# keypad.Keys houdt de knop op de achtergrond in de gaten.
# pull=True zet de ingebouwde pull-up aan: losgelaten = 3,3 V,
# ingedrukt = verbonden met GND (dus "pressed" bij waarde False).
knoppen = keypad.Keys((KNOP,), value_when_pressed=False, pull=True)


def zet_rgb(rood, groen, blauw, helderheid=1.0):
    # Elke kleur 0..1, helderheid 0..1 -> duty_cycle 0..65535
    for uitgang, waarde in zip(rgb, (rood, groen, blauw)):
        duty = int(waarde * helderheid * 65535)
        if RGB_COMMON_ANODE:
            duty = 65535 - duty     # bij common anode is alles omgekeerd
        uitgang.duty_cycle = duty


def led_test():
    # Laat bij het opstarten elke LED even branden, zodat je ziet of de
    # bedrading klopt. Kijk mee in Mu: de tekst zegt wat je moet zien.
    print("LED-test: rode LED")
    rode_led.value = True
    time.sleep(1)
    rode_led.value = False
    for naam, kleur in (("rood", (1, 0, 0)), ("groen", (0, 1, 0)),
                        ("blauw", (0, 0, 1))):
        print("LED-test: RGB", naam)
        zet_rgb(*kleur)
        time.sleep(1)
    zet_rgb(0, 0, 0)
    print("LED-test klaar")


def helderheid_uit_ldr(ldr):
    # LDR_DONKER of minder -> zachtste stand, LDR_LICHT of meer -> 100 %.
    # Daartussen een curve (macht 2.2): je ogen zien het verschil tussen
    # 80 % en 40 % nauwelijks, tussen 20 % en 3 % wel. Zo zie je het dimmen.
    deel = (ldr - LDR_DONKER) / (LDR_LICHT - LDR_DONKER)
    deel = max(0.0, min(1.0, deel))
    return max(MIN_HELDERHEID, deel ** 2.2)


def toon_op_leds(meting, stemming):
    # Kleur volgens de stemming; helderheid volgens de LDR,
    # zodat de LED 's avonds niet verblindt. Zonder LDR: vol licht.
    kleur = KLEUREN.get(stemming, (1, 1, 1))
    helderheid = helderheid_uit_ldr(meting.get("ldr", 100))
    print("RGB-helderheid: {:.0f} %".format(helderheid * 100))
    zet_rgb(*kleur, helderheid=helderheid)
    # Rode LED aan als de plant aandacht nodig heeft
    rode_led.value = stemming in ("dorst", "reservoir_leeg")


led_test()


# ---------------------------------------------------------------------
# 3d. Relais en pomp
# ---------------------------------------------------------------------
relais = digitalio.DigitalInOut(RELAIS_PIN)


def pomp(aan):
    # Zet de pomp aan (True) of uit (False) via het relais.
    if RELAIS_ACTIEF_LAAG:
        if aan:
            relais.switch_to_output(value=False)    # 0 V -> relais AAN
        else:
            # Pin "loslaten" (geen 0 V en geen 3,3 V): de relaismodule
            # trekt IN dan zelf naar 5 V en schakelt zeker UIT.
            # (Met 3,3 V zou een 5 V-module soms half aan blijven.)
            relais.switch_to_input()
    else:
        relais.switch_to_output(value=aan)


pomp(False)                 # bij het opstarten: pomp zeker uit


def relais_test():
    print("Relais-test: je hoort nu 2 klikjes (aan en uit)")
    pomp(True)
    time.sleep(0.5)
    pomp(False)
    time.sleep(0.2)
    herstart_oled()         # de klik kan het scherm verstoord hebben


relais_test()

laatste_meting = {}                         # de meest recente meting
laatst_water = -WACHTTIJD_NA_WATER          # tijdstip van de laatste beurt
water_verzoek = None                        # (seconden, bron) of None


def water_geven(seconden, bron):
    # Geeft water, tenzij het reservoir (bijna) leeg is.
    global laatst_water
    seconden = max(0, min(MAX_WATER_SECONDEN, seconden))
    peil = laatste_meting.get("waterpeil")
    if peil is None and sonar:
        # Veilig kiezen: weten we het waterpeil niet, dan pompen we NIET.
        print("GEEN water gegeven: waterpeil onbekend (HC-SR04 meet niet)")
        if oled:
            oled[1].text = "Waterpeil onbekend!"
        client.publish(TOPIC_EVENT_WATER, json.dumps(
            {"bron": bron, "seconden": 0, "geweigerd": "waterpeil_onbekend"}))
        return
    if peil is not None and peil < MIN_WATERPEIL:
        print("GEEN water gegeven: reservoir bijna leeg (", peil, "%)")
        if oled:
            oled[1].text = "Geen water! Vul bij"
        client.publish(TOPIC_EVENT_WATER, json.dumps(
            {"bron": bron, "seconden": 0, "geweigerd": "reservoir_leeg"}))
        return
    print("Water geven:", seconden, "s (bron:", bron, ")")
    if oled:
        oled[1].text = "Glug glug... water!"
    zet_rgb(0, 0.4, 1)                      # lichtblauw tijdens het pompen
    pomp(True)
    time.sleep(seconden)
    pomp(False)
    time.sleep(0.2)
    herstart_oled()                         # scherm herstellen na de klik
    laatst_water = time.monotonic()
    client.publish(TOPIC_EVENT_WATER, json.dumps(
        {"bron": bron, "seconden": seconden}))
    print("Klaar met water geven")


aantal_metingen = 0


def toon_op_oled(meting, stemming):
    # Zet de metingen op het scherm. Elke regel is max. 21 tekens breed.
    if not oled:
        return
    def waarde(naam, opmaak):
        return opmaak.format(meting[naam]) if naam in meting else "?"
    # Bovenste regel: een teller die elke meting 1 hoger wordt.
    # Verandert het getal, dan weet je: de Pico werkt nog.
    global aantal_metingen
    aantal_metingen += 1
    oled[0].text = "PlantPal    #" + str(aantal_metingen)
    oled[1].text = ZINNEN.get(stemming, stemming)
    oled[2].text = "Vocht " + waarde("bodemvocht", "{:.0f}%") + \
        " (min " + waarde("drempel", "{:.0f}%") + ")"
    oled[3].text = "Licht: " + waarde("licht", "{:.0f} lx")
    oled[4].text = "Temp:  " + waarde("temperatuur", "{:.1f} C")


# ---------------------------------------------------------------------
# 4. Wifi
# ---------------------------------------------------------------------
def verbind_wifi():
    # Probeert elk netwerk uit settings.toml, tot er een lukt.
    global WIFI_SSID, BROKER
    while True:
        for ssid, wachtwoord, broker in NETWERKEN:
            print("Verbinden met wifi:", ssid)
            if oled:
                oled[1].text = "Wifi: " + ssid[:15]
            try:
                wifi.radio.connect(ssid, wachtwoord, timeout=10)
                WIFI_SSID, BROKER = ssid, broker
                return
            except ConnectionError as fout:
                print("  lukt niet:", fout)
        print("Geen enkel netwerk gevonden, opnieuw over 5 s...")
        if oled:
            oled[1].text = "Geen wifi gevonden"
        time.sleep(5)


verbind_wifi()
print("Verbonden met", WIFI_SSID, "- IP-adres van de Pico:", wifi.radio.ipv4_address)
print("MQTT-broker (de Pi):", BROKER)
pool = socketpool.SocketPool(wifi.radio)


# ---------------------------------------------------------------------
# 5. MQTT (zoals in stap 1)
# ---------------------------------------------------------------------
def bij_verbinden(client, userdata, flags, rc):
    print("Verbonden met de MQTT-broker op", BROKER)
    client.publish(TOPIC_STATUS, "online", retain=True)
    client.subscribe(TOPIC_LED)
    client.subscribe(TOPIC_CMD_WATER)


def bij_bericht(client, topic, bericht):
    global water_verzoek
    print("Ontvangen op", topic, ":", bericht)
    if topic == TOPIC_LED:
        led.value = (bericht == "aan")
    elif topic == TOPIC_CMD_WATER:
        # Verwacht {"seconden": 3}. We geven hier nog GEEN water, we
        # onthouden alleen het verzoek; de hoofdlus voert het uit.
        try:
            seconden = float(json.loads(bericht).get("seconden", WATER_SECONDEN))
        except (ValueError, AttributeError):
            seconden = WATER_SECONDEN
        water_verzoek = (seconden, "dashboard")


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
laatste_oled_herstart = time.monotonic()

while True:
    client.loop(timeout=1)

    # Is de knop ingedrukt sinds de vorige keer? (keypad onthoudt het)
    knop_gedrukt = False
    gebeurtenis = knoppen.events.get()
    while gebeurtenis:
        if gebeurtenis.pressed:
            knop_gedrukt = True
        gebeurtenis = knoppen.events.get()
    if knop_gedrukt:
        print("Knop ingedrukt!")
        client.publish(TOPIC_KNOP, json.dumps({"bron": "knop"}))
        water_verzoek = (WATER_SECONDEN, "knop")

    if time.monotonic() - laatst_verstuurd >= INTERVAL:
        meting = meet_alles()
        stemming = bepaal_stemming(meting)
        meting["stemming"] = stemming       # ook meesturen naar de Pi
        laatste_meting = meting
        toon_op_oled(meting, stemming)
        toon_op_leds(meting, stemming)
        tekst = json.dumps(meting)          # dict -> JSON-tekst
        client.publish(TOPIC_SENSORS, tekst)
        print("Verstuurd:", tekst)
        laatst_verstuurd = time.monotonic()

        # Automatisch water geven: dorst, en de vorige beurt is lang genoeg
        # geleden (anders krijgt de plant water voor het in de grond trok).
        if (AUTOMATISCH_WATER and stemming == "dorst" and water_verzoek is None
                and (meting.get("waterpeil") is not None or not sonar)
                and time.monotonic() - laatst_water >= WACHTTIJD_NA_WATER):
            water_verzoek = (WATER_SECONDEN, "automatisch")

    # Is er een verzoek om water (knop, dashboard of automatisch)?
    if water_verzoek:
        seconden, bron = water_verzoek
        water_verzoek = None
        water_geven(seconden, bron)
        laatst_verstuurd = 0                # meteen opnieuw meten

    # Het scherm af en toe opnieuw opstarten (zie OLED_HERSTART)
    if time.monotonic() - laatste_oled_herstart >= OLED_HERSTART:
        herstart_oled()
        laatste_oled_herstart = time.monotonic()
