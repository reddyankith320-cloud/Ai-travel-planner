"""Geolocation endpoints.

Reverse geocoding runs server-side, so the geocoding key stays on the server
even though the coordinates come from the browser.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from ..providers import geocoding
from ..schemas.common import Coordinates, Provenance
from ..schemas.enums import Confidence, DataSource
from .deps import CacheDep, ClientDep, SettingsDep

router = APIRouter(prefix="/geo", tags=["geo"])


class ResolvedPlace(BaseModel):
    """A place with coordinates and a label we are willing to stand behind."""

    coordinates: Coordinates
    label: str
    state: str | None = None
    country: str | None = None
    provenance: Provenance
    geocoder_configured: bool


@router.get("/reverse", response_model=ResolvedPlace, summary="Name a point from the device")
async def reverse(
    settings: SettingsDep,
    client: ClientDep,
    cache: CacheDep,
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
) -> ResolvedPlace:
    """Describe coordinates from browser geolocation.

    Browser geolocation yields a point, not an address. When no geocoder is
    configured this returns the coordinates as the label rather than guessing
    a place name -- planning works identically either way.
    """
    failures: list[str] = []
    hit = await geocoding.reverse_geocode(
        lat, lon, settings=settings, client=client, cache=cache, failures=failures
    )
    if hit is not None:
        return ResolvedPlace(
            coordinates=hit.coordinates,
            label=hit.display_name,
            state=hit.state,
            country=hit.country,
            provenance=hit.provenance,
            geocoder_configured=True,
        )
    return ResolvedPlace(
        coordinates=Coordinates(lat=lat, lon=lon),
        label=f"Your location ({geocoding.describe_coordinates(lat, lon)})",
        state=None,
        country=None,
        provenance=Provenance(
            source=DataSource.LIVE,
            confidence=Confidence.HIGH,
            provider="device-geolocation",
            note=(
                "No geocoder is configured, so we cannot resolve a street address. "
                "Distances and costs are unaffected."
                if not settings.geocoding_providers
                else "Every configured geocoder failed; using raw coordinates."
            ),
        ),
        geocoder_configured=bool(settings.geocoding_providers),
    )


@router.get("/search", response_model=ResolvedPlace, summary="Resolve a typed place name")
async def search(
    settings: SettingsDep,
    client: ClientDep,
    cache: CacheDep,
    q: str = Query(..., min_length=2, max_length=160),
) -> ResolvedPlace:
    """Geocode a place the user typed, for the manual-entry fallback."""
    failures: list[str] = []
    hit = await geocoding.geocode(
        q, settings=settings, client=client, cache=cache, failures=failures
    )
    if hit is None:
        detail = (
            f"Could not find '{q}'. "
            + (
                "No geocoding provider is configured on this deployment -- set one of "
                "GOOGLE_MAPS_SERVER_KEY, GEOAPIFY_API_KEY or LOCATIONIQ_API_KEY, or enable "
                "ENABLE_NOMINATIM."
                if not settings.geocoding_providers
                else "Try a larger nearby city, or allow location access instead."
            )
        )
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)
    return ResolvedPlace(
        coordinates=hit.coordinates,
        label=hit.display_name,
        state=hit.state,
        country=hit.country,
        provenance=hit.provenance,
        geocoder_configured=True,
    )
