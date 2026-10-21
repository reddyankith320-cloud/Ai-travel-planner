"""Geocoding and reverse geocoding, across providers, with fallback.

Providers are tried in the order `Settings.geocoding_providers` gives, which
puts credentialed providers ahead of the keyless fair-use ones. If every
provider fails, the caller gets `None` and decides what to do -- we never
invent coordinates.

An important honesty point: browser geolocation returns coordinates, not an
address. When no geocoder is configured we say "near 17.39, 78.49" rather than
pretending to know the street.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..config import Settings
from ..schemas.common import Coordinates, Provenance
from ..schemas.enums import Confidence, DataSource
from .cache import CacheBackend
from .http import ProviderClient, ProviderError

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class GeocodeResult:
    """A resolved place."""

    coordinates: Coordinates
    display_name: str
    state: str | None
    country: str | None
    provenance: Provenance


async def geocode(
    query: str,
    *,
    settings: Settings,
    client: ProviderClient,
    cache: CacheBackend,
    failures: list[str] | None = None,
) -> GeocodeResult | None:
    """Resolve a place name to coordinates, trying each provider in turn."""
    query = (query or "").strip()
    if not query:
        return None

    cache_key = f"geocode:{query.lower()}"
    cached = cache.get(cache_key)
    if cached:
        return GeocodeResult(
            coordinates=Coordinates(lat=cached["lat"], lon=cached["lon"]),
            display_name=cached["display_name"],
            state=cached.get("state"),
            country=cached.get("country"),
            provenance=Provenance(
                source=DataSource.CACHED,
                confidence=Confidence.HIGH,
                provider=cached.get("provider"),
                note="Previously resolved address, served from cache.",
            ),
        )

    for provider in settings.geocoding_providers:
        try:
            result = await _geocode_with(provider, query, settings=settings, client=client)
        except ProviderError as exc:
            logger.warning("geocoding provider %s failed: %s", provider, exc.detail)
            if failures is not None:
                failures.append(f"geocoding/{provider}: {exc.detail}")
            continue
        if result is not None:
            cache.set(
                cache_key,
                {
                    "lat": result.coordinates.lat,
                    "lon": result.coordinates.lon,
                    "display_name": result.display_name,
                    "state": result.state,
                    "country": result.country,
                    "provider": provider,
                },
                settings.cache_ttl_seconds,
            )
            return result
    return None


async def _geocode_with(
    provider: str, query: str, *, settings: Settings, client: ProviderClient
) -> GeocodeResult | None:
    if provider == "google":
        payload = await client.get_json(
            "google-geocoding",
            "https://maps.googleapis.com/maps/api/geocode/json",
            params={
                "address": query,
                "key": settings.google_maps_server_key,
                "region": "in",
            },
        )
        if payload.get("status") != "OK" or not payload.get("results"):
            return None
        top = payload["results"][0]
        location = top["geometry"]["location"]
        return GeocodeResult(
            coordinates=Coordinates(lat=location["lat"], lon=location["lng"]),
            display_name=top.get("formatted_address", query),
            state=_component(top.get("address_components", []), "administrative_area_level_1"),
            country=_component(top.get("address_components", []), "country"),
            provenance=Provenance.live("google-geocoding"),
        )

    if provider == "geoapify":
        payload = await client.get_json(
            "geoapify-geocoding",
            "https://api.geoapify.com/v1/geocode/search",
            params={
                "text": query,
                "apiKey": settings.geoapify_api_key,
                "limit": 1,
                "filter": "countrycode:in",
            },
        )
        features = payload.get("features") or []
        if not features:
            return None
        props = features[0]["properties"]
        return GeocodeResult(
            coordinates=Coordinates(lat=props["lat"], lon=props["lon"]),
            display_name=props.get("formatted", query),
            state=props.get("state"),
            country=props.get("country"),
            provenance=Provenance.live("geoapify-geocoding"),
        )

    if provider == "locationiq":
        payload = await client.get_json(
            "locationiq-geocoding",
            "https://us1.locationiq.com/v1/search",
            params={
                "key": settings.locationiq_api_key,
                "q": query,
                "format": "json",
                "limit": 1,
                "countrycodes": "in",
                "addressdetails": 1,
            },
        )
        if not payload:
            return None
        top = payload[0]
        address = top.get("address", {})
        return GeocodeResult(
            coordinates=Coordinates(lat=float(top["lat"]), lon=float(top["lon"])),
            display_name=top.get("display_name", query),
            state=address.get("state"),
            country=address.get("country"),
            provenance=Provenance.live("locationiq-geocoding"),
        )

    if provider == "nominatim":
        payload = await client.get_json(
            "nominatim",
            f"{settings.nominatim_base_url}/search",
            params={
                "q": query,
                "format": "jsonv2",
                "limit": 1,
                "countrycodes": "in",
                "addressdetails": 1,
                "email": settings.osm_contact_email or "",
            },
        )
        if not payload:
            return None
        top = payload[0]
        address = top.get("address", {})
        return GeocodeResult(
            coordinates=Coordinates(lat=float(top["lat"]), lon=float(top["lon"])),
            display_name=top.get("display_name", query),
            state=address.get("state"),
            country=address.get("country"),
            provenance=Provenance(
                source=DataSource.LIVE,
                confidence=Confidence.MEDIUM,
                provider="nominatim",
                note="OpenStreetMap/Nominatim, fair-use tier. Data (c) OpenStreetMap contributors.",
            ),
        )

    return None


async def reverse_geocode(
    lat: float,
    lon: float,
    *,
    settings: Settings,
    client: ProviderClient,
    cache: CacheBackend,
    failures: list[str] | None = None,
) -> GeocodeResult | None:
    """Turn device coordinates into a human-readable place, if we can.

    Returns `None` when no geocoder is configured -- the caller then shows the
    coordinates themselves rather than guessing at a place name.
    """
    cache_key = f"revgeo:{lat:.4f},{lon:.4f}"
    cached = cache.get(cache_key)
    if cached:
        return GeocodeResult(
            coordinates=Coordinates(lat=lat, lon=lon),
            display_name=cached["display_name"],
            state=cached.get("state"),
            country=cached.get("country"),
            provenance=Provenance(
                source=DataSource.CACHED,
                confidence=Confidence.HIGH,
                provider=cached.get("provider"),
            ),
        )

    for provider in settings.geocoding_providers:
        try:
            result = await _reverse_with(provider, lat, lon, settings=settings, client=client)
        except ProviderError as exc:
            logger.warning("reverse geocoding provider %s failed: %s", provider, exc.detail)
            if failures is not None:
                failures.append(f"reverse-geocoding/{provider}: {exc.detail}")
            continue
        if result is not None:
            cache.set(
                cache_key,
                {
                    "display_name": result.display_name,
                    "state": result.state,
                    "country": result.country,
                    "provider": provider,
                },
                settings.cache_ttl_seconds,
            )
            return result
    return None


async def _reverse_with(
    provider: str, lat: float, lon: float, *, settings: Settings, client: ProviderClient
) -> GeocodeResult | None:
    coordinates = Coordinates(lat=lat, lon=lon)

    if provider == "google":
        payload = await client.get_json(
            "google-geocoding",
            "https://maps.googleapis.com/maps/api/geocode/json",
            params={"latlng": f"{lat},{lon}", "key": settings.google_maps_server_key},
        )
        if payload.get("status") != "OK" or not payload.get("results"):
            return None
        top = payload["results"][0]
        return GeocodeResult(
            coordinates=coordinates,
            display_name=top.get("formatted_address", f"{lat:.4f}, {lon:.4f}"),
            state=_component(top.get("address_components", []), "administrative_area_level_1"),
            country=_component(top.get("address_components", []), "country"),
            provenance=Provenance.live("google-geocoding"),
        )

    if provider == "geoapify":
        payload = await client.get_json(
            "geoapify-geocoding",
            "https://api.geoapify.com/v1/geocode/reverse",
            params={"lat": lat, "lon": lon, "apiKey": settings.geoapify_api_key},
        )
        features = payload.get("features") or []
        if not features:
            return None
        props = features[0]["properties"]
        return GeocodeResult(
            coordinates=coordinates,
            display_name=props.get("formatted", f"{lat:.4f}, {lon:.4f}"),
            state=props.get("state"),
            country=props.get("country"),
            provenance=Provenance.live("geoapify-geocoding"),
        )

    if provider == "locationiq":
        payload = await client.get_json(
            "locationiq-geocoding",
            "https://us1.locationiq.com/v1/reverse",
            params={
                "key": settings.locationiq_api_key,
                "lat": lat,
                "lon": lon,
                "format": "json",
                "addressdetails": 1,
            },
        )
        address = payload.get("address", {})
        return GeocodeResult(
            coordinates=coordinates,
            display_name=payload.get("display_name", f"{lat:.4f}, {lon:.4f}"),
            state=address.get("state"),
            country=address.get("country"),
            provenance=Provenance.live("locationiq-geocoding"),
        )

    if provider == "nominatim":
        payload = await client.get_json(
            "nominatim",
            f"{settings.nominatim_base_url}/reverse",
            params={
                "lat": lat,
                "lon": lon,
                "format": "jsonv2",
                "addressdetails": 1,
                "zoom": 12,
                "email": settings.osm_contact_email or "",
            },
        )
        address = payload.get("address", {})
        return GeocodeResult(
            coordinates=coordinates,
            display_name=payload.get("display_name", f"{lat:.4f}, {lon:.4f}"),
            state=address.get("state"),
            country=address.get("country"),
            provenance=Provenance(
                source=DataSource.LIVE,
                confidence=Confidence.MEDIUM,
                provider="nominatim",
                note="Data (c) OpenStreetMap contributors.",
            ),
        )

    return None


def _component(components: list[dict], wanted_type: str) -> str | None:
    for component in components:
        if wanted_type in component.get("types", []):
            return component.get("long_name")
    return None


def describe_coordinates(lat: float, lon: float) -> str:
    """Honest label for a point we could not reverse geocode."""
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    return f"{abs(lat):.3f}{ns}, {abs(lon):.3f}{ew}"
