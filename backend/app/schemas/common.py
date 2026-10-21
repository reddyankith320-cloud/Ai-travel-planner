"""Small value objects reused across requests and responses."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field

from .enums import Confidence, DataSource


class Coordinates(BaseModel):
    """A WGS-84 point."""

    model_config = ConfigDict(frozen=True)

    lat: float = Field(..., ge=-90.0, le=90.0)
    lon: float = Field(..., ge=-180.0, le=180.0)


class Provenance(BaseModel):
    """Where a figure came from and how fresh it is.

    Attached to anything a user might mistake for a live quote. `as_of` is
    only set for data that actually came from a provider.
    """

    source: DataSource
    confidence: Confidence
    provider: str | None = None
    as_of: datetime | None = None
    note: str | None = None

    @property
    def is_live(self) -> bool:
        return self.source in (DataSource.LIVE, DataSource.CACHED)

    @classmethod
    def estimated(cls, note: str | None = None, confidence: Confidence = Confidence.MEDIUM) -> "Provenance":
        return cls(source=DataSource.ESTIMATED, confidence=confidence, note=note)

    @classmethod
    def curated(cls, note: str | None = None) -> "Provenance":
        return cls(source=DataSource.CURATED, confidence=Confidence.MEDIUM, note=note)

    @classmethod
    def live(cls, provider: str, confidence: Confidence = Confidence.HIGH) -> "Provenance":
        return cls(
            source=DataSource.LIVE,
            confidence=confidence,
            provider=provider,
            as_of=datetime.now(timezone.utc),
        )


class BookingLink(BaseModel):
    """An outbound link to an official booking channel.

    TripWise never claims to hold inventory. Where real-time fares are not
    available through a licensed API, we link to the operator instead.
    """

    label: str
    url: str
    official: bool = True
    note: str | None = None


class PlaceRef(BaseModel):
    """A point of interest."""

    name: str
    category: str
    coordinates: Coordinates | None = None
    entry_fee_inr: float | None = None
    opening_hours: str | None = None
    rating: float | None = Field(default=None, ge=0.0, le=5.0)
    typical_visit_minutes: int | None = Field(default=None, ge=5, le=600)
    description: str | None = None
    provenance: Provenance


class Assumption(BaseModel):
    """A visible, editable modelling assumption.

    Exposing these is what makes the estimate auditable rather than a
    black-box number.
    """

    key: str
    label: str
    value: float
    unit: str
    editable: bool = True
    provenance: Provenance
