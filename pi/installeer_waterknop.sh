#!/usr/bin/env bash
# =============================================================================
# PlantPal - STAP 10b: de waterknop installeren als achtergronddienst
#
# Gebruik (als gewone gebruiker, NIET met sudo ervoor):
#     cd ~/iot_team_project/pi
#     bash installeer_waterknop.sh
#
# Wat doet dit script?
#   1. Controleert of water_knop.py en config.env er zijn.
#   2. Maakt een systemd-dienst "plantpal-waterknop" die water_knop.py op de
#      achtergrond laat draaien (poort 8080), ook na een herstart van de Pi.
#   3. Test of de knoppenpagina antwoordt.
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$SCRIPT_DIR/water_knop.py"
VENV="$HOME/env"
DIENST="plantpal-waterknop"
UNIT_FILE="/etc/systemd/system/$DIENST.service"

groen() { echo -e "\e[32m$*\e[0m"; }
rood()  { echo -e "\e[31m$*\e[0m"; }
stap()  { echo; echo -e "\e[1m== $* ==\e[0m"; }

if [[ $EUID -eq 0 ]]; then
    rood "Voer dit script uit ZONDER sudo: bash installeer_waterknop.sh"; exit 1
fi
[[ -f "$SCRIPT" ]] || { rood "water_knop.py niet gevonden in $SCRIPT_DIR"; exit 1; }
[[ -f "$SCRIPT_DIR/config.env" ]] || { rood "config.env niet gevonden in $SCRIPT_DIR"; exit 1; }
[[ -x "$VENV/bin/python3" ]] || { rood "Geen venv in $VENV (zie stap 3)"; exit 1; }

stap "1/3 paho-mqtt controleren"
"$VENV/bin/pip" install -q --upgrade paho-mqtt
groen "OK"

stap "2/3 Achtergronddienst '$DIENST' aanmaken"
sudo tee "$UNIT_FILE" > /dev/null <<EOF
[Unit]
Description=PlantPal waterknop (web -> MQTT)
After=network-online.target mosquitto.service
Wants=network-online.target

[Service]
User=$USER
WorkingDirectory=$SCRIPT_DIR
ExecStart=$VENV/bin/python3 -u $SCRIPT
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable "$DIENST" > /dev/null 2>&1
sudo systemctl restart "$DIENST"

stap "3/3 Controle"
sleep 3
IP="$(hostname -I | awk '{print $1}')"
if curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/ | grep -q 200; then
    groen "De waterknop DRAAIT."
    echo "  Knoppenpagina:  http://$IP:8080   of   http://$(hostname).local:8080"
else
    rood "De waterknop antwoordt niet. Meldingen:"
    sudo journalctl -u "$DIENST" -n 15 --no-pager -o cat || true
fi
