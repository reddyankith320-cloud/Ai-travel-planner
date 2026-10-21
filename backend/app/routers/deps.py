"""Shared FastAPI dependencies."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from ..config import Settings, get_settings
from ..providers.cache import CacheBackend, get_cache
from ..providers.http import ProviderClient, get_client
from ..services.planner import PlannerContext

SettingsDep = Annotated[Settings, Depends(get_settings)]


def provider_cache(settings: SettingsDep) -> CacheBackend:
    return get_cache(settings.redis_url, settings.cache_max_entries)


def provider_client(settings: SettingsDep) -> ProviderClient:
    return get_client(
        timeout_seconds=settings.http_timeout_seconds,
        max_retries=settings.http_max_retries,
        backoff_base_seconds=settings.http_backoff_base_seconds,
        contact_email=settings.osm_contact_email,
    )


def planner_context(
    settings: SettingsDep,
    client: Annotated[ProviderClient, Depends(provider_client)],
    cache: Annotated[CacheBackend, Depends(provider_cache)],
) -> PlannerContext:
    """Fresh per-request context so failures are scoped to one request."""
    return PlannerContext(settings=settings, client=client, cache=cache)


PlannerContextDep = Annotated[PlannerContext, Depends(planner_context)]
CacheDep = Annotated[CacheBackend, Depends(provider_cache)]
ClientDep = Annotated[ProviderClient, Depends(provider_client)]


def owner_token(
    x_tripwise_owner: Annotated[str | None, Header(alias="X-TripWise-Owner")] = None,
) -> str:
    """Identify whose saved trips these are.

    Not authentication. It scopes an anonymous trip list and is rejected if it
    is short enough to be guessable, so one client cannot trivially enumerate
    another's saved trips.
    """
    token = (x_tripwise_owner or "").strip()
    if len(token) < 16 or len(token) > 64 or not token.replace("-", "").isalnum():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Send an X-TripWise-Owner header: 16-64 alphanumeric characters that your "
                "client generates once and reuses. It scopes your saved trips."
            ),
        )
    return token


OwnerTokenDep = Annotated[str, Depends(owner_token)]
