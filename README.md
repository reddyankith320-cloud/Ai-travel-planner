# TripWise

**A budget-first travel planner for India. You don't pick a destination — it works out where you can afford to go.**

Give TripWise where you're starting from, what you can spend, when you're
free and how you like to travel. It prices a trip to every destination it
knows, discards the ones that don't fit, ranks what's left on a published
scoring model, and builds a day-by-day plan for the one you choose.

There is no destination field anywhere in the app. That's the point.

```
Budget ₹35,000 · 2 travellers · 4 days from Hyderabad · any mode
   ↓
#1  Hampi        ₹12,840   train, 8h 5m each way    comfortable fit
#2  Mysuru       ₹14,005   train, 15h 26m           comfortable fit
#3  Tirupati     ₹12,577   train, 11h 4m            comfortable fit
#4  North Goa    ₹19,714   train, 13h 58m           comfortable fit
#5  Puducherry   ₹17,013   train, 16h 1m            comfortable fit
```

---

## What makes this different

**It runs with zero API keys.** Out of the box, destination discovery, budget
calculation, ranking and full day-by-day itineraries all work. Every figure
comes from a deterministic cost model and is labelled as an estimate. Nothing
is ever dressed up as a live quote.

**Every number reconciles.** The headline total is the sum of the line items —
by construction, not by convention. The category breakdown is a computed
projection of the same list, so the dashboard cannot drift from the total.
Tests assert this across every combination of destination, group size and
travel mode.

**It tells you where the numbers came from.** Every figure carries a
provenance badge: `live`, `cached`, `estimated` or `curated`. A modelled bus
fare never looks like a real one.

**It won't invent travel data.** There is no legitimate public API for Indian
Railways or intercity bus fares. Rather than scrape one, TripWise models those
fares, says plainly that they're estimates, and links to IRCTC and the state
RTCs for the real thing.

**The AI can't make up prices.** An LLM improves itinerary sequencing and
writing, but every rupee is recomputed server-side from TripWise's own data. A
hallucinated price can't reach you because no price the model emits is ever
read. Without an LLM key, a deterministic planner does the job.

---

## Quick start

### The fast way

```bash
git clone <your-repo-url> && cd Ai-travel-planner

# Backend
cd backend
python3 -m venv ../.venv && source ../.venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload --port 8000

# Frontend, in a second terminal
cd frontend
npm install
npm run dev
```

Open <http://localhost:5173>. No configuration, no API keys, no database
server. It starts in demo mode and tells you so.

### With Docker

```bash
cp .env.example .env     # it runs fine empty
docker compose up --build
```

Then <http://localhost:8080>. This brings up PostgreSQL, Redis, the API (with
migrations applied) and the frontend behind nginx.

---

## Adding API keys

**You need none to start.** See **[docs/API_KEYS.md](docs/API_KEYS.md)** for
exactly what each key buys, step-by-step signup instructions, and what it
costs. The short version:

| Want | Set | Cost |
|---|---|---|
| Let users type a city instead of sharing location | `GEOAPIFY_API_KEY` | Free tier |
| AI-written itineraries | `ANTHROPIC_API_KEY` | Per token |
| Real road distances | `OPENROUTESERVICE_API_KEY` | Free tier |
| Live flight/hotel prices | `AMADEUS_CLIENT_ID` + `_SECRET` | ⚠️ [status unconfirmed](docs/PROVIDERS.md) |
| Indian train or bus fares | — | **No legitimate API exists.** Always estimated. |

Copy `.env.example` to `backend/.env` and fill in only what you need.

> **Never put a private key in `frontend/.env`.** Anything in a `VITE_*`
> variable is compiled into the browser bundle and is public. Run
> `./scripts/verify-no-secrets.sh` to prove your build is clean.

---

## How it works

1. **Discover** — every destination in the catalogue is costed against your
   constraints. This pass makes **zero network calls**, so searching widely is
   free.
2. **Filter** — anything over budget, unreachable in your timeframe, or too
   close to be a trip is dropped, each with a stated reason.
3. **Rank** — a transparent weighted score. The weights are published at
   `/api/meta/ranking` and shown in the UI.
4. **Refine** — only the top few get live routing, weather and images, then
   they're re-scored.
5. **Plan** — the chosen destination gets a day-by-day itinerary with
   activities grouped by area so you aren't crossing town four times a day.

### The ranking model

| Factor | Weight | What it measures |
|---|---|---|
| Budget fit | 24% | How well the trip *uses* your budget — not how cheap it is |
| Interest match | 22% | How much of what you asked for the place actually offers |
| Value for money | 15% | Quality per rupee of daily spend, relative to the alternatives |
| Travel time | 15% | Share of your trip spent getting there and back |
| Destination quality | 12% | Curated quality, variety of things to do, draw |
| Season fit | 7% | Whether your dates are the right time of year |
| Stay availability | 3% | Whether your preferred accommodation exists there |
| Estimate confidence | 2% | How much of the cost is backed by live data |

Budget fit peaks between 40% and 90% utilisation. A trip costing 40% of your
budget is usually a worse trip for no reason, and one costing 99% has no
slack.

**A consequence worth knowing:** a *shorter* trip can cost *more*. Hyderabad
to Goa is ~14 h each way by train — on a 3-day trip that's 39% of your hours,
past the feasibility limit, so flying becomes the only option and the price
jumps. Give it 6 days and the train qualifies and the total drops. The UI
always shows which mode it picked.

### The cost model

Every rate lives in one file: `backend/app/services/cost_model.py`.

- **Intercity transport, both directions.** Rail uses a telescopic fare table
  (SL/3A/2A, including reservation and superfast charges) because that's how
  Indian Railways actually prices. Road travel is priced **per vehicle** and
  shared across the group. Flights factor in distance, lead time, seasonality
  and airport transfers at both ends.
- **Accommodation by occupancy.** Hostels are priced per bed; hotels per room.
  Four travellers in hostels pay for four beds; in budget hotels, two rooms.
- **Food, local transport and activities,** adjusted by a per-destination cost
  index, with a shared-ride discount as the group grows.
- **GST at the real slabs** — 12% or 18% on accommodation depending on tariff,
  5% on AC transport — applied per line item, never as a blanket percentage.
- **A contingency buffer** on top, default 8% and configurable.

Every assumption is returned to the client and is editable. Change the food
allowance and the whole estimate recalculates.

---

## Project layout

```
backend/
  app/
    config.py            settings + which providers are live
    data/                58 destinations, 308 attractions, 60 airports
    schemas/             the Pydantic contract
    services/
      cost_model.py      every rate, in one place
      transport.py       per-person vs per-vehicle pricing
      accommodation.py   room/bed occupancy
      activities.py      selection + geographic day-clustering
      budget.py          the calculator
      ranking.py         filters, scoring, shortfall explanations
      itinerary.py       deterministic planner + LLM containment
      planner.py         orchestration
    providers/           one adapter each, all optional
    routers/             HTTP surface
    db/                  models + sessions
  alembic/               migrations
  tests/                 335 tests
frontend/
  src/
    lib/                 api client, types, formatting, geolocation
    components/          provenance badges, cost dashboard, maps
    pages/               setup · recommendations · itinerary · saved
  e2e/smoke.mjs          browser smoke test
docs/
  API_KEYS.md            what to get, how, and what it costs
  PROVIDERS.md           what we chose and rejected, and why
  ARCHITECTURE.md        design decisions
scripts/
  verify-no-secrets.sh   fails if a credential reaches the bundle
```

---

## Testing

```bash
cd backend && pytest                 # 335 tests
cd frontend && npm test              # 36 tests
cd frontend && npx tsc --noEmit      # typecheck
./scripts/verify-no-secrets.sh       # credential scan of the built bundle
```

The backend suite runs fully offline — no test touches the network. Provider
behaviour is exercised against scripted fakes, so the failure paths are
genuinely covered.

Coverage focuses where it matters:

- **Budget calculator** — reconciliation across every destination/group/mode
  combination, occupancy rules, round-trip inclusion, tax slabs, contingency,
  per-vehicle vs per-person, assumption overrides.
- **Ranking engine** — ordering, score reconciliation, feasibility filters,
  and that results actually *change* when budget, travellers, days, mode or
  interests change.
- **LLM containment** — that a model emitting fabricated prices or invented
  places cannot affect a single rupee or put a fake venue in your plan.
- **Providers** — retry and backoff, no-retry on 4xx, circuit breaking,
  caching, fallback chains, and that no credential appears in a logged URL.
- **API** — contracts, validation, error handling, saved-trip scoping.

### Browser smoke test

```bash
# with both servers running
cd frontend && npm run e2e -- ./screenshots
```

Drives Chromium at 390px and 1440px through the whole flow. Fails on any
console error, uncaught exception, or horizontal overflow.

---

## API

Interactive docs at `/docs` once running.

| Endpoint | Purpose |
|---|---|
| `POST /api/recommendations` | **Discover destinations.** Takes constraints; there is deliberately no destination parameter. |
| `POST /api/itinerary` | Day-by-day plan for a chosen destination |
| `GET /api/geo/reverse` | Name a point from browser geolocation |
| `GET /api/geo/search` | Resolve a typed place name |
| `GET /api/meta/config` | What this deployment can do (never credentials) |
| `GET /api/meta/ranking` | The published ranking weights |
| `GET /api/meta/destinations` | The full candidate catalogue |
| `GET/POST/DELETE /api/trips` | Saved trips |

An empty `recommendations` list is a valid answer, not an error — `shortfall`
then explains the gap and what would close it.

---

## Reading the numbers

| Badge | Meaning |
|---|---|
| `Live` | Fetched from a provider API during this request |
| `Live (cached)` | An earlier provider response, still within its cache window |
| `Estimate` | Computed by TripWise's cost model. **Not a quote.** |
| `Reference data` | From the curated dataset in this repo. Approximate. |

TripWise sells nothing and holds no inventory. Confirm fares, room rates and
admission charges on the operator's own site before committing money.

---

## Known limitations

- **Coverage is India-only.** The architecture isn't India-specific, but the
  dataset, the rate tables and the GST logic are.
- **Rail and bus fares are always estimated.** No legitimate API exists. Spot
  checks land within ~10% of real fares, but they are models, not quotes.
- **Airfare estimates are the least reliable figure on screen** and are marked
  low-confidence. Real fares move daily.
- **Opening hours and entry fees are approximate**, from the curated dataset.
  Verify before you travel.
- **Saved trips have no authentication.** They're scoped by a browser-local
  token — a convenience, not a security boundary. The `users` table exists for
  when real auth is added; shipping half-built auth would be worse than none.
- **The rate limiter is per-process.** Behind multiple workers, put a shared
  limiter in front.
- **Amadeus integration is unverified** — see [docs/PROVIDERS.md](docs/PROVIDERS.md).

## Licence and attribution

Map tiles and place geometry © [OpenStreetMap](https://www.openstreetmap.org/copyright)
contributors (ODbL). Destination photographs via Wikipedia/Wikimedia under
their respective licences, credited in the UI. Weather data from Open-Meteo
(CC-BY 4.0) — **free tier is non-commercial**.

If you enable LocationIQ's free tier, you must add their attribution link
yourself.
