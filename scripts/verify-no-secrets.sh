#!/usr/bin/env bash
#
# Proves the browser bundle carries no server credentials.
#
# Run after `npm run build`, and before any release:
#   ./scripts/verify-no-secrets.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST="$ROOT/frontend/dist"
FAILED=0

[ -d "$DIST" ] || { echo "FAIL: build the frontend first (cd frontend && npm run build)"; exit 1; }

# ---------------------------------------------------------------------------
# 1. Credential-shaped content
# ---------------------------------------------------------------------------
# The Google BROWSER key is public by design and legitimately appears in the
# bundle, and it is indistinguishable by shape from the server key -- both
# start with AIzaSy. So shape alone cannot tell them apart; check 3 below does
# it by value instead.
PATTERNS='sk-proj-|sk-ant-|GOOGLE_MAPS_SERVER_KEY|OPENAI_API_KEY|DATABASE_URL|postgres://|postgresql://'

echo "1. Scanning the bundle for credential patterns..."
if grep -rInE "$PATTERNS" "$DIST" 2>/dev/null; then
  echo "   FAIL: credential-shaped content found in the browser bundle."
  FAILED=1
else
  echo "   PASS"
fi

# ---------------------------------------------------------------------------
# 2. Direct provider calls
# ---------------------------------------------------------------------------
# maps.googleapis.com is allowed: that is the tile endpoint, which uses the
# public browser key. Everything else must go through our own backend.
echo "2. Checking the bundle makes no direct provider API calls..."
if grep -rIoE 'https://(places\.googleapis\.com|routes\.googleapis\.com|api\.openai\.com|api\.anthropic\.com)' "$DIST" 2>/dev/null | sort -u; then
  echo "   FAIL: the bundle calls a provider directly. Route it through the backend."
  FAILED=1
else
  echo "   PASS"
fi

# ---------------------------------------------------------------------------
# 3. Every backend secret, by exact value
# ---------------------------------------------------------------------------
# The strongest check available: take the literal values out of backend/.env
# and prove none of them appears in the bundle. This is what catches a SERVER
# Google key leaking in, which no pattern could distinguish from the browser
# key.
BACKEND_ENV="$ROOT/backend/.env"
[ -f "$BACKEND_ENV" ] || BACKEND_ENV="$ROOT/.env"

echo "3. Checking no backend .env value appears in the bundle..."
if [ -f "$BACKEND_ENV" ]; then
  LEAKS=0
  while IFS= read -r line; do
    case "$line" in \#*|'') continue ;; esac
    name="${line%%=*}"
    value="${line#*=}"
    value="${value%\"}"; value="${value#\"}"
    value="${value%\'}"; value="${value#\'}"
    # Ignore blanks, short values, and non-secret settings that may legitimately
    # match (URLs, flags, numbers).
    [ ${#value} -lt 12 ] && continue
    case "$name" in
      *BROWSER_KEY*) continue ;;   # public by design
      CORS_ORIGINS|API_PREFIX|*_URL|ENABLE_*|*_SECONDS|*_REQUESTS|*_ENTRIES) continue ;;
    esac
    if grep -rqF "$value" "$DIST" 2>/dev/null; then
      echo "   FAIL: the value of $name appears in the browser bundle."
      LEAKS=1
    fi
  done < "$BACKEND_ENV"
  [ "$LEAKS" -eq 0 ] && echo "   PASS" || FAILED=1
else
  echo "   SKIP (no backend/.env on this machine)"
fi

# ---------------------------------------------------------------------------
# 4. Any stray Google key that is not the configured browser key
# ---------------------------------------------------------------------------
echo "4. Checking any Google key in the bundle is the browser key..."
FRONT_ENV="$ROOT/frontend/.env"
EXPECTED=""
[ -f "$FRONT_ENV" ] && EXPECTED="$(grep -E '^VITE_GOOGLE_MAPS_BROWSER_KEY=' "$FRONT_ENV" 2>/dev/null | cut -d= -f2- | tr -d '"'\''' | xargs || true)"
FOUND="$(grep -rIoE 'AIza[0-9A-Za-z_-]{10,}' "$DIST" 2>/dev/null | sed 's/.*://' | sort -u || true)"
if [ -z "$FOUND" ]; then
  echo "   PASS (no Google key in the bundle)"
else
  STRAY=0
  while IFS= read -r key; do
    [ -z "$key" ] && continue
    if [ "$key" != "$EXPECTED" ]; then
      echo "   FAIL: a Google key in the bundle does not match VITE_GOOGLE_MAPS_BROWSER_KEY."
      echo "         This may be the SERVER key. Rotate it immediately."
      STRAY=1
    fi
  done <<< "$FOUND"
  [ "$STRAY" -eq 0 ] && echo "   PASS (matches the configured browser key)" || FAILED=1
fi

echo
if [ "$FAILED" -eq 0 ]; then
  echo "All checks passed. No server credential reaches the browser."
else
  echo "FAILED. Do not release this build."
fi
exit "$FAILED"
