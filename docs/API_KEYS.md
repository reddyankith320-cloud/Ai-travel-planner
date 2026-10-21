# API keys: what TripWise needs, and what it costs

**TripWise needs zero API keys to run.** With an empty `.env` it starts in
demo mode: destination discovery, budget calculation, ranking and day-by-day
itineraries all work, every figure comes from the deterministic cost model,
and every figure is labelled as an estimate. Nothing is presented as a live
quote.

Keys buy you three things, in rough order of value:

1. **Geocoding** — lets users type a city name instead of granting location access.
2. **An LLM** — better-written, better-sequenced itineraries.
3. **Live prices** — real flight fares and hotel rates instead of modelled ones.

Everything below was checked against provider documentation in October 2026.
**Pricing and free tiers change often — verify on the provider's own page
before you commit.** Where we could not confirm something, this says so.

---

## TL;DR — the short list

| Priority | Variable | Provider | Cost | What you lose without it |
|---|---|---|---|---|
| **Recommended** | `GEOAPIFY_API_KEY` | Geoapify | Free tier, card required on some plans | Typed-city search falls back to fair-use Nominatim |
| **Recommended** | `ANTHROPIC_API_KEY` | Anthropic | Pay per token, no free tier | Itineraries come from the deterministic planner instead |
| Optional | `OPENAI_API_KEY` + `OPENAI_MODEL` | OpenAI | Pay per token | Alternative to Anthropic — you need one, not both |
| Optional | `LOCATIONIQ_API_KEY` | LocationIQ | Free tier allows commercial use **with attribution** | Another geocoding fallback |
| Optional | `OPENROUTESERVICE_API_KEY` | OpenRouteService | Free tier for registered users | Road routing falls back to the OSRM demo server |
| Optional | `GOOGLE_MAPS_SERVER_KEY` | Google Maps Platform | **Billing account required**, per-SKU free allowance | Highest-quality geocoding/routing/places |
| Optional | `GOOGLE_MAPS_BROWSER_KEY` | Google Maps Platform | Same billing account | Only needed if you swap OpenStreetMap tiles for Google tiles |
| Optional | `FOURSQUARE_API_KEY` | Foursquare | Pay-as-you-go | Another places source |
| Optional | `AMADEUS_CLIENT_ID` / `_SECRET` | Amadeus | See the warning below | Flight and hotel prices stay modelled |
| **Never needed** | — | OpenStreetMap, OSRM, Overpass, Open-Meteo, Wikimedia | Free, keyless, fair-use | — |

**There is no key for Indian trains or buses, because no legitimate public API
exists.** See [the rail and bus section](#indian-rail-and-bus-there-is-no-key).

---

## Filling the slots

```bash
cp .env.example backend/.env
# paste a key after the "=" of its slot
./scripts/check-keys.sh
```

Things that are handled for you, so you do not have to think about them:

| What you do | What happens |
|---|---|
| Leave a slot blank | That provider is not used. Same as deleting the line. |
| Leave a placeholder in (`your-key-here`) | Ignored, with a warning. It will **not** be sent to the provider. |
| Paste with quotes (`KEY="abc"`) | Quotes stripped. The key works. |
| Paste with stray spaces | Trimmed. The key works. |
| Set `OPENAI_API_KEY` but not `OPENAI_MODEL` | OpenAI stays off, and `check-keys.sh` tells you why. |
| Set `AMADEUS_*` but leave `FLIGHT_PROVIDER=none` | Stays off, and `check-keys.sh` tells you which switch to flip. |

A half-filled `.env` is a normal, supported state. Add keys as you get them.

---

## 1. Geocoding — the one worth getting first

Without a geocoder, a user who declines location access cannot start a trip by
typing their city. The keyless Nominatim fallback is on by default but is a
fair-use community service with no SLA — fine for development, not for traffic.

### Geoapify (the recommendation)

One key covers geocoding, routing **and** places, which is why it is first.

1. Go to <https://myprojects.geoapify.com/> and sign up.
2. Create a project. A key is generated automatically.
3. Copy the key into `.env`:
   ```
   GEOAPIFY_API_KEY=your-key-here
   ```
4. In the Geoapify console, restrict the key by allowed origin/IP.

**Cost:** a free tier is advertised at roughly 3,000 requests/day (reported by
third-party aggregators, not confirmed on Geoapify's own page — check it).
TripWise caches geocoding results for 6 hours, so repeat lookups of the same
city cost nothing.

### LocationIQ (alternative)

1. Sign up at <https://locationiq.com/register>.
2. Copy the access token from the dashboard into `LOCATIONIQ_API_KEY`.

**Cost:** the free plan is documented as usable in commercial projects
**provided you display a "Search by LocationIQ.com" link**. If you use it in
production, add that attribution to your UI — TripWise does not add it for you.
Reported limits are around 5,000 requests/day and 2 requests/second.

### Google Maps Platform (highest quality, needs billing)

**Read this before starting:** the old $200/month universal credit is gone.
Billing is now per-SKU, each API carrying its own monthly free allowance
(roughly 10,000 free events/month for Essentials-tier SKUs, less for Pro and
Enterprise tiers), and the allowances do **not** pool. A runaway loop can
generate a real bill. Verify current pricing at
<https://mapsplatform.google.com/pricing/>.

1. Create a project at <https://console.cloud.google.com/>.
2. **Enable billing** on it. Nothing works without this.
3. Enable the APIs you want: Geocoding API, Routes API, Places API (New).
4. **Create two separate keys** — this matters:
   - **Server key** → *Credentials → Create credentials → API key*.
     Set *Application restrictions* to **IP addresses** and list your server's
     IPs. Set *API restrictions* to only the APIs above. This key goes in
     `GOOGLE_MAPS_SERVER_KEY` and **never leaves the backend**.
   - **Browser key** (only if you switch to Google map tiles) →
     *Application restrictions* → **HTTP referrers**, listing your exact
     domains (`https://yourdomain.com/*`). *API restrictions* → Maps
     JavaScript API only. This one is public by design; the referrer
     restriction is the only thing protecting it.
5. Set a **budget alert** and a **daily quota cap** per API under
   *APIs & Services → Quotas*. Do this before you ship, not after.

```
GOOGLE_MAPS_SERVER_KEY=AIza...        # backend only, IP-restricted
GOOGLE_MAPS_BROWSER_KEY=AIza...       # public, referrer-restricted
```

---

## 2. LLM — better itineraries

TripWise produces complete itineraries without any LLM. The deterministic
planner geo-clusters attractions into days, orders each day by nearest
neighbour from your accommodation, respects published opening hours, pins
sunrise and sunset stops to the right end of the day, and inserts meals and
rest. An LLM improves the sequencing judgement and the writing.

**The model never sets a price.** It proposes structure and prose; every rupee
is recomputed by the cost model from the activities it chose. A hallucinated
price cannot reach a user, and a hallucinated *place* is downgraded to an
unpriced free-time block. This is enforced in code and covered by tests in
`backend/tests/test_itinerary.py::TestLLMContainment`.

### Anthropic (default)

1. Sign up at <https://console.anthropic.com/>.
2. *API keys → Create key*.
3. Add credit — there is no free tier.
4. ```
   ANTHROPIC_API_KEY=sk-ant-...
   ANTHROPIC_MODEL=claude-sonnet-5-5
   ```
5. Set a monthly spend limit in the console.

**Cost:** per token. One itinerary is a single call with a few thousand input
tokens and a few thousand output tokens — fractions of a cent to a few cents
depending on the model. Current rates: <https://www.anthropic.com/pricing>

### OpenAI (alternative)

1. Sign up at <https://platform.openai.com/>.
2. *API keys → Create new secret key*.
3. Add credit and set a usage limit.
4. **Look up a current model ID** — this is required, and TripWise has no
   default on purpose, because OpenAI model IDs change often enough that a
   hardcoded one would silently break:
   ```bash
   curl https://api.openai.com/v1/models \
     -H "Authorization: Bearer $OPENAI_API_KEY" | jq -r '.data[].id' | sort
   ```
5. ```
   OPENAI_API_KEY=sk-proj-...
   OPENAI_MODEL=<an id from the list above>
   LLM_PROVIDER=openai
   ```

With both keys set and `LLM_PROVIDER=auto`, Anthropic is used.

---

## 3. Routing — real road distances

Without a routing provider, TripWise estimates road distance as straight-line
distance inflated by a terrain factor (1.24× plains, 1.55× hills, 1.85×
high Himalaya). Spot-checked against real corridors this lands within a few
percent — Hyderabad→Goa estimates 672 km against roughly 660 km real, and
Delhi→Agra estimates 220 km against roughly 230 km real. It is labelled
`estimated` either way.

### OpenRouteService

1. Sign up at <https://openrouteservice.org/dev/#/signup>.
2. Request a token (free tier for registered users; verify current limits on
   their site — we could not confirm them from an official page).
3. `OPENROUTESERVICE_API_KEY=your-token`

### OSRM demo server (default, keyless)

On by default via `ENABLE_OSRM=true`. It is a **community demo server with no
SLA and no availability guarantee**. TripWise rate-limits it to ~1 req/sec and
caches results for 6 hours. For production either self-host OSRM or use a
credentialed provider, and set `ENABLE_OSRM=false`.

---

## 4. Places and attractions

TripWise ships a curated dataset of 58 Indian destinations with 308
attractions, including coordinates, approximate opening hours, approximate
entry fees and typical visit durations. This is what demo mode uses and it is
labelled `curated` throughout. Places providers *augment* it.

| Variable | Provider | Notes |
|---|---|---|
| `FOURSQUARE_API_KEY` | Foursquare Places | Pay-as-you-go. Official pricing shows $0.00 CPM for the first 500 Pro-endpoint calls, then ~$15 CPM. Third-party sources claim a larger free tier; the official page is the one to trust. Sign up: <https://foursquare.com/developers/> |
| `GOOGLE_MAPS_SERVER_KEY` | Google Places (New) | Same key as geocoding. Uses a field mask so you are only billed for fields TripWise actually reads. |
| `GEOAPIFY_API_KEY` | Geoapify Places | Already covered if you set it up for geocoding. |
| *(keyless)* | OpenStreetMap Overpass | On by default. Fair-use, rate-limited to one request every 2 s. |

**None of these publish admission charges.** TripWise leaves `entry_fee_inr`
empty rather than inventing one, and shows fees only from its curated dataset,
marked approximate.

---

## 5. Flights and hotels — live prices

### Amadeus Self-Service — status unconfirmed, off by default

Multiple third-party migration guides state that Amadeus decommissioned its
**Self-Service** developer portal on 17 July 2026, leaving only Enterprise
access under a sales contract. We could not confirm this on Amadeus's own
site, and their documentation pages still describe the Self-Service test tier.

The adapter is written against the documented Self-Service API shape, is
disabled by default, and TripWise is fully functional without it. **Check your
account status with Amadeus before relying on it.**

1. Register at <https://developers.amadeus.com/> if the portal is still open.
2. Create an app to get a Client ID and Client Secret.
3. ```
   AMADEUS_CLIENT_ID=your-id
   AMADEUS_CLIENT_SECRET=your-secret
   AMADEUS_ENVIRONMENT=test      # 'test' serves cached, limited data
   FLIGHT_PROVIDER=amadeus
   HOTEL_PROVIDER=amadeus
   ```

**Keep `AMADEUS_ENVIRONMENT=test` for development only.** The test environment
returns cached, non-market data. It is labelled as a live provider quote
because that is what the provider calls it — which is exactly why you should
not show it to real users.

### Other hotel options

- **Booking.com affiliate** — revenue-share, no setup fee, widest independent
  Indian inventory. Apply at <https://www.booking.com/affiliate-program/>.
- **Hotelbeds / TBO Holidays** — wholesale bedbanks with the deepest Indian
  coverage, but they need a commercial agreement and cost thousands of USD a
  year. Not a fit unless you are operating at scale.

Neither is implemented. `backend/app/providers/amadeus.py` shows the shape an
adapter takes: return a per-room-per-night INR figure, or `None`.

---

## Indian rail and bus: there is no key

**We looked, and there is no legitimate public API for Indian Railways fares
or schedules, nor for intercity bus fares.**

- IRCTC runs no open developer programme. Its API work is business-to-business
  under formal agreement.
- eRail.in's developer API is suspended.
- The npm packages that claim to offer IRCTC access are unofficial scrapers.
  One states outright that it is "for educational purposes" and that using it
  may violate IRCTC's terms.

TripWise therefore **estimates** rail and bus fares and says so on every
figure. The rail model uses a telescopic fare table (rate per km falls with
distance, as Indian Railways actually prices) for SL, 3A and 2A, including
reservation and superfast charges. Spot checks land within about 10% of real
fares.

Alongside every estimate we link to the **official** booking channel: IRCTC
and NTES for rail, the relevant state road transport corporation for buses.

We do not scrape, and we do not bypass authentication, CAPTCHAs or paywalls.
If you need live rail data, approach IRCTC for a commercial agreement.

---

## Keyless services (no signup, no cost)

| Service | Used for | Terms |
|---|---|---|
| OpenStreetMap tiles | Map rendering in the browser | Attribution required and rendered by Leaflet. Heavy use should use a commercial tile host. |
| Nominatim | Geocoding fallback | ~1 req/sec, identifiable User-Agent required — set `OSM_CONTACT_EMAIL`. |
| OSRM demo | Routing fallback | Fair use, no SLA. Self-host for production. |
| Overpass | Places fallback | Fair use, rate-limited to one request per 2 s. |
| Open-Meteo | Seasonal weather | Free for **non-commercial** use under ~10,000 calls/day, CC-BY 4.0. Commercial deployments need their paid plan — set `ENABLE_OPEN_METEO=false` or subscribe. |
| Wikipedia / Wikimedia | Destination photographs | Freely licensed; attribution is mandatory and the UI renders it. |

TripWise rate-limits and caches all of these. Each can be disabled with its
`ENABLE_*` flag.

---

## Security rules, non-negotiable

1. **Private keys go in `backend/.env` only.** `.gitignore` already excludes it.
2. **Never put a private key in `frontend/.env`.** Anything in a `VITE_*`
   variable is compiled into the JavaScript bundle and is readable by anyone
   who loads the page.
3. **Restrict every key** in its provider's console — by IP for server keys,
   by HTTP referrer for browser keys.
4. **Set quota caps and budget alerts** before you ship.
5. **Rotate a key the moment it is exposed.** Revoke the old one; do not just
   issue a new one alongside it.
6. **Verify before each release:**
   ```bash
   ./scripts/verify-no-secrets.sh
   ```
   This fails the build if any credential pattern appears in the built bundle,
   or if the bundle calls a provider directly instead of going through the
   backend.
