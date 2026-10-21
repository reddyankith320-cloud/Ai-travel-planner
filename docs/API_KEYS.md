# API keys: the two that exist, and what they cost

**TripWise is free to run.** With no keys at all it does destination
discovery, budget calculation, ranking and full day-by-day itineraries, using
OpenStreetMap for maps, geocoding, routing and places, Open-Meteo for seasonal
weather, Wikipedia for photographs, and its own planner for itineraries. None
of that needs a key, a card, or a signup.

There are exactly **two** optional keys. Both cost money. Neither is required.

| Key | Buys you | Cost | Free fallback if unset |
|---|---|---|---|
| `GOOGLE_MAPS_SERVER_KEY` | Better geocoding, real road distances, richer places | Billing account required; per-API monthly free allowance | OpenStreetMap (Nominatim, OSRM, Overpass) |
| `GOOGLE_MAPS_BROWSER_KEY` | Google map tiles | Same billing account | OpenStreetMap tiles |
| `OPENAI_API_KEY` | Itineraries written by a model | **No free tier** — every call is billed | Built-in deterministic planner |

> **There is no key for Indian trains or buses.** No legitimate public API
> exists. See [the rail and bus section](#indian-rail-and-bus-there-is-no-key).

---

## Being honest about "free"

Google Maps and OpenAI are both paid products. What is genuinely free is the
path TripWise takes by default:

| What you want | Free way | Paid way |
|---|---|---|
| Maps | OpenStreetMap tiles | Google tiles |
| "Where am I?" | Browser geolocation (free, built in) | — |
| Typing a city name | Nominatim | Google Geocoding |
| Road distances | Terrain-adjusted estimate, or OSRM | Google Routes |
| Places to visit | Curated dataset + Overpass | Google Places |
| Weather | Open-Meteo | — |
| Photos | Wikipedia | — |
| Itineraries | Built-in planner | OpenAI |
| Budgets and ranking | Always TripWise's own model | — |

**Google can be effectively free at low volume.** Each API carries its own
monthly free allowance, and TripWise is built to stay inside it: the broad
search over all 58 destinations makes **zero** API calls, only the shortlisted
few get live lookups, and every response is cached for 6 hours. A typical trip
search is 5–8 calls before caching. But it still requires a card on file, and
an unattended loop can still bill you, so set a cap.

**OpenAI cannot be free.** There is no free tier. If cost is the priority,
leave `OPENAI_API_KEY` blank — the built-in planner geo-clusters stops,
respects opening hours, pins sunrise and sunset visits to the right end of the
day, and inserts meals and rest. It is what every screenshot in this repo was
produced with.

---

## Filling the slots

```bash
cp .env.example backend/.env
# paste a key after the "=" of its slot
./scripts/check-keys.sh
```

`check-keys.sh` shows what registered, what it costs, and what the free
fallback is for anything you left blank. It never prints a key value.

Handled for you, so you do not have to think about them:

| What you do | What happens |
|---|---|
| Leave a slot blank | Free fallback is used. Same as deleting the line. |
| Leave a placeholder in (`your-key-here`) | Ignored, with a warning. Never sent to the provider. |
| Paste with quotes (`KEY="abc"`) | Quotes stripped. The key works. |
| Paste with stray spaces | Trimmed. The key works. |
| Set `OPENAI_API_KEY` but not `OPENAI_MODEL` | A current model is resolved from the API. No action needed. |

A half-filled `.env` is a normal, supported state.

---

## 1. Google Maps Platform

**Read this before starting.** The old $200/month universal credit is gone.
Billing is now per-SKU, each API carrying its own monthly free allowance, and
the allowances do **not** pool. A runaway loop can generate a real bill.
Verify current pricing at <https://mapsplatform.google.com/pricing/>.

### Steps

1. Create a project at <https://console.cloud.google.com/>.
2. **Enable billing** on it. Nothing works without this.
3. Enable these APIs (*APIs & Services → Library*):
   - **Geocoding API** — turning a typed city into coordinates
   - **Routes API** — real road distance and duration
   - **Places API (New)** — attractions near a destination
   - **Maps JavaScript API** — only if you want Google tiles
4. **Create two separate keys.** This matters:

   **Server key** → *Credentials → Create credentials → API key*
   - *Application restrictions* → **IP addresses**, listing your server's IPs
   - *API restrictions* → Geocoding, Routes, Places only
   - Goes in `GOOGLE_MAPS_SERVER_KEY`. **Never leaves the backend.**

   **Browser key** (only for Google tiles) → create a second key
   - *Application restrictions* → **HTTP referrers**, e.g. `https://yourdomain.com/*`
   - *API restrictions* → Maps JavaScript API only
   - Goes in **both** `GOOGLE_MAPS_BROWSER_KEY` (backend, for reporting) and
     `VITE_GOOGLE_MAPS_BROWSER_KEY` (frontend, used at build time)
   - This key ships in the browser bundle and anyone can read it. The referrer
     restriction is the only thing protecting it.

5. **Set a budget alert and per-API quota caps** under *APIs & Services →
   Quotas*. Do this before you ship, not after.

### If you only want one

Take the **server key**. It improves the data users actually feel — typed-city
search and real road distances. The browser key only changes how the map
*looks*; OpenStreetMap tiles are free and perfectly good.

---

## 2. OpenAI

### Steps

1. Sign up at <https://platform.openai.com/>.
2. *API keys → Create new secret key*.
3. Add credit. There is no free tier.
4. **Set a monthly usage limit** under *Settings → Limits*.
5. ```
   OPENAI_API_KEY=sk-proj-...
   OPENAI_MODEL=
   ```

### Leave `OPENAI_MODEL` blank

OpenAI's model IDs change often enough that any hardcoded default eventually
breaks. When the slot is blank, TripWise asks the API which models your key
can use, picks the newest suitable one, and caches that for the process. It
logs which it chose.

Set it only to pin a specific model. To see your options:

```bash
curl https://api.openai.com/v1/models \
  -H "Authorization: Bearer $OPENAI_API_KEY" | jq -r '.data[].id' | sort
```

### What it costs per trip

One itinerary is a single call: a few thousand input tokens and a few thousand
output. At typical rates that is a fraction of a cent to a few cents. The
expensive mistake would be regenerating in a loop, which the per-IP rate limit
already guards against.

### What the model is and is not allowed to do

It proposes ordering, day groupings and prose. It **never** sets a price.
Every rupee is recomputed by the cost model from the activities it chose, so a
hallucinated price cannot reach a user, and a place it invents is downgraded to
an unpriced free-time block. If it is unreachable, returns malformed JSON, or
fails schema validation twice, the built-in planner takes over and the response
says `generated_by: "deterministic"`.

This is enforced in code and covered by
`backend/tests/test_itinerary.py::TestLLMContainment`.

---

## Indian rail and bus: there is no key

**We looked, and no legitimate public API exists** for Indian Railways fares or
schedules, or for intercity bus fares.

- IRCTC runs no open developer programme. Its API work is business-to-business
  under formal agreement.
- eRail.in's developer API is suspended.
- The npm packages claiming IRCTC access are unofficial scrapers. One states
  outright that it is "for educational purposes" and that using it may violate
  IRCTC's terms.

TripWise therefore **estimates** rail and bus fares and says so on every
figure. The rail model uses a telescopic fare table — the rate per km falls
with distance, as Indian Railways actually prices — covering SL, 3A and 2A
including reservation and superfast charges. Spot checks land within about 10%
of real fares.

Alongside every estimate we link to the **official** booking channel: IRCTC and
NTES for rail, the relevant state road transport corporation for buses.

Flight and hotel prices are modelled for the same reason — no licensed fare or
rate API is wired up. We do not scrape, and we do not bypass authentication,
CAPTCHAs or paywalls.

---

## Free services already in use (no signup)

| Service | Used for | Terms |
|---|---|---|
| OpenStreetMap tiles | Map rendering | Attribution required, rendered by Leaflet |
| Nominatim | Typed-city geocoding | ~1 req/sec, identifiable User-Agent — set `OSM_CONTACT_EMAIL` |
| OSRM demo | Road distances | Fair use, no SLA. Self-host for production. |
| Overpass | Nearby places | Fair use, one request per 2 s |
| Open-Meteo | Seasonal weather | Free for **non-commercial** use under ~10k calls/day, CC-BY 4.0 |
| Wikipedia / Wikimedia | Destination photos | Freely licensed, attribution rendered in the UI |

All are rate-limited and cached, and each can be turned off with its
`ENABLE_*` flag.

---

## Security rules, non-negotiable

1. **Private keys go in `backend/.env` only.** `.gitignore` excludes it.
2. **Only the browser key may appear in `frontend/.env`.** Anything in a
   `VITE_*` variable is compiled into the bundle and is public.
3. **Restrict both Google keys** in the console — server by IP, browser by
   HTTP referrer. An unrestricted key is a blank cheque.
4. **Set quota caps and budget alerts** before you ship.
5. **Rotate a key the moment it is exposed.** Revoke the old one.
6. **Verify before each release:**
   ```bash
   ./scripts/verify-no-secrets.sh
   ```
   This fails if any credential pattern appears in the built bundle, or if the
   bundle calls a provider directly instead of going through the backend.
