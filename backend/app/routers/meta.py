"""Metadata: health, provider status, and the option lists the UI needs."""

from __future__ import annotations

from fastapi import APIRouter

from ..__init__ import __version__
from ..data.catalog import all_tags, dataset_version, load_destinations
from ..schemas.enums import AccommodationType, Interest, Pace, TravelMode
from ..services import cost_model as cm
from ..services.ranking import COMPONENT_LABELS, RANKING_WEIGHTS
from .deps import SettingsDep

router = APIRouter(tags=["meta"])


@router.get("/health", summary="Liveness probe")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@router.get("/meta/config", summary="What this deployment can actually do")
async def config(settings: SettingsDep) -> dict[str, object]:
    """Capabilities and provider status -- never credentials.

    The frontend uses this to label estimates honestly and to show a demo-mode
    banner when nothing live is wired up.
    """
    status = settings.provider_status()
    return {
        **status,
        "version": __version__,
        "dataset_version": dataset_version(),
        "destination_count": len(load_destinations()),
        "cost_model_reference_date": cm.REFERENCE_DATE.isoformat(),
        "contingency_rate_default": settings.contingency_rate,
        "stretch_tolerance": settings.stretch_budget_tolerance,
        "demo_mode_note": (
            "No provider credentials are configured. Every figure is produced by TripWise's "
            "deterministic cost model and labelled as an estimate. Nothing is presented as a "
            "live quote, and the planner is fully functional in this mode."
            if status["demo_mode"]
            else None
        ),
    }


@router.get("/meta/options", summary="Choices for the trip setup form")
async def options() -> dict[str, object]:
    return {
        "travel_modes": [
            {"value": m.value, "label": m.value.title()} for m in TravelMode
        ],
        "accommodation_types": [
            {
                "value": a.value,
                "label": a.value.replace("_", " ").title(),
                "priced_per": "bed" if a == AccommodationType.HOSTEL else "room",
                "sleeps_per_unit": cm.UNIT_OCCUPANCY[a],
                "indicative_inr_per_night": cm.ACCOMMODATION_PER_NIGHT[a],
            }
            for a in AccommodationType
        ],
        "paces": [
            {
                "value": p.value,
                "label": p.value.title(),
                "activities_per_day": cm.ACTIVITIES_PER_DAY[p],
                "active_hours_per_day": cm.ACTIVE_HOURS_PER_DAY[p],
            }
            for p in Pace
        ],
        "interests": [
            {"value": i.value, "label": i.value.replace("_", " ").title()} for i in Interest
        ],
        "dataset_tags": all_tags(),
    }


@router.get("/meta/ranking", summary="How destinations are ranked")
async def ranking() -> dict[str, object]:
    """The ranking model, published rather than hidden."""
    return {
        "weights": [
            {
                "key": key,
                "label": COMPONENT_LABELS.get(key, key),
                "weight": weight,
            }
            for key, weight in sorted(RANKING_WEIGHTS.items(), key=lambda kv: -kv[1])
        ],
        "weights_sum": round(sum(RANKING_WEIGHTS.values()), 6),
        "feasibility_rules": {
            "max_travel_share_of_trip": cm.MAX_TRAVEL_SHARE_OF_TRIP,
            "min_trip_distance_km": cm.MIN_TRIP_DISTANCE_KM,
            "max_origin_airport_km": cm.MAX_ORIGIN_AIRPORT_KM,
            "min_sensible_flight_km": cm.FLIGHT_MIN_SENSIBLE_KM,
        },
        "note": (
            "Destinations are not ranked by price alone. Budget fit rewards using the budget "
            "well rather than spending the least, and a cheap destination that takes two days "
            "to reach will lose to a slightly pricier one nearby."
        ),
    }


@router.get("/meta/destinations", summary="The candidate destination catalogue")
async def destinations() -> dict[str, object]:
    """The full candidate pool, for transparency and for browsing."""
    return {
        "version": dataset_version(),
        "source": "curated",
        "note": (
            "This is the pool destination discovery searches. Coordinates are approximate "
            "centroids; fees and hours are typical published values, not live data."
        ),
        "destinations": [
            {
                "id": d.id,
                "name": d.name,
                "region": d.region,
                "state": d.state,
                "lat": d.lat,
                "lon": d.lon,
                "tier": d.tier,
                "blurb": d.blurb,
                "tags": list(d.tags),
                "best_months": list(d.best_months),
                "ideal_days": list(d.ideal_days),
                "attraction_count": len(d.attractions),
            }
            for d in load_destinations()
        ],
    }
