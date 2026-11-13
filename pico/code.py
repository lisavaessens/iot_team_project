# =====================================================================
# PlantPal - slimme plantenbak op een Raspberry Pi Pico W (CircuitPython)
# =====================================================================
# Wat doet dit programma?
#   - Het meet licht, temperatuur, luchtdruk, bodemvocht, het waterpeil
#     in het reservoir en leest een LDR en een potmeter uit.
#   - Daaruit bepalen we de "stemming" van de plant (blij, dorst, ...).
#   - Die stemming tonen we op een OLED-scherm en met een RGB-LED.
#   - Alle metingen sturen we via MQTT naar de broker (de Raspberry Pi).
#   - Via de drukknop, het dashboard of automatisch kan de pomp water geven.
# =====================================================================


# Hier importeren we alle modules die we nodig hebben.
import os           # om instellingen uit settings.toml te lezen (os.getenv)
import time         # voor pauzes (sleep) en tijdsmetingen (monotonic)
import json         # om metingen om te zetten naar JSON-tekst voor MQTT
import board        # bevat de namen van de pinnen van de Pico (GP0, GP1, ...)
import busio        # voor de communicatiebussen I2C en SPI
import digitalio    # voor gewone digitale in- en uitgangen (aan/uit)
import pwmio        # voor PWM-signalen (helderheid van de RGB-LED regelen)
import keypad       # om de drukknop betrouwbaar uit te lezen (met debounce)
import wifi         # om met het wifi-netwerk te verbinden
import socketpool   # zorgt voor netwerk-sockets, nodig voor MQTT
import adafruit_minimqtt.adafruit_minimqtt as MQTT   # de MQTT-bibliotheek


# ---------------------------------------------------------------------
# 1. Instellingen
# ---------------------------------------------------------------------
# Hier verzamelen we alle instellingen op één plek, zodat je ze makkelijk
# kunt aanpassen zonder door de hele code te moeten zoeken.

# Hier maken we een lijst van wifi-netwerken waarmee de Pico mag verbinden.
# We lezen tot 3 netwerken uit settings.toml (bv. thuis, school, hotspot).
# Bij elk netwerk hoort ook het IP-adres van de MQTT-broker in dat netwerk.
NETWERKEN = []
for nr in ("", "_2", "_3"):
    if nr == "":
        # Het eerste netwerk gebruikt de standaardnamen van CircuitPython.
        ssid = os.getenv("CIRCUITPY_WIFI_SSID")
        wachtwoord = os.getenv("CIRCUITPY_WIFI_PASSWORD")
    else:
        # Het 2e en 3e netwerk gebruiken WIFI_SSID_2, WIFI_SSID_3, ...
        ssid = os.getenv("WIFI_SSID" + nr)
        wachtwoord = os.getenv("WIFI_PASSWORD" + nr)
    broker = os.getenv("MQTT_BROKER" + nr)
    # Alleen als er een netwerknaam én een broker is ingevuld, voegen we het toe.
    if ssid and broker:
        NETWERKEN.append((ssid, wachtwoord, broker))
WIFI_SSID = None    # wordt ingevuld zodra de verbinding lukt
BROKER = None
# Hier lezen we de MQTT-poort en eventuele login (standaard poort 1883).
PORT = int(os.getenv("MQTT_PORT", 1883))
MQTT_USER = os.getenv("MQTT_USER") or None
MQTT_PASS = os.getenv("MQTT_PASS") or None

# Hier leggen we de MQTT-topics vast (de "kanalen" waarop we berichten sturen/ontvangen).
TOPIC_SENSORS = "plantpal/sensors"          # hierop sturen we alle metingen
TOPIC_LED = "plantpal/cmd/led"              # hierop ontvangen we: LED aan/uit
TOPIC_STATUS = "plantpal/status"            # online/offline-status van de Pico
TOPIC_KNOP = "plantpal/event/knop"          # melding: de knop is ingedrukt
TOPIC_CMD_WATER = "plantpal/cmd/water"      # opdracht van het dashboard: geef water
TOPIC_EVENT_WATER = "plantpal/event/water"  # melding: er is (geen) water gegeven

# Om de hoeveel seconden we een nieuwe meting doen en versturen.
INTERVAL = 5


# Hier kiezen we de pinnen voor de I2C-bus (BH1750, BMP280 en bodemvochtsensor).
I2C_SDA = board.GP4    # fysieke pin 6
I2C_SCL = board.GP5    # fysieke pin 7


# Kalibratie van de bodemvochtsensor: ruwe waarde bij droge en natte grond.
# Hiermee rekenen we de ruwe waarde later om naar een percentage.
VOCHT_DROOG = 300
VOCHT_NAT = 1000


# Hier zetten we de ultrasone sensor (HC-SR04) aan of uit.
HCSR04_AANGESLOTEN = True


# Pinnen van de HC-SR04 (waterpeil in het reservoir).
HCSR04_TRIG = board.GP20   # fysieke pin 26
HCSR04_ECHO = board.GP21   # fysieke pin 27


# Afstand van de sensor tot het water bij een leeg en een vol reservoir.
# Hoe kleiner de afstand, hoe voller het reservoir.
RESERVOIR_LEEG_CM = 13.0
RESERVOIR_VOL_CM = 3.0


# Hier kiezen we de SPI-pinnen voor de MCP3008 (analoog-naar-digitaal-omzetter).
# De Pico heeft niet genoeg analoge ingangen, daarom gebruiken we deze chip
# voor de LDR en de potmeter.
SPI_SCK = board.GP18    # fysieke pin 24 -> MCP3008 pin 13 (CLK)
SPI_MOSI = board.GP19   # fysieke pin 25 -> MCP3008 pin 11 (DIN)
SPI_MISO = board.GP16   # fysieke pin 21 -> MCP3008 pin 12 (DOUT)
MCP_CS = board.GP17     # fysieke pin 22 -> MCP3008 pin 10 (CS)


# Hier kiezen we de pinnen voor het OLED-scherm (SSD1306, via een aparte SPI-bus).
OLED_SCK = board.GP10   # fysieke pin 14 -> OLED "SCL" (= klok, ook D0/CLK)
OLED_MOSI = board.GP11  # fysieke pin 15 -> OLED "SDA" (= data, ook D1/DATA)
OLED_DC = board.GP14    # fysieke pin 19 -> OLED "DC"
OLED_RST = board.GP15   # fysieke pin 20 -> OLED "RES" / "RST"

# Ons OLED-scherm heeft geen CS-pin, daarom staat deze op None.
OLED_CS = None

# Sommige OLED-schermen werken alleen in SPI-modus 3; bij ons is dat niet nodig.
OLED_MODUS3 = False


# Hier kiezen we de pinnen voor de RGB-LED (één pin per kleur).
RGB_ROOD = board.GP6    # fysieke pin 9
RGB_GROEN = board.GP7   # fysieke pin 10
RGB_BLAUW = board.GP8   # fysieke pin 11

# Onze RGB-LED heeft een gemeenschappelijke kathode (GND), dus False.
# Bij een common-anode-LED moet je dit op True zetten (dan wordt alles omgekeerd).
RGB_COMMON_ANODE = False


# Pin van de losse rode waarschuwings-LED.
LED_ROOD = board.GP9    # fysieke pin 12


# Pin van de drukknop.
KNOP = board.GP12       # fysieke pin 16


# Pin waarmee we het relais (en dus de waterpomp) aansturen.
RELAIS_PIN = board.GP22    # fysieke pin 29 -> "IN" van de relaismodule

# Onze relaismodule schakelt in als de ingang LAAG (0 V) is.
RELAIS_ACTIEF_LAAG = True


# Hier stellen we het water geven in.
AUTOMATISCH_WATER = False  # True = de plant krijgt vanzelf water bij dorst
WATER_SECONDEN = 3         # standaard aantal seconden dat de pomp draait
MAX_WATER_SECONDEN = 10    # veiligheidslimiet: nooit langer dan dit pompen
WACHTTIJD_NA_WATER = 60    # zoveel seconden wachten voor we automatisch opnieuw water geven


# Om de hoeveel seconden we het OLED-scherm opnieuw opstarten
# (het scherm kan soms vastlopen door storing van de pomp/het relais).
OLED_HERSTART = 30
# Onder dit waterpeil (%) weigeren we om de pomp aan te zetten (pomp mag niet droog draaien).
MIN_WATERPEIL = 10


# Instellingen om de helderheid van de RGB-LED aan te passen aan het omgevingslicht (LDR).
LDR_DONKER = 20         # LDR-waarde (%) waarbij het "donker" is
LDR_LICHT = 80          # LDR-waarde (%) waarbij het "licht" is
MIN_HELDERHEID = 0.03   # de LED gaat nooit helemaal uit (minimaal 3 %)


# Hier koppelen we elke stemming aan een kleur (rood, groen, blauw van 0 tot 1).
KLEUREN = {
    "blij": (0, 1, 0),             # groen
    "dorst": (1, 0, 0),            # rood
    "te_warm": (1, 0.3, 0),        # oranje
    "slapen": (0, 0, 1),           # blauw
    "reservoir_leeg": (1, 0, 1),   # paars
}


# Grenswaarden om de stemming te bepalen.
TE_WARM_C = 30          # boven 30 °C is het te warm
DONKER_LUX = 5          # onder 5 lux is het nacht -> de plant slaapt


# ---------------------------------------------------------------------
# 2. LED op de Pico
# ---------------------------------------------------------------------
# Hier gebruiken we de ingebouwde LED van de Pico als uitgang.
# Die kunnen we later via MQTT aan- en uitzetten (topic plantpal/cmd/led).
led = digitalio.DigitalInOut(board.LED)
led.direction = digitalio.Direction.OUTPUT
led.value = False   # bij het opstarten staat de LED uit


# ---------------------------------------------------------------------
# 3. De I2C-bus starten en kijken welke sensoren er hangen
# ---------------------------------------------------------------------
# Hier starten we de I2C-bus en scannen we welke apparaten erop hangen.
# Elk I2C-apparaat heeft een eigen adres (bv. 0x23 = BH1750, 0x76/0x77 = BMP280,
# 0x36 = bodemvochtsensor).
try:
    i2c = busio.I2C(I2C_SCL, I2C_SDA)

    # De bus eerst "op slot" doen, zodat niemand anders hem tegelijk gebruikt.
    while not i2c.try_lock():
        pass
    gevonden = [hex(adres) for adres in i2c.scan()]   # lijst met gevonden adressen
    i2c.unlock()                                      # bus weer vrijgeven
except RuntimeError as fout:
    # Hier vangen we de fout op als de I2C-bus niet werkt (bv. een draadje los),
    # zodat de rest van het programma toch verder kan.
    print("I2C WERKT NIET:", fout)
    print("  -> kijk SDA (GP4, pin 6), SCL (GP5, pin 7), 3,3 V en GND na")
    i2c = None
    gevonden = []
print("I2C-adressen gevonden:", gevonden)


# Hulpfunctie om een sensor veilig te starten.
# Als het starten mislukt, crasht het programma niet: we krijgen gewoon None terug
# en de rest van de code slaat die sensor dan over.
def start_sensor(naam, maak):
    try:
        sensor = maak()
        print("OK   ", naam)
        return sensor
    except Exception as fout:
        print("NIET ", naam, "->", fout)
        return None


# Hier maken we de lichtsensor (BH1750) aan, die meet in lux.
def maak_bh1750():
    import adafruit_bh1750
    return adafruit_bh1750.BH1750(i2c)


# Hier maken we de temperatuur- en luchtdruksensor (BMP280) aan.
def maak_bmp280():
    import adafruit_bmp280
    # De BMP280 kan op adres 0x76 of 0x77 zitten; we kiezen het adres dat de scan vond.
    if "0x76" in gevonden:
        return adafruit_bmp280.Adafruit_BMP280_I2C(i2c, address=0x76)
    return adafruit_bmp280.Adafruit_BMP280_I2C(i2c, address=0x77)


# Hier maken we de bodemvochtsensor (Adafruit Seesaw, adres 0x36) aan.
def maak_seesaw():
    from adafruit_seesaw.seesaw import Seesaw
    return Seesaw(i2c, addr=0x36)


# Hier maken we de ultrasone afstandssensor (HC-SR04) aan voor het waterpeil.
def maak_hcsr04():
    import adafruit_hcsr04
    return adafruit_hcsr04.HCSR04(trigger_pin=HCSR04_TRIG, echo_pin=HCSR04_ECHO)


# Hier starten we de drie I2C-sensoren.
lichtsensor = start_sensor("BH1750 (licht)", maak_bh1750)
tempsensor = start_sensor("BMP280 (temperatuur/druk)", maak_bmp280)
vochtsensor = start_sensor("Seesaw (bodemvocht)", maak_seesaw)


# Hier maken we de MCP3008 aan via SPI en kiezen we twee kanalen:
#   kanaal 0 (P0) = LDR (lichtgevoelige weerstand)
#   kanaal 1 (P1) = potmeter (daarmee stel je de minimale bodemvochtigheid in)
def maak_mcp3008():
    import adafruit_mcp3xxx.mcp3008 as MCP
    from adafruit_mcp3xxx.analog_in import AnalogIn
    spi = busio.SPI(clock=SPI_SCK, MOSI=SPI_MOSI, MISO=SPI_MISO)

    cs = digitalio.DigitalInOut(MCP_CS)   # chip-select-pin van de MCP3008
    mcp = MCP.MCP3008(spi, cs)

    # getattr zorgt dat het werkt met zowel oude als nieuwe versies van de bibliotheek.
    kanaal_ldr = AnalogIn(mcp, getattr(MCP, "P0", 0))
    kanaal_pot = AnalogIn(mcp, getattr(MCP, "P1", 1))
    return (kanaal_ldr, kanaal_pot)


adc = start_sensor("MCP3008 (LDR + potmeter)", maak_mcp3008)


# Hier zetten we een analoge waarde (0 - 65535) om naar een percentage (0 - 100 %).
def naar_procent_adc(kanaal):
    return kanaal.value / 65535 * 100


# Hier starten we de HC-SR04, maar alleen als die in de instellingen aan staat.
if HCSR04_AANGESLOTEN:
    sonar = start_sensor("HC-SR04 (waterpeil)", maak_hcsr04)
else:
    sonar = None
    print("UIT  ", "HC-SR04 (waterpeil) - staat uit in de instellingen")


# Hier rekenen we de ruwe bodemvochtwaarde om naar een percentage.
# max/min zorgt dat het resultaat altijd tussen 0 en 100 % blijft.
def naar_procent(ruw):
    procent = (ruw - VOCHT_DROOG) / (VOCHT_NAT - VOCHT_DROOG) * 100
    return max(0, min(100, procent))


# Hier rekenen we de gemeten afstand (cm) om naar een waterpeil in procent.
# Grote afstand = weinig water, kleine afstand = veel water.
def waterpeil_procent(afstand):
    procent = (RESERVOIR_LEEG_CM - afstand) / (RESERVOIR_LEEG_CM - RESERVOIR_VOL_CM) * 100
    return max(0, min(100, procent))


# Hier lezen we alle sensoren uit en stoppen we de waarden in één dictionary.
# Elke sensor staat in een eigen try/except: als één sensor faalt,
# gaan de andere metingen gewoon door.
def meet_alles():
    meting = {}

    # Licht in lux (BH1750)
    if lichtsensor:
        try:
            meting["licht"] = round(lichtsensor.lux, 1)
        except Exception as fout:
            print("Fout bij BH1750:", fout)

    # Temperatuur (°C) en luchtdruk (hPa) (BMP280)
    if tempsensor:
        try:
            meting["temperatuur"] = round(tempsensor.temperature, 1)
            meting["luchtdruk"] = round(tempsensor.pressure, 1)
        except Exception as fout:
            print("Fout bij BMP280:", fout)

    # Bodemvocht: zowel de ruwe waarde als het percentage
    if vochtsensor:
        try:
            ruw = vochtsensor.moisture_read()
            meting["bodemvocht_ruw"] = ruw
            meting["bodemvocht"] = round(naar_procent(ruw), 1)
        except Exception as fout:
            print("Fout bij bodemvochtsensor:", fout)

    # LDR (omgevingslicht in %) en potmeter (drempel voor dorst in %)
    if adc:
        try:
            kanaal_ldr, kanaal_pot = adc
            meting["ldr"] = round(naar_procent_adc(kanaal_ldr), 1)
            meting["drempel"] = round(naar_procent_adc(kanaal_pot), 1)
        except Exception as fout:
            print("Fout bij MCP3008:", fout)

    # Waterpeil via de HC-SR04
    if sonar:
        afstand = meet_afstand()
        if afstand is None:
            print("HC-SR04: geen betrouwbare meting (kijk VCC = 5 V, TRIG, ECHO en de deler na)")
        else:
            # Hier nemen we het gemiddelde van de laatste 3 metingen,
            # zodat het waterpeil niet zo hard heen en weer springt.
            laatste_afstanden.append(afstand)
            if len(laatste_afstanden) > 3:
                laatste_afstanden.pop(0)    # oudste meting weggooien
            afstand = sum(laatste_afstanden) / len(laatste_afstanden)
            meting["afstand_water_cm"] = round(afstand, 1)
            meting["waterpeil"] = round(waterpeil_procent(afstand), 1)
    return meting


# Hier bewaren we de laatste paar afstandsmetingen (voor het gemiddelde hierboven).
laatste_afstanden = []


# Hier meten we de afstand tot het water op een betrouwbare manier:
# we meten 7 keer, gooien onmogelijke waarden weg en nemen de mediaan
# (de middelste waarde). Zo hebben uitschieters geen invloed.
def meet_afstand():
    waarden = []
    for _ in range(7):
        try:
            afstand = sonar.distance
            if 2 <= afstand <= 400:          # de HC-SR04 meet alleen tussen 2 en 400 cm
                waarden.append(afstand)
        except RuntimeError:
            pass                             # geen echo ontvangen -> deze meting overslaan
        time.sleep(0.06)                     # even wachten zodat de vorige echo uitgestorven is
    if len(waarden) < 4:
        return None                          # te weinig goede metingen -> niet betrouwbaar
    waarden.sort()
    return waarden[len(waarden) // 2]        # de mediaan teruggeven


# Hier bepalen we de stemming van de plant op basis van de metingen.
# De volgorde is belangrijk: het eerste probleem dat we vinden, "wint".
def bepaal_stemming(meting):
    vocht = meting.get("bodemvocht")
    drempel = meting.get("drempel")
    # 1. Reservoir bijna leeg? Dat is het belangrijkst.
    if meting.get("waterpeil") is not None and meting["waterpeil"] < 10:
        return "reservoir_leeg"
    # 2. Is de grond droger dan de drempel die we met de potmeter instellen?
    if vocht is not None and drempel is not None and vocht < drempel:
        return "dorst"
    # 3. Is het te warm?
    if meting.get("temperatuur", 0) > TE_WARM_C:
        return "te_warm"
    # 4. Is het donker? Dan slaapt de plant.
    if meting.get("licht", 1000) < DONKER_LUX:
        return "slapen"
    # 5. Anders is alles in orde.
    return "blij"


# Hier koppelen we elke stemming aan een zin die we op het OLED-scherm tonen.
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
# Deze variabelen onthouden de SPI-bus en de tekstregels van het scherm,
# zodat we het scherm later opnieuw kunnen opstarten zonder de tekst te verliezen.
oled_spi = None
oled_groep = None
oled_regels = None


# Hier starten we het OLED-scherm (128 x 64 pixels) en maken we 5 tekstregels.
def maak_oled():
    global oled_spi, oled_groep, oled_regels
    import displayio
    import terminalio                        # het standaard lettertype
    from adafruit_display_text import label
    import adafruit_displayio_ssd1306
    # FourWire zit afhankelijk van de CircuitPython-versie in een andere module.
    try:
        from fourwire import FourWire
    except ImportError:
        from displayio import FourWire

    # Eerst een eventueel oud scherm en de oude SPI-bus vrijgeven,
    # anders krijgen we een fout dat de pinnen al in gebruik zijn.
    displayio.release_displays()
    if oled_spi is not None:
        try:
            oled_spi.deinit()
        except Exception:
            pass
    oled_spi = busio.SPI(clock=OLED_SCK, MOSI=OLED_MOSI)

    # Hier maken we de verbinding met het scherm (SPI + DC- en reset-pin).
    modus = 1 if OLED_MODUS3 else 0
    bus = FourWire(oled_spi, command=OLED_DC, chip_select=OLED_CS,
                   reset=OLED_RST, baudrate=1000000,
                   polarity=modus, phase=modus)
    scherm = adafruit_displayio_ssd1306.SSD1306(bus, width=128, height=64)

    # De tekstregels maken we maar één keer aan; bij een herstart gebruiken
    # we dezelfde regels opnieuw, zodat de tekst blijft staan.
    if oled_groep is None:
        oled_groep = displayio.Group()
        oled_regels = []
        for y in (5, 19, 33, 45, 57):         # y-positie van elke regel op het scherm
            regel = label.Label(terminalio.FONT, text="", color=0xFFFFFF, x=0, y=y)
            oled_groep.append(regel)
            oled_regels.append(regel)
        oled_regels[0].text = "==== PlantPal ===="
        oled_regels[1].text = "Opstarten..."
    scherm.root_group = oled_groep            # de regels op het scherm zetten
    return oled_regels


# Hier starten we het OLED-scherm opnieuw op.
# Dat doen we na het schakelen van de pomp en elke 30 seconden,
# omdat storing van de pomp het scherm soms laat vastlopen.
def herstart_oled():
    global oled
    try:
        oled = maak_oled()
    except Exception as fout:
        print("OLED herstarten mislukt:", fout)
        oled = None


# Hier starten we het scherm voor de eerste keer.
# "oled" is daarna de lijst met 5 tekstregels (of None als het scherm niet werkt).
oled = start_sensor("OLED-scherm", maak_oled)


# ---------------------------------------------------------------------
# 3c. LEDs en drukknop
# ---------------------------------------------------------------------
# Hier maken we 3 PWM-uitgangen voor de RGB-LED (rood, groen, blauw).
# Met PWM kunnen we de helderheid regelen i.p.v. alleen aan/uit.
rgb = [pwmio.PWMOut(pin, frequency=1000, duty_cycle=0)
       for pin in (RGB_ROOD, RGB_GROEN, RGB_BLAUW)]

# Hier stellen we de rode waarschuwings-LED in als gewone digitale uitgang.
rode_led = digitalio.DigitalInOut(LED_ROOD)
rode_led.direction = digitalio.Direction.OUTPUT
rode_led.value = False

# Hier stellen we de drukknop in. keypad doet de debounce voor ons
# (een knop "dendert" bij indrukken, waardoor je anders meerdere drukken meet).
# pull=True zet de interne pull-up aan; ingedrukt = laag (False).
knoppen = keypad.Keys((KNOP,), value_when_pressed=False, pull=True)


# Hier zetten we een kleur op de RGB-LED.
# rood/groen/blauw zijn waarden tussen 0 en 1, helderheid ook (0 - 1).
def zet_rgb(rood, groen, blauw, helderheid=1.0):
    for uitgang, waarde in zip(rgb, (rood, groen, blauw)):
        duty = int(waarde * helderheid * 65535)   # omzetten naar PWM-waarde (0 - 65535)
        if RGB_COMMON_ANODE:
            duty = 65535 - duty                   # bij common anode is alles omgekeerd
        uitgang.duty_cycle = duty


# Hier testen we bij het opstarten alle LEDs, zodat je meteen ziet of ze werken:
# eerst de rode LED, daarna de RGB-LED in rood, groen en blauw.
def led_test():
    print("LED-test: rode LED")
    rode_led.value = True
    time.sleep(1)
    rode_led.value = False
    for naam, kleur in (("rood", (1, 0, 0)), ("groen", (0, 1, 0)),
                        ("blauw", (0, 0, 1))):
        print("LED-test: RGB", naam)
        zet_rgb(*kleur)
        time.sleep(1)
    zet_rgb(0, 0, 0)    # alles weer uit
    print("LED-test klaar")


# Hier berekenen we hoe fel de RGB-LED moet branden, op basis van de LDR.
# Is het donker in de kamer, dan brandt de LED zachter (verblindt niet).
# De macht 2.2 (gammacorrectie) zorgt dat het verloop natuurlijk aanvoelt voor ons oog.
def helderheid_uit_ldr(ldr):
    deel = (ldr - LDR_DONKER) / (LDR_LICHT - LDR_DONKER)
    deel = max(0.0, min(1.0, deel))
    return max(MIN_HELDERHEID, deel ** 2.2)


# Hier tonen we de stemming op de LEDs:
#   - de RGB-LED krijgt de kleur van de stemming,
#   - de rode LED gaat aan als de plant dorst heeft of het reservoir leeg is.
def toon_op_leds(meting, stemming):
    kleur = KLEUREN.get(stemming, (1, 1, 1))     # onbekende stemming -> wit
    helderheid = helderheid_uit_ldr(meting.get("ldr", 100))
    print("RGB-helderheid: {:.0f} %".format(helderheid * 100))
    zet_rgb(*kleur, helderheid=helderheid)

    rode_led.value = stemming in ("dorst", "reservoir_leeg")


# Hier voeren we de LED-test uit bij het opstarten.
led_test()


# ---------------------------------------------------------------------
# 3d. Relais en pomp
# ---------------------------------------------------------------------
# Hier maken we de pin aan waarmee we het relais (de schakelaar voor de pomp) bedienen.
relais = digitalio.DigitalInOut(RELAIS_PIN)


# Hier zetten we de pomp aan of uit.
# Ons relais is "actief laag": 0 V = pomp aan.
# Om de pomp uit te zetten maken we de pin een ingang (zwevend). Zo komt er
# zeker geen laag signaal meer op het relais en schakelt het betrouwbaar uit,
# ook al werkt de relaismodule op 5 V en de Pico op 3,3 V.
def pomp(aan):
    if RELAIS_ACTIEF_LAAG:
        if aan:
            relais.switch_to_output(value=False)    # laag = relais aan = pomp aan
        else:
            relais.switch_to_input()                # zwevend = relais uit = pomp uit
    else:
        relais.switch_to_output(value=aan)


# Hier zorgen we dat de pomp zeker uit staat bij het opstarten.
pomp(False)


# Hier testen we het relais: je hoort het twee keer klikken (aan en uit).
# Daarna herstarten we het OLED-scherm, want het schakelen kan storing geven.
def relais_test():
    print("Relais-test: je hoort nu 2 klikjes (aan en uit)")
    pomp(True)
    time.sleep(0.5)
    pomp(False)
    time.sleep(0.2)
    herstart_oled()


relais_test()

# Variabelen die we tijdens het programma bijhouden:
laatste_meting = {}                        # de meest recente meting (o.a. voor het waterpeil)
laatst_water = -WACHTTIJD_NA_WATER         # tijdstip van de laatste watergift (zo mag het meteen)
water_verzoek = None                       # (seconden, bron) als er water gegeven moet worden


# Hier geven we de plant water.
# "bron" zegt wie erom vroeg: de knop, het dashboard of automatisch.
# Eerst controleren we of het veilig is (genoeg water in het reservoir),
# daarna laten we de pomp een aantal seconden draaien en melden we dat via MQTT.
def water_geven(seconden, bron):
    global laatst_water
    # Veiligheid: het aantal seconden begrenzen tussen 0 en het maximum.
    seconden = max(0, min(MAX_WATER_SECONDEN, seconden))
    peil = laatste_meting.get("waterpeil")

    # Controle 1: als de HC-SR04 aangesloten is maar niets meet, weten we
    # niet hoeveel water er is -> geen risico nemen, geen water geven.
    if peil is None and sonar:
        print("GEEN water gegeven: waterpeil onbekend (HC-SR04 meet niet)")
        if oled:
            oled[1].text = "Waterpeil onbekend!"
        client.publish(TOPIC_EVENT_WATER, json.dumps(
            {"bron": bron, "seconden": 0, "geweigerd": "waterpeil_onbekend"}))
        return

    # Controle 2: is het reservoir (bijna) leeg, dan zetten we de pomp niet aan.
    if peil is not None and peil < MIN_WATERPEIL:
        print("GEEN water gegeven: reservoir bijna leeg (", peil, "%)")
        if oled:
            oled[1].text = "Geen water! Vul bij"
        client.publish(TOPIC_EVENT_WATER, json.dumps(
            {"bron": bron, "seconden": 0, "geweigerd": "reservoir_leeg"}))
        return

    # Alles in orde: hier geven we echt water.
    print("Water geven:", seconden, "s (bron:", bron, ")")
    if oled:
        oled[1].text = "Glug glug... water!"
    zet_rgb(0, 0.4, 1)                      # RGB-LED lichtblauw tijdens het water geven
    pomp(True)
    time.sleep(seconden)                    # de pomp laten draaien
    pomp(False)
    time.sleep(0.2)
    herstart_oled()                         # scherm herstarten na storing van de pomp
    laatst_water = time.monotonic()         # onthouden wanneer we water gaven
    # Via MQTT melden dat er water gegeven is (voor het dashboard/de database).
    client.publish(TOPIC_EVENT_WATER, json.dumps(
        {"bron": bron, "seconden": seconden}))
    print("Klaar met water geven")


# Teller voor het aantal metingen (tonen we rechtsboven op het scherm).
aantal_metingen = 0


# Hier zetten we de metingen en de stemming op het OLED-scherm.
def toon_op_oled(meting, stemming):
    if not oled:
        return      # geen scherm -> niets doen

    # Hulpfunctie: geeft de waarde netjes opgemaakt terug, of "?" als die ontbreekt.
    def waarde(naam, opmaak):
        return opmaak.format(meting[naam]) if naam in meting else "?"

    global aantal_metingen
    aantal_metingen += 1
    oled[0].text = "PlantPal    #" + str(aantal_metingen)          # regel 1: titel + teller
    oled[1].text = ZINNEN.get(stemming, stemming)                  # regel 2: stemming
    oled[2].text = "Vocht " + waarde("bodemvocht", "{:.0f}%") + \
        " (min " + waarde("drempel", "{:.0f}%") + ")"              # regel 3: bodemvocht + drempel
    oled[3].text = "Licht: " + waarde("licht", "{:.0f} lx")        # regel 4: licht
    oled[4].text = "Temp:  " + waarde("temperatuur", "{:.1f} C")   # regel 5: temperatuur


# ---------------------------------------------------------------------
# 4. Wifi
# ---------------------------------------------------------------------
# Hier verbinden we met wifi. We proberen alle netwerken uit de lijst
# één voor één. Lukt geen enkel netwerk, dan wachten we 5 seconden
# en proberen we opnieuw (eindeloos, tot het lukt).
def verbind_wifi():
    global WIFI_SSID, BROKER
    while True:
        for ssid, wachtwoord, broker in NETWERKEN:
            print("Verbinden met wifi:", ssid)
            if oled:
                oled[1].text = "Wifi: " + ssid[:15]    # max. 15 tekens, anders past het niet
            try:
                wifi.radio.connect(ssid, wachtwoord, timeout=10)
                # Gelukt: onthouden met welk netwerk en welke broker we werken.
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
# Hier maken we een socket-pool aan: die heeft MQTT nodig om over het netwerk te praten.
pool = socketpool.SocketPool(wifi.radio)


# ---------------------------------------------------------------------
# 5. MQTT (zoals in stap 1)
# ---------------------------------------------------------------------
# Deze functie wordt automatisch uitgevoerd zodra we verbonden zijn met de broker.
# Hier melden we dat de Pico online is en abonneren we ons op de commando-topics.
def bij_verbinden(client, userdata, flags, rc):
    print("Verbonden met de MQTT-broker op", BROKER)
    # retain=True: de broker onthoudt dit bericht, nieuwe abonnees zien het meteen.
    client.publish(TOPIC_STATUS, "online", retain=True)
    client.subscribe(TOPIC_LED)
    client.subscribe(TOPIC_CMD_WATER)


# Deze functie wordt automatisch uitgevoerd als er een MQTT-bericht binnenkomt.
def bij_bericht(client, topic, bericht):
    global water_verzoek
    print("Ontvangen op", topic, ":", bericht)
    if topic == TOPIC_LED:
        # Hier zetten we de LED op de Pico aan of uit vanuit het dashboard.
        led.value = (bericht == "aan")
    elif topic == TOPIC_CMD_WATER:
        # Hier lezen we uit het JSON-bericht hoeveel seconden we water moeten geven.
        # Is het bericht ongeldig, dan gebruiken we de standaardtijd.
        try:
            seconden = float(json.loads(bericht).get("seconden", WATER_SECONDEN))
        except (ValueError, AttributeError):
            seconden = WATER_SECONDEN
        # We geven hier nog geen water, we onthouden alleen het verzoek.
        # De hoofdlus voert het daarna uit (zo blokkeren we de MQTT-afhandeling niet).
        water_verzoek = (seconden, "dashboard")


# Hier maken we de MQTT-client aan met de gegevens van de broker.
client = MQTT.MQTT(
    broker=BROKER,
    port=PORT,
    username=MQTT_USER,
    password=MQTT_PASS,
    client_id="plantpal-pico",      # unieke naam van dit apparaat bij de broker
    socket_pool=pool,
)
# "Last Will": valt de Pico onverwacht weg, dan stuurt de broker zelf "offline".
client.will_set(TOPIC_STATUS, "offline", retain=True)
# Hier koppelen we onze functies aan de MQTT-gebeurtenissen.
client.on_connect = bij_verbinden
client.on_message = bij_bericht
client.connect()


# ---------------------------------------------------------------------
# 6. Hoofdlus: meten en versturen
# ---------------------------------------------------------------------
# Hier zorgen we dat de eerste meting meteen gebeurt (en niet pas na 5 s).
laatst_verstuurd = time.monotonic() - INTERVAL
laatste_oled_herstart = time.monotonic()

# De hoofdlus: deze blijft eindeloos draaien.
# We gebruiken time.monotonic() i.p.v. lange sleeps, zodat de Pico
# ondertussen kan blijven reageren op de knop en op MQTT-berichten.
while True:
    # Hier kijken we max. 1 seconde of er MQTT-berichten binnenkomen
    # (en houden we de verbinding met de broker in leven).
    client.loop(timeout=1)

    # Hier lezen we alle knopgebeurtenissen uit die sinds de vorige keer gebeurden.
    knop_gedrukt = False
    gebeurtenis = knoppen.events.get()
    while gebeurtenis:
        if gebeurtenis.pressed:          # alleen indrukken telt, loslaten niet
            knop_gedrukt = True
        gebeurtenis = knoppen.events.get()
    if knop_gedrukt:
        # Knop ingedrukt: melden via MQTT en een waterverzoek klaarzetten.
        print("Knop ingedrukt!")
        client.publish(TOPIC_KNOP, json.dumps({"bron": "knop"}))
        water_verzoek = (WATER_SECONDEN, "knop")

    # Hier doen we elke INTERVAL seconden een nieuwe meting.
    if time.monotonic() - laatst_verstuurd >= INTERVAL:
        meting = meet_alles()                 # alle sensoren uitlezen
        stemming = bepaal_stemming(meting)    # stemming van de plant bepalen
        meting["stemming"] = stemming         # stemming mee versturen
        laatste_meting = meting
        toon_op_oled(meting, stemming)        # op het scherm tonen
        toon_op_leds(meting, stemming)        # op de LEDs tonen
        tekst = json.dumps(meting)            # dictionary -> JSON-tekst
        client.publish(TOPIC_SENSORS, tekst)  # naar de broker sturen
        print("Verstuurd:", tekst)
        laatst_verstuurd = time.monotonic()

        # Hier beslissen we of de plant automatisch water krijgt. Dat gebeurt alleen als:
        #   - automatisch water geven aan staat,
        #   - de plant dorst heeft,
        #   - er nog geen ander waterverzoek klaarstaat,
        #   - het waterpeil bekend is (of er geen HC-SR04 is),
        #   - en de vorige watergift lang genoeg geleden is.
        if (AUTOMATISCH_WATER and stemming == "dorst" and water_verzoek is None
                and (meting.get("waterpeil") is not None or not sonar)
                and time.monotonic() - laatst_water >= WACHTTIJD_NA_WATER):
            water_verzoek = (WATER_SECONDEN, "automatisch")

    # Hier voeren we een waterverzoek uit (van knop, dashboard of automatisch).
    if water_verzoek:
        seconden, bron = water_verzoek
        water_verzoek = None               # verzoek wissen, zodat het maar één keer gebeurt
        water_geven(seconden, bron)
        laatst_verstuurd = 0               # meteen opnieuw meten, zodat het dashboard het effect ziet

    # Hier herstarten we het OLED-scherm elke OLED_HERSTART seconden,
    # voor het geval het door storing vastgelopen is.
    if time.monotonic() - laatste_oled_herstart >= OLED_HERSTART:
        herstart_oled()
        laatste_oled_herstart = time.monotonic()
