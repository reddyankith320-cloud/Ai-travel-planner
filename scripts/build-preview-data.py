"""Generate the example trips for the static preview build.

The preview is a single web page with no server behind it, so it cannot run the
planner. Instead it replays results that the REAL backend computed for a fixed
set of example trips. Nothing here is invented: every number comes out of the
same code path the live app uses, with providers disabled so the run is fully
deterministic.

    python scripts/build-preview-data.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

# Hermetic: no provider may be contacted while generating.
os.environ.update(
    DATABASE_URL=f"sqlite+pysqlite:///{tempfile.mkdtemp()}/preview.db",
    ENABLE_NOMINATIM="false",
    ENABLE_OSRM="false",
    ENABLE_OVERPASS="false",
    ENABLE_OPEN_METEO="false",
    ENABLE_WIKIMEDIA_IMAGES="false",
    RATE_LIMIT_REQUESTS="100000",
)
for key in ("OPENAI_API_KEY", "GOOGLE_MAPS_SERVER_KEY", "GOOGLE_MAPS_BROWSER_KEY"):
    os.environ.pop(key, None)

from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402

START = (date.today() + timedelta(days=45)).isoformat()

CITIES = {
    "Hyderabad": (17.385, 78.486),
    "New Delhi": (28.6139, 77.2090),
    "Mumbai": (19.076, 72.877),
    "Bengaluru": (12.9716, 77.5946),
    "Kolkata": (22.5726, 88.3639),
}

SCENARIOS = [
    ("Beaches and heritage from Hyderabad", "Hyderabad", 35000, 2, 4, "budget_hotel", "balanced", ["beaches", "heritage"]),
    ("Hill stations from Delhi", "New Delhi", 90000, 3, 6, "midrange_hotel", "relaxed", ["hill_station", "nature"]),
    ("Mountains from Mumbai (budget too small)", "Mumbai", 60000, 3, 5, "budget_hotel", "balanced", ["mountains", "trekking"]),
    ("Open-ended from Bengaluru", "Bengaluru", 40000, 2, 4, "budget_hotel", "balanced", []),
    ("Wildlife from Kolkata", "Kolkata", 60000, 4, 5, "budget_hotel", "balanced", ["wildlife"]),
    ("Budget too low from Hyderabad", "Hyderabad", 9500, 2, 4, "budget_hotel", "balanced", []),
]


def main() -> None:
    client = TestClient(create_app())
    out: dict = {
        "generated_on": date.today().isoformat(),
        "start_date": START,
        "config": client.get("/api/meta/config").json(),
        "options": client.get("/api/meta/options").json(),
        "scenarios": [],
    }

    for label, city, budget, pax, days, stay, pace, interests in SCENARIOS:
        lat, lon = CITIES[city]
        trip = {
            "origin": {"coordinates": {"lat": lat, "lon": lon}, "label": city, "detection": "manual"},
            "budget_inr": budget,
            "travelers": pax,
            "days": days,
            "start_date": START,
            "travel_mode": "any",
            "accommodation": stay,
            "pace": pace,
            "interests": interests,
            "max_one_way_hours": None,
            "include_stretch_options": True,
            "assumption_overrides": {},
        }
        response = client.post("/api/recommendations", json={**trip, "limit": 5})
        response.raise_for_status()
        recs = response.json()

        itineraries: dict[str, dict] = {}
        for rec in recs["recommendations"] + recs["stretch_options"]:
            dest = rec["destination"]["id"]
            it = client.post(
                "/api/itinerary",
                json={"destination_id": dest, "trip": trip, "excluded_place_names": [], "regenerate": False},
            )
            it.raise_for_status()
            itineraries[dest] = it.json()

        out["scenarios"].append(
            {
                "id": f"s{len(out['scenarios']) + 1}",
                "label": label,
                "request": trip,
                "recommendations": recs,
                "itineraries": itineraries,
            }
        )
        print(f"  {label:44} {len(recs['recommendations'])} recs, {len(itineraries)} itineraries")

    target = ROOT / "frontend" / "src" / "preview" / "data.json"
    target.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    print(f"\nwrote {target.relative_to(ROOT)}  ({target.stat().st_size / 1e6:.2f} MB)")


if __name__ == "__main__":
    main()
