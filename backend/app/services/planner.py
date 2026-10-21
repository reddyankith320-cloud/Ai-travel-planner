"""Orchestration: resolve the origin, rank destinations, refine the finalists.

This is the only layer that knows about both the pure engine and the network.
The engine stays provider-agnostic and fully testable; everything that can
fail over a wire is handled here, and every failure is recorded in
`provider_failures` so the response can tell the user what degraded.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date

from ..config import Settings
from ..data.catalog import Destination, dataset_version, get_destination
from ..providers import amadeus, geocoding, images, routing, weather
from ..providers.cache import CacheBackend
from ..providers.http import ProviderClient
from ..schemas.common import Coordinates, Provenance
from ..schemas.enums import Confidence, DataSource, TravelMode
from ..schemas.recommendation import (
    DestinationRecommendation,
    DestinationSummary,
    RecommendationResponse,
    ResponseMeta,
    SeasonInfo,
    TravelLeg,
)
from ..schemas.trip import TripRequest
from . import cost_model as cm
from .accommodation import resolve_stay_type
from .activities import plan_activities
from .itinerary import to_place_ref
from .ranking import (
    Candidate,
    RANKING_WEIGHTS,
    build_candidate,
    build_interest_gap,
    caveats_for,
    rank_destinations,
    score_candidates,
    why_recommended,
)

logger = logging.getLogger(__name__)

DATA_DISCLAIMER = (
    "TripWise does not sell tickets or hold inventory. Figures marked 'estimated' come from "
    "our own cost model, not from a booking system; figures marked 'live' were fetched from a "
    "provider API just now. Always confirm fares, room rates and admission charges on the "
    "operator's own site before you commit money."
)


@dataclass(slots=True)
class ResolvedOrigin:
    """Where the trip starts, and how confident we are about the label."""

    coordinates: Coordinates
    label: str
    state: str | None
    provenance: Provenance


@dataclass(slots=True)
class PlannerContext:
    """Per-request provider plumbing and diagnostics."""

    settings: Settings
    client: ProviderClient
    cache: CacheBackend
    failures: list[str] = field(default_factory=list)
    providers_used: set[str] = field(default_factory=set)
    images: dict[str, object] = field(default_factory=dict)

    def note_failure(self, message: str) -> None:
        """Record a provider failure once, however many candidates hit it."""
        if message not in self.failures:
            self.failures.append(message)

    def dedupe_failures(self) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for message in self.failures:
            if message not in seen:
                seen.add(message)
                out.append(message)
        return out


async def resolve_origin(request: TripRequest, ctx: PlannerContext) -> ResolvedOrigin | None:
    """Turn the submitted origin into coordinates plus a usable label.

    Browser geolocation gives coordinates with no address. If no geocoder is
    configured we label the point by its coordinates rather than inventing a
    place name.
    """
    origin = request.origin

    if origin.coordinates is not None:
        label = (origin.label or "").strip()
        if not label:
            hit = await geocoding.reverse_geocode(
                origin.coordinates.lat,
                origin.coordinates.lon,
                settings=ctx.settings,
                client=ctx.client,
                cache=ctx.cache,
                failures=ctx.failures,
            )
            if hit is not None:
                ctx.providers_used.add(hit.provenance.provider or "geocoder")
                return ResolvedOrigin(
                    coordinates=origin.coordinates,
                    label=hit.display_name,
                    state=hit.state,
                    provenance=hit.provenance,
                )
            return ResolvedOrigin(
                coordinates=origin.coordinates,
                label=f"Your location ({geocoding.describe_coordinates(origin.coordinates.lat, origin.coordinates.lon)})",
                state=None,
                provenance=Provenance(
                    source=DataSource.LIVE,
                    confidence=Confidence.HIGH,
                    provider="device-geolocation",
                    note=(
                        "Coordinates from your device. No geocoder is configured, so we cannot "
                        "show a place name -- this does not affect any distance or cost."
                    ),
                ),
            )
        return ResolvedOrigin(
            coordinates=origin.coordinates,
            label=label,
            state=origin.state,
            provenance=Provenance(
                source=DataSource.LIVE,
                confidence=Confidence.HIGH,
                provider="device-geolocation",
                note="Coordinates from your device.",
            ),
        )

    # Name only -- it has to be geocoded or we cannot compute anything.
    hit = await geocoding.geocode(
        origin.label or "",
        settings=ctx.settings,
        client=ctx.client,
        cache=ctx.cache,
        failures=ctx.failures,
    )
    if hit is None:
        return None
    ctx.providers_used.add(hit.provenance.provider or "geocoder")
    return ResolvedOrigin(
        coordinates=hit.coordinates,
        label=hit.display_name,
        state=hit.state,
        provenance=hit.provenance,
    )


def _make_refiner(
    request: TripRequest,
    origin: ResolvedOrigin,
    ctx: PlannerContext,
    today: date,
):
    """Build the async refinement pass for the shortlisted candidates.

    Live lookups happen here and only here, so a request costs a handful of
    provider calls rather than one per candidate destination.
    """

    async def refine(shortlist: list[Candidate]) -> list[Candidate]:
        refined: list[Candidate] = []
        for candidate in shortlist:
            destination = candidate.destination
            live_route = None
            live_flight_fare = None
            live_nightly = None
            live_hotel_provider = None

            if ctx.settings.routing_providers:
                live_route = await routing.road_route(
                    origin.coordinates.lat,
                    origin.coordinates.lon,
                    destination.lat,
                    destination.lon,
                    settings=ctx.settings,
                    client=ctx.client,
                    cache=ctx.cache,
                    failures=ctx.failures,
                )
                if live_route is not None:
                    ctx.providers_used.add(live_route.provenance.provider or "routing")

            if (
                ctx.settings.flight_provider == "amadeus"
                and candidate.transport.mode == TravelMode.FLIGHT
            ):
                detail = candidate.transport.detail
                origin_iata = str(detail.get("origin_iata") or "")
                dest_iata = str(detail.get("destination_iata") or "")
                if origin_iata and dest_iata:
                    live_flight_fare = await amadeus.cheapest_flight_fare_inr(
                        origin_iata=origin_iata,
                        destination_iata=dest_iata,
                        departure_date=request.start_date.isoformat(),
                        adults=request.travelers,
                        settings=ctx.settings,
                        client=ctx.client,
                        failures=ctx.failures,
                    )
                    if live_flight_fare is not None:
                        ctx.providers_used.add("amadeus-flights")

            if ctx.settings.hotel_provider == "amadeus" and request.nights > 0:
                # Price the stay type we will actually book, which may be a
                # substitution if the destination has no match for the request.
                stay_type, _ = resolve_stay_type(destination, request.accommodation)
                rooms = cm.accommodation_units(stay_type, request.travelers)
                live_nightly = await amadeus.cheapest_hotel_nightly_inr(
                    lat=destination.lat,
                    lon=destination.lon,
                    check_in=request.start_date.isoformat(),
                    check_out=(request.end_date or request.computed_end_date).isoformat(),
                    rooms=rooms,
                    adults=request.travelers,
                    settings=ctx.settings,
                    client=ctx.client,
                    failures=ctx.failures,
                )
                if live_nightly is not None:
                    live_hotel_provider = "amadeus-hotels"
                    ctx.providers_used.add("amadeus-hotels")

            rebuilt, _ = build_candidate(
                request=request,
                destination=destination,
                origin_lat=origin.coordinates.lat,
                origin_lon=origin.coordinates.lon,
                settings=ctx.settings,
                today=today,
                live_route=live_route,
                live_flight_fare=live_flight_fare,
                live_nightly_inr=live_nightly,
                live_hotel_provider=live_hotel_provider,
            )
            # If refinement pushes a candidate out of budget, keep the refined
            # truth: an estimate that was wrong is not a reason to show it.
            chosen = rebuilt if rebuilt is not None else candidate

            if ctx.settings.enable_open_meteo:
                climate = await weather.month_climate(
                    destination.lat,
                    destination.lon,
                    request.start_date.month,
                    settings=ctx.settings,
                    client=ctx.client,
                    cache=ctx.cache,
                    failures=ctx.failures,
                )
                if climate is not None:
                    ctx.providers_used.add("open-meteo")
                    # Blend measured climate with the curated season window:
                    # the dataset knows about crowds and road closures, the
                    # weather data knows about actual heat and rain.
                    chosen.season_score = round(
                        0.5 * chosen.season_score + 0.5 * climate.score, 4
                    )
                    chosen.season_label = climate.label
                    chosen.season_note = f"{chosen.season_note} {climate.note}"
                    chosen.season_provenance = climate.provenance

            if ctx.settings.enable_wikimedia_images:
                picture = await images.destination_image(
                    name=destination.name,
                    state=destination.state,
                    settings=ctx.settings,
                    client=ctx.client,
                    cache=ctx.cache,
                    failures=ctx.failures,
                )
                if picture is not None:
                    ctx.providers_used.add("wikimedia")
                    ctx.images[destination.id] = picture

            refined.append(chosen)
        return refined

    return refine


def _to_travel_legs(candidate: Candidate) -> tuple[TravelLeg, TravelLeg]:
    """Outbound and return legs. Symmetric, because the cost model is."""
    transport = candidate.transport
    leg = TravelLeg(
        mode=transport.mode,
        distance_km=transport.one_way_km,
        duration_hours=transport.one_way_hours,
        provenance=transport.provenance,
        route_note=transport.route_note,
        transfer_note=transport.transfer_note,
        booking_links=transport.booking_links,
    )
    return leg, leg.model_copy()


def _to_recommendation(
    candidate: Candidate,
    rank: int,
    request: TripRequest,
    images_by_id: dict[str, object] | None = None,
) -> DestinationRecommendation:
    destination = candidate.destination
    picture = (images_by_id or {}).get(destination.id)
    outbound, inbound = _to_travel_legs(candidate)
    assert candidate.score is not None
    return DestinationRecommendation(
        destination=DestinationSummary(
            id=destination.id,
            name=destination.name,
            region=destination.region,
            state=destination.state,
            coordinates=Coordinates(lat=destination.lat, lon=destination.lon),
            blurb=destination.blurb,
            tags=list(destination.tags),
            image_url=getattr(picture, "url", None),
            image_attribution=getattr(picture, "attribution", None),
            provenance=Provenance.curated(
                f"TripWise destination dataset v{dataset_version()}."
            ),
        ),
        rank=rank,
        score=candidate.score,
        budget_fit=candidate.cost.budget_fit,
        is_stretch=candidate.is_stretch,
        cost=candidate.cost,
        outbound=outbound,
        inbound=inbound,
        season=SeasonInfo(
            in_season=candidate.season_score >= 0.7,
            score=candidate.season_score,
            label=candidate.season_label,
            note=candidate.season_note,
            provenance=candidate.season_provenance,
        ),
        why_recommended=why_recommended(candidate, request),
        highlights=[to_place_ref(a) for a in candidate.highlights[:8]],
        caveats=caveats_for(candidate, request),
    )


async def plan_recommendations(
    request: TripRequest,
    *,
    ctx: PlannerContext,
    limit: int = 5,
    today: date | None = None,
) -> RecommendationResponse | None:
    """Full recommendation flow. Returns None when the origin cannot be resolved."""
    started = time.monotonic()
    today = today or date.today()

    origin = await resolve_origin(request, ctx)
    if origin is None:
        return None

    refiner = _make_refiner(request, origin, ctx, today)
    outcome = await _rank_with_async_refine(
        request, origin, ctx, limit=limit, today=today, refiner=refiner
    )

    recommendations = [
        _to_recommendation(candidate, index + 1, request, ctx.images)
        for index, candidate in enumerate(outcome.ranked)
    ]
    stretch = [
        _to_recommendation(candidate, len(recommendations) + index + 1, request, ctx.images)
        for index, candidate in enumerate(outcome.stretch)
    ]

    overall_confidence = (
        min((r.cost.confidence for r in recommendations), key=_confidence_rank)
        if recommendations
        else Confidence.LOW
    )

    elapsed_ms = int((time.monotonic() - started) * 1000)
    cache_stats = ctx.cache.stats()

    return RecommendationResponse(
        request=request,
        origin_resolved=origin.label,
        origin_coordinates=origin.coordinates,
        recommendations=recommendations,
        stretch_options=stretch,
        # Cap the rejection list: it is for explanation, not an audit dump.
        rejected=outcome.rejected[:12],
        shortfall=outcome.shortfall,
        interest_gap=outcome.interest_gap,
        ranking_weights=dict(RANKING_WEIGHTS),
        meta=ResponseMeta(
            demo_mode=ctx.settings.demo_mode,
            providers_used=sorted(ctx.providers_used),
            provider_failures=ctx.dedupe_failures()[:10],
            candidates_considered=outcome.considered,
            candidates_feasible=outcome.feasible,
            cache_hits=int(cache_stats.get("hits", 0)),
            elapsed_ms=elapsed_ms,
            currency=ctx.settings.default_currency,
            data_disclaimer=DATA_DISCLAIMER,
        ),
        confidence=overall_confidence,
    )


def _confidence_rank(confidence: Confidence) -> int:
    return {Confidence.LOW: 0, Confidence.MEDIUM: 1, Confidence.HIGH: 2}[confidence]


async def _rank_with_async_refine(
    request: TripRequest,
    origin: ResolvedOrigin,
    ctx: PlannerContext,
    *,
    limit: int,
    today: date,
    refiner,
):
    """Run the synchronous engine, then await the async refinement step.

    `rank_destinations` takes a synchronous callback, so the broad pass runs
    first without refinement, the shortlist is refined here, and the engine is
    re-entered with the refined candidates pinned in place.
    """
    from .ranking import RankingOutcome

    broad = rank_destinations(
        request,
        origin_lat=origin.coordinates.lat,
        origin_lon=origin.coordinates.lon,
        settings=ctx.settings,
        limit=limit,
        today=today,
    )
    if not broad.ranked and not broad.stretch:
        return broad

    shortlist = broad.ranked + broad.stretch
    refined = await refiner(shortlist)
    score_candidates(refined, request, ctx.settings)
    refined.sort(key=lambda c: (-(c.score.total if c.score else 0.0), c.destination.name))

    in_budget = [c for c in refined if not c.is_stretch]
    stretch = [c for c in refined if c.is_stretch]

    # Refinement can push a candidate over budget once real distances land.
    # If that empties the in-budget list, explain the gap rather than showing
    # nothing: the refined numbers are the honest ones.
    shortfall = broad.shortfall
    if not in_budget and shortfall is None:
        from .ranking import build_shortfall
        from ..schemas.recommendation import RejectedDestination

        refined_rejections = [
            RejectedDestination(
                id=c.destination.id,
                name=c.destination.name,
                reason_code="over_budget",
                reason=(
                    f"Once real distances were checked, the plan came to "
                    f"Rs {c.cost.total_inr:,.0f}, which is Rs {c.cost.overrun_inr:,.0f} over "
                    "your budget."
                ),
                estimated_total_inr=c.cost.total_inr,
                shortfall_inr=c.cost.overrun_inr,
            )
            for c in refined
        ]
        shortfall = build_shortfall(refined_rejections + broad.rejected, request)

    ranked = in_budget[:limit]
    stretch_out = stretch[:2] if request.include_stretch_options else []
    return RankingOutcome(
        ranked=ranked,
        stretch=stretch_out,
        rejected=broad.rejected,
        shortfall=shortfall,
        considered=broad.considered,
        feasible=broad.feasible,
        interest_gap=build_interest_gap(
            recommended=ranked + stretch_out, rejected=broad.rejected, request=request
        ),
    )


async def build_single_candidate(
    destination_id: str,
    request: TripRequest,
    *,
    ctx: PlannerContext,
    today: date | None = None,
) -> tuple[Destination, Candidate] | None:
    """Cost one chosen destination, with live refinement, for the itinerary view."""
    today = today or date.today()
    destination = get_destination(destination_id)
    if destination is None:
        return None

    origin = await resolve_origin(request, ctx)
    if origin is None:
        return None

    candidate, _ = build_candidate(
        request=request,
        destination=destination,
        origin_lat=origin.coordinates.lat,
        origin_lon=origin.coordinates.lon,
        settings=ctx.settings,
        today=today,
    )
    if candidate is None:
        # Over budget or unreachable -- still cost it so the user sees why,
        # using a budget high enough that the filter cannot reject it.
        relaxed = request.model_copy(update={"budget_inr": request.budget_inr * 100})
        candidate, _ = build_candidate(
            request=relaxed,
            destination=destination,
            origin_lat=origin.coordinates.lat,
            origin_lon=origin.coordinates.lon,
            settings=ctx.settings,
            today=today,
        )
        if candidate is None:
            return None

    refiner = _make_refiner(request, origin, ctx, today)
    refined = await refiner([candidate])
    score_candidates(refined, request, ctx.settings)
    return destination, refined[0]
