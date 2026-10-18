#!/usr/bin/env bash
# =============================================================================
# PlantPal - Raspberry Pi klaarmaken als MQTT-broker (stap 1)
#
# Gebruik (als gewone gebruiker, NIET met sudo ervoor):
#     cd ~/iot_team_project/pi
#     bash setup_pi.sh
#
# Wat doet dit script?
#   1. Mosquitto (broker) en mosquitto-clients installeren
#   2. config.env aanmaken uit config.env.example (als die nog niet bestaat)
#   3. Mosquitto instellen zodat de Pico hem via wifi kan bereiken
#   4. paho-mqtt installeren in je venv (~/env)
#   5. config.env in .gitignore zetten
#   6. Testen of alles werkt (test_mqtt.sh)
#
# Je mag het script zo vaak uitvoeren als je wilt: het geeft telkens
# hetzelfde resultaat. Pas je config.env aan, voer het dan gewoon opnieuw uit.
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF_FILE="/etc/mosquitto/conf.d/plantpal.conf"
PASS_FILE="/etc/mosquitto/plantpal_passwd"
VENV="$HOME/env"

groen() { echo -e "\e[32m$*\e[0m"; }
geel()  { echo -e "\e[33m$*\e[0m"; }
rood()  { echo -e "\e[31m$*\e[0m"; }
stap()  { echo; echo -e "\e[1m== $* ==\e[0m"; }

if [[ $EUID -eq 0 ]]; then
    rood "Voer dit script uit ZONDER sudo: bash setup_pi.sh"
    rood "(het vraagt zelf om je wachtwoord waar nodig)"
    exit 1
fi

# --- 1. Pakketten -----------------------------------------------------------
stap "1/6 Mosquitto installeren"
sudo apt-get update -qq
sudo apt-get install -y -qq mosquitto mosquitto-clients
sudo systemctl enable mosquitto >/dev/null
groen "Mosquitto is geinstalleerd."

# --- 2. config.env ----------------------------------------------------------
stap "2/6 Instellingen (config.env)"
if [[ ! -f "$SCRIPT_DIR/config.env" ]]; then
    cp "$SCRIPT_DIR/config.env.example" "$SCRIPT_DIR/config.env"
    geel "config.env aangemaakt met standaardwaarden."
    geel "Wil je een ander wachtwoord? Pas pi/config.env aan en voer dit script opnieuw uit."
else
    groen "Bestaande config.env gevonden, die wordt gebruikt."
fi
# shellcheck disable=SC1091
source "$SCRIPT_DIR/config.env"
MQTT_PORT="${MQTT_PORT:-1883}"
MQTT_USER="${MQTT_USER:-}"
MQTT_PASS="${MQTT_PASS:-}"

# --- 3. Mosquitto configureren ----------------------------------------------
stap "3/6 Mosquitto instellen"

# Andere config-bestanden met een eigen 'listener' (bv. uit de lessen)
# botsen met de onze: dan start Mosquitto niet.
for f in /etc/mosquitto/conf.d/*.conf; do
    [[ -e "$f" && "$f" != "$CONF_FILE" ]] || continue
    if grep -qE '^\s*(listener|port)\b' "$f"; then
        geel "Let op: $f bevat ook een 'listener'. Dat kan botsen."
        geel "Hernoem het als Mosquitto niet start: sudo mv $f $f.uit"
    fi
done

if [[ -n "$MQTT_USER" ]]; then
    sudo tee "$CONF_FILE" >/dev/null <<EOF
# Aangemaakt door setup_pi.sh (PlantPal) - niet met de hand aanpassen,
# pas pi/config.env aan en voer setup_pi.sh opnieuw uit.
per_listener_settings false
listener $MQTT_PORT
allow_anonymous false
password_file $PASS_FILE
EOF
    sudo rm -f "$PASS_FILE"
    sudo touch "$PASS_FILE"
    sudo chmod 600 "$PASS_FILE"
    sudo mosquitto_passwd -b "$PASS_FILE" "$MQTT_USER" "$MQTT_PASS"
    # Mosquitto leest dit bestand als gebruiker 'mosquitto'
    sudo chown mosquitto:mosquitto "$PASS_FILE"
    groen "Broker met login: gebruiker '$MQTT_USER'."
else
    sudo tee "$CONF_FILE" >/dev/null <<EOF
# Aangemaakt door setup_pi.sh (PlantPal) - niet met de hand aanpassen,
# pas pi/config.env aan en voer setup_pi.sh opnieuw uit.
per_listener_settings false
listener $MQTT_PORT
allow_anonymous true
EOF
    sudo rm -f "$PASS_FILE"
    geel "Broker ZONDER login (MQTT_USER is leeg)."
fi

sudo systemctl restart mosquitto
sleep 1
if systemctl is-active --quiet mosquitto; then
    groen "Mosquitto draait op poort $MQTT_PORT."
else
    rood "Mosquitto start niet. Laatste meldingen:"
    sudo journalctl -u mosquitto -n 15 --no-pager
    exit 1
fi

# --- 4. Python-bibliotheek --------------------------------------------------
stap "4/6 paho-mqtt in je venv"
if [[ ! -d "$VENV" ]]; then
    geel "Geen venv gevonden in $VENV, ik maak er een aan."
    python3 -m venv "$VENV" --system-site-packages
fi
"$VENV/bin/pip" install -q --upgrade paho-mqtt
groen "paho-mqtt staat in $VENV."

# --- 5. .gitignore ----------------------------------------------------------
stap "5/6 Wachtwoorden uit Git houden"
if REPO_ROOT="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null)"; then
    touch "$REPO_ROOT/.gitignore"
    for regel in "config.env" "secrets.py" "__pycache__/"; do
        grep -qxF "$regel" "$REPO_ROOT/.gitignore" || echo "$regel" >> "$REPO_ROOT/.gitignore"
    done
    groen ".gitignore bijgewerkt (config.env, secrets.py)."
else
    geel "Dit is geen Git-map; .gitignore overgeslagen."
fi

# --- 6. Test ----------------------------------------------------------------
stap "6/6 Test"
bash "$SCRIPT_DIR/test_mqtt.sh"

IP="$(hostname -I | awk '{print $1}')"
echo
groen "Klaar! Deze gegevens heeft de Pico straks nodig:"
echo "   MQTT_BROKER = \"$IP\"   (of \"$(hostname).local\")"
echo "   MQTT_PORT   = $MQTT_PORT"
[[ -n "$MQTT_USER" ]] && echo "   MQTT_USER   = \"$MQTT_USER\""
echo
geel "Het IP-adres kan veranderen als je van netwerk wisselt (bv. andere hotspot)."
