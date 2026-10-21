"""Points of interest around a destination.

Each provider returns only the fields it genuinely supplies. Opening hours and
admission charges are populated when the source has them and left `None`
otherwise -- an empty field is honest, an invented one is not.

Live results *augment* the curated dataset rather than replacing it, so a
destination always has usable highlights even when every provider is down.
"""

from __future__ import annotations

import logging

from ..config import Settings
from ..schemas.common import Coordinates, PlaceRef, Provenance
from ..schemas.enums import Confidence, DataSource
from .cache import CacheBackend
from .http import ProviderClient, ProviderError

logger = logging.getLogger(__name__)

# OSM tags worth surfacing as attractions, mapped to our categories.
_OSM_CATEGORY_MAP: dict[tuple[str, str], str] = {
    ("tourism", "attraction"): "activity",
    ("tourism", "museum"): "museum",
    ("tourism", "viewpoint"): "viewpoint",
    ("tourism", "artwork"): "art_culture",
    ("tourism", "zoo"): "wildlife",
    ("historic", "monument"): "heritage",
    ("historic", "fort"): "heritage",
    ("historic", "ruins"): "heritage",
    ("historic", "castle"): "heritage",
    ("historic", "archaeological_site"): "heritage",
    ("natural", "beach"): "beach",
    ("natural", "waterfall"): "waterfall",
    ("natural", "peak"): "viewpoint",
    ("leisure", "park"): "garden",
    ("leisure", "garden"): "garden",
    ("amenity", "place_of_worship"): "spiritual",
}

_GOOGLE_CATEGORY_MAP: dict[str, str] = {
    "tourist_attraction": "activity",
    "museum": "museum",
    "park": "garden",
    "hindu_temple": "spiritual",
    "church": "spiritual",
    "mosque": "spiritual",
    "place_of_worship": "spiritual",
    "art_gallery": "art_culture",
    "zoo": "wildlife",
    "restaurant": "food",
    "shopping_mall": "shopping",
    "natural_feature": "nature",
    "historical_landmark": "heritage",
}


async def nearby_places(
    lat: float,
    lon: float,
    *,
    settings: Settings,
    client: ProviderClient,
    cache: CacheBackend,
    radius_m: int = 15_000,
    limit: int = 20,
    failures: list[str] | None = None,
) -> list[PlaceRef]:
    """Attractions near a point, from the first provider that answers."""
    cache_key = f"places:{lat:.3f},{lon:.3f}:{radius_m}:{limit}"
    cached = cache.get(cache_key)
    if cached is not None:
        return [_place_from_cache(entry) for entry in cached]

    for provider in settings.places_providers:
        try:
            places = await _places_with(
                provider,
                lat,
                lon,
                settings=settings,
                client=client,
                radius_m=radius_m,
                limit=limit,
            )
        except ProviderError as exc:
            logger.warning("places provider %s failed: %s", provider, exc.detail)
            if failures is not None:
                failures.append(f"places/{provider}: {exc.detail}")
            continue
        if places:
            cache.set(
                cache_key,
                [p.model_dump(mode="json") for p in places],
                settings.cache_ttl_seconds,
            )
            return places
    return []


def _place_from_cache(entry: dict) -> PlaceRef:
    place = PlaceRef.model_validate(entry)
    return place.model_copy(
        update={
            "provenance": Provenance(
                source=DataSource.CACHED,
                confidence=place.provenance.confidence,
                provider=place.provenance.provider,
                note="Served from the provider cache.",
            )
        }
    )


async def _places_with(
    provider: str,
    lat: float,
    lon: float,
    *,
    settings: Settings,
    client: ProviderClient,
    radius_m: int,
    limit: int,
) -> list[PlaceRef]:
    if provider == "google":
        payload = await client.post_json(
            "google-places",
            "https://places.googleapis.com/v1/places:searchNearby",
            json_body={
                "includedTypes": ["tourist_attraction", "museum", "park", "historical_landmark"],
                "maxResultCount": min(20, limit),
                "locationRestriction": {
                    "circle": {
                        "center": {"latitude": lat, "longitude": lon},
                        "radius": float(min(radius_m, 50_000)),
                    }
                },
            },
            headers={
                "X-Goog-Api-Key": settings.google_maps_server_key or "",
                # Field mask keeps the response -- and the per-SKU cost -- minimal.
                "X-Goog-FieldMask": (
                    "places.displayName,places.location,places.types,places.rating,"
                    "places.regularOpeningHours.weekdayDescriptions"
                ),
                "Content-Type": "application/json",
            },
        )
        out: list[PlaceRef] = []
        for place in payload.get("places", []):
            name = (place.get("displayName") or {}).get("text")
            location = place.get("location") or {}
            if not name or "latitude" not in location:
                continue
            types = place.get("types", [])
            hours_block = (place.get("regularOpeningHours") or {}).get("weekdayDescriptions")
            out.append(
                PlaceRef(
                    name=name,
                    category=_first_category(types, _GOOGLE_CATEGORY_MAP, "activity"),
                    coordinates=Coordinates(
                        lat=location["latitude"], lon=location["longitude"]
                    ),
                    # Google Places does not publish admission charges.
                    entry_fee_inr=None,
                    opening_hours="; ".join(hours_block[:2]) if hours_block else None,
                    rating=place.get("rating"),
                    provenance=Provenance.live("google-places"),
                )
            )
        return out[:limit]

    if provider == "foursquare":
        payload = await client.get_json(
            "foursquare-places",
            "https://api.foursquare.com/v3/places/search",
            params={
                "ll": f"{lat},{lon}",
                "radius": min(radius_m, 100_000),
                "categories": "16000,10000",  # landmarks/outdoors, arts/entertainment
                "limit": min(50, limit),
                "sort": "POPULARITY",
            },
            headers={
                "Authorization": settings.foursquare_api_key or "",
                "accept": "application/json",
            },
        )
        out = []
        for place in payload.get("results", []):
            geo = (place.get("geocodes") or {}).get("main") or {}
            if "latitude" not in geo:
                continue
            categories = [c.get("name", "") for c in place.get("categories", [])]
            out.append(
                PlaceRef(
                    name=place.get("name", "Unnamed place"),
                    category=_foursquare_category(categories),
                    coordinates=Coordinates(lat=geo["latitude"], lon=geo["longitude"]),
                    entry_fee_inr=None,
                    opening_hours=None,
                    rating=None,
                    description=", ".join(categories[:2]) or None,
                    provenance=Provenance.live("foursquare-places"),
                )
            )
        return out[:limit]

    if provider == "geoapify":
        payload = await client.get_json(
            "geoapify-places",
            "https://api.geoapify.com/v2/places",
            params={
                "categories": "tourism.attraction,tourism.sights,entertainment.museum,natural",
                "filter": f"circle:{lon},{lat},{min(radius_m, 50_000)}",
                "bias": f"proximity:{lon},{lat}",
                "limit": min(100, limit),
                "apiKey": settings.geoapify_api_key,
            },
        )
        out = []
        for feature in payload.get("features", []):
            props = feature.get("properties", {})
            if not props.get("name") or "lat" not in props:
                continue
            out.append(
                PlaceRef(
                    name=props["name"],
                    category=_geoapify_category(props.get("categories", [])),
                    coordinates=Coordinates(lat=props["lat"], lon=props["lon"]),
                    entry_fee_inr=None,
                    opening_hours=props.get("opening_hours"),
                    rating=None,
                    provenance=Provenance.live("geoapify-places"),
                )
            )
        return out[:limit]

    if provider == "overpass":
        radius = min(radius_m, 25_000)
        query = f"""
        [out:json][timeout:25];
        (
          node["tourism"~"attraction|museum|viewpoint|artwork|zoo"](around:{radius},{lat},{lon});
          node["historic"~"monument|fort|ruins|castle|archaeological_site"](around:{radius},{lat},{lon});
          node["natural"~"beach|waterfall|peak"](around:{radius},{lat},{lon});
        );
        out body {min(200, limit * 6)};
        """
        payload = await client.post_json(
            "overpass",
            settings.overpass_base_url,
            data={"data": query},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        out = []
        for element in payload.get("elements", []):
            tags = element.get("tags", {})
            name = tags.get("name") or tags.get("name:en")
            if not name or "lat" not in element:
                continue
            out.append(
                PlaceRef(
                    name=name,
                    category=_osm_category(tags),
                    coordinates=Coordinates(lat=element["lat"], lon=element["lon"]),
                    # OSM has a `fee` tag but almost never a price; report only
                    # what is actually there.
                    entry_fee_inr=_osm_fee(tags),
                    opening_hours=tags.get("opening_hours"),
                    rating=None,
                    description=tags.get("description"),
                    provenance=Provenance(
                        source=DataSource.LIVE,
                        confidence=Confidence.MEDIUM,
                        provider="overpass",
                        note=(
                            "OpenStreetMap via Overpass. Community-maintained, so coverage "
                            "and detail vary. Data (c) OpenStreetMap contributors (ODbL)."
                        ),
                    ),
                )
            )
        return out[:limit]

    return []


def _first_category(types: list[str], mapping: dict[str, str], default: str) -> str:
    for type_name in types:
        if type_name in mapping:
            return mapping[type_name]
    return default


def _osm_category(tags: dict[str, str]) -> str:
    for (key, value), category in _OSM_CATEGORY_MAP.items():
        if tags.get(key) == value:
            return category
    if "tourism" in tags:
        return "activity"
    if "historic" in tags:
        return "heritage"
    if "natural" in tags:
        return "nature"
    return "activity"


def _osm_fee(tags: dict[str, str]) -> float | None:
    """Only return a fee when OSM actually carries a numeric charge."""
    raw = tags.get("charge") or tags.get("fee:amount")
    if not raw:
        return None
    digits = "".join(ch for ch in raw if ch.isdigit() or ch == ".")
    try:
        return float(digits) if digits else None
    except ValueError:
        return None


def _foursquare_category(names: list[str]) -> str:
    joined = " ".join(names).lower()
    for needle, category in (
        ("beach", "beach"),
        ("museum", "museum"),
        ("temple", "spiritual"),
        ("church", "spiritual"),
        ("mosque", "spiritual"),
        ("historic", "heritage"),
        ("monument", "heritage"),
        ("fort", "heritage"),
        ("park", "garden"),
        ("waterfall", "waterfall"),
        ("lake", "lake"),
        ("scenic", "viewpoint"),
        ("restaurant", "food"),
        ("market", "shopping"),
    ):
        if needle in joined:
            return category
    return "activity"


def _geoapify_category(categories: list[str]) -> str:
    joined = " ".join(categories).lower()
    for needle, category in (
        ("beach", "beach"),
        ("museum", "museum"),
        ("religion", "spiritual"),
        ("castle", "heritage"),
        ("fort", "heritage"),
        ("memorial", "heritage"),
        ("water", "waterfall"),
        ("mountain", "viewpoint"),
        ("viewpoint", "viewpoint"),
        ("park", "garden"),
    ):
        if needle in joined:
            return category
    return "activity"
