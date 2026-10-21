#!/usr/bin/env bash
# Proves the browser bundle contains no server credentials.
set -uo pipefail
DIST="frontend/dist"
[ -d "$DIST" ] || { echo "FAIL: build the frontend first (npm run build)"; exit 1; }

# Patterns that would indicate a server-side credential had leaked in.
PATTERNS='sk-ant-|sk-proj-|AIzaSy|GOOGLE_MAPS_SERVER_KEY|AMADEUS_CLIENT_SECRET|ANTHROPIC_API_KEY|OPENAI_API_KEY|GEOAPIFY_API_KEY|LOCATIONIQ_API_KEY|FOURSQUARE_API_KEY|DATABASE_URL|postgres://|postgresql://'
echo "Scanning $DIST for credential patterns..."
if grep -rInE "$PATTERNS" "$DIST" 2>/dev/null; then
  echo "FAIL: credential-shaped content found in the browser bundle."
  exit 1
fi
echo "PASS: no credential patterns in the browser bundle."

# The bundle must also not call third-party APIs directly: every provider
# request belongs on the server, behind our own /api.
echo "Checking the bundle makes no direct third-party API calls..."
if grep -rIoE 'https://(maps\.googleapis\.com|places\.googleapis\.com|api\.geoapify\.com|us1\.locationiq\.com|api\.openrouteservice\.org|test\.api\.amadeus\.com|api\.amadeus\.com|api\.openai\.com|api\.anthropic\.com|api\.foursquare\.com)' "$DIST" 2>/dev/null | sort -u; then
  echo "FAIL: the bundle talks to a provider directly. Route it through the backend."
  exit 1
fi
echo "PASS: the bundle only talks to TripWise's own API (plus OpenStreetMap tiles, which need no key)."
