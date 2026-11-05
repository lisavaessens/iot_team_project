

import os
import time
import json         
import board
import busio       
import digitalio
import pwmio        
import keypad       
import wifi
import socketpool
import adafruit_minimqtt.adafruit_minimqtt as MQTT


# ---------------------------------------------------------------------
# 1. Instellingen
# ---------------------------------------------------------------------

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

TOPIC_SENSORS = "plantpal/sensors"   
TOPIC_LED = "plantpal/cmd/led"       
TOPIC_STATUS = "plantpal/status"     
TOPIC_KNOP = "plantpal/event/knop"   
TOPIC_CMD_WATER = "plantpal/cmd/water"      
TOPIC_EVENT_WATER = "plantpal/event/water"  

INTERVAL = 5   


I2C_SDA = board.GP4    # fysieke pin 6
I2C_SCL = board.GP5    # fysieke pin 7


VOCHT_DROOG = 300
VOCHT_NAT = 1000


HCSR04_AANGESLOTEN = True


HCSR04_TRIG = board.GP20   # fysieke pin 26
HCSR04_ECHO = board.GP21   # fysieke pin 27


RESERVOIR_LEEG_CM = 7.0
RESERVOIR_VOL_CM = 3.0     


SPI_SCK = board.GP18    # fysieke pin 24 -> MCP3008 pin 13 (CLK)
SPI_MOSI = board.GP19   # fysieke pin 25 -> MCP3008 pin 11 (DIN)
SPI_MISO = board.GP16   # fysieke pin 21 -> MCP3008 pin 12 (DOUT)
MCP_CS = board.GP17     # fysieke pin 22 -> MCP3008 pin 10 (CS)


OLED_SCK = board.GP10   # fysieke pin 14 -> OLED "SCL" (= klok, ook D0/CLK)
OLED_MOSI = board.GP11  # fysieke pin 15 -> OLED "SDA" (= data, ook D1/DATA)
OLED_DC = board.GP14    # fysieke pin 19 -> OLED "DC"
OLED_RST = board.GP15   # fysieke pin 20 -> OLED "RES" / "RST"

OLED_CS = None

OLED_MODUS3 = False


RGB_ROOD = board.GP6    # fysieke pin 9
RGB_GROEN = board.GP7   # fysieke pin 10
RGB_BLAUW = board.GP8   # fysieke pin 11

RGB_COMMON_ANODE = False


LED_ROOD = board.GP9    # fysieke pin 12


KNOP = board.GP12       # fysieke pin 16


RELAIS_PIN = board.GP22    # fysieke pin 29 -> "IN" van de relaismodule

RELAIS_ACTIEF_LAAG = True


AUTOMATISCH_WATER = False  
WATER_SECONDEN = 3         
MAX_WATER_SECONDEN = 10    
WACHTTIJD_NA_WATER = 60    
                        

OLED_HERSTART = 30
MIN_WATERPEIL = 10         


LDR_DONKER = 20         
LDR_LICHT = 80          
MIN_HELDERHEID = 0.03   


KLEUREN = {
    "blij": (0, 1, 0),             # groen
    "dorst": (1, 0, 0),            # rood
    "te_warm": (1, 0.3, 0),        # oranje
    "slapen": (0, 0, 1),           # blauw
    "reservoir_leeg": (1, 0, 1),   # paars
}


TE_WARM_C = 30          
DONKER_LUX = 5          


# ---------------------------------------------------------------------
# 2. LED op de Pico 
# ---------------------------------------------------------------------
led = digitalio.DigitalInOut(board.LED)
led.direction = digitalio.Direction.OUTPUT
led.value = False


# ---------------------------------------------------------------------
# 3. De I2C-bus starten en kijken welke sensoren er hangen
# ---------------------------------------------------------------------
try:
    i2c = busio.I2C(I2C_SCL, I2C_SDA)   
    
    while not i2c.try_lock():         
        pass
    gevonden = [hex(adres) for adres in i2c.scan()]
    i2c.unlock()                       
except RuntimeError as fout:

    print("I2C WERKT NIET:", fout)
    print("  -> kijk SDA (GP4, pin 6), SCL (GP5, pin 7), 3,3 V en GND na")
    i2c = None
    gevonden = []
print("I2C-adressen gevonden:", gevonden)



def start_sensor(naam, maak):

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

    import adafruit_mcp3xxx.mcp3008 as MCP
    from adafruit_mcp3xxx.analog_in import AnalogIn
    spi = busio.SPI(clock=SPI_SCK, MOSI=SPI_MOSI, MISO=SPI_MISO)

    cs = digitalio.DigitalInOut(MCP_CS)
    mcp = MCP.MCP3008(spi, cs)

    kanaal_ldr = AnalogIn(mcp, getattr(MCP, "P0", 0))
    kanaal_pot = AnalogIn(mcp, getattr(MCP, "P1", 1))
    return (kanaal_ldr, kanaal_pot)



adc = start_sensor("MCP3008 (LDR + potmeter)", maak_mcp3008)


def naar_procent_adc(kanaal):

    return kanaal.value / 65535 * 100


if HCSR04_AANGESLOTEN:
    sonar = start_sensor("HC-SR04 (waterpeil)", maak_hcsr04)
else:
    sonar = None
    print("UIT  ", "HC-SR04 (waterpeil) - staat uit in de instellingen")


def naar_procent(ruw):

    procent = (ruw - VOCHT_DROOG) / (VOCHT_NAT - VOCHT_DROOG) * 100
    return max(0, min(100, procent))


def waterpeil_procent(afstand):

    procent = (RESERVOIR_LEEG_CM - afstand) / (RESERVOIR_LEEG_CM - RESERVOIR_VOL_CM) * 100
    return max(0, min(100, procent))


def meet_alles():

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

            laatste_afstanden.append(afstand)
            if len(laatste_afstanden) > 3:
                laatste_afstanden.pop(0)
            afstand = sum(laatste_afstanden) / len(laatste_afstanden)
            meting["afstand_water_cm"] = round(afstand, 1)
            meting["waterpeil"] = round(waterpeil_procent(afstand), 1)
    return meting


laatste_afstanden = []   


def meet_afstand():

    waarden = []
    for _ in range(7):
        try:
            afstand = sonar.distance
            if 2 <= afstand <= 400:          
                waarden.append(afstand)
        except RuntimeError:
            pass                            
        time.sleep(0.06)                     
    if len(waarden) < 4:
        return None
    waarden.sort()
    return waarden[len(waarden) // 2]


def bepaal_stemming(meting):

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
oled_spi = None       
oled_groep = None     
oled_regels = None


def maak_oled():

    global oled_spi, oled_groep, oled_regels
    import displayio
    import terminalio                       
    from adafruit_display_text import label
    import adafruit_displayio_ssd1306
    try:
        from fourwire import FourWire        
    except ImportError:
        from displayio import FourWire       

    displayio.release_displays()            
    if oled_spi is not None:
        try:
            oled_spi.deinit()                
        except Exception:
            pass
    oled_spi = busio.SPI(clock=OLED_SCK, MOSI=OLED_MOSI)

    modus = 1 if OLED_MODUS3 else 0
    bus = FourWire(oled_spi, command=OLED_DC, chip_select=OLED_CS,
                   reset=OLED_RST, baudrate=1000000,
                   polarity=modus, phase=modus)
    scherm = adafruit_displayio_ssd1306.SSD1306(bus, width=128, height=64)

    if oled_groep is None:

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

rgb = [pwmio.PWMOut(pin, frequency=1000, duty_cycle=0)
       for pin in (RGB_ROOD, RGB_GROEN, RGB_BLAUW)]

rode_led = digitalio.DigitalInOut(LED_ROOD)
rode_led.direction = digitalio.Direction.OUTPUT
rode_led.value = False


knoppen = keypad.Keys((KNOP,), value_when_pressed=False, pull=True)


def zet_rgb(rood, groen, blauw, helderheid=1.0):

    for uitgang, waarde in zip(rgb, (rood, groen, blauw)):
        duty = int(waarde * helderheid * 65535)
        if RGB_COMMON_ANODE:
            duty = 65535 - duty    
        uitgang.duty_cycle = duty


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
    zet_rgb(0, 0, 0)
    print("LED-test klaar")


def helderheid_uit_ldr(ldr):

    deel = (ldr - LDR_DONKER) / (LDR_LICHT - LDR_DONKER)
    deel = max(0.0, min(1.0, deel))
    return max(MIN_HELDERHEID, deel ** 2.2)


def toon_op_leds(meting, stemming):

    kleur = KLEUREN.get(stemming, (1, 1, 1))
    helderheid = helderheid_uit_ldr(meting.get("ldr", 100))
    print("RGB-helderheid: {:.0f} %".format(helderheid * 100))
    zet_rgb(*kleur, helderheid=helderheid)

    rode_led.value = stemming in ("dorst", "reservoir_leeg")


led_test()


# ---------------------------------------------------------------------
# 3d. Relais en pomp
# ---------------------------------------------------------------------
relais = digitalio.DigitalInOut(RELAIS_PIN)


def pomp(aan):

    if RELAIS_ACTIEF_LAAG:
        if aan:
            relais.switch_to_output(value=False)    
        else:

            relais.switch_to_input()
    else:
        relais.switch_to_output(value=aan)


pomp(False)                 


def relais_test():
    print("Relais-test: je hoort nu 2 klikjes (aan en uit)")
    pomp(True)
    time.sleep(0.5)
    pomp(False)
    time.sleep(0.2)
    herstart_oled()         


relais_test()

laatste_meting = {}                        
laatst_water = -WACHTTIJD_NA_WATER         
water_verzoek = None                      


def water_geven(seconden, bron):

    global laatst_water
    seconden = max(0, min(MAX_WATER_SECONDEN, seconden))
    peil = laatste_meting.get("waterpeil")
    if peil is None and sonar:

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
    zet_rgb(0, 0.4, 1)                      
    pomp(True)
    time.sleep(seconden)
    pomp(False)
    time.sleep(0.2)
    herstart_oled()                        
    laatst_water = time.monotonic()
    client.publish(TOPIC_EVENT_WATER, json.dumps(
        {"bron": bron, "seconden": seconden}))
    print("Klaar met water geven")


aantal_metingen = 0


def toon_op_oled(meting, stemming):

    if not oled:
        return
    def waarde(naam, opmaak):
        return opmaak.format(meting[naam]) if naam in meting else "?"

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
laatst_verstuurd = time.monotonic() - INTERVAL   
laatste_oled_herstart = time.monotonic()

while True:
    client.loop(timeout=1)


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
        meting["stemming"] = stemming       
        laatste_meting = meting
        toon_op_oled(meting, stemming)
        toon_op_leds(meting, stemming)
        tekst = json.dumps(meting)         
        client.publish(TOPIC_SENSORS, tekst)
        print("Verstuurd:", tekst)
        laatst_verstuurd = time.monotonic()


        if (AUTOMATISCH_WATER and stemming == "dorst" and water_verzoek is None
                and (meting.get("waterpeil") is not None or not sonar)
                and time.monotonic() - laatst_water >= WACHTTIJD_NA_WATER):
            water_verzoek = (WATER_SECONDEN, "automatisch")


    if water_verzoek:
        seconden, bron = water_verzoek
        water_verzoek = None
        water_geven(seconden, bron)
        laatst_verstuurd = 0               


    if time.monotonic() - laatste_oled_herstart >= OLED_HERSTART:
        herstart_oled()
        laatste_oled_herstart = time.monotonic()
