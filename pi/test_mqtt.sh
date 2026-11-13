#!/usr/bin/env bash
# =============================================================================
# PlantPal - test of de MQTT-broker werkt
#
# Gebruik:  bash test_mqtt.sh            (test de broker op deze Pi)
#           bash test_mqtt.sh 192.168.1.42   (test een andere broker, bv. die
#                                             van je teamgenoot)
#
# Het script stuurt een bericht en controleert of het terugkomt, eerst via
# localhost en dan via het netwerk-IP (de weg die de Pico straks neemt).
# =============================================================================
set -uo pipefail

# Hier zoeken we config.env op in dezelfde map en lezen we de MQTT-instellingen in.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ ! -f "$SCRIPT_DIR/config.env" ]]; then
    echo "Geen config.env gevonden. Voer eerst setup_pi.sh uit."
    exit 1
fi
# shellcheck disable=SC1091
source "$SCRIPT_DIR/config.env"
MQTT_PORT="${MQTT_PORT:-1883}"

# Hier maken we de login-opties klaar (-u gebruiker -P wachtwoord), alleen als er een gebruiker is.
AUTH=()
[[ -n "${MQTT_USER:-}" ]] && AUTH=(-u "$MQTT_USER" -P "$MQTT_PASS")

# Hulpfuncties om meldingen in kleur te tonen.
groen() { echo -e "\e[32m$*\e[0m"; }
rood()  { echo -e "\e[31m$*\e[0m"; }

# Hier tellen we hoeveel tests er mislukken.
FOUTEN=0

# Deze functie test één broker (adres = $1):
# we abonneren ons op een testtopic, sturen er een bericht op
# en kijken of hetzelfde bericht terugkomt.
test_host() {
    local host="$1"
    local topic="plantpal/test/$$"
    local bericht="hallo plant $(date +%H:%M:%S)"

    # Luisteren op de achtergrond: stop na 1 bericht of na 5 seconden
    # Hier luisteren we op de achtergrond (&) naar het testtopic.
    mosquitto_sub -h "$host" -p "$MQTT_PORT" "${AUTH[@]}" \
        -t "$topic" -C 1 -W 5 > /tmp/plantpal_test_$$ 2>&1 &
    local pid=$!
    sleep 0.5
    # Hier sturen we het testbericht en wachten we tot mosquitto_sub klaar is.
    mosquitto_pub -h "$host" -p "$MQTT_PORT" "${AUTH[@]}" -t "$topic" -m "$bericht" 2>/dev/null
    wait "$pid"

    # Hier controleren we of het ontvangen bericht hetzelfde is als wat we stuurden.
    if grep -qxF "$bericht" /tmp/plantpal_test_$$; then
        groen "  OK   $host  ->  '$bericht' ontvangen"
    else
        rood  "  FOUT $host  ->  niets ontvangen"
        sed 's/^/       /' /tmp/plantpal_test_$$
        FOUTEN=$((FOUTEN + 1))
    fi
    rm -f /tmp/plantpal_test_$$
}

# Hier kiezen we wat we testen: een opgegeven IP-adres,
# of anders localhost én het netwerk-IP van deze Pi (de weg die de Pico neemt).
if [[ $# -ge 1 ]]; then
    echo "Test van broker $1:"
    test_host "$1"
else
    IP="$(hostname -I | awk '{print $1}')"
    echo "Test via localhost en via het netwerk ($IP):"
    test_host "localhost"
    [[ -n "$IP" ]] && test_host "$IP"

    # Met login: controleer ook dat een fout wachtwoord geweigerd wordt
    # Hier testen we ook de beveiliging: een fout wachtwoord MOET geweigerd worden.
    if [[ -n "${MQTT_USER:-}" ]]; then
        uitvoer="$(mosquitto_pub -h localhost -p "$MQTT_PORT" -u "$MQTT_USER" -P "fout_ww_$$" \
                   -t plantpal/test -m x 2>&1)"
        if grep -qi "not authori" <<< "$uitvoer"; then
            groen "  OK   fout wachtwoord wordt geweigerd"
        else
            rood  "  FOUT fout wachtwoord werd niet geweigerd: ${uitvoer:-aanvaard}"
            FOUTEN=$((FOUTEN + 1))
        fi
    fi
fi

# Hier tonen we het eindresultaat. exit 1 laat setup_pi.sh weten dat er iets mis is.
if [[ $FOUTEN -eq 0 ]]; then
    groen "Alle tests geslaagd."
else
    rood "$FOUTEN test(s) mislukt."
    exit 1
fi
