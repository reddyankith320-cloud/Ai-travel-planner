"""TripWise API application.

Run it with:
    uvicorn app.main:app --reload --port 8000     (from the `backend/` directory)
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .config import get_settings
from .db import init_db
from .middleware import ErrorHandlingMiddleware, RateLimitMiddleware
from .providers.http import shutdown_client
from .routers import geo, itinerary, meta, recommendations, trips

DESCRIPTION = """
**TripWise** is a budget-first travel planner: you do not enter a destination.

You give a starting point, a budget in INR, dates, group size, travel mode and
interests. TripWise discovers which destinations are genuinely affordable and
reachable, ranks them with a published weighting, and builds a day-by-day plan
for the one you pick.

### How to read the numbers
Every figure carries a `provenance` block:

| source | meaning |
|---|---|
| `live` | fetched from a provider API during this request |
| `cached` | an earlier live provider response, still within its TTL |
| `estimated` | computed by TripWise's deterministic cost model |
| `curated` | from the maintained destination dataset in this repository |
| `sample` | illustrative only |

TripWise does not sell tickets and holds no inventory. There is no public API
for Indian rail or intercity bus fares, so those are always `estimated` and we
link to the official booking channel. Nothing is ever presented as a live
quote unless a provider actually returned it.
"""


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    # These are chatty and can echo full request URLs, which may carry keys.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    logger = logging.getLogger("tripwise")

    init_db(settings)
    status = settings.provider_status()
    if status["demo_mode"]:
        logger.warning(
            "Starting in DEMO MODE: no provider credentials configured. All figures will be "
            "deterministic estimates, clearly labelled. See .env.example to add providers."
        )
    else:
        logger.info(
            "Providers -- geocoding=%s routing=%s places=%s llm=%s",
            status["geocoding"], status["routing"], status["places"], status["llm"],
        )
    yield
    await shutdown_client()
    logger.info("TripWise API shut down")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        description=DESCRIPTION,
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    app.add_middleware(ErrorHandlingMiddleware)
    app.add_middleware(
        RateLimitMiddleware,
        max_requests=settings.rate_limit_requests,
        window_seconds=settings.rate_limit_window_seconds,
    )
    # Explicit origins only -- never "*" with credentials.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-TripWise-Owner"],
        max_age=600,
    )

    prefix = settings.api_prefix
    app.include_router(meta.router, prefix=prefix)
    app.include_router(geo.router, prefix=prefix)
    app.include_router(recommendations.router, prefix=prefix)
    app.include_router(itinerary.router, prefix=prefix)
    app.include_router(trips.router, prefix=prefix)

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {
            "name": settings.app_name,
            "version": __version__,
            "docs": "/docs",
            "health": f"{prefix}/health",
        }

    return app


app = create_app()
