"""Saved trips: store, list, fetch, delete and export a plan."""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from ..db import SavedTrip, session_scope
from ..schemas.itinerary import Itinerary
from ..schemas.trip import TripRequest
from .deps import OwnerTokenDep, SettingsDep

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/trips", tags=["trips"])

MAX_TRIPS_PER_OWNER = 100


class SaveTripRequest(BaseModel):
    """What the client sends to save a plan."""

    title: str = Field(..., min_length=1, max_length=200)
    destination_id: str = Field(..., max_length=80)
    destination_name: str = Field(..., max_length=160)
    trip: TripRequest
    itinerary: Itinerary
    notes: str | None = Field(default=None, max_length=4000)


class TripSummary(BaseModel):
    """A row in the saved-trips list."""

    id: str
    title: str
    destination_id: str
    destination_name: str
    budget_inr: float
    total_estimate_inr: float
    per_traveler_inr: float
    travelers: int
    days: int
    start_date: str
    travel_mode: str
    currency: str
    notes: str | None
    created_at: datetime
    updated_at: datetime


class TripDetail(TripSummary):
    """A saved trip including the plan as it was saved."""

    request: dict
    snapshot: dict


def _to_summary(row: SavedTrip) -> TripSummary:
    return TripSummary(
        id=row.id,
        title=row.title,
        destination_id=row.destination_id,
        destination_name=row.destination_name,
        budget_inr=row.budget_inr,
        total_estimate_inr=row.total_estimate_inr,
        per_traveler_inr=row.per_traveler_inr,
        travelers=row.travelers,
        days=row.days,
        start_date=row.start_date,
        travel_mode=row.travel_mode,
        currency=row.currency,
        notes=row.notes,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.post("", response_model=TripSummary, status_code=status.HTTP_201_CREATED)
async def save_trip(
    payload: SaveTripRequest, owner: OwnerTokenDep, settings: SettingsDep
) -> TripSummary:
    """Save a plan under the caller's owner token."""
    cost = payload.itinerary.cost
    with session_scope(settings) as db:
        existing = (
            db.query(SavedTrip).filter(SavedTrip.owner_token == owner).count()
        )
        if existing >= MAX_TRIPS_PER_OWNER:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"You already have {MAX_TRIPS_PER_OWNER} saved trips. Delete one first."
                ),
            )
        row = SavedTrip(
            owner_token=owner,
            title=payload.title,
            destination_id=payload.destination_id,
            destination_name=payload.destination_name,
            budget_inr=payload.trip.budget_inr,
            total_estimate_inr=cost.total_inr,
            per_traveler_inr=cost.per_traveler_inr,
            travelers=payload.trip.travelers,
            days=payload.trip.days,
            start_date=payload.trip.start_date.isoformat(),
            travel_mode=payload.trip.travel_mode.value,
            currency=cost.currency,
            notes=payload.notes,
            request=payload.trip.model_dump(mode="json"),
            snapshot=payload.itinerary.model_dump(mode="json"),
        )
        db.add(row)
        db.flush()
        return _to_summary(row)


@router.get("", response_model=list[TripSummary])
async def list_trips(
    owner: OwnerTokenDep,
    settings: SettingsDep,
    limit: int = Query(50, ge=1, le=100),
) -> list[TripSummary]:
    """The caller's saved trips, newest first."""
    with session_scope(settings) as db:
        rows = (
            db.query(SavedTrip)
            .filter(SavedTrip.owner_token == owner)
            .order_by(SavedTrip.created_at.desc())
            .limit(limit)
            .all()
        )
        return [_to_summary(row) for row in rows]


@router.get("/{trip_id}", response_model=TripDetail)
async def get_trip(trip_id: str, owner: OwnerTokenDep, settings: SettingsDep) -> TripDetail:
    """One saved trip, including the plan exactly as it was saved."""
    with session_scope(settings) as db:
        row = (
            db.query(SavedTrip)
            .filter(SavedTrip.id == trip_id, SavedTrip.owner_token == owner)
            .one_or_none()
        )
        if row is None:
            # Same response whether it does not exist or belongs to someone
            # else, so the endpoint cannot be used to probe for trip IDs.
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trip not found.")
        summary = _to_summary(row)
        return TripDetail(
            **summary.model_dump(), request=row.request, snapshot=row.snapshot
        )


@router.delete("/{trip_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_trip(trip_id: str, owner: OwnerTokenDep, settings: SettingsDep) -> None:
    """Delete one of the caller's saved trips."""
    with session_scope(settings) as db:
        deleted = (
            db.query(SavedTrip)
            .filter(SavedTrip.id == trip_id, SavedTrip.owner_token == owner)
            .delete()
        )
        if not deleted:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trip not found.")
