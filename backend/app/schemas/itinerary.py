"""Day-by-day itinerary types.

The LLM proposes structure and prose; every rupee is recomputed by the
deterministic cost model before it reaches the client.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field, computed_field

from .common import Coordinates, PlaceRef, Provenance
from .cost import CostBreakdown
from .enums import ActivityKind, Confidence
from .recommendation import DestinationSummary


class ItineraryActivity(BaseModel):
    """A single time block within a day."""

    order: int = Field(..., ge=0)
    kind: ActivityKind
    title: str
    start_time: str = Field(..., description="Local 24h time, HH:MM.")
    end_time: str = Field(..., description="Local 24h time, HH:MM.")
    duration_minutes: int = Field(..., ge=0, le=900)
    description: str
    place: PlaceRef | None = None
    distance_from_previous_km: float | None = Field(default=None, ge=0)
    transport_note: str | None = None
    transport_cost_inr: float = Field(0.0, ge=0)
    entry_cost_inr: float = Field(0.0, ge=0)
    food_cost_inr: float = Field(0.0, ge=0)
    booking_required: bool = False
    weather_alternative: str | None = None
    tips: list[str] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def block_cost_inr(self) -> float:
        return round(self.transport_cost_inr + self.entry_cost_inr + self.food_cost_inr, 2)


class ItineraryDay(BaseModel):
    """One day of the trip."""

    day_number: int = Field(..., ge=1)
    date: date
    title: str
    summary: str
    area_focus: str = Field(..., description="The geographic cluster this day covers.")
    activities: list[ItineraryActivity]
    total_travel_km: float = Field(0.0, ge=0)
    walking_intensity: str = "moderate"
    weather_note: str | None = None
    notes: list[str] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def day_cost_inr(self) -> float:
        return round(sum(a.block_cost_inr for a in self.activities), 2)


class Itinerary(BaseModel):
    """A complete plan for one destination."""

    destination: DestinationSummary
    start_date: date
    end_date: date
    travelers: int
    days: list[ItineraryDay]
    cost: CostBreakdown
    accommodation_area: str
    accommodation_coordinates: Coordinates | None = None
    packing_notes: list[str] = Field(default_factory=list)
    local_tips: list[str] = Field(default_factory=list)
    generated_by: str = Field(..., description="'llm:<provider>' or 'deterministic'.")
    provenance: Provenance
    confidence: Confidence
    disclaimer: str

    @computed_field  # type: ignore[prop-decorator]
    @property
    def activity_count(self) -> int:
        return sum(len(d.activities) for d in self.days)


class ItineraryRequest(BaseModel):
    """Generate (or regenerate) an itinerary for a chosen destination."""

    destination_id: str
    trip: "TripRequestRef"
    excluded_place_names: list[str] = Field(
        default_factory=list, description="Places the user removed from the plan."
    )
    regenerate: bool = False
    seed: int | None = None


# Imported late to avoid a circular import at module load.
from .trip import TripRequest as TripRequestRef  # noqa: E402

ItineraryRequest.model_rebuild()
