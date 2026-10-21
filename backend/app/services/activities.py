"""Attraction selection and activity costing.

The same selection runs for ranking and for itinerary building, so the cost
shown on a recommendation card is the cost of the plan the user then sees.
If these diverged, the headline figure would quietly stop matching the detail.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..data.catalog import Attraction, Destination
from ..schemas.common import Provenance
from ..schemas.enums import Confidence, CostBasis, CostCategory, DataSource, Interest, Pace
from ..schemas.cost import CostLineItem
from . import cost_model as cm
from .geo_math import balanced_clusters, nearest_neighbour_order

# Which attraction categories satisfy which stated interest.
INTEREST_CATEGORIES: dict[Interest, set[str]] = {
    Interest.BEACHES: {"beach"},
    Interest.MOUNTAINS: {"viewpoint", "trek"},
    Interest.HILL_STATION: {"viewpoint", "garden", "lake"},
    Interest.HERITAGE: {"heritage", "museum"},
    Interest.WILDLIFE: {"wildlife"},
    Interest.ADVENTURE: {"adventure", "activity"},
    Interest.TREKKING: {"trek"},
    Interest.SPIRITUAL: {"spiritual"},
    Interest.FOOD: {"food"},
    Interest.NIGHTLIFE: {"food", "shopping"},
    Interest.SHOPPING: {"shopping"},
    Interest.NATURE: {"nature", "waterfall", "lake", "garden"},
    Interest.LAKES: {"lake"},
    Interest.WATERFALLS: {"waterfall"},
    Interest.DESERT: {"desert"},
    Interest.BACKWATERS: {"activity", "nature"},
    Interest.ART_CULTURE: {"art_culture", "museum", "heritage"},
    Interest.PHOTOGRAPHY: {"viewpoint", "photography"},
    Interest.OFFBEAT: {"offbeat"},
    Interest.WELLNESS: {"wellness"},
}


@dataclass(slots=True)
class ActivityPlan:
    """Chosen attractions, grouped into days and costed."""

    selected: list[Attraction]
    day_groups: list[list[Attraction]]
    line_items: list[CostLineItem] = field(default_factory=list)
    skipped_for_budget: list[Attraction] = field(default_factory=list)

    @property
    def total_inr(self) -> float:
        return round(sum(i.total_inr for i in self.line_items), 2)

    @property
    def paid_count(self) -> int:
        return sum(1 for a in self.selected if not a.is_free)


def _interest_bonus(attraction: Attraction, interests: list[Interest]) -> float:
    """0-1 bonus for how well an attraction matches the stated interests."""
    if not interests:
        return 0.5
    wanted: set[str] = set()
    for interest in interests:
        wanted |= INTEREST_CATEGORIES.get(interest, set())
    return 1.0 if attraction.category in wanted else 0.15


def score_attraction(
    attraction: Attraction, interests: list[Interest], budget_pressure: float
) -> float:
    """Rank an attraction for inclusion.

    `budget_pressure` in 0-1 says how tight money is; under pressure, free
    attractions are favoured over ticketed ones.
    """
    rating = (attraction.rating or 4.0) / 5.0
    interest = _interest_bonus(attraction, interests)
    # Free entry is worth more when the budget is strained.
    thrift = 1.0 if attraction.is_free else max(0.0, 1.0 - attraction.entry_fee_inr / 2500.0)
    return round(
        0.42 * interest + 0.33 * rating + 0.25 * thrift * budget_pressure,
        4,
    )


def select_attractions(
    destination: Destination,
    *,
    days: int,
    pace: Pace,
    interests: list[Interest],
    budget_pressure: float = 0.3,
    excluded_names: set[str] | None = None,
) -> list[Attraction]:
    """Pick the attractions a trip of this length and pace can actually fit."""
    excluded = {n.strip().lower() for n in (excluded_names or set())}
    pool = [a for a in destination.attractions if a.name.lower() not in excluded]
    if not pool:
        return []

    capacity = cm.ACTIVITIES_PER_DAY[pace] * days
    ranked = sorted(
        pool,
        key=lambda a: (-score_attraction(a, interests, budget_pressure), a.name),
    )
    chosen = ranked[:capacity]

    # Respect the daily time budget: a day of 8-hour treks is not a day.
    available_minutes = cm.ACTIVE_HOURS_PER_DAY[pace] * 60 * days
    kept: list[Attraction] = []
    spent = 0.0
    for attraction in chosen:
        if spent + attraction.visit_minutes <= available_minutes or not kept:
            kept.append(attraction)
            spent += attraction.visit_minutes
    return kept


def group_by_day(
    attractions: list[Attraction], days: int, base: tuple[float, float], seed: int = 7
) -> list[list[Attraction]]:
    """Cluster attractions geographically, one cluster per day.

    Grouping by location is what stops a plan from crossing town four times.
    Within a day, stops are ordered nearest-neighbour from the accommodation.
    """
    if days <= 0:
        return []
    if not attractions:
        return [[] for _ in range(days)]

    points = [(a.lat, a.lon) for a in attractions]
    buckets = balanced_clusters(points, days, seed=seed)

    # Visit the cluster nearest the base first, so day 1 starts gently.
    def bucket_distance(bucket: list[int]) -> float:
        if not bucket:
            return float("inf")
        from .geo_math import haversine_km

        return min(haversine_km(base[0], base[1], points[i][0], points[i][1]) for i in bucket)

    buckets.sort(key=bucket_distance)

    grouped: list[list[Attraction]] = []
    for bucket in buckets:
        if not bucket:
            grouped.append([])
            continue
        local_points = [points[i] for i in bucket]
        order = nearest_neighbour_order(base, local_points)
        grouped.append([attractions[bucket[i]] for i in order])
    return grouped


def plan_activities(
    destination: Destination,
    *,
    days: int,
    travelers: int,
    pace: Pace,
    interests: list[Interest],
    overrides: cm.ModelOverrides,
    budget_pressure: float = 0.3,
    excluded_names: set[str] | None = None,
) -> ActivityPlan:
    """Select, group and cost the trip's activities."""
    selected = select_attractions(
        destination,
        days=days,
        pace=pace,
        interests=interests,
        budget_pressure=budget_pressure,
        excluded_names=excluded_names,
    )
    groups = group_by_day(selected, days, (destination.lat, destination.lon))

    items: list[CostLineItem] = []
    paid = [a for a in selected if not a.is_free]
    if paid:
        total_fee = sum(a.entry_fee_inr for a in paid)
        items.append(
            CostLineItem.build(
                category=CostCategory.ACTIVITIES,
                label=f"Entry fees and tickets ({len(paid)} paid activities)",
                basis=CostBasis.PER_PERSON_TRIP,
                units=travelers,
                unit_label="traveller",
                unit_price_inr=total_fee,
                provenance=Provenance(
                    source=DataSource.CURATED,
                    confidence=Confidence.MEDIUM,
                    note=(
                        "Published admission charges from our maintained dataset. "
                        "Treat as approximate -- gate prices change and some sites "
                        "charge extra for cameras or guides."
                    ),
                ),
                detail=", ".join(f"{a.name} (Rs {a.entry_fee_inr:,.0f})" for a in paid[:6])
                + (" ..." if len(paid) > 6 else ""),
            )
        )

    buffer_rate = (
        overrides.activity_buffer_per_person_per_day
        if overrides.activity_buffer_per_person_per_day is not None
        else cm.ACTIVITY_BUFFER_PER_PERSON_PER_DAY[pace]
    )
    if buffer_rate > 0:
        items.append(
            CostLineItem.build(
                category=CostCategory.ACTIVITIES,
                label="Allowance for local tours, rentals and unlisted tickets",
                basis=CostBasis.PER_PERSON_PER_DAY,
                units=travelers * days,
                unit_label="person-day",
                unit_price_inr=buffer_rate,
                provenance=Provenance.estimated(
                    f"{pace.value.title()} pace allowance for experiences we do not have a "
                    "published price for (scooter hire, guides, local tours)."
                ),
            )
        )

    return ActivityPlan(selected=selected, day_groups=groups, line_items=items)
