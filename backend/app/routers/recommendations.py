"""Destination discovery: the endpoint that answers "where can I afford to go?"."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status

from ..schemas.recommendation import RecommendationResponse
from ..schemas.trip import RecommendationQuery
from ..services.planner import plan_recommendations
from .deps import PlannerContextDep

logger = logging.getLogger(__name__)

router = APIRouter(tags=["recommendations"])


@router.post(
    "/recommendations",
    response_model=RecommendationResponse,
    summary="Discover affordable destinations for a budget and constraints",
)
async def recommend(query: RecommendationQuery, ctx: PlannerContextDep) -> RecommendationResponse:
    """Rank destinations the user can actually afford and reach.

    Note what this endpoint does *not* take: a destination. That is the
    product. The caller supplies origin, budget, dates, group size, mode and
    preferences; TripWise decides where they can go.

    An empty `recommendations` list is a valid, useful answer: `shortfall`
    then explains the gap and what would close it.
    """
    try:
        response = await plan_recommendations(query, ctx=ctx, limit=query.limit)
    except Exception:
        logger.exception("recommendation request failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not build recommendations. Please try again.",
        ) from None

    if response is None:
        raise HTTPException(
            # 422 Unprocessable Content: the request parsed, but we could not
            # resolve the origin, so there is nothing to compute from.
            status_code=422,
            detail=(
                "Could not work out where you are starting from. Allow location access, or "
                "type a city name that a geocoder can resolve."
            ),
        )
    return response
