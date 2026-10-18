# PlantPal - Raspberry Pi

Scripts om de Raspberry Pi klaar te maken als MQTT-broker voor de Pico.
Beide teamleden voeren exact dezelfde stappen uit op hun eigen Pi.

## Eenmalig: de Pi instellen

```bash
cd ~/iot_team_project
git pull
cd pi
bash setup_pi.sh
```

Niet `sudo bash setup_pi.sh` typen: het script vraagt zelf om sudo waar nodig.

Het script:
1. installeert Mosquitto (de MQTT-broker) en zet hem aan bij het opstarten;
2. maakt `config.env` aan met de MQTT-login (dit bestand gaat niet naar GitHub);
3. stelt Mosquitto zo in dat de Pico hem via wifi kan bereiken;
4. installeert `paho-mqtt` in je venv `~/env`;
5. zet `config.env` en `secrets.py` in `.gitignore`;
6. test of berichten heen en weer gaan.

Op het einde toont het het IP-adres dat je straks in de config van de Pico zet.

## Afspraken

- **Gebruik op beide Pi's dezelfde `MQTT_USER` en `MQTT_PASS`** in `config.env`.
  Dan werkt de Pico met de Pi van wie er ook mee bezig is; alleen het IP-adres verschilt.
- Iets veranderd in `config.env`? Voer `bash setup_pi.sh` gewoon opnieuw uit.
- Pico en Pi moeten op **hetzelfde netwerk** zitten (geen eduroam: gebruik een hotspot).

## Testen

```bash
bash test_mqtt.sh                 # test je eigen broker
bash test_mqtt.sh 192.168.1.42    # test een andere broker (bv. die van je teamgenoot)
```

Zelf meekijken naar alle PlantPal-berichten:

```bash
source config.env
mosquitto_sub -h localhost -u "$MQTT_USER" -P "$MQTT_PASS" -t "plantpal/#" -v
```

## Problemen

| Melding | Oplossing |
| --- | --- |
| `Mosquitto start niet` | Lees de meldingen die het script toont. Staat er "Address already in use", dan bevat een ander bestand in `/etc/mosquitto/conf.d/` ook een `listener`: hernoem het met `sudo mv bestand.conf bestand.conf.uit`. |
| `Connection refused: not authorised` | Verkeerde gebruiker of wachtwoord. Vergelijk met `config.env`. |
| Je oude oefenscripts uit de les werken niet meer | Die verbinden zonder login. Voeg `client.username_pw_set(USER, PASS)` toe, of laat `MQTT_USER` leeg in `config.env` en voer het script opnieuw uit. |
| Test via IP-adres mislukt, localhost lukt | Controleer of je op het juiste netwerk zit met `hostname -I`. |
