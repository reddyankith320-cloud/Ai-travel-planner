#!/usr/bin/env bash
#
# Shows what TripWise picked up from backend/.env, what it costs you, and
# what the free fallback is for anything you have not set.
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
  echo
  echo "  No backend/.env found — that is a valid setup."
  echo "  TripWise runs free on OpenStreetMap with no keys at all."
  echo
  echo "  To add a key:  cp .env.example backend/.env"
  echo
fi

"$PY" - <<'PYEOF'
import sys
sys.path.insert(0, ".")
from app.config import Settings

s = Settings()
st = s.provider_status()
G, Y, C, D, B, R = "\033[32m", "\033[33m", "\033[36m", "\033[2m", "\033[1m", "\033[0m"

print(f"\n{B}The two optional keys{R}  (both cost money; both are off by default)")
print("  " + "-" * 94)

google = bool(s.google_maps_server_key)
print(f"  {'GOOGLE_MAPS_SERVER_KEY':<26} "
      f"{(G + 'set' + R) if google else (D + 'not set' + R):<20} "
      + ("geocoding, road routes, places  " + Y + "(billed per API — check your quota caps)" + R
         if google else
         D + "free fallback: OpenStreetMap (Nominatim, OSRM, Overpass)" + R))

browser = bool(s.google_maps_browser_key)
print(f"  {'GOOGLE_MAPS_BROWSER_KEY':<26} "
      f"{(G + 'set' + R) if browser else (D + 'not set' + R):<20} "
      + ((Y + "must be HTTP-referrer restricted in the Google console" + R)
         if browser else
         D + "free fallback: OpenStreetMap tiles" + R))
if browser:
    print(f"  {'':<26} {D}also set VITE_GOOGLE_MAPS_BROWSER_KEY in frontend/.env{R}")

openai = bool(s.openai_api_key)
model = s.openai_model or "resolved from the API on first use"
print(f"  {'OPENAI_API_KEY':<26} "
      f"{(G + 'set' + R) if openai else (D + 'not set' + R):<20} "
      + (f"model: {model}  " + Y + "(no free tier — every call is billed)" + R
         if openai else
         D + "free fallback: built-in deterministic planner" + R))

print(f"\n{B}What is actually running{R}")
print("  " + "-" * 94)
for name, chain in (
    ("geocoding", st["geocoding"]), ("routing", st["routing"]),
    ("places", st["places"]), ("weather", st["weather"]), ("images", st["images"]),
):
    free = not chain or chain[0] != "google"
    tag = (D + "free" + R) if free else (Y + "billed" + R)
    print(f"  {name:<12} {' -> '.join(chain) if chain else D + 'none' + R:<34} {tag}")
llm = st["llm"]
print(f"  {'itinerary':<12} "
      f"{('openai' if llm['enabled'] else 'built-in planner'):<34} "
      + ((Y + "billed" + R) if llm["enabled"] else (D + "free" + R)))
print(f"  {'fares/rates':<12} {'cost model (always estimated)':<34} {D}free{R}")

print()
if st["demo_mode"]:
    print(f"  {G}FREE MODE{R} — no billed provider configured. Nothing can charge you.")
    print(f"  {D}Everything works; every figure is a labelled estimate.{R}")
else:
    billed = [n for n, c in (("Google Maps", st["geocoding"]),) if c and c[0] == "google"]
    if llm["enabled"]:
        billed.append("OpenAI")
    print(f"  {Y}BILLED{R} — in use: {', '.join(billed)}.")
    print(f"  {D}Set quota caps and budget alerts in each provider's console.{R}")

if s.openai_api_key and not s.openai_model:
    print(f"\n  {C}Note:{R} OPENAI_MODEL is blank, so a current model is resolved from the API")
    print(f"  {D}on first use. Set it to pin one. To see what your key can use:{R}")
    print(f"  {D}  curl https://api.openai.com/v1/models -H \"Authorization: Bearer $OPENAI_API_KEY\"{R}")
if s.google_maps_browser_key and not s.google_maps_server_key:
    print(f"\n  {C}Note:{R} the browser key is set but the server key is not, so you get Google")
    print(f"  {D}tiles with OpenStreetMap geocoding and routing. That is a valid mix.{R}")
if not s.osm_contact_email and (s.enable_nominatim or s.enable_osrm) and not s.google_maps_server_key:
    print(f"\n  {C}Note:{R} Nominatim's usage policy asks for a contact address.")
    print(f"  {D}Set OSM_CONTACT_EMAIL since you rely on the free geocoder.{R}")
print()
PYEOF
