# Architecture

## The shape of the problem

TripWise inverts the usual travel-planning flow. The user does not supply a
destination; they supply constraints, and the system works out where those
constraints can actually take them. That makes **destination discovery the
product**, and it drives three decisions that shape everything else:

1. **The cost model must be deterministic and server-side.** If the user is
   going to trust "you can afford Hampi but not Manali", the arithmetic behind
   it has to be reproducible, auditable, and the same every time.
2. **Search has to be cheap.** Costing 58 destinations against a budget cannot
   mean 58 API calls. It makes **zero**.
3. **Provenance has to be first-class.** Mixing a modelled bus fare with a
   live hotel rate is fine — presenting them identically is not.

## Request flow

```
Browser
  │  POST /api/recommendations   {origin, budget, dates, travellers, mode, …}
  ▼
routers/recommendations.py ──► services/planner.py        (orchestration)
                                      │
            ┌─────────────────────────┼──────────────────────────┐
            ▼                         ▼                          ▼
   providers/geocoding.py     services/ranking.py        providers/* (live)
   resolve the origin         ┌──────────────────┐       routing · weather
                              │  BROAD PASS      │       flights · hotels
                              │  all 58 dests    │       places  · images
                              │  0 network calls │              ▲
                              └────────┬─────────┘              │
                                       │ top 5–8                │
                                       ▼                        │
                              ┌──────────────────┐              │
                              │  REFINE PASS     │──────────────┘
                              │  live data, then │
                              │  re-score        │
                              └────────┬─────────┘
                                       ▼
                              RecommendationResponse
```

Two passes exist specifically so that widening the candidate pool stays free.
A destination the budget rules out never costs an API call.

## Modules

### Backend (`backend/app/`)

| Module | Responsibility |
|---|---|
| `config.py` | Settings from env. Also decides *which* providers are live and whether we are in demo mode. Never exposes a credential. |
| `schemas/` | The Pydantic contract. `enums.py` defines `DataSource`, `CostBasis` and the rest of the vocabulary the whole system shares. |
| `data/` | The curated dataset: 58 destinations, 308 attractions, 60 airports, plus `catalog.py` for typed access. |
| `services/cost_model.py` | **Every rate in one file.** Fares, tariffs, GST slabs, occupancy, feasibility thresholds. The only place money constants live. |
| `services/transport.py` | Mode-aware round-trip pricing. Owns the per-person vs per-vehicle distinction. |
| `services/accommodation.py` | Room/bed occupancy and substitution when a stay type is unavailable. |
| `services/activities.py` | Attraction selection and geographic day-clustering. |
| `services/budget.py` | Assembles line items into a `CostBreakdown` that reconciles by construction. |
| `services/ranking.py` | Feasibility filters, the weighted score, shortfall and interest-gap explanations. |
| `services/itinerary.py` | Deterministic day planner, LLM prompt, and the merge that re-derives every number. |
| `services/planner.py` | Orchestration: the only layer that knows about both the pure engine and the network. |
| `providers/` | One adapter per external service, plus shared HTTP, caching and circuit breaking. |
| `routers/` | HTTP surface. Thin — all logic lives in services. |
| `db/` | SQLAlchemy models and sessions. |

### Frontend (`frontend/src/`)

| Module | Responsibility |
|---|---|
| `lib/api.ts` | The only module that touches the network. Talks exclusively to our own backend. |
| `lib/types.ts` | Hand-written mirrors of the backend schemas. |
| `lib/format.ts` | Indian-format currency, durations, distances — one implementation, used everywhere. |
| `lib/geolocation.ts` | Browser geolocation. Returns coordinates only; never claims to know an address. |
| `components/ProvenanceBadge.tsx` | The label that keeps the UI honest. |
| `components/CostBreakdown.tsx` | The budget dashboard, derived entirely from `categories`. |
| `pages/` | Four screens: setup, recommendations, itinerary, saved trips. |

---

## Key design decisions

### 1. The total is the sum of the line items — by construction

`CostBreakdown.categories` is a **computed property**, not a stored field. It
is derived from `line_items` plus tax and contingency every time it is read.
There is no code path that can make the headline figure disagree with the
breakdown, because there is only one source of truth.

```python
@computed_field
@property
def categories(self) -> list[CategoryTotal]:
    buckets = {}
    for item in self.line_items:
        buckets[item.category] = buckets.get(item.category, 0) + item.total_inr
    ...
```

The test suite asserts this across every combination of destination, group
size and travel mode.

### 2. `CostBasis` makes double-counting visible

Every line item records *how* its quantity is counted:
`PER_PERSON_TRIP`, `PER_VEHICLE_TRIP`, `PER_ROOM_PER_NIGHT`, `PER_BED_PER_NIGHT`…

This is not decoration. The classic bug in a trip budgeter is multiplying a
car's fuel cost by the number of passengers. Here, fuel is
`PER_VEHICLE_TRIP` with `units = vehicles`, so the mistake cannot be made
silently — and a test asserts that four people in one car pay for one car.

### 3. One paid provider per capability, one free fallback behind it

Every capability resolves to a chain: Google first when a key is configured,
the free keyless service otherwise and as the fallback when Google fails. That
keeps the deployment decision to a single question per capability -- pay or
don't -- instead of a matrix of overlapping vendors, and it means the app is
fully functional before any billing account exists.

### 4. The LLM is contained, not trusted

The model receives a fixed list of candidate activities and returns an
ordering with prose. The merge step (`_merge_llm_days`) then:

- looks each proposed place up **by name in our own data**;
- recomputes distance, travel time, transport cost and entry fee from that data;
- downgrades anything it does not recognise to an unpriced `free_time` block.

A hallucinated price is discarded because no price the model emits is ever
read. A hallucinated place cannot acquire coordinates or a cost. If the model
is unreachable, returns malformed JSON, or fails schema validation twice, the
deterministic planner takes over and the response says so.

### 5. Budget fit rewards *using* the budget, not minimising it

A trip costing 40% of the budget is usually a worse trip for no reason. The
budget-fit curve peaks between 40% and 90% utilisation, drops at 90–100%
(no slack), and collapses past 100%. Combined with a travel-time penalty, this
is why a cheap destination 14 hours away loses to a slightly pricier one
nearby.

A consequence worth knowing: **a shorter trip can cost more.** Hyderabad→Goa
is ~14 h each way by train; on a 3-day trip that is 39% of the available
hours, past the feasibility limit, so flying is the only option — and flying
costs more. Give the same trip 6 days and the train qualifies and the total
drops. This is tested, not accidental.

### 6. Two passes, so discovery is free

The broad pass runs entirely in-process: 58 destinations × up to 4 modes,
fully costed, in about 50 ms. Only the shortlist gets live routing, weather
and images. Adding destinations to the dataset costs nothing in API spend.

### 7. Circuit breaking is a correctness feature, not just speed

Without it, an unreachable provider cost its full timeout-and-retry budget on
every shortlisted destination — 20 seconds for one request. The breaker opens
after repeated failures and short-circuits for 60 seconds. Transport-level
failures open it immediately, because retrying cannot reach a host that has no
route. Measured: 20.3 s → 5.5 s cold → 63 ms warm.

### 8. Nothing secret reaches the browser

Every provider call originates server-side. The frontend's only network
dependency is our own `/api`, plus map tiles -- OpenStreetMap by default, which
needs no key, or Google when a referrer-restricted browser key is supplied.
`scripts/verify-no-secrets.sh` fails the build if a credential pattern appears
in the bundle, or if the bundle references a provider domain directly.

---

## Data model

```
users                  (reserved for when auth is added)
  └── saved_trips      request JSON + itinerary snapshot
destination_overrides  per-deployment catalogue additions
```

`saved_trips` stores both the **request** (so a plan can be re-run against
current rates) and a **snapshot** (so the user sees exactly what they saved).
Recommendations themselves are never cached in the database — they are cheap
to recompute and would go stale.

Trips are scoped by an `owner_token`: a random 32-hex-character id the browser
generates once and sends as `X-TripWise-Owner`. It is **not** authentication
and grants nothing beyond listing trips saved with the same token. Reads and
deletes filter on it, and a mismatch returns 404 rather than 403 so the
endpoint cannot be used to probe for trip ids.

## Extending it

**Add destinations** — append to `backend/app/data/destinations.json`. The
schema is validated on load; no code changes needed.

**Add a provider** — write an adapter in `backend/app/providers/` that returns
a typed result or `None`, add a settings field, and add it to the relevant
chain property in `config.py`. The fallback behaviour comes for free.

**Change a rate** — edit `backend/app/services/cost_model.py` and run the
tests. Nothing else hardcodes money.

**Support another currency** — `CostBreakdown.currency` already threads
through. The work is a conversion layer in `cost_model.py` and localised
formatting in `frontend/src/lib/format.ts`.
