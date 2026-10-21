"""Typed access to the curated destination and airport datasets.

These files are the *candidate pool* for discovery. They are versioned in the
repository, carry CURATED provenance, and are never presented as live data.
A Places provider, when configured, enriches them -- it does not replace them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..services.geo_math import haversine_km

DATA_DIR = Path(__file__).parent


@dataclass(frozen=True, slots=True)
class Attraction:
    """A point of interest within a destination."""

    name: str
    category: str
    lat: float
    lon: float
    entry_fee_inr: float
    opening_hours: str
    visit_minutes: int
    rating: float | None = None

    @property
    def is_free(self) -> bool:
        return self.entry_fee_inr <= 0


@dataclass(frozen=True, slots=True)
class AccessPoint:
    """Nearest airport or railhead, with the road distance to reach it."""

    name: str
    distance_km: float
    code: str | None = None

    @property
    def exists(self) -> bool:
        return bool(self.name) and "no rail" not in self.name.lower()


@dataclass(frozen=True, slots=True)
class Destination:
    """A candidate destination."""

    id: str
    name: str
    region: str
    state: str
    lat: float
    lon: float
    tier: str
    blurb: str
    tags: tuple[str, ...]
    quality: float
    popularity: float
    cost_index: dict[str, float]
    best_months: tuple[int, ...]
    airport: AccessPoint
    railhead: AccessPoint
    stay_types: tuple[str, ...]
    ideal_days: tuple[int, int]
    local_transport: str
    attractions: tuple[Attraction, ...] = field(default=())

    # ---- Derived characteristics -----------------------------------------
    @property
    def terrain(self) -> str:
        """Terrain class, used to pick a road-distance detour factor."""
        if self.id in {"spiti", "leh-ladakh"}:
            return "high_himalaya"
        # Only ridge-road destinations count as hills. A "trekking" tag on a
        # plateau site like Hampi must not slow the drive to it.
        hilly = {"hill_station", "mountains"}
        return "hills" if hilly & set(self.tags) else "plains"

    @property
    def attraction_variety(self) -> float:
        """0-1 score for how many *different kinds* of things there are to do."""
        if not self.attractions:
            return 0.0
        kinds = {a.category for a in self.attractions}
        return min(1.0, len(kinds) / 6.0)

    @property
    def free_attraction_share(self) -> float:
        if not self.attractions:
            return 0.0
        return sum(1 for a in self.attractions if a.is_free) / len(self.attractions)

    def supports_stay(self, stay_type: str) -> bool:
        return stay_type in self.stay_types

    def in_season(self, month: int) -> bool:
        return month in self.best_months

    def season_distance(self, month: int) -> int:
        """Months away from the nearest in-season month (0 = in season)."""
        if not self.best_months:
            return 0
        return min(min(abs(month - m), 12 - abs(month - m)) for m in self.best_months)

    def distance_km_to(self, lat: float, lon: float) -> float:
        return haversine_km(self.lat, self.lon, lat, lon)


@dataclass(frozen=True, slots=True)
class Airport:
    """A commercial airport."""

    iata: str
    name: str
    city: str
    lat: float
    lon: float
    tier: str

    @property
    def is_well_connected(self) -> bool:
        return self.tier in ("major", "regional")


def _parse_destination(raw: dict[str, Any]) -> Destination:
    airport_raw = raw.get("airport") or {}
    rail_raw = raw.get("railhead") or {}
    return Destination(
        id=raw["id"],
        name=raw["name"],
        region=raw["region"],
        state=raw["state"],
        lat=float(raw["lat"]),
        lon=float(raw["lon"]),
        tier=raw["tier"],
        blurb=raw["blurb"],
        tags=tuple(raw["tags"]),
        quality=float(raw["quality"]),
        popularity=float(raw["popularity"]),
        cost_index={k: float(v) for k, v in raw["cost_index"].items()},
        best_months=tuple(int(m) for m in raw["best_months"]),
        airport=AccessPoint(
            name=airport_raw.get("name", ""),
            distance_km=float(airport_raw.get("km", 0) or 0),
            code=airport_raw.get("iata"),
        ),
        railhead=AccessPoint(
            name=rail_raw.get("name", ""),
            distance_km=float(rail_raw.get("km", 0) or 0),
        ),
        stay_types=tuple(raw["stay_types"]),
        ideal_days=(int(raw["ideal_days"][0]), int(raw["ideal_days"][1])),
        local_transport=raw["local_transport"],
        attractions=tuple(
            Attraction(
                name=a["name"],
                category=a["category"],
                lat=float(a["lat"]),
                lon=float(a["lon"]),
                entry_fee_inr=float(a.get("fee", 0) or 0),
                opening_hours=a.get("hours", "Hours not published"),
                visit_minutes=int(a.get("minutes", 60)),
                rating=float(a["rating"]) if a.get("rating") is not None else None,
            )
            for a in raw.get("attractions", [])
        ),
    )


@lru_cache(maxsize=1)
def load_destinations() -> tuple[Destination, ...]:
    """All curated destinations, loaded once per process."""
    payload = json.loads((DATA_DIR / "destinations.json").read_text(encoding="utf-8"))
    return tuple(_parse_destination(d) for d in payload["destinations"])


@lru_cache(maxsize=1)
def dataset_version() -> str:
    payload = json.loads((DATA_DIR / "destinations.json").read_text(encoding="utf-8"))
    return str(payload.get("version", "unknown"))


@lru_cache(maxsize=1)
def load_airports() -> tuple[Airport, ...]:
    payload = json.loads((DATA_DIR / "airports.json").read_text(encoding="utf-8"))
    return tuple(
        Airport(
            iata=a["iata"],
            name=a["name"],
            city=a["city"],
            lat=float(a["lat"]),
            lon=float(a["lon"]),
            tier=a.get("tier", "small"),
        )
        for a in payload["airports"]
    )


@lru_cache(maxsize=1)
def _destination_index() -> dict[str, Destination]:
    return {d.id: d for d in load_destinations()}


def get_destination(destination_id: str) -> Destination | None:
    return _destination_index().get(destination_id)


def nearest_airport(lat: float, lon: float, *, well_connected_only: bool = True) -> tuple[Airport, float] | None:
    """Closest airport to a point, with straight-line distance in km."""
    candidates = [
        a for a in load_airports() if a.is_well_connected or not well_connected_only
    ]
    if not candidates:
        return None
    best = min(candidates, key=lambda a: haversine_km(lat, lon, a.lat, a.lon))
    return best, round(haversine_km(lat, lon, best.lat, best.lon), 1)


def airport_by_iata(code: str) -> Airport | None:
    code = (code or "").strip().upper()
    for a in load_airports():
        if a.iata == code:
            return a
    return None


def all_tags() -> list[str]:
    """Every tag present in the dataset, for UI interest pickers."""
    tags: set[str] = set()
    for d in load_destinations():
        tags.update(d.tags)
    return sorted(tags)
