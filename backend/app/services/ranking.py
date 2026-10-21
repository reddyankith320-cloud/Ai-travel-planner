"""The destination recommendation engine.

This runs *before* any itinerary is generated and before any LLM is called.
The user never types a destination; this module decides which places are
reachable, affordable and worth the journey, then ranks them.

Two passes, deliberately:

  1. A broad pass over every candidate using estimated road distances. No
     provider calls, so widening the candidate pool costs nothing and we never
     burn an API quota on a destination the budget rules out anyway.
  2. A refinement pass over the finalists only, where live routing and live
     fares (when configured) replace the estimates.

Ranking is a transparent weighted sum. Every component's score, weight and
one-line explanation is returned to the client, so a recommendation can always
be challenged against its own arithmetic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Iterable, Sequence

from ..config import Settings
from ..data.catalog import Destination, load_destinations
from ..schemas.common import Provenance
from ..schemas.cost import CostBreakdown
from ..schemas.enums import (
    BudgetFit,
    Confidence,
    Interest,
    TravelMode,
)
from ..schemas.recommendation import (
    BudgetShortfall,
    InterestGap,
    RejectedDestination,
    ScoreBreakdown,
    ScoreComponent,
    UnmetInterest,
)
from ..schemas.trip import TripRequest
from . import cost_model as cm
from .accommodation import AccommodationQuote, quote_accommodation, resolve_stay_type
from .activities import INTEREST_CATEGORIES, ActivityPlan, plan_activities
from .budget import BudgetInputs, compute_breakdown, minimum_viable_cost
from .transport import (
    RouteInfo,
    TransportQuote,
    build_route,
    quote_bus,
    quote_car,
    quote_flight,
    quote_train,
)

# --------------------------------------------------------------------------
# Weights. These sum to 1.0 and are returned with every response.
# --------------------------------------------------------------------------

# Weights sum to 1.0 and are published at /api/meta/ranking.
#
# Interest match carries 0.22 after A/B testing against the alternative at
# 0.16. At the lower weight, a search from Delhi for "hill station + nature"
# put a desert temple town second, because it was cheaper per day: value and
# budget fit together outvoted the only signal of what the user actually
# wanted. At 0.22 the same search returns Shimla, Manali, Bir Billing,
# Nainital and Kasol. Searches that state no interests are unaffected, because
# interest match then returns a flat neutral score for every candidate.
RANKING_WEIGHTS: dict[str, float] = {
    "budget_fit": 0.24,
    "interest_match": 0.22,
    "value": 0.15,
    "travel_time": 0.15,
    "destination_quality": 0.12,
    "season_fit": 0.07,
    "accommodation_fit": 0.03,
    "data_confidence": 0.02,
}

COMPONENT_LABELS: dict[str, str] = {
    "budget_fit": "Budget fit",
    "value": "Value for money",
    "travel_time": "Travel time",
    "interest_match": "Matches your interests",
    "destination_quality": "Destination quality",
    "season_fit": "Right time of year",
    "accommodation_fit": "Stay preference available",
    "data_confidence": "Estimate confidence",
}


@dataclass(slots=True)
class Candidate:
    """A destination that has been costed, before scoring.

    It carries the exact quotes it was costed from, so a later stage (the
    itinerary view) can re-cost against the same accommodation and transport
    instead of reverse-engineering them from the line items.
    """

    destination: Destination
    transport: TransportQuote
    route: RouteInfo
    cost: CostBreakdown
    accommodation: AccommodationQuote
    activities: ActivityPlan
    accommodation_substituted: bool
    season_score: float
    season_label: str
    season_note: str
    season_provenance: Provenance
    value_raw: float = 0.0
    score: ScoreBreakdown | None = None
    is_stretch: bool = False
    highlights: list = field(default_factory=list)


@dataclass(slots=True)
class RankingOutcome:
    """Everything the recommendations endpoint needs."""

    ranked: list[Candidate]
    stretch: list[Candidate]
    rejected: list[RejectedDestination]
    shortfall: BudgetShortfall | None
    considered: int
    feasible: int
    interest_gap: InterestGap | None = None


# --------------------------------------------------------------------------
# Component scores
# --------------------------------------------------------------------------

def score_budget_fit(utilisation: float, stretch_tolerance: float) -> tuple[float, str]:
    """Reward using the budget well -- not merely being cheap.

    A trip that spends 40% of the budget is not better than one that spends
    85% and does more; it usually means a worse trip for no reason.
    """
    if utilisation > 1.0 + stretch_tolerance:
        return 0.0, f"Estimated cost is {utilisation:.0%} of your budget -- out of range."
    if utilisation > 1.0:
        return 0.30, (
            f"Costs {utilisation:.0%} of your budget, so it only works as a stretch option."
        )
    if utilisation > 0.90:
        return 0.85, f"Uses {utilisation:.0%} of the budget -- affordable but with little slack."
    if utilisation >= 0.40:
        return 1.00, f"Uses {utilisation:.0%} of your budget, which is a comfortable, well-used fit."
    score = 0.60 + 0.875 * utilisation
    return round(score, 4), (
        f"Only uses {utilisation:.0%} of your budget -- cheap, but you could afford more trip."
    )


def score_travel_time(round_trip_hours: float, days: int) -> tuple[float, str]:
    """Penalise journeys that eat the trip."""
    total_hours = days * 24
    share = round_trip_hours / total_hours if total_hours else 1.0
    score = max(0.0, min(1.0, 1.0 - share / 0.40))
    return round(score, 4), (
        f"{round_trip_hours:.0f} h of return travel over a {days}-day trip "
        f"({share:.0%} of your time)."
    )


def score_interest_match(
    destination: Destination, interests: Sequence[Interest]
) -> tuple[float, str]:
    """How much of what the user asked for this place actually offers."""
    if not interests:
        return 0.60, "No interests specified, so this did not affect the ranking."

    tags = set(destination.tags)
    wanted = {i.value for i in interests}
    matched = wanted & tags
    tag_coverage = len(matched) / len(wanted)

    wanted_categories: set[str] = set()
    for interest in interests:
        wanted_categories |= INTEREST_CATEGORIES.get(interest, set())
    present = {a.category for a in destination.attractions}
    attraction_coverage = (
        len(wanted_categories & present) / len(wanted_categories) if wanted_categories else 0.0
    )

    score = round(0.70 * tag_coverage + 0.30 * attraction_coverage, 4)
    if matched:
        pretty = ", ".join(sorted(m.replace("_", " ") for m in matched))
        return score, f"Matches {len(matched)} of your {len(wanted)} interests: {pretty}."
    return score, "Does not directly match your stated interests."


def score_destination_quality(destination: Destination) -> tuple[float, str]:
    """Intrinsic appeal: curated quality, variety of things to do, draw."""
    score = round(
        0.60 * destination.quality
        + 0.25 * destination.attraction_variety
        + 0.15 * destination.popularity,
        4,
    )
    kinds = len({a.category for a in destination.attractions})
    return score, (
        f"{len(destination.attractions)} notable sights across {kinds} different kinds of "
        "experience."
    )


def score_season(destination: Destination, travel_month: int) -> tuple[float, str, str]:
    """Seasonal suitability from the curated best-months window.

    Returns (score, short_label, explanation). A weather provider, when
    configured, can adjust this afterwards.
    """
    distance = destination.season_distance(travel_month)
    month_name = date(2000, travel_month, 1).strftime("%B")
    if distance == 0:
        return 1.0, "In season", f"{month_name} is within the best window to visit."
    if distance == 1:
        return 0.70, "Shoulder season", f"{month_name} is just outside the ideal window."
    if distance == 2:
        return 0.45, "Off season", f"{month_name} is two months off the best window."
    if distance == 3:
        return 0.25, "Poor season", f"{month_name} is well outside the recommended window."
    return 0.10, "Avoid", f"{month_name} is the wrong time of year for {destination.name}."


def score_accommodation_fit(substituted: bool, destination: Destination) -> tuple[float, str]:
    if not substituted:
        return 1.0, "Your preferred kind of stay is available here."
    return 0.55, (
        f"Your preferred stay type is not typical in {destination.name}; we priced the closest "
        "available option."
    )


def score_data_confidence(cost: CostBreakdown) -> tuple[float, str]:
    mapping = {Confidence.HIGH: 1.0, Confidence.MEDIUM: 0.65, Confidence.LOW: 0.35}
    score = mapping[cost.confidence]
    if cost.live_data_share > 0:
        return score, f"{cost.live_data_share:.0%} of the estimate is backed by live quotes."
    return score, "Modelled estimate -- no live provider quotes were available."


# --------------------------------------------------------------------------
# Candidate construction
# --------------------------------------------------------------------------

def _best_transport(
    *,
    request: TripRequest,
    destination: Destination,
    route: RouteInfo,
    origin_lat: float,
    origin_lon: float,
    today: date,
    overrides: cm.ModelOverrides,
    live_flight_fare: float | None = None,
) -> tuple[TransportQuote | None, list[str]]:
    """Cheapest time-feasible way to get there, plus why modes were ruled out.

    When the user said "any", every mode is priced and the cheapest one that
    fits in the trip's time wins. A mode that cannot be done at all (no
    railhead, no airport, a 200 km flight) is reported, not silently dropped.
    """
    reasons: list[str] = []
    viable: list[TransportQuote] = []

    for mode in cm.modes_for(request.travel_mode):
        if mode == TravelMode.TRAIN:
            quote = quote_train(
                route=route,
                destination=destination,
                travelers=request.travelers,
                accommodation=request.accommodation,
                overrides=overrides,
            )
        elif mode == TravelMode.BUS:
            quote = quote_bus(
                route=route,
                destination=destination,
                travelers=request.travelers,
                overrides=overrides,
            )
        elif mode == TravelMode.CAR:
            quote = quote_car(
                route=route,
                destination=destination,
                travelers=request.travelers,
                days=request.days,
                overrides=overrides,
            )
        else:
            quote = quote_flight(
                route=route,
                destination=destination,
                origin_lat=origin_lat,
                origin_lon=origin_lon,
                travelers=request.travelers,
                start_date=request.start_date,
                today=today,
                overrides=overrides,
                live_fare_inr=live_flight_fare,
            )

        if not quote.feasible:
            reasons.append(f"{mode.value}: {quote.infeasible_reason}")
            continue

        # Time feasibility, per mode.
        share = quote.round_trip_hours / (request.days * 24)
        if share > cm.MAX_TRAVEL_SHARE_OF_TRIP:
            reasons.append(
                f"{mode.value}: {quote.round_trip_hours:.0f} h of return travel is too much "
                f"for a {request.days}-day trip"
            )
            continue
        if request.max_one_way_hours and quote.one_way_hours > request.max_one_way_hours:
            reasons.append(
                f"{mode.value}: {quote.one_way_hours:.1f} h one way exceeds your "
                f"{request.max_one_way_hours:.0f} h limit"
            )
            continue
        viable.append(quote)

    if not viable:
        return None, reasons
    best = min(viable, key=lambda q: q.round_trip_total_inr)
    return best, reasons


def build_candidate(
    *,
    request: TripRequest,
    destination: Destination,
    origin_lat: float,
    origin_lon: float,
    settings: Settings,
    today: date,
    live_route: RouteInfo | None = None,
    live_flight_fare: float | None = None,
    live_nightly_inr: float | None = None,
    live_hotel_provider: str | None = None,
) -> tuple[Candidate | None, RejectedDestination | None]:
    """Cost one destination, or explain why it is out."""
    overrides = cm.ModelOverrides.from_mapping(request.assumption_overrides)
    route = build_route(origin_lat, origin_lon, destination, live_route)

    straight_km = destination.distance_km_to(origin_lat, origin_lon)
    if straight_km < cm.MIN_TRIP_DISTANCE_KM:
        return None, RejectedDestination(
            id=destination.id,
            name=destination.name,
            reason_code="too_close",
            reason=(
                f"Only {straight_km:.0f} km from your starting point -- that is a day out, "
                "not a trip away."
            ),
            tags=list(destination.tags),
        )

    transport, mode_reasons = _best_transport(
        request=request,
        destination=destination,
        route=route,
        origin_lat=origin_lat,
        origin_lon=origin_lon,
        today=today,
        overrides=overrides,
        live_flight_fare=live_flight_fare,
    )
    if transport is None:
        return None, RejectedDestination(
            id=destination.id,
            name=destination.name,
            reason_code="unreachable",
            reason=(
                "No workable way to get there and back in time. "
                + ("; ".join(mode_reasons[:3]) if mode_reasons else "")
            ).strip(),
            tags=list(destination.tags),
        )

    stay_type, substituted = resolve_stay_type(destination, request.accommodation)
    accommodation = quote_accommodation(
        destination=destination,
        requested=request.accommodation,
        travelers=request.travelers,
        nights=request.nights,
        check_in=request.start_date,
        overrides=overrides,
        live_nightly_inr=live_nightly_inr,
        live_provider=live_hotel_provider,
    )

    # A first costing with neutral budget pressure tells us how tight money is;
    # if it is tight, re-pick activities favouring free ones. This is why a
    # destination can come in under budget with a sensible plan instead of
    # being rejected for ticket prices it did not need to pay.
    provisional = plan_activities(
        destination,
        days=request.days,
        travelers=request.travelers,
        pace=request.pace,
        interests=request.interests,
        overrides=overrides,
        budget_pressure=0.3,
    )
    provisional_cost = compute_breakdown(
        BudgetInputs(request, destination, transport, accommodation, provisional), settings
    )
    activities = provisional
    cost = provisional_cost
    if provisional_cost.budget_utilisation > 0.95:
        thrifty = plan_activities(
            destination,
            days=request.days,
            travelers=request.travelers,
            pace=request.pace,
            interests=request.interests,
            overrides=overrides,
            budget_pressure=1.0,
        )
        thrifty_cost = compute_breakdown(
            BudgetInputs(request, destination, transport, accommodation, thrifty), settings
        )
        if thrifty_cost.total_inr < provisional_cost.total_inr:
            activities, cost = thrifty, thrifty_cost

    if cost.budget_fit == BudgetFit.OVER:
        return None, RejectedDestination(
            id=destination.id,
            name=destination.name,
            reason_code="over_budget",
            reason=(
                f"Cheapest workable plan comes to Rs {cost.total_inr:,.0f}, which is "
                f"Rs {cost.overrun_inr:,.0f} over your budget."
            ),
            estimated_total_inr=cost.total_inr,
            shortfall_inr=cost.overrun_inr,
            tags=list(destination.tags),
        )

    season_score, season_label, season_note = score_season(destination, request.start_date.month)

    return (
        Candidate(
            destination=destination,
            transport=transport,
            route=route,
            cost=cost,
            accommodation=accommodation,
            activities=activities,
            accommodation_substituted=substituted is not None,
            season_score=season_score,
            season_label=season_label,
            season_note=season_note,
            season_provenance=Provenance.curated(
                "Best-season window from the maintained destination dataset."
            ),
            highlights=list(activities.selected),
        ),
        None,
    )


# --------------------------------------------------------------------------
# Scoring and assembly
# --------------------------------------------------------------------------

def _value_raw(candidate: Candidate) -> float:
    """Quality delivered per rupee of daily spend."""
    destination = candidate.destination
    quality = 0.60 * destination.quality + 0.40 * destination.attraction_variety
    per_person_day = max(1.0, candidate.cost.per_traveler_per_day_inr)
    return quality / per_person_day


def _normalise(values: Sequence[float]) -> list[float]:
    """Min-max normalise, mapping a degenerate set to a neutral 0.6."""
    if not values:
        return []
    low, high = min(values), max(values)
    if high - low < 1e-12:
        return [0.6] * len(values)
    return [(v - low) / (high - low) for v in values]


def score_candidates(
    candidates: list[Candidate], request: TripRequest, settings: Settings
) -> None:
    """Attach a full ScoreBreakdown to each candidate, in place."""
    for candidate in candidates:
        candidate.value_raw = _value_raw(candidate)
    normalised_value = _normalise([c.value_raw for c in candidates])

    for candidate, value_score in zip(candidates, normalised_value):
        cost = candidate.cost
        budget_score, budget_why = score_budget_fit(
            cost.budget_utilisation, settings.stretch_budget_tolerance
        )
        time_score, time_why = score_travel_time(
            candidate.transport.round_trip_hours, request.days
        )
        interest_score, interest_why = score_interest_match(
            candidate.destination, request.interests
        )
        quality_score, quality_why = score_destination_quality(candidate.destination)
        stay_score, stay_why = score_accommodation_fit(
            candidate.accommodation_substituted, candidate.destination
        )
        confidence_score, confidence_why = score_data_confidence(cost)

        components = [
            ScoreComponent(
                key="budget_fit",
                label=COMPONENT_LABELS["budget_fit"],
                score=budget_score,
                weight=RANKING_WEIGHTS["budget_fit"],
                explanation=budget_why,
            ),
            ScoreComponent(
                key="value",
                label=COMPONENT_LABELS["value"],
                score=round(value_score, 4),
                weight=RANKING_WEIGHTS["value"],
                explanation=(
                    f"Rs {cost.per_traveler_per_day_inr:,.0f} per person per day for what this "
                    "destination offers, compared with the other options found."
                ),
            ),
            ScoreComponent(
                key="travel_time",
                label=COMPONENT_LABELS["travel_time"],
                score=time_score,
                weight=RANKING_WEIGHTS["travel_time"],
                explanation=time_why,
            ),
            ScoreComponent(
                key="interest_match",
                label=COMPONENT_LABELS["interest_match"],
                score=interest_score,
                weight=RANKING_WEIGHTS["interest_match"],
                explanation=interest_why,
            ),
            ScoreComponent(
                key="destination_quality",
                label=COMPONENT_LABELS["destination_quality"],
                score=quality_score,
                weight=RANKING_WEIGHTS["destination_quality"],
                explanation=quality_why,
            ),
            ScoreComponent(
                key="season_fit",
                label=COMPONENT_LABELS["season_fit"],
                score=candidate.season_score,
                weight=RANKING_WEIGHTS["season_fit"],
                explanation=candidate.season_note,
            ),
            ScoreComponent(
                key="accommodation_fit",
                label=COMPONENT_LABELS["accommodation_fit"],
                score=stay_score,
                weight=RANKING_WEIGHTS["accommodation_fit"],
                explanation=stay_why,
            ),
            ScoreComponent(
                key="data_confidence",
                label=COMPONENT_LABELS["data_confidence"],
                score=confidence_score,
                weight=RANKING_WEIGHTS["data_confidence"],
                explanation=confidence_why,
            ),
        ]
        total = round(sum(c.weighted for c in components), 4)
        candidate.score = ScoreBreakdown(total=min(1.0, total), components=components)
        candidate.is_stretch = cost.budget_fit == BudgetFit.STRETCH


def why_recommended(candidate: Candidate, request: TripRequest) -> list[str]:
    """Plain-language reasons, each grounded in a computed number."""
    cost = candidate.cost
    destination = candidate.destination
    reasons: list[str] = []

    if cost.budget_fit in (BudgetFit.GOOD, BudgetFit.COMFORTABLE):
        reasons.append(
            f"Fits your budget at an estimated Rs {cost.total_inr:,.0f} for "
            f"{request.travelers} traveller(s), leaving about Rs {cost.remaining_inr:,.0f} spare."
        )
    elif cost.budget_fit == BudgetFit.TIGHT:
        reasons.append(
            f"Just fits, at Rs {cost.total_inr:,.0f} against your Rs {request.budget_inr:,.0f} "
            "budget -- there is little room for extras."
        )
    else:
        reasons.append(
            f"Slightly over budget at Rs {cost.total_inr:,.0f} "
            f"(Rs {cost.overrun_inr:,.0f} more than you set aside)."
        )

    reasons.append(
        f"About {candidate.transport.one_way_hours:.0f} h each way by "
        f"{candidate.transport.mode.value}, which is the cheapest workable option we found."
    )

    if request.interests:
        _, interest_why = score_interest_match(destination, request.interests)
        reasons.append(interest_why)

    reasons.append(f"{candidate.season_label}: {candidate.season_note}")

    if cost.per_traveler_per_day_inr:
        reasons.append(
            f"Works out to roughly Rs {cost.per_traveler_per_day_inr:,.0f} per person per day."
        )
    return reasons


def caveats_for(candidate: Candidate, request: TripRequest) -> list[str]:
    """Things the user should know before committing."""
    out: list[str] = []
    cost = candidate.cost
    destination = candidate.destination

    if candidate.is_stretch:
        out.append(
            f"This exceeds your budget by Rs {cost.overrun_inr:,.0f}. It is shown as a stretch "
            "option, not a recommendation within budget."
        )
    if candidate.season_score < 0.5:
        out.append(f"Timing is not ideal: {candidate.season_note}")
    if candidate.accommodation_substituted:
        out.append(
            f"Your preferred stay type is not typical in {destination.name}; the estimate uses "
            "the closest available option."
        )
    if candidate.transport.mode == TravelMode.FLIGHT:
        out.append(
            "Airfare is the least stable part of this estimate -- it is modelled from distance "
            "and lead time, not quoted."
        )
    if request.days < destination.ideal_days[0]:
        out.append(
            f"{destination.name} really wants {destination.ideal_days[0]}-"
            f"{destination.ideal_days[1]} days; {request.days} will feel rushed."
        )
    if destination.id in {"spiti", "leh-ladakh"}:
        out.append(
            "High altitude: budget the first 24-36 h for acclimatisation and check road status "
            "before travelling."
        )
    if cost.live_data_share == 0:
        out.append("No live provider quotes were used -- verify fares and rates before booking.")
    return out


# --------------------------------------------------------------------------
# Shortfall explanation
# --------------------------------------------------------------------------

def build_shortfall(
    rejected: list[RejectedDestination], request: TripRequest
) -> BudgetShortfall | None:
    """Explain an empty result set and say what would fix it."""
    over_budget = [
        r for r in rejected if r.reason_code == "over_budget" and r.estimated_total_inr
    ]
    if not over_budget:
        return None

    over_budget.sort(key=lambda r: r.estimated_total_inr or float("inf"))
    cheapest = over_budget[0]
    cheapest_total = float(cheapest.estimated_total_inr or 0.0)
    extra = round(max(0.0, cheapest_total - request.budget_inr), 2)
    ground_floor = minimum_viable_cost(request)

    levers: list[str] = []
    suggested = round(cheapest_total * 1.02, -2)

    levers.append(
        f"Raise the budget to about Rs {suggested:,.0f} -- that is Rs {extra:,.0f} more -- "
        f"and {cheapest.name} comes into range."
    )
    if request.days > 2:
        per_day = cheapest_total / request.days
        levers.append(
            f"Cut one day. At roughly Rs {per_day:,.0f} a day, a {request.days - 1}-day trip "
            f"would come to about Rs {cheapest_total - per_day:,.0f}."
        )
    if request.accommodation.value != "hostel":
        levers.append(
            "Switch to hostels or dorms. On a trip this length that usually saves 30-50% of the "
            "accommodation line."
        )
    if request.travel_mode == TravelMode.FLIGHT:
        levers.append("Allow trains or buses as well -- flying is what is pushing this over.")
    elif request.travel_mode == TravelMode.ANY:
        levers.append("Look at destinations closer to home: return transport is a fixed cost.")
    if request.travelers > 1:
        levers.append(
            f"Rooms and vehicles are already shared across {request.travelers} travellers; "
            "the per-head cost is as low as the group size allows."
        )
    if ground_floor > request.budget_inr:
        levers.insert(
            0,
            f"Even staying put would cost about Rs {ground_floor:,.0f} for "
            f"{request.travelers} traveller(s) over {request.days} days on food, local transport "
            "and the cheapest beds. The budget cannot cover the trip's shape, let alone travel.",
        )

    return BudgetShortfall(
        message=(
            f"Nothing in our destination set fits Rs {request.budget_inr:,.0f} for "
            f"{request.travelers} traveller(s) over {request.days} days. The closest option is "
            f"{cheapest.name} at about Rs {cheapest_total:,.0f}."
        ),
        cheapest_total_inr=cheapest_total,
        additional_budget_required_inr=extra,
        suggested_budget_inr=float(suggested),
        alternatives=over_budget[:5],
        levers=levers,
    )


def build_interest_gap(
    *,
    recommended: list[Candidate],
    rejected: list[RejectedDestination],
    request: TripRequest,
) -> InterestGap | None:
    """Say so when the budget cannot buy the kind of trip that was asked for.

    Without this, a user who asks for mountains and is shown beach towns can
    only conclude their preferences were ignored. They were not: the mountain
    destinations were costed and came out over budget. Reporting the gap, and
    what it would take to close it, is the honest version of that answer.
    """
    if not request.interests:
        return None

    wanted = [interest.value for interest in request.interests]
    offered: set[str] = set()
    for candidate in recommended:
        offered |= set(candidate.destination.tags)

    matched = [interest for interest in wanted if interest in offered]
    missing = [interest for interest in wanted if interest not in offered]
    if not missing:
        return None

    over_budget = [
        r for r in rejected if r.reason_code == "over_budget" and r.estimated_total_inr
    ]

    unmet: list[UnmetInterest] = []
    for interest in missing:
        matching = [r for r in over_budget if interest in r.tags]
        matching.sort(key=lambda r: r.estimated_total_inr or float("inf"))
        cheapest = matching[0] if matching else None
        unmet.append(
            UnmetInterest(
                interest=interest,
                cheapest_matching_name=cheapest.name if cheapest else None,
                cheapest_matching_total_inr=cheapest.estimated_total_inr if cheapest else None,
                additional_budget_required_inr=cheapest.shortfall_inr if cheapest else None,
            )
        )

    gaps = [u.additional_budget_required_inr for u in unmet if u.additional_budget_required_inr]
    smallest = min(gaps) if gaps else None

    pretty = ", ".join(interest.replace("_", " ") for interest in missing)
    if smallest is not None:
        reachable = next(
            u for u in unmet if u.additional_budget_required_inr == smallest
        )
        message = (
            f"Nothing within your budget covers {pretty}. The closest match is "
            f"{reachable.cheapest_matching_name} at about "
            f"Rs {reachable.cheapest_matching_total_inr:,.0f} -- "
            f"Rs {smallest:,.0f} more than you set aside. The options below were chosen on "
            "your other criteria."
        )
    else:
        message = (
            f"Nothing in our destination set covers {pretty} within reach of your starting "
            "point and dates. The options below were chosen on your other criteria."
        )

    return InterestGap(
        message=message,
        unmet=unmet,
        matched=matched,
        smallest_additional_budget_inr=smallest,
    )


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def rank_destinations(
    request: TripRequest,
    *,
    origin_lat: float,
    origin_lon: float,
    settings: Settings,
    limit: int = 5,
    today: date | None = None,
    candidate_pool: Iterable[Destination] | None = None,
    refine: Callable[[list[Candidate]], list[Candidate]] | None = None,
) -> RankingOutcome:
    """Discover, cost, filter and rank destinations for one trip request.

    `refine` is an optional hook the API layer uses to upgrade the finalists
    with live routing and live fares. Keeping it as a callback means the engine
    itself stays pure and fully testable without any network access.
    """
    today = today or date.today()
    pool = list(candidate_pool) if candidate_pool is not None else list(load_destinations())

    candidates: list[Candidate] = []
    rejected: list[RejectedDestination] = []

    for destination in pool:
        candidate, rejection = build_candidate(
            request=request,
            destination=destination,
            origin_lat=origin_lat,
            origin_lon=origin_lon,
            settings=settings,
            today=today,
        )
        if candidate is not None:
            candidates.append(candidate)
        elif rejection is not None:
            rejected.append(rejection)

    considered = len(pool)
    feasible = len(candidates)

    if not candidates:
        return RankingOutcome(
            ranked=[],
            stretch=[],
            rejected=rejected,
            shortfall=build_shortfall(rejected, request),
            considered=considered,
            feasible=0,
        )

    # Score once to find the finalists cheaply, refine them, then rescore so
    # the published ranking reflects the refined numbers.
    score_candidates(candidates, request, settings)
    candidates.sort(key=lambda c: (-(c.score.total if c.score else 0.0), c.destination.name))

    shortlist_size = min(len(candidates), max(limit * 2, limit + 3))
    shortlist = candidates[:shortlist_size]
    if refine is not None:
        shortlist = refine(shortlist)
    score_candidates(shortlist, request, settings)
    shortlist.sort(key=lambda c: (-(c.score.total if c.score else 0.0), c.destination.name))

    in_budget = [c for c in shortlist if not c.is_stretch]
    stretch = [c for c in shortlist if c.is_stretch]

    ranked = in_budget[:limit]
    # Only offer stretch options when they would actually add something.
    stretch_out = (
        stretch[: max(0, min(2, limit - len(ranked) + 1))]
        if request.include_stretch_options
        else []
    )

    shortfall = None
    if not ranked:
        shortfall = build_shortfall(rejected, request)

    return RankingOutcome(
        ranked=ranked,
        stretch=stretch_out,
        rejected=rejected,
        shortfall=shortfall,
        considered=considered,
        feasible=feasible,
        interest_gap=build_interest_gap(
            recommended=ranked + stretch_out, rejected=rejected, request=request
        ),
    )
