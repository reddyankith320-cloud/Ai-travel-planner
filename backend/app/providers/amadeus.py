"""Amadeus Self-Service adapter: OAuth2 token, flight fares, hotel rates.

Status warning, as of October 2026: third-party migration guides report that
Amadeus is decommissioning the *Self-Service* developer portal (a date of
17 July 2026 is cited) leaving only Enterprise access under a sales contract.
We could not confirm this on Amadeus's own site. Treat this adapter as
unverified: it is written to the documented Self-Service API shape, is off by
default (`FLIGHT_PROVIDER=none`, `HOTEL_PROVIDER=none`), and TripWise works
fully without it. Confirm your account status with Amadeus before enabling it,
and see `docs/PROVIDERS.md`.

The test environment (`AMADEUS_ENVIRONMENT=test`) serves cached, limited data.
Anything it returns is labelled as a live provider quote because that is what
the provider calls it -- but test-environment prices are not real market fares,
so keep it on `test` only for development.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from ..config import Settings
from .http import ProviderClient, ProviderError

logger = logging.getLogger(__name__)

_TEST_HOST = "https://test.api.amadeus.com"
_PRODUCTION_HOST = "https://api.amadeus.com"


@dataclass
class _Token:
    value: str
    expires_at: float

    @property
    def valid(self) -> bool:
        # Refresh a little early so a request never races the expiry.
        return bool(self.value) and time.time() < self.expires_at - 30


_token: _Token | None = None


def base_host(settings: Settings) -> str:
    return _TEST_HOST if settings.amadeus_environment == "test" else _PRODUCTION_HOST


async def get_access_token(settings: Settings, client: ProviderClient) -> str | None:
    """Fetch (and cache in-process) an OAuth2 client-credentials token."""
    global _token
    if _token is not None and _token.valid:
        return _token.value
    if not (settings.amadeus_client_id and settings.amadeus_client_secret):
        return None

    try:
        payload = await client.post_json(
            "amadeus-auth",
            f"{base_host(settings)}/v1/security/oauth2/token",
            data={
                "grant_type": "client_credentials",
                "client_id": settings.amadeus_client_id,
                "client_secret": settings.amadeus_client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    except ProviderError as exc:
        logger.warning("Amadeus authentication failed: %s", exc.detail)
        return None

    access_token = payload.get("access_token")
    if not access_token:
        return None
    _token = _Token(value=access_token, expires_at=time.time() + float(payload.get("expires_in", 1799)))
    return _token.value


def reset_token() -> None:
    """Clear the cached token. Used by tests."""
    global _token
    _token = None


async def cheapest_flight_fare_inr(
    *,
    origin_iata: str,
    destination_iata: str,
    departure_date: str,
    adults: int,
    settings: Settings,
    client: ProviderClient,
    failures: list[str] | None = None,
) -> float | None:
    """Lowest one-way fare per adult in INR, or None.

    Returns a *per-person one-way* figure because that is the unit the cost
    model prices flights in. Dividing the provider's total by the passenger
    count here keeps the per-person/per-group rule in one place.
    """
    token = await get_access_token(settings, client)
    if not token:
        if failures is not None:
            failures.append("flights/amadeus: could not authenticate")
        return None

    try:
        payload = await client.get_json(
            "amadeus-flights",
            f"{base_host(settings)}/v2/shopping/flight-offers",
            params={
                "originLocationCode": origin_iata,
                "destinationLocationCode": destination_iata,
                "departureDate": departure_date,
                "adults": adults,
                "currencyCode": "INR",
                "max": 5,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
    except ProviderError as exc:
        logger.warning("Amadeus flight search failed: %s", exc.detail)
        if failures is not None:
            failures.append(f"flights/amadeus: {exc.detail}")
        return None

    offers = payload.get("data") or []
    totals: list[float] = []
    for offer in offers:
        price = offer.get("price") or {}
        # `grandTotal` covers all travellers on the offer.
        raw = price.get("grandTotal") or price.get("total")
        currency = price.get("currency")
        if raw is None or currency != "INR":
            continue
        try:
            totals.append(float(raw))
        except (TypeError, ValueError):
            continue

    if not totals:
        return None
    return round(min(totals) / max(1, adults), 2)


async def cheapest_hotel_nightly_inr(
    *,
    lat: float,
    lon: float,
    check_in: str,
    check_out: str,
    rooms: int,
    adults: int,
    settings: Settings,
    client: ProviderClient,
    failures: list[str] | None = None,
) -> float | None:
    """Lowest nightly rate per room in INR, or None.

    Two calls: find hotels by geocode, then price them. Hotel IDs are capped
    so one request cannot fan out into an expensive query.
    """
    token = await get_access_token(settings, client)
    if not token:
        if failures is not None:
            failures.append("hotels/amadeus: could not authenticate")
        return None

    headers = {"Authorization": f"Bearer {token}"}
    try:
        listing = await client.get_json(
            "amadeus-hotels",
            f"{base_host(settings)}/v1/reference-data/locations/hotels/by-geocode",
            params={"latitude": lat, "longitude": lon, "radius": 20, "radiusUnit": "KM"},
            headers=headers,
        )
    except ProviderError as exc:
        logger.warning("Amadeus hotel lookup failed: %s", exc.detail)
        if failures is not None:
            failures.append(f"hotels/amadeus: {exc.detail}")
        return None

    hotel_ids = [h["hotelId"] for h in (listing.get("data") or []) if h.get("hotelId")][:20]
    if not hotel_ids:
        return None

    try:
        offers = await client.get_json(
            "amadeus-hotels",
            f"{base_host(settings)}/v3/shopping/hotel-offers",
            params={
                "hotelIds": ",".join(hotel_ids),
                "checkInDate": check_in,
                "checkOutDate": check_out,
                "roomQuantity": rooms,
                "adults": adults,
                "currency": "INR",
                "bestRateOnly": "true",
            },
            headers=headers,
        )
    except ProviderError as exc:
        logger.warning("Amadeus hotel offers failed: %s", exc.detail)
        if failures is not None:
            failures.append(f"hotels/amadeus: {exc.detail}")
        return None

    nights = _nights_between(check_in, check_out)
    nightly: list[float] = []
    for hotel in offers.get("data") or []:
        for offer in hotel.get("offers") or []:
            price = offer.get("price") or {}
            if price.get("currency") != "INR":
                continue
            # `base`/`total` cover the whole stay for `roomQuantity` rooms.
            raw = price.get("base") or price.get("total")
            try:
                stay_total = float(raw)
            except (TypeError, ValueError):
                continue
            per_room_night = stay_total / max(1, nights) / max(1, rooms)
            nightly.append(per_room_night)

    if not nightly:
        return None
    return round(min(nightly), 2)


def _nights_between(check_in: str, check_out: str) -> int:
    from datetime import date

    try:
        start = date.fromisoformat(check_in)
        end = date.fromisoformat(check_out)
    except ValueError:
        return 1
    return max(1, (end - start).days)
