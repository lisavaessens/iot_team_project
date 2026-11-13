#!/usr/bin/env bash
# =====================================================================
# PlantPal - STAP 10: Grafana-dashboard op de Pi
#
# Wat doet dit script?
#   1. Installeert Grafana (als het er nog niet op staat).
#   2. Maakt automatisch de verbinding (datasource) met InfluxDB.
#      De gegevens (org, bucket, token) komen uit ../config.env.
#   3. Zet het dashboard "PlantPal" klaar (plantpal_dashboard.json).
#   4. Start Grafana, ook na elke herstart van de Pi.
#
# Zo krijgt iedereen in het team exact hetzelfde dashboard.
#
# Gebruik (op de Pi):
#   cd ~/iot_team_project/pi/grafana
#   bash installeer_grafana.sh
#
# Daarna: http://<IP-van-de-Pi>:3000  (eerste keer: admin / admin)
# Je mag het script gerust opnieuw uitvoeren, bv. na een aanpassing
# aan plantpal_dashboard.json.
# =====================================================================
set -euo pipefail

# Hier bepalen we de paden: deze map, config.env (een map hoger) en het dashboardbestand.
MAP="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="$MAP/../config.env"
DASHBOARD="$MAP/plantpal_dashboard.json"

# Hulpfunctie die één waarde (NAAM=...) uit config.env leest.
lees() {   # lees NAAM -> waarde uit config.env (zonder aanhalingstekens)
    grep -E "^$1=" "$CONFIG" | tail -n 1 | cut -d= -f2- | tr -d '"'"'" || true
}

echo "=== PlantPal: Grafana installeren ==="

# Hier controleren we dat config.env en het dashboard bestaan.
[[ -f "$CONFIG" ]]    || { echo "FOUT: $CONFIG niet gevonden."; exit 1; }
[[ -f "$DASHBOARD" ]] || { echo "FOUT: $DASHBOARD niet gevonden."; exit 1; }

# Hier lezen we de InfluxDB-gegevens in (met standaardwaarden).
# Zonder token kan Grafana de database niet lezen, dus dan stoppen we.
INFLUX_URL="$(lees INFLUX_URL)";       INFLUX_URL="${INFLUX_URL:-http://localhost:8086}"
INFLUX_ORG="$(lees INFLUX_ORG)";       INFLUX_ORG="${INFLUX_ORG:-PlantPal}"
INFLUX_BUCKET="$(lees INFLUX_BUCKET)"; INFLUX_BUCKET="${INFLUX_BUCKET:-plantpal_metingen}"
INFLUX_TOKEN="$(lees INFLUX_TOKEN)"
[[ -n "$INFLUX_TOKEN" ]] || { echo "FOUT: geen INFLUX_TOKEN in config.env (zie stap 3)."; exit 1; }

# ---------------------------------------------------------------------
# 1. Grafana installeren (alleen als het er nog niet op staat)
# ---------------------------------------------------------------------
# Hier kijken we of Grafana al geinstalleerd is. Zo niet:
# we voegen de officiele Grafana-pakketbron toe (met de GPG-sleutel om te
# controleren dat de pakketten echt van Grafana komen) en installeren Grafana.
if dpkg -s grafana >/dev/null 2>&1; then
    echo "[1/4] Grafana staat er al op: $(dpkg -s grafana | grep '^Version' )"
else
    echo "[1/4] Grafana installeren (dit duurt enkele minuten)..."
    sudo apt-get update
    sudo apt-get install -y apt-transport-https wget gpg
    sudo mkdir -p /etc/apt/keyrings
    wget -q -O - https://apt.grafana.com/gpg.key | gpg --dearmor | \
        sudo tee /etc/apt/keyrings/grafana.gpg > /dev/null
    echo "deb [signed-by=/etc/apt/keyrings/grafana.gpg] https://apt.grafana.com stable main" | \
        sudo tee /etc/apt/sources.list.d/grafana.list > /dev/null
    sudo apt-get update
    sudo apt-get install -y grafana
fi

# ---------------------------------------------------------------------
# 2. Datasource: de verbinding met InfluxDB
#    (dit bestand bevat de token; het staat alleen op de Pi, niet op GitHub)
# ---------------------------------------------------------------------
echo "[2/4] Verbinding met InfluxDB instellen (org $INFLUX_ORG, bucket $INFLUX_BUCKET)"
# Hier schrijven we de datasource: dit vertelt Grafana waar InfluxDB staat
# en met welke token, org en bucket het moet lezen (querytaal Flux).
sudo tee /etc/grafana/provisioning/datasources/plantpal.yaml > /dev/null <<EOF
apiVersion: 1
datasources:
  - name: PlantPal InfluxDB
    uid: plantpal-influx
    type: influxdb
    access: proxy
    url: $INFLUX_URL
    jsonData:
      version: Flux
      organization: $INFLUX_ORG
      defaultBucket: $INFLUX_BUCKET
    secureJsonData:
      token: $INFLUX_TOKEN
    editable: true
EOF
# Hier beperken we de rechten: alleen root en Grafana mogen dit bestand (met token) lezen.
sudo chown root:grafana /etc/grafana/provisioning/datasources/plantpal.yaml
sudo chmod 640 /etc/grafana/provisioning/datasources/plantpal.yaml

# ---------------------------------------------------------------------
# 3. Dashboard klaarzetten
# ---------------------------------------------------------------------
echo "[3/4] Dashboard 'PlantPal' klaarzetten"
# Hier kopieren we het dashboard naar de map van Grafana.
# sed vervangt __BUCKET__ in het JSON-bestand door de echte bucketnaam.
sudo mkdir -p /var/lib/grafana/dashboards/plantpal
sed "s/__BUCKET__/$INFLUX_BUCKET/g" "$DASHBOARD" | \
    sudo tee /var/lib/grafana/dashboards/plantpal/plantpal_dashboard.json > /dev/null
sudo chown -R grafana:grafana /var/lib/grafana/dashboards

# Hier vertellen we Grafana dat het dashboards uit die map moet laden
# (elke 30 s kijkt het of het bestand veranderd is).
sudo tee /etc/grafana/provisioning/dashboards/plantpal.yaml > /dev/null <<'EOF'
apiVersion: 1
providers:
  - name: PlantPal
    folder: PlantPal
    type: file
    allowUiUpdates: true
    updateIntervalSeconds: 30
    options:
      path: /var/lib/grafana/dashboards/plantpal
EOF

# ---------------------------------------------------------------------
# 4. Grafana (her)starten en automatisch laten opstarten
# ---------------------------------------------------------------------
echo "[4/4] Grafana starten"
# Hier zetten we Grafana aan bij het opstarten en herstarten we het.
sudo systemctl daemon-reload
sudo systemctl enable grafana-server >/dev/null 2>&1
sudo systemctl restart grafana-server

# Even wachten tot Grafana antwoordt
# Hier wachten we (max. 60 s) tot Grafana antwoordt op poort 3000.
for i in $(seq 1 30); do
    if curl -s -o /dev/null http://localhost:3000/api/health; then break; fi
    sleep 2
done

# Hier tonen we het adres van het dashboard, of hoe je de fout kunt bekijken.
IP="$(hostname -I | awk '{print $1}')"
echo
if systemctl is-active --quiet grafana-server; then
    echo "KLAAR! Open in je browser:  http://$IP:3000"
    echo "  Eerste keer inloggen: admin / admin (kies daarna een nieuw wachtwoord)"
    echo "  Dashboard: menu Dashboards -> map PlantPal -> PlantPal"
else
    echo "LET OP: Grafana draait niet. Bekijk de fout met:"
    echo "  sudo journalctl -u grafana-server -n 30 --no-pager"
fi
