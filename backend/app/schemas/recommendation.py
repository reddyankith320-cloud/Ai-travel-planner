"""Destination recommendation types, including the transparent score."""

from __future__ import annotations

from pydantic import BaseModel, Field, computed_field

from .common import BookingLink, Coordinates, PlaceRef, Provenance
from .cost import CostBreakdown
from .enums import BudgetFit, Confidence, TravelMode
from .trip import TripRequest


class ScoreComponent(BaseModel):
    """One factor in the ranking, with its contribution spelled out."""

    key: str
    label: str
    score: float = Field(..., ge=0.0, le=1.0)
    weight: float = Field(..., ge=0.0, le=1.0)
    explanation: str

    @computed_field  # type: ignore[prop-decorator]
    @property
    def weighted(self) -> float:
        return round(self.score * self.weight, 4)


class ScoreBreakdown(BaseModel):
    """The full ranking calculation for one destination."""

    total: float = Field(..., ge=0.0, le=1.0)
    components: list[ScoreComponent]

    def reconciles(self, tolerance: float = 0.001) -> bool:
        return abs(sum(c.weighted for c in self.components) - self.total) <= tolerance


class TravelLeg(BaseModel):
    """One direction of the intercity journey."""

    mode: TravelMode
    distance_km: float = Field(..., ge=0)
    duration_hours: float = Field(..., ge=0)
    provenance: Provenance
    route_note: str | None = None
    transfer_note: str | None = None
    booking_links: list[BookingLink] = Field(default_factory=list)


class SeasonInfo(BaseModel):
    """Seasonal suitability for the requested dates."""

    in_season: bool
    score: float = Field(..., ge=0.0, le=1.0)
    label: str
    note: str
    provenance: Provenance


class DestinationSummary(BaseModel):
    """Identity and character of a candidate destination."""

    id: str
    name: str
    region: str
    state: str
    country: str = "India"
    coordinates: Coordinates
    blurb: str
    tags: list[str]
    image_url: str | None = None
    image_attribution: str | None = None
    provenance: Provenance


class DestinationRecommendation(BaseModel):
    """A ranked, fully costed destination suggestion."""

    destination: DestinationSummary
    rank: int
    score: ScoreBreakdown
    budget_fit: BudgetFit
    is_stretch: bool = False

    cost: CostBreakdown
    outbound: TravelLeg
    inbound: TravelLeg
    season: SeasonInfo

    why_recommended: list[str] = Field(
        ..., description="Plain-language reasons, grounded in the computed numbers."
    )
    highlights: list[PlaceRef] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def round_trip_hours(self) -> float:
        return round(self.outbound.duration_hours + self.inbound.duration_hours, 2)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def intercity_cost_inr(self) -> float:
        from .enums import CostCategory

        return round(
            sum(
                i.total_inr
                for i in self.cost.line_items
                if i.category == CostCategory.INTERCITY_TRANSPORT
            ),
            2,
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def accommodation_cost_inr(self) -> float:
        from .enums import CostCategory

        return round(
            sum(
                i.total_inr
                for i in self.cost.line_items
                if i.category == CostCategory.ACCOMMODATION
            ),
            2,
        )


class RejectedDestination(BaseModel):
    """A candidate that did not make the cut, and why.

    Returned so the UI can explain itself instead of silently dropping places.
    """

    id: str
    name: str
    reason_code: str
    reason: str
    estimated_total_inr: float | None = None
    shortfall_inr: float | None = None
    tags: list[str] = Field(default_factory=list)


class UnmetInterest(BaseModel):
    """An interest the user asked for that nothing affordable satisfies."""

    interest: str
    cheapest_matching_name: str | None = None
    cheapest_matching_total_inr: float | None = None
    additional_budget_required_inr: float | None = None


class InterestGap(BaseModel):
    """Told plainly when the budget cannot buy what the user asked for.

    Serving beach towns to someone who asked for mountains, without saying so,
    would look like the engine ignoring them. It did not: the mountains were
    priced and came out over budget. This says that out loud.
    """

    message: str
    unmet: list[UnmetInterest]
    matched: list[str] = Field(default_factory=list)
    smallest_additional_budget_inr: float | None = None


class BudgetShortfall(BaseModel):
    """What to tell the user when nothing fits the budget."""

    message: str
    cheapest_total_inr: float
    additional_budget_required_inr: float
    suggested_budget_inr: float
    alternatives: list[RejectedDestination]
    levers: list[str] = Field(
        ...,
        description="Concrete changes that would bring a trip into budget.",
    )


class ResponseMeta(BaseModel):
    """Per-response diagnostics, safe to show the user."""

    demo_mode: bool
    providers_used: list[str]
    provider_failures: list[str] = Field(default_factory=list)
    candidates_considered: int
    candidates_feasible: int
    cache_hits: int = 0
    elapsed_ms: int
    currency: str = "INR"
    data_disclaimer: str


class RecommendationResponse(BaseModel):
    """Top-level payload for the recommendations screen."""

    request: TripRequest
    origin_resolved: str
    origin_coordinates: Coordinates
    recommendations: list[DestinationRecommendation]
    stretch_options: list[DestinationRecommendation] = Field(default_factory=list)
    rejected: list[RejectedDestination] = Field(default_factory=list)
    shortfall: BudgetShortfall | None = None
    interest_gap: InterestGap | None = None
    ranking_weights: dict[str, float]
    meta: ResponseMeta
    confidence: Confidence
