#!/usr/bin/env bash
#
# Shows which API key slots TripWise actually picked up, and what each one
# unlocks. Run it after editing backend/.env to confirm a key registered
# before you go looking for bugs elsewhere.
#
# It never prints a key value.
#
#   ./scripts/check-keys.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT/backend" || exit 1

PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || PY="python3"

if [ ! -f .env ] && [ ! -f ../.env ]; then
  echo "No .env found."
  echo "  cp .env.example backend/.env    then paste your keys into it."
  echo
  echo "TripWise runs fine without one — it just stays in demo mode."
  echo
fi

"$PY" - <<'PYEOF'
import sys
sys.path.insert(0, ".")
from app.config import Settings

s = Settings()
st = s.provider_status()

GREEN, YELLOW, DIM, BOLD, RESET = "\033[32m", "\033[33m", "\033[2m", "\033[1m", "\033[0m"

def row(label, configured, unlocks, fallback):
    mark = f"{GREEN}set{RESET}" if configured else f"{DIM}not set{RESET}"
    print(f"  {label:<26} {mark:<22} {unlocks if configured else DIM + fallback + RESET}")

print(f"\n{BOLD}API key slots{RESET}")
print("  " + "-" * 92)
row("GEOAPIFY_API_KEY", bool(s.geoapify_api_key),
    "geocoding, routing, places", "falls back to keyless OSM services")
row("ANTHROPIC_API_KEY", bool(s.anthropic_api_key),
    "AI-written itineraries", "deterministic planner is used")
row("OPENAI_API_KEY + MODEL", bool(s.openai_api_key and s.openai_model),
    "AI-written itineraries", "not used"
    if not s.openai_api_key else "KEY SET BUT OPENAI_MODEL IS EMPTY")
row("OPENROUTESERVICE_API_KEY", bool(s.openrouteservice_api_key),
    "live road distances", "distances are estimated from terrain")
row("LOCATIONIQ_API_KEY", bool(s.locationiq_api_key),
    "geocoding (needs attribution)", "not used")
row("FOURSQUARE_API_KEY", bool(s.foursquare_api_key),
    "extra points of interest", "curated dataset + Overpass")
row("GOOGLE_MAPS_SERVER_KEY", bool(s.google_maps_server_key),
    "geocoding, routes, places", "not used")
row("GOOGLE_MAPS_BROWSER_KEY", bool(s.google_maps_browser_key),
    "Google map tiles", "OpenStreetMap tiles (no key needed)")
row("AMADEUS_CLIENT_ID/SECRET", bool(s.amadeus_client_id and s.amadeus_client_secret),
    "live flight + hotel prices", "prices are modelled")

print(f"\n{BOLD}Active provider chains{RESET}  (tried in order, falling back on failure)")
print("  " + "-" * 92)
for name, chain in (
    ("geocoding", st["geocoding"]), ("routing", st["routing"]),
    ("places", st["places"]), ("weather", st["weather"]),
    ("images", st["images"]), ("flights", st["flights"]), ("hotels", st["hotels"]),
):
    print(f"  {name:<12} {' -> '.join(chain) if chain else DIM + 'none' + RESET}")
llm = st["llm"]
print(f"  {'llm':<12} {llm['provider'] + ' (' + str(llm['model']) + ')' if llm['enabled'] else DIM + 'none — deterministic planner' + RESET}")

print()
if st["demo_mode"]:
    print(f"  {YELLOW}DEMO MODE{RESET} — no credentialed provider configured.")
    print("  Everything works; every figure is a labelled estimate.")
else:
    print(f"  {GREEN}LIVE{RESET} — at least one credentialed provider is configured.")
    print("  Figures from a provider are badged 'Live'; the rest stay estimates.")

if s.amadeus_client_id and s.flight_provider == "none":
    print(f"\n  {YELLOW}Note:{RESET} Amadeus credentials are set but FLIGHT_PROVIDER=none.")
    print("  Set FLIGHT_PROVIDER=amadeus and/or HOTEL_PROVIDER=amadeus to use them.")
if s.openai_api_key and not s.openai_model:
    print(f"\n  {YELLOW}Note:{RESET} OPENAI_API_KEY is set but OPENAI_MODEL is empty, so OpenAI")
    print("  will not be used. List current model IDs with:")
    print("    curl https://api.openai.com/v1/models -H \"Authorization: Bearer $OPENAI_API_KEY\"")
if not s.osm_contact_email and (s.enable_nominatim or s.enable_osrm):
    print(f"\n  {YELLOW}Note:{RESET} Nominatim's usage policy asks for a contact address.")
    print("  Set OSM_CONTACT_EMAIL if you rely on the keyless geocoder.")
print()
PYEOF
