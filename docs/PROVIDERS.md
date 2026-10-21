# Providers: what the app talks to, and why

TripWise deliberately has a **small** provider surface: one paid option and one
free fallback per capability. This document records what that is and what was
removed to get there.

Compiled **October 2026**. Pricing and availability move fast — verify before
you commit.

## The shape

| Capability | Free (default) | Paid (optional) |
|---|---|---|
| Map tiles | OpenStreetMap | Google Maps |
| Geocoding | Nominatim | Google Geocoding |
| Road routing | OSRM, or a terrain-adjusted estimate | Google Routes |
| Places | Curated dataset + Overpass | Google Places (New) |
| Weather | Open-Meteo | — |
| Photos | Wikipedia / Wikimedia | — |
| Itineraries | Built-in deterministic planner | OpenAI |
| Fares, room rates, budgets, ranking | TripWise's own cost model | — |

Two keys total: `GOOGLE_MAPS_SERVER_KEY` (plus a separate browser key for
tiles) and `OPENAI_API_KEY`. Everything else is free and keyless.

## Selection principles

1. **The app must work with nothing configured.** Any provider that becomes a
   hard dependency is the wrong provider.
2. **One paid option per capability.** Three competing geocoders is complexity
   with no payoff for the person deploying this.
3. **Legitimate access only.** No scraping, no bypassing authentication or
   CAPTCHAs, no undocumented endpoints. Where no licensed API exists, we
   estimate and say so.
4. **Honest labelling over apparent completeness.** An estimate marked as an
   estimate beats a fabricated "live" price.

---

## The free stack

| Provider | Role | Terms | Risk |
|---|---|---|---|
| **OpenStreetMap tiles** | Map rendering | Free, attribution required | Tile policy discourages heavy automated use; use Google tiles or a commercial host at scale |
| **Nominatim** | Geocoding | Free, ~1 req/s, identifiable User-Agent required | Fair use only; blocked if abused |
| **OSRM demo** | Routing | Free, no SLA | Can disappear without notice; self-host for production |
| **Overpass** | Places | Free, heavily rate-limited | Slow; coverage varies by region |
| **Open-Meteo** | Seasonal climate | Free for **non-commercial** use under ~10k/day, CC-BY 4.0 | **Commercial use needs their paid plan** |
| **Wikipedia** | Destination photos | Freely licensed, attribution mandatory | Image match can be wrong; falls back to generated cover art rather than showing the wrong place |

All are rate-limited client-side, cached (6 hours; a week for climate and
images), and individually switchable with `ENABLE_*` flags.

## The paid options

### Google Maps Platform

The old $200/month universal credit **no longer exists**. Billing is per-SKU,
each API carrying its own monthly free allowance, and allowances do not pool.
A billing account with a card is required even to stay within the free tier.

TripWise is built to keep call volume low: the broad pass over all 58
destinations makes **zero** API calls, only the shortlisted few get live
lookups, and responses cache for 6 hours. A trip search is roughly 5–8 calls
before caching.

Two separate keys, by design: an IP-restricted server key that never leaves the
backend, and a referrer-restricted browser key for tiles that is public by
nature.

### OpenAI

No free tier. One itinerary is a single call — a few thousand tokens each way.
The model never sets a price; it proposes structure and prose, and every rupee
is recomputed from TripWise's own data.

Model IDs are resolved from the API at runtime when `OPENAI_MODEL` is blank,
because hardcoding one guarantees eventual breakage.

---

## Removed, and why

Earlier versions supported Geoapify, LocationIQ, OpenRouteService, Foursquare,
Amadeus and Anthropic. All were removed to narrow the surface to Google +
OpenAI + the free stack.

| Removed | Was used for | Why it went |
|---|---|---|
| Geoapify | Geocoding, routing, places | Overlapped Google entirely; three geocoders is complexity without payoff |
| LocationIQ | Geocoding | Free tier required a visible attribution link most deployments would forget |
| OpenRouteService | Routing | Covered by Google, with OSRM free behind it |
| Foursquare | Places | Covered by Google Places; conflicting reports about its free tier |
| Amadeus | Flight + hotel prices | Third-party reports its Self-Service portal was decommissioned in July 2026; we could not confirm with Amadeus and would not build a dependency on it |
| Anthropic | Itineraries | OpenAI chosen as the single LLM |

Adding one back is a contained change: write an adapter in
`backend/app/providers/` returning a typed result or `None`, add a settings
field, and append it to the relevant chain in `config.py`. The fallback
behaviour comes for free.

### Indian Railways / IRCTC — no legitimate API exists

- **IRCTC** runs no public developer programme. Its documented API work is B2B
  under formal agreement.
- **eRail.in**, historically an official schedule API, lists its service as
  suspended.
- **npm `irctc-api` / `irctc-connect`** are unofficial. `irctc-api` states it
  is "strictly for educational purposes" and that using it in ways that violate
  IRCTC's terms is prohibited. `irctc-connect` is deprecated.

**Decision:** estimate rail fares with a telescopic model, label them as
estimates with an explicit "this is not a quote", and link to IRCTC and NTES.
Scraping was never on the table.

### Intercity buses — same conclusion

No open API covers private operators or most state RTCs; redBus has no public
developer API. We estimate, and link to the relevant state road transport
corporation (official URLs for 16 states) plus redBus as a labelled aggregator.

### Flights and hotels

With Amadeus removed, no licensed fare or rate API is wired up, so both are
always produced by the cost model and labelled `estimated`. The seam remains:
`quote_flight(live_fare_inr=...)` and `quote_accommodation(live_nightly_inr=...)`
take a provider figure and relabel the provenance as live.

---

## What each failure does

TripWise degrades rather than fails. This was verified for real: the build
sandbox blocked OSRM and Open-Meteo, every retry failed, and the API still
returned correct ranked recommendations.

| Failure | Behaviour |
|---|---|
| Google geocoding down | Falls to Nominatim. If that fails too and the user shared coordinates, planning proceeds with a coordinate label. |
| Google Routes down | Falls to OSRM, then to the terrain-adjusted estimate. Provenance stays `estimated`. |
| Google Places down | Falls to Overpass, then the curated dataset. |
| Weather down | Seasonal scoring falls back to the curated best-months window. |
| OpenAI down or invalid output | Deterministic planner produces the itinerary; response says `generated_by: "deterministic"`. |
| Cache (Redis) down | Switches to in-process caching. Never fails a request. |

Every failure is surfaced in `meta.provider_failures` rather than hidden.

### Circuit breaking

A provider that fails repeatedly is skipped for 60 seconds instead of being
retried on every shortlisted destination. Without it, one dead dependency cost
**20 s** per request; with it, 5.5 s on the first call and **63 ms** once open.
A transport-level failure (DNS, connect, proxy, TLS) opens the circuit
immediately, since retrying cannot reach an unreachable host.

---

## Rough operating cost

For ~1,000 trip searches a month, with 6-hour caching and live lookups limited
to shortlisted destinations:

| Setup | Monthly cost | What you get |
|---|---|---|
| **Nothing configured** | **₹0** | Everything except typed-city precision and AI prose |
| Google server key only | ₹0 at low volume, billed past the per-API free allowance | Typed-city search, real road distances, richer places |
| OpenAI only | ~₹400–1,700 | AI-written itineraries, free maps |
| Both | the above combined | Everything |

## Compliance notes

- **Attribution.** OpenStreetMap attribution is rendered by Leaflet and in the
  footer, and switches to Google when Google tiles are active. Wikimedia image
  credits appear on each cover.
- **Caching.** Default TTL is 6 hours. Some provider licences limit how long
  responses may be stored — check before raising `CACHE_TTL_SECONDS`.
- **Open-Meteo's free tier is non-commercial.** Subscribe or set
  `ENABLE_OPEN_METEO=false` for commercial use.
- **No scraping anywhere in this codebase.** Every outbound request goes to a
  documented API endpoint through `providers/http.py`.
