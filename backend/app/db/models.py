"""Persistence models.

Only two things are worth storing: the destination catalogue (read from JSON,
mirrored here so deployments can extend it) and the trips a user saves. A
recommendation is cheap to recompute and would go stale, so it is not cached in
the database -- the saved *request* is, and the plan is recomputed from it.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON


class Base(DeclarativeBase):
    """Declarative base. `JSON` works on both PostgreSQL and SQLite."""


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    """A user account.

    Authentication is intentionally not implemented: shipping a half-built
    auth system is worse than shipping none. The table exists so saved trips
    have somewhere to hang when auth is added, and `owner_token` below gives
    anonymous users a working "my trips" list in the meantime.
    """

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    trips: Mapped[list["SavedTrip"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class SavedTrip(Base):
    """A saved plan.

    `request` holds the validated TripRequest that produced the plan, so it can
    be re-run against current rates. `snapshot` holds what the user actually
    saw, so the two can be compared and the user is never silently shown
    different numbers than the ones they saved.
    """

    __tablename__ = "saved_trips"
    __table_args__ = (
        Index("ix_saved_trips_owner_created", "owner_token", "created_at"),
        Index("ix_saved_trips_destination", "destination_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    # Opaque client-generated token identifying an anonymous owner. Not a
    # credential and not a secret -- it only scopes a trip list.
    owner_token: Mapped[str] = mapped_column(String(64), index=True)

    title: Mapped[str] = mapped_column(String(200))
    destination_id: Mapped[str] = mapped_column(String(80))
    destination_name: Mapped[str] = mapped_column(String(160))

    budget_inr: Mapped[float] = mapped_column(Float)
    total_estimate_inr: Mapped[float] = mapped_column(Float)
    per_traveler_inr: Mapped[float] = mapped_column(Float)
    travelers: Mapped[int] = mapped_column(Integer)
    days: Mapped[int] = mapped_column(Integer)
    start_date: Mapped[str] = mapped_column(String(10))
    travel_mode: Mapped[str] = mapped_column(String(20))
    currency: Mapped[str] = mapped_column(String(8), default="INR")

    request: Mapped[dict] = mapped_column(JSON)
    snapshot: Mapped[dict] = mapped_column(JSON)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    user: Mapped["User | None"] = relationship(back_populates="trips")


class DestinationOverride(Base):
    """Deployment-local additions and corrections to the destination catalogue.

    Lets an operator add or re-price a destination without editing the shipped
    JSON file, which keeps upgrades clean.
    """

    __tablename__ = "destination_overrides"
    __table_args__ = (UniqueConstraint("destination_id", name="uq_destination_override"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    destination_id: Mapped[str] = mapped_column(String(80), index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    active: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
