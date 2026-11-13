#!/usr/bin/env bash
# =============================================================================
# PlantPal - STAP 12: de AI-voorspelling installeren als achtergronddienst
#
# Gebruik (als gewone gebruiker, NIET met sudo ervoor):
#     cd ~/iot_team_project/pi
#     bash installeer_ai.sh
#
# Wat doet dit script?
#   1. Installeert numpy, scikit-learn, influxdb-client en paho-mqtt in ~/env.
#   2. Test het model met nepgegevens (--demo).
#   3. Maakt een systemd-dienst "plantpal-ai" die elke 5 minuten een
#      voorspelling maakt, ook na een herstart van de Pi.
#   4. Toont de eerste voorspelling.
# =============================================================================
set -euo pipefail

# Hier bepalen we de paden: de map, het AI-script, de venv en het systemd-bestand.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$SCRIPT_DIR/ai_voorspelling.py"
VENV="$HOME/env"
DIENST="plantpal-ai"
UNIT_FILE="/etc/systemd/system/$DIENST.service"

# Hulpfuncties om meldingen in kleur te tonen.
groen() { echo -e "\e[32m$*\e[0m"; }
rood()  { echo -e "\e[31m$*\e[0m"; }
stap()  { echo; echo -e "\e[1m== $* ==\e[0m"; }

# Hier controleren we dat het script niet met sudo gestart is
# en dat het AI-script, config.env en de venv bestaan.
if [[ $EUID -eq 0 ]]; then
    rood "Voer dit script uit ZONDER sudo: bash installeer_ai.sh"; exit 1
fi
[[ -f "$SCRIPT" ]] || { rood "ai_voorspelling.py niet gevonden in $SCRIPT_DIR"; exit 1; }
[[ -f "$SCRIPT_DIR/config.env" ]] || { rood "config.env niet gevonden in $SCRIPT_DIR"; exit 1; }
[[ -x "$VENV/bin/python3" ]] || { rood "Geen venv in $VENV (zie stap 3)"; exit 1; }

stap "1/4 Python-pakketten (dit kan enkele minuten duren)"
# Hier installeren we de bibliotheken: numpy (rekenen), scikit-learn (machine learning),
# influxdb-client (database) en paho-mqtt (MQTT).
"$VENV/bin/pip" install -q --upgrade numpy scikit-learn influxdb-client paho-mqtt
groen "OK"

stap "2/4 Het model testen met nepgegevens"
# Hier testen we het model eerst met nepgegevens, zonder database.
"$VENV/bin/python3" "$SCRIPT" --demo

stap "3/4 Achtergronddienst '$DIENST' aanmaken"
# Hier schrijven we het systemd-bestand voor de dienst plantpal-ai
# (start na netwerk, Mosquitto en InfluxDB, en herstart na een crash).
sudo tee "$UNIT_FILE" > /dev/null <<EOF
[Unit]
Description=PlantPal AI-voorspelling (InfluxDB -> model -> InfluxDB + MQTT)
After=network-online.target mosquitto.service influxdb.service
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
# Hier laten we systemd de dienst inlezen, aanzetten bij het opstarten en starten.
sudo systemctl daemon-reload
sudo systemctl enable "$DIENST" > /dev/null 2>&1
sudo systemctl restart "$DIENST"

stap "4/4 Controle"
# Hier wachten we even (eerste voorspelling) en controleren we of de dienst draait.
sleep 8
if systemctl is-active --quiet "$DIENST"; then
    groen "De AI-dienst DRAAIT. Eerste voorspelling:"
else
    rood "De AI-dienst draait NIET. Meldingen:"
fi
sudo journalctl -u "$DIENST" -n 5 --no-pager -o cat || true
echo
echo "Live meekijken:  sudo journalctl -u $DIENST -f     (stoppen: Ctrl+C)"
