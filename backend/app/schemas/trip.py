"""The trip request -- note there is deliberately no `destination` field.

Destination discovery is the product. The user supplies constraints; TripWise
decides where they can afford to go.
"""

from __future__ import annotations

from datetime import date, timedelta

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .common import Coordinates
from .enums import AccommodationType, Interest, Pace, TravelMode

MAX_TRIP_DAYS = 21
MAX_TRAVELERS = 20


class OriginLocation(BaseModel):
    """Where the trip starts from.

    Browser geolocation yields coordinates only -- it does not yield a street
    address. `label` therefore stays optional, and `resolved_name` is filled in
    server-side by reverse geocoding only when a geocoder is configured.
    """

    coordinates: Coordinates | None = None
    label: str | None = Field(
        default=None,
        max_length=160,
        description="Free-text place name, used when coordinates are absent.",
    )
    resolved_name: str | None = None
    state: str | None = None
    detection: str = Field(
        default="manual",
        description="How the origin was obtained: 'geolocation' | 'manual' | 'resolved'.",
    )

    @model_validator(mode="after")
    def _require_something(self) -> "OriginLocation":
        if self.coordinates is None and not (self.label or "").strip():
            raise ValueError(
                "Provide either coordinates (from device geolocation) or a place name."
            )
        return self


class TripRequest(BaseModel):
    """Everything TripWise needs to discover and cost a trip."""

    model_config = ConfigDict(extra="forbid")

    origin: OriginLocation
    budget_inr: float = Field(
        ...,
        gt=0,
        le=100_000_000,
        description="Total budget for the whole group, in INR.",
    )
    travelers: int = Field(1, ge=1, le=MAX_TRAVELERS)
    days: int = Field(..., ge=1, le=MAX_TRIP_DAYS, description="Total trip days, inclusive.")
    start_date: date
    end_date: date | None = None

    travel_mode: TravelMode = TravelMode.ANY
    accommodation: AccommodationType = AccommodationType.BUDGET_HOTEL
    pace: Pace = Pace.BALANCED
    interests: list[Interest] = Field(default_factory=list, max_length=12)

    # Optional constraints / overrides
    max_one_way_hours: float | None = Field(default=None, gt=0, le=72)
    include_stretch_options: bool = True
    contingency_rate: float | None = Field(default=None, ge=0.0, le=0.5)
    assumption_overrides: dict[str, float] = Field(
        default_factory=dict,
        description="Override cost-model assumptions by key, e.g. {'food_per_person_per_day': 450}.",
    )

    @property
    def nights(self) -> int:
        """Nights of accommodation implied by the trip length.

        A 3-day trip needs 2 nights. A single-day trip needs none.
        """
        return max(0, self.days - 1)

    @property
    def computed_end_date(self) -> date:
        return self.start_date + timedelta(days=self.days - 1)

    @model_validator(mode="after")
    def _reconcile_dates(self) -> "TripRequest":
        # An explicit end date wins and redefines `days`, so the two can never
        # disagree in the cost model.
        if self.end_date is not None:
            if self.end_date < self.start_date:
                raise ValueError("end_date cannot be before start_date.")
            span = (self.end_date - self.start_date).days + 1
            if span > MAX_TRIP_DAYS:
                raise ValueError(f"Trip length cannot exceed {MAX_TRIP_DAYS} days.")
            object.__setattr__(self, "days", span)
        else:
            object.__setattr__(self, "end_date", self.computed_end_date)

        if self.start_date < date.today() - timedelta(days=1):
            raise ValueError("start_date is in the past.")
        return self

    @model_validator(mode="after")
    def _sane_budget(self) -> "TripRequest":
        # Guard against obviously impossible inputs early, with a message that
        # explains the arithmetic rather than just rejecting.
        floor_per_person_per_day = 600.0
        minimum = floor_per_person_per_day * self.travelers * self.days
        if self.budget_inr < minimum:
            raise ValueError(
                f"A {self.days}-day trip for {self.travelers} traveller(s) needs at least "
                f"about Rs {minimum:,.0f} to cover food, local transport and the cheapest "
                f"stay, before any intercity travel. Raise the budget, shorten the trip, "
                f"or travel with fewer people."
            )
        return self


class RecommendationQuery(TripRequest):
    """Trip request plus result-shaping options."""

    limit: int = Field(5, ge=1, le=10)
