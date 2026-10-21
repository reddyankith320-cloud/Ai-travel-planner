# Providers: what we chose, what we rejected, and what it costs

This is the provider-selection record the build was based on. It was compiled
in **October 2026** from provider documentation and current reporting. Pricing
and availability move fast — treat every figure as a starting point to verify,
not a guarantee.

## Selection principles

1. **The app must work with nothing configured.** Any provider that becomes a
   hard dependency is the wrong provider.
2. **Minimum practical set.** One key that covers geocoding, routing and places
   beats three keys that each cover one.
3. **Legitimate access only.** No scraping, no bypassing authentication or
   CAPTCHAs, no undocumented endpoints. Where no licensed API exists, we
   estimate and say so.
4. **Honest labelling over apparent completeness.** An estimate marked as an
   estimate is more useful than a fabricated "live" price.

---

## Chosen providers

### Tier 0 — keyless, on by default

| Provider | Role | Terms as of Oct 2026 | Risk |
|---|---|---|---|
| **OpenStreetMap tiles** | Map rendering | Free, attribution required | Tile usage policy discourages heavy automated use; move to a commercial tile host at scale |
| **Nominatim** | Geocoding fallback | Free, ~1 req/s, identifiable User-Agent required | Fair use only; blocked if abused |
| **OSRM demo server** | Routing fallback | Free, no SLA | Can disappear without notice; self-host for production |
| **Overpass** | Places fallback | Free, heavily rate-limited | Slow; coverage varies by region |
| **Open-Meteo** | Seasonal climate | Free for **non-commercial** use under ~10k calls/day, CC-BY 4.0 | **Commercial use needs their paid plan** |
| **Wikipedia / Wikimedia** | Destination photos | Freely licensed, attribution mandatory | Image match can be wrong; we fall back to generated art rather than show a wrong photo |

All six are rate-limited client-side, cached for 6 hours (a week for climate
and images), and individually switchable with `ENABLE_*` flags.

### Tier 1 — recommended for production

| Provider | Role | Cost | Why chosen |
|---|---|---|---|
| **Geoapify** | Geocoding + routing + places | Free tier reported at ~3,000 req/day | One key, three services. Best effort-to-value ratio. |
| **Anthropic** | Itinerary generation | Per token, no free tier | Strong structured-JSON adherence, which matters when the output is schema-validated |

### Tier 2 — optional

| Provider | Role | Cost | Caveat |
|---|---|---|---|
| **Google Maps Platform** | Geocoding, Routes, Places | Billing account required; **the $200/month universal credit no longer exists** — per-SKU allowances now, roughly 10k free events/month on Essentials SKUs, which do not pool | Best data quality, highest risk of a surprise bill. Set quota caps. |
| **LocationIQ** | Geocoding | Free tier permits commercial use **with a visible attribution link** | You must add the attribution yourself |
| **OpenRouteService** | Routing | Free tier for registered users | Could not confirm current limits from an official page |
| **Foursquare** | Places | $0.00 CPM for the first 500 Pro calls, then ~$15 CPM | Third-party claims of a 10k free tier conflict with the official pricing page |
| **OpenAI** | Itinerary generation | Per token | Model IDs change often; `OPENAI_MODEL` is required with no default |
| **Amadeus** | Flights + hotels | See below | **Status unconfirmed** |

---

## Rejected, and why

### Indian Railways / IRCTC — no legitimate API exists

We searched specifically for this and found nothing usable:

- **IRCTC** runs no public developer programme. Its documented API work is
  B2B under formal agreement (for example a 2023 MoU where MSRTC supplied bus
  ticketing APIs *to* IRCTC).
- **eRail.in**, historically described as an official schedule API, lists its
  service as suspended.
- **npm `irctc-api` / `irctc-connect`** are unofficial. `irctc-api` states it
  is "strictly for educational purposes" and that using it for activities that
  violate IRCTC's terms is prohibited. `irctc-connect` is deprecated.

**Decision:** estimate rail fares with a telescopic fare model, label them
`estimated` with an explicit "this is not a quote" note, and link to IRCTC and
NTES for the real thing. Scraping was never on the table.

### Intercity buses — same conclusion

No open API covers private operators or most state RTCs. redBus has no public
developer API. **Decision:** estimate, and link to the relevant state road
transport corporation (we carry official URLs for 16 states) plus redBus as a
clearly-labelled aggregator.

### Amadeus Self-Service — implemented but disabled

Several third-party migration guides state the Self-Service portal was
decommissioned on **17 July 2026**, leaving Enterprise-only access via a sales
contract. One listing claims existing keys stopped working and the official
SDKs were archived. Amadeus's own documentation pages still describe the
Self-Service test tier, and we could not reach their site to confirm either
way.

Those sources are competitors with an interest in the outcome, so we did not
treat the shutdown as established fact — but we did not build a dependency on
it either.

**Decision:** write the adapter to the documented API shape, default it to
off, and document the uncertainty. If your account works, set
`FLIGHT_PROVIDER=amadeus` and it activates. If it does not, nothing breaks.

### Hotel wholesalers — out of proportion

Hotelbeds and TBO Holidays have the deepest Indian inventory but need
commercial agreements costing low thousands to tens of thousands USD a year.
Booking.com's affiliate programme is revenue-share with no setup cost and is
the realistic path for a product at this stage — but it is an affiliate feed,
not a pricing API, so it does not slot into the cost model cleanly.

**Decision:** model accommodation from a tiered rate table adjusted by a
per-destination cost index, and link out to search. The
`quote_accommodation(live_nightly_inr=...)` parameter is the seam where a real
provider plugs in.

---

## What each provider failure actually does

TripWise degrades rather than fails. This was verified for real during
development: the build sandbox blocked OSRM and Open-Meteo, every retry
failed, and the API still returned correct ranked recommendations.

| Failure | Behaviour |
|---|---|
| Geocoder down | Falls to the next geocoder, then to Nominatim. If all fail and the user shared coordinates, planning proceeds with a coordinate label. If they only typed a name, a 422 explains that the origin could not be resolved. |
| Routing down | Falls back to the terrain-adjusted distance estimate. Provenance stays `estimated`. |
| Places down | The curated dataset carries the itinerary. |
| Weather down | Seasonal scoring falls back to the curated best-months window. |
| Flight/hotel API down | Prices fall back to the cost model, labelled `estimated`. |
| LLM down or invalid output | The deterministic planner produces the itinerary. The response says `generated_by: "deterministic"`. |
| Cache (Redis) down | Switches to in-process caching. Never fails a request. |

Every failure is surfaced to the user in `meta.provider_failures` rather than
hidden.

### Circuit breaking

A provider that fails repeatedly is skipped for 60 seconds instead of being
retried on every candidate destination. Without this, one dead dependency cost
**20 seconds** per request; with it, the same request takes 5.5 s on the first
call and **63 ms** once the circuit is open. A transport-level failure (DNS,
connect, proxy, TLS) opens the circuit immediately, since retrying inside the
same request cannot fix an unreachable host.

---

## Rough operating cost

For a small deployment serving ~1,000 trip searches a month, with 6-hour
caching and refinement limited to shortlisted destinations only:

| Setup | Monthly cost | What you get |
|---|---|---|
| Demo mode | **₹0** | Everything except live prices and typed-city search |
| Geoapify + keyless | **₹0** | Typed-city search, real road distances, live places |
| Geoapify + Anthropic | **~₹400–1,700** | The above plus AI-written itineraries |
| Google Maps + Anthropic | **~₹1,700–8,000** | Highest data quality; watch the per-SKU billing |
| Everything incl. Amadeus | **varies** | Live fares, if Self-Service is available to you |

Request volume per search is deliberately low: the broad pass over all 58
destinations makes **zero** provider calls, and only the shortlisted 5–8 get
live routing, weather and images. One search is roughly 5–8 provider calls
before caching, and far fewer after.

---

## Compliance notes

- **Attribution.** OpenStreetMap attribution is rendered by Leaflet and in the
  footer. Wikimedia image credits are rendered on each cover. **If you enable
  LocationIQ's free tier you must add their attribution link yourself.**
- **Caching.** Default TTL is 6 hours. Some provider licences restrict how
  long responses may be stored — check yours before raising `CACHE_TTL_SECONDS`.
- **Open-Meteo is non-commercial on the free tier.** Subscribe or set
  `ENABLE_OPEN_METEO=false` for commercial use.
- **No scraping anywhere in this codebase.** Every outbound request goes to a
  documented API endpoint through `providers/http.py`.
