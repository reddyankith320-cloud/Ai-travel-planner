"""Seasonal feasibility from historical climate data.

Open-Meteo's archive endpoint needs no API key and gives real measured
normals, which is the right basis for "is November a good time to go?" --
a forecast cannot answer that for a trip three months out.

Terms: free for non-commercial use under roughly 10,000 calls/day, attribution
CC-BY 4.0. Commercial deployments need their paid plan; `ENABLE_OPEN_METEO`
turns this off.
"""

from __future__ import annotations

import calendar
import logging
from dataclasses import dataclass
from datetime import date

from ..config import Settings
from ..schemas.common import Provenance
from ..schemas.enums import Confidence, DataSource
from .cache import CacheBackend
from .http import ProviderClient, ProviderError

logger = logging.getLogger(__name__)

# Comfort thresholds for sightseeing on foot.
_TOO_HOT_C = 36.0
_TOO_COLD_C = 2.0
_HEAVY_RAIN_MM_PER_DAY = 12.0


@dataclass(frozen=True, slots=True)
class ClimateSummary:
    """Measured climate normals for a place and month."""

    mean_max_c: float
    mean_min_c: float
    mean_rain_mm_per_day: float
    score: float
    label: str
    note: str
    provenance: Provenance


async def month_climate(
    lat: float,
    lon: float,
    month: int,
    *,
    settings: Settings,
    client: ProviderClient,
    cache: CacheBackend,
    failures: list[str] | None = None,
) -> ClimateSummary | None:
    """Climate normals for one month, from the last three years of records."""
    if not settings.enable_open_meteo:
        return None

    cache_key = f"climate:{lat:.2f},{lon:.2f}:{month}"
    cached = cache.get(cache_key)
    if cached:
        return ClimateSummary(
            mean_max_c=cached["max_c"],
            mean_min_c=cached["min_c"],
            mean_rain_mm_per_day=cached["rain_mm"],
            score=cached["score"],
            label=cached["label"],
            note=cached["note"],
            provenance=Provenance(
                source=DataSource.CACHED,
                confidence=Confidence.HIGH,
                provider="open-meteo",
                note="Climate normals from cache.",
            ),
        )

    # Sample the same month across the three most recent complete years.
    reference_year = date.today().year - 1
    windows = [
        (date(year, month, 1), _month_end(year, month))
        for year in range(reference_year - 2, reference_year + 1)
    ]

    maxima: list[float] = []
    minima: list[float] = []
    rainfall: list[float] = []

    try:
        for start, end in windows:
            payload = await client.get_json(
                "open-meteo",
                f"{settings.open_meteo_base_url}/archive",
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "start_date": start.isoformat(),
                    "end_date": end.isoformat(),
                    "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
                    "timezone": "auto",
                },
            )
            daily = payload.get("daily") or {}
            maxima.extend(v for v in (daily.get("temperature_2m_max") or []) if v is not None)
            minima.extend(v for v in (daily.get("temperature_2m_min") or []) if v is not None)
            rainfall.extend(v for v in (daily.get("precipitation_sum") or []) if v is not None)
    except ProviderError as exc:
        logger.warning("open-meteo failed: %s", exc.detail)
        if failures is not None:
            failures.append(f"weather/open-meteo: {exc.detail}")
        return None

    if not maxima or not minima:
        return None

    mean_max = round(sum(maxima) / len(maxima), 1)
    mean_min = round(sum(minima) / len(minima), 1)
    mean_rain = round(sum(rainfall) / len(rainfall), 2) if rainfall else 0.0
    score, label, note = _score_climate(mean_max, mean_min, mean_rain, month)

    cache.set(
        cache_key,
        {
            "max_c": mean_max,
            "min_c": mean_min,
            "rain_mm": mean_rain,
            "score": score,
            "label": label,
            "note": note,
        },
        # Climate normals barely move; cache them for a week.
        max(settings.cache_ttl_seconds, 604_800),
    )

    return ClimateSummary(
        mean_max_c=mean_max,
        mean_min_c=mean_min,
        mean_rain_mm_per_day=mean_rain,
        score=score,
        label=label,
        note=note,
        provenance=Provenance(
            source=DataSource.LIVE,
            confidence=Confidence.HIGH,
            provider="open-meteo",
            note="Measured climate normals from the last three years (Open-Meteo, CC-BY 4.0).",
        ),
    )


def _month_end(year: int, month: int) -> date:
    """Last calendar day of the given month."""
    return date(year, month, calendar.monthrange(year, month)[1])


def _score_climate(
    mean_max: float, mean_min: float, rain_mm: float, month: int
) -> tuple[float, str, str]:
    """Turn normals into a 0-1 suitability score with an explanation."""
    score = 1.0
    problems: list[str] = []

    if mean_max > _TOO_HOT_C:
        penalty = min(0.55, (mean_max - _TOO_HOT_C) / 12.0)
        score -= penalty
        problems.append(f"daytime highs average {mean_max:.0f}C")
    if mean_min < _TOO_COLD_C:
        penalty = min(0.40, (_TOO_COLD_C - mean_min) / 15.0)
        score -= penalty
        problems.append(f"nights average {mean_min:.0f}C")
    if rain_mm > _HEAVY_RAIN_MM_PER_DAY:
        penalty = min(0.45, (rain_mm - _HEAVY_RAIN_MM_PER_DAY) / 25.0)
        score -= penalty
        problems.append(f"{rain_mm:.0f} mm of rain a day on average")

    score = round(max(0.05, min(1.0, score)), 3)
    month_name = date(2000, month, 1).strftime("%B")

    if not problems:
        return score, "Good weather", (
            f"{month_name} averages {mean_max:.0f}C by day and {mean_min:.0f}C at night, "
            "with little rain."
        )
    return score, "Weather warning", f"In {month_name}, " + " and ".join(problems) + "."
