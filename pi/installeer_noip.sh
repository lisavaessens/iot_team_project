#!/usr/bin/env bash
# =============================================================================
# PlantPal - STAP 11: dashboard online bereikbaar via No-IP (dynamische DNS)
#
# Thuis krijgt je internetaansluiting een publiek IP-adres dat af en toe
# verandert. No-IP geeft je een vaste naam (bv. plantpal.ddns.net) die altijd
# naar dat adres wijst. De "DUC" (Dynamic Update Client) op de Pi meldt
# elke 5 minuten aan No-IP wat je huidige adres is.
#
# Vooraf (in de browser, zie uitleg):
#   - gratis account op www.noip.com
#   - een hostname aanmaken (bv. plantpal-lisa.ddns.net)
#   - liefst een "DDNS Key" aanmaken (gebruikersnaam + wachtwoord alleen
#     voor deze hostname)
#
# Gebruik (als gewone gebruiker, NIET met sudo ervoor):
#     cd ~/iot_team_project/pi
#     bash installeer_noip.sh
#
# Het script vraagt je hostname, gebruikersnaam en wachtwoord. Die komen
# alleen in /etc/default/noip-duc op de Pi (niet op GitHub).
# =============================================================================
set -euo pipefail

groen() { echo -e "\e[32m$*\e[0m"; }
rood()  { echo -e "\e[31m$*\e[0m"; }
stap()  { echo; echo -e "\e[1m== $* ==\e[0m"; }

if [[ $EUID -eq 0 ]]; then
    rood "Voer dit script uit ZONDER sudo: bash installeer_noip.sh"; exit 1
fi

# ---------------------------------------------------------------------------
stap "1/4 No-IP DUC installeren"
if command -v noip-duc >/dev/null 2>&1; then
    groen "noip-duc staat er al op: $(noip-duc --version 2>/dev/null || echo '?')"
else
    WERK="$(mktemp -d)"
    cd "$WERK"
    wget -q --content-disposition https://www.noip.com/download/linux/latest
    tar xf noip-duc_*.tar.gz
    ARCH="$(dpkg --print-architecture)"          # arm64 op een 64-bit Pi
    DEB="$(ls noip-duc_*/binaries/noip-duc_*_"$ARCH".deb | head -n 1)"
    [[ -f "$DEB" ]] || { rood "Geen pakket voor $ARCH gevonden."; exit 1; }
    sudo apt-get install -y "./$DEB"
    cd - >/dev/null
    rm -rf "$WERK"
    groen "noip-duc geinstalleerd."
fi

# ---------------------------------------------------------------------------
stap "2/4 Je No-IP-gegevens"
read -r -p "Hostname (bv. plantpal-lisa.ddns.net): " NOIP_HOST
read -r -p "Gebruikersnaam (DDNS Key of No-IP-login): " NOIP_USER
read -r -s -p "Wachtwoord (je ziet niets terwijl je typt): " NOIP_PASS; echo
[[ -n "$NOIP_HOST" && -n "$NOIP_USER" && -n "$NOIP_PASS" ]] || { rood "Niet alles ingevuld."; exit 1; }

sudo tee /etc/default/noip-duc > /dev/null <<EOF
NOIP_USERNAME=$NOIP_USER
NOIP_PASSWORD=$NOIP_PASS
NOIP_HOSTNAMES=$NOIP_HOST
EOF
sudo chmod 600 /etc/default/noip-duc
groen "Opgeslagen in /etc/default/noip-duc (alleen leesbaar voor root)."

# ---------------------------------------------------------------------------
stap "3/4 Achtergronddienst"
# Het pakket brengt meestal zelf een dienst mee; zo niet, dan maken we er een.
if ! systemctl cat noip-duc >/dev/null 2>&1; then
    sudo tee /etc/systemd/system/noip-duc.service > /dev/null <<'EOF'
[Unit]
Description=No-IP Dynamic Update Client
After=network-online.target
Wants=network-online.target

[Service]
EnvironmentFile=/etc/default/noip-duc
ExecStart=/usr/bin/noip-duc
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
EOF
fi
sudo systemctl daemon-reload
sudo systemctl enable noip-duc >/dev/null 2>&1
sudo systemctl restart noip-duc

# ---------------------------------------------------------------------------
stap "4/4 Controle"
sleep 8
if systemctl is-active --quiet noip-duc; then
    groen "No-IP DUC DRAAIT."
else
    rood "No-IP DUC draait NIET."
fi
sudo journalctl -u noip-duc -n 10 --no-pager -o cat || true
echo
echo "Publiek IP van deze aansluiting: $(curl -s https://api.ipify.org || echo '?')"
echo "Je hostname wijst naar:          $(getent hosts "$NOIP_HOST" | awk '{print $1}' || echo '?')"
echo "(Die twee moeten na enkele minuten gelijk zijn.)"
echo
echo "Volgende stap: port forwarding op je router: poort 3000 -> $(hostname -I | awk '{print $1}'):3000"
