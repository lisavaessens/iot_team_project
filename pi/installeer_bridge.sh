#!/usr/bin/env bash
# =============================================================================
# PlantPal - STAP 3: de brug MQTT -> InfluxDB installeren als achtergronddienst
#
# Gebruik (als gewone gebruiker, NIET met sudo ervoor):
#     cd ~/iot_team_project/pi
#     bash installeer_bridge.sh
#
# Wat doet dit script?
#   1. Controleert of config.env de InfluxDB-instellingen bevat en vult ze
#      zo nodig aan (het vraagt je token als het die nog niet kent).
#   2. Installeert paho-mqtt en influxdb-client in je venv (~/env).
#   3. Maakt een systemd-dienst "plantpal-bridge" die mqtt_naar_influx.py
#      op de achtergrond laat draaien: automatisch bij het opstarten van de
#      Pi, en opnieuw gestart als het script ooit crasht.
#   4. Toont of de dienst draait en de laatste meldingen.
#
# Je mag het script zo vaak uitvoeren als je wilt (bv. na een aanpassing
# in config.env of in mqtt_naar_influx.py).
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="$SCRIPT_DIR/config.env"
BRIDGE="$SCRIPT_DIR/mqtt_naar_influx.py"
VENV="$HOME/env"
DIENST="plantpal-bridge"
UNIT_FILE="/etc/systemd/system/$DIENST.service"

groen() { echo -e "\e[32m$*\e[0m"; }
geel()  { echo -e "\e[33m$*\e[0m"; }
rood()  { echo -e "\e[31m$*\e[0m"; }
stap()  { echo; echo -e "\e[1m== $* ==\e[0m"; }

if [[ $EUID -eq 0 ]]; then
    rood "Voer dit script uit ZONDER sudo: bash installeer_bridge.sh"
    exit 1
fi
if [[ ! -f "$BRIDGE" ]]; then
    rood "mqtt_naar_influx.py niet gevonden in $SCRIPT_DIR"
    exit 1
fi
if [[ ! -f "$CONFIG" ]]; then
    rood "config.env niet gevonden. Voer eerst 'bash setup_pi.sh' uit."
    exit 1
fi

# Hulpfunctie: geeft de waarde van NAAM uit config.env (leeg als ze ontbreekt)
lees() { grep -E "^$1=" "$CONFIG" | tail -n1 | cut -d= -f2- | tr -d '"' | tr -d "'" || true; }
# Hulpfunctie: voegt NAAM=waarde toe aan config.env als NAAM nog niet bestaat
voeg_toe() {
    if [[ -z "$(lees "$1")" ]]; then
        echo "$1=$2" >> "$CONFIG"
        geel "  toegevoegd aan config.env: $1=$2"
    fi
}

# --- 1. Instellingen -------------------------------------------------------
stap "1/4 InfluxDB-instellingen in config.env"
if ! grep -q "InfluxDB" "$CONFIG"; then
    printf '\n# --- InfluxDB (stap 3) ---\n' >> "$CONFIG"
fi
voeg_toe INFLUX_URL "http://localhost:8086"
voeg_toe INFLUX_ORG "PlantPal"
voeg_toe INFLUX_BUCKET "plantpal_metingen"

if [[ -z "$(lees INFLUX_TOKEN)" ]]; then
    if [[ -n "${INFLUXDB_TOKEN:-}" ]]; then
        echo "INFLUX_TOKEN=$INFLUXDB_TOKEN" >> "$CONFIG"
        geel "  token overgenomen uit je terminal (INFLUXDB_TOKEN)"
    else
        echo "Ik ken je InfluxDB-token nog niet."
        echo "Maak er een aan in de browser: http://<IP-van-je-Pi>:8086"
        echo "  Load Data -> API Tokens -> Generate API Token -> All Access"
        read -r -p "Plak hier je token en druk op Enter: " TOKEN
        TOKEN="$(echo "$TOKEN" | tr -d '[:space:]"'"'")"
        if [[ -z "$TOKEN" ]]; then
            rood "Geen token ingegeven. Gestopt."
            exit 1
        fi
        echo "INFLUX_TOKEN=$TOKEN" >> "$CONFIG"
    fi
fi
groen "config.env bevat alle InfluxDB-instellingen:"
echo "  INFLUX_URL=$(lees INFLUX_URL)"
echo "  INFLUX_ORG=$(lees INFLUX_ORG)"
echo "  INFLUX_BUCKET=$(lees INFLUX_BUCKET)"
echo "  INFLUX_TOKEN=$(lees INFLUX_TOKEN | cut -c1-6)...  (verborgen)"

# --- 2. Python-pakketten ---------------------------------------------------
stap "2/4 Python-pakketten in $VENV"
if [[ ! -x "$VENV/bin/python3" ]]; then
    rood "Geen venv gevonden in $VENV. Maak er een met: python3 -m venv ~/env --system-site-packages"
    exit 1
fi
"$VENV/bin/pip" install -q --upgrade paho-mqtt influxdb-client
groen "paho-mqtt en influxdb-client zijn geinstalleerd."

# --- 3. systemd-dienst -----------------------------------------------------
stap "3/4 Achtergronddienst '$DIENST' aanmaken"
sudo tee "$UNIT_FILE" > /dev/null <<EOF
[Unit]
Description=PlantPal brug MQTT naar InfluxDB
After=network-online.target mosquitto.service influxdb.service
Wants=network-online.target

[Service]
User=$USER
WorkingDirectory=$SCRIPT_DIR
ExecStart=$VENV/bin/python3 -u $BRIDGE
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable "$DIENST" > /dev/null 2>&1
sudo systemctl restart "$DIENST"
groen "Dienst aangemaakt en gestart. Hij start voortaan vanzelf bij het opstarten."

# --- 4. Controle -----------------------------------------------------------
stap "4/4 Controle"
sleep 4
if systemctl is-active --quiet "$DIENST"; then
    groen "De dienst DRAAIT."
else
    rood "De dienst draait NIET. Bekijk de meldingen hieronder."
fi
echo
echo "Laatste meldingen van de brug:"
sudo journalctl -u "$DIENST" -n 15 --no-pager -o cat || true
echo
echo "Live meekijken:   sudo journalctl -u $DIENST -f      (stoppen: Ctrl+C)"
echo "Stoppen:          sudo systemctl stop $DIENST"
echo "Opnieuw starten:  sudo systemctl restart $DIENST"
