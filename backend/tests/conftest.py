"""Shared test fixtures.

Every test runs with providers disabled, so the suite is hermetic: no test
touches the network, and the deterministic engine is exercised directly.
Provider behaviour is tested separately against fakes.
"""

from __future__ import annotations

import os
import tempfile
from datetime import date, timedelta

import pytest

from app.config import Settings, get_settings
from app.data.catalog import get_destination
from app.providers import cache as cache_module
from app.schemas.enums import AccommodationType, Interest, Pace, TravelMode
from app.schemas.trip import OriginLocation, TripRequest

# Hyderabad -- central enough that candidates exist in every direction.
ORIGIN_LAT = 17.385
ORIGIN_LON = 78.486


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Make the suite hermetic.

    Every test must exercise the deterministic engine or a scripted fake.
    If one reaches for a real socket, fail loudly rather than silently
    depending on a provider being up -- and on network speed.
    """
    import socket

    real_connect = socket.socket.connect

    def guard(self, address, *args, **kwargs):
        host = address[0] if isinstance(address, tuple) else str(address)
        # Loopback is fine: TestClient and SQLite-over-TCP use it.
        if isinstance(host, str) and (
            host.startswith("127.") or host in ("::1", "localhost")
        ):
            return real_connect(self, address, *args, **kwargs)
        raise AssertionError(
            f"A test tried to open a network connection to {host}. "
            "Tests must use fakes, not live providers."
        )

    monkeypatch.setattr(socket.socket, "connect", guard)
    yield


@pytest.fixture(autouse=True)
def _isolate_caches():
    """Reset module-level singletons between tests."""
    get_settings.cache_clear()
    cache_module.reset_cache()
    yield
    get_settings.cache_clear()
    cache_module.reset_cache()


@pytest.fixture
def settings(tmp_path) -> Settings:
    """Offline settings: no credentials, no keyless providers, temp database."""
    return Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path}/test.db",
        enable_nominatim=False,
        enable_osrm=False,
        enable_overpass=False,
        enable_open_meteo=False,
        enable_wikimedia_images=False,
        openai_api_key=None,
        redis_url=None,
    )


@pytest.fixture
def start_date() -> date:
    """A date far enough ahead to avoid last-minute fare loading."""
    return date.today() + timedelta(days=45)


@pytest.fixture
def trip_request(start_date) -> TripRequest:
    return TripRequest(
        origin=OriginLocation(
            label="Hyderabad", coordinates={"lat": ORIGIN_LAT, "lon": ORIGIN_LON}
        ),
        budget_inr=35_000,
        travelers=2,
        days=4,
        start_date=start_date,
        travel_mode=TravelMode.ANY,
        accommodation=AccommodationType.BUDGET_HOTEL,
        pace=Pace.BALANCED,
        interests=[Interest.BEACHES, Interest.HERITAGE],
    )


def make_request(**overrides) -> TripRequest:
    """Build a TripRequest with sensible defaults, overriding any field."""
    base = {
        "origin": OriginLocation(
            label="Hyderabad", coordinates={"lat": ORIGIN_LAT, "lon": ORIGIN_LON}
        ),
        "budget_inr": 35_000,
        "travelers": 2,
        "days": 4,
        "start_date": date.today() + timedelta(days=45),
        "travel_mode": TravelMode.ANY,
        "accommodation": AccommodationType.BUDGET_HOTEL,
        "pace": Pace.BALANCED,
        "interests": [Interest.BEACHES],
    }
    base.update(overrides)
    return TripRequest(**base)


@pytest.fixture
def goa():
    return get_destination("north-goa")


@pytest.fixture
def hampi():
    return get_destination("hampi")


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    """A TestClient wired to an isolated database with providers disabled."""
    from fastapi.testclient import TestClient

    db_path = tmp_path / "api.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{db_path}")
    monkeypatch.setenv("ENABLE_NOMINATIM", "false")
    monkeypatch.setenv("ENABLE_OSRM", "false")
    monkeypatch.setenv("ENABLE_OVERPASS", "false")
    monkeypatch.setenv("ENABLE_OPEN_METEO", "false")
    monkeypatch.setenv("ENABLE_WIKIMEDIA_IMAGES", "false")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    # A generous limit so functional tests are not throttled.
    monkeypatch.setenv("RATE_LIMIT_REQUESTS", "500")

    get_settings.cache_clear()
    from app.db.session import reset_engine

    reset_engine()
    from app.main import create_app

    with TestClient(create_app()) as client:
        yield client
    reset_engine()
    get_settings.cache_clear()


@pytest.fixture
def owner_headers() -> dict[str, str]:
    return {"X-TripWise-Owner": "testowner0123456789abcd"}
