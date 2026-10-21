"""Road routing: real driving distance and duration, with fallback.

When no routing provider answers, the caller keeps the geometric estimate from
`services/geo_math.py` and the provenance stays ESTIMATED. We never relabel an
estimate as a live route -- the distinction is exactly what the user needs to
judge the number.
"""

from __future__ import annotations

import logging

from ..config import Settings
from ..schemas.common import Provenance
from ..schemas.enums import Confidence, DataSource
from ..services.transport import RouteInfo
from .cache import CacheBackend
from .http import ProviderClient, ProviderError

logger = logging.getLogger(__name__)


async def road_route(
    origin_lat: float,
    origin_lon: float,
    dest_lat: float,
    dest_lon: float,
    *,
    settings: Settings,
    client: ProviderClient,
    cache: CacheBackend,
    failures: list[str] | None = None,
) -> RouteInfo | None:
    """Live driving distance/duration, or None if no provider answered."""
    cache_key = (
        f"route:{origin_lat:.3f},{origin_lon:.3f}->{dest_lat:.3f},{dest_lon:.3f}"
    )
    cached = cache.get(cache_key)
    if cached:
        return RouteInfo(
            road_km=cached["km"],
            road_hours=cached["hours"],
            provenance=Provenance(
                source=DataSource.CACHED,
                confidence=Confidence.HIGH,
                provider=cached.get("provider"),
                note="Road distance from a previous provider lookup.",
            ),
            note="Cached road route",
        )

    for provider in settings.routing_providers:
        try:
            result = await _route_with(
                provider,
                origin_lat,
                origin_lon,
                dest_lat,
                dest_lon,
                settings=settings,
                client=client,
            )
        except ProviderError as exc:
            logger.warning("routing provider %s failed: %s", provider, exc.detail)
            if failures is not None:
                failures.append(f"routing/{provider}: {exc.detail}")
            continue
        if result is not None:
            cache.set(
                cache_key,
                {"km": result.road_km, "hours": result.road_hours, "provider": provider},
                settings.cache_ttl_seconds,
            )
            return result
    return None


async def _route_with(
    provider: str,
    origin_lat: float,
    origin_lon: float,
    dest_lat: float,
    dest_lon: float,
    *,
    settings: Settings,
    client: ProviderClient,
) -> RouteInfo | None:
    if provider == "google":
        # Routes API v2. `fieldMask` is required and keeps the response (and
        # the bill) to just what we use.
        payload = await client.post_json(
            "google-routes",
            "https://routes.googleapis.com/directions/v2:computeRoutes",
            json_body={
                "origin": {
                    "location": {"latLng": {"latitude": origin_lat, "longitude": origin_lon}}
                },
                "destination": {
                    "location": {"latLng": {"latitude": dest_lat, "longitude": dest_lon}}
                },
                "travelMode": "DRIVE",
                "routingPreference": "TRAFFIC_UNAWARE",
            },
            headers={
                "X-Goog-Api-Key": settings.google_maps_server_key or "",
                "X-Goog-FieldMask": "routes.distanceMeters,routes.duration",
                "Content-Type": "application/json",
            },
        )
        routes = payload.get("routes") or []
        if not routes:
            return None
        metres = routes[0].get("distanceMeters")
        duration = routes[0].get("duration", "0s")
        if metres is None:
            return None
        seconds = float(str(duration).rstrip("s") or 0)
        return RouteInfo(
            road_km=round(metres / 1000.0, 1),
            road_hours=round(seconds / 3600.0, 2),
            provenance=Provenance.live("google-routes"),
            note="Live road route",
        )

    if provider == "osrm":
        payload = await client.get_json(
            "osrm",
            f"{settings.osrm_base_url}/route/v1/driving/"
            f"{origin_lon},{origin_lat};{dest_lon},{dest_lat}",
            params={"overview": "false", "alternatives": "false"},
        )
        if payload.get("code") != "Ok" or not payload.get("routes"):
            return None
        route = payload["routes"][0]
        return RouteInfo(
            road_km=round(route["distance"] / 1000.0, 1),
            road_hours=round(route["duration"] / 3600.0, 2),
            provenance=Provenance(
                source=DataSource.LIVE,
                confidence=Confidence.MEDIUM,
                provider="osrm",
                note=(
                    "OSRM demo server, fair-use and no availability guarantee. "
                    "Self-host for production. Data (c) OpenStreetMap contributors."
                ),
            ),
            note="Live road route",
        )

    return None
