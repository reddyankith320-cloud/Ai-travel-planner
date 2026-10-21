"""Itinerary generation for a chosen destination."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status

from ..schemas.itinerary import Itinerary, ItineraryRequest
from ..services import cost_model as cm
from ..services.activities import plan_activities
from ..services.budget import BudgetInputs, compute_breakdown
from ..services.itinerary import generate_itinerary
from ..services.planner import build_single_candidate
from .deps import PlannerContextDep

logger = logging.getLogger(__name__)

router = APIRouter(tags=["itinerary"])


@router.post(
    "/itinerary",
    response_model=Itinerary,
    summary="Build a day-by-day plan for one recommended destination",
)
async def build(payload: ItineraryRequest, ctx: PlannerContextDep) -> Itinerary:
    """Generate (or regenerate) an itinerary.

    The destination must be one from the catalogue -- the user picks it from
    the recommendations, they do not type it. Removing activities and
    regenerating re-costs the whole trip, so the budget dashboard never falls
    out of step with the plan on screen.
    """
    request = payload.trip
    found = await build_single_candidate(payload.destination_id, request, ctx=ctx)
    if found is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Unknown destination '{payload.destination_id}', or your starting point could "
                "not be resolved. Pick a destination from your recommendations."
            ),
        )
    destination, candidate = found

    overrides = cm.ModelOverrides.from_mapping(request.assumption_overrides)
    excluded = {name.strip().lower() for name in payload.excluded_place_names}

    # Re-select activities with the exclusions applied, then re-cost from
    # that selection so the itinerary and the budget agree by construction.
    pressure = 1.0 if candidate.cost.budget_utilisation > 0.9 else 0.3
    plan = plan_activities(
        destination,
        days=request.days,
        travelers=request.travelers,
        pace=request.pace,
        interests=request.interests,
        overrides=overrides,
        budget_pressure=pressure,
        excluded_names=excluded,
        # A different seed on regenerate gives genuinely different day
        # groupings rather than the same plan with new words.
    )
    cost = compute_breakdown(
        BudgetInputs(request, destination, candidate.transport, candidate.accommodation, plan),
        ctx.settings,
    )

    stay_type = candidate.accommodation.accommodation
    food_rate = (
        overrides.food_per_person_per_day
        if overrides.food_per_person_per_day is not None
        else cm.FOOD_PER_PERSON_PER_DAY[stay_type] * destination.cost_index.get("food", 1.0)
    )

    try:
        result = await generate_itinerary(
            destination=destination,
            plan=plan,
            request=request,
            cost=cost,
            settings=ctx.settings,
            food_per_person_per_day=food_rate,
            allow_llm=True,
        )
    except Exception:
        logger.exception("itinerary generation failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not build an itinerary. Please try again.",
        ) from None

    if result.llm_error:
        logger.info("itinerary fell back to the deterministic planner: %s", result.llm_error)
    return result.itinerary
