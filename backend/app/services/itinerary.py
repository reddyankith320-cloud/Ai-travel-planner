"""Itinerary generation: deterministic planner first, LLM as an enhancement.

The deterministic planner is not a stub. It geo-clusters the selected
attractions into days, orders each day nearest-neighbour from the
accommodation, fits time blocks around real opening hours and visit durations,
and inserts meals and rest. That is the itinerary users see in demo mode, and
it is the floor the LLM has to improve on.

When an LLM is configured it is asked to re-sequence and narrate the *same*
activity set. It may drop activities and reorder days; it may not invent
places, and it may not set prices. Costs are recomputed from the resulting
activity list by the deterministic cost model either way, so the itinerary
total always matches the budget dashboard.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, timedelta

from pydantic import BaseModel, Field, ValidationError

from ..config import Settings
from ..data.catalog import Attraction, Destination
from ..schemas.common import Coordinates, PlaceRef, Provenance
from ..schemas.cost import CostBreakdown
from ..schemas.enums import ActivityKind, Confidence, DataSource, Pace
from ..schemas.itinerary import Itinerary, ItineraryActivity, ItineraryDay
from ..schemas.recommendation import DestinationSummary
from ..schemas.trip import TripRequest
from ..providers.llm import LLMUnavailable, complete_json
from . import cost_model as cm
from .activities import ActivityPlan
from .geo_math import haversine_km

logger = logging.getLogger(__name__)

DISCLAIMER = (
    "Opening hours, admission charges and travel times are approximate and come from "
    "TripWise's maintained dataset or a places provider, not from a live booking system. "
    "Confirm timings and prices with each venue before you go."
)

# Local-transport cost between two stops, as a shared auto/cab.
_LOCAL_HOP_BASE_INR = 35.0
_LOCAL_HOP_PER_KM_INR = 14.0
_WALKABLE_KM = 1.2
_LOCAL_SPEED_KMPH = 24.0


# --------------------------------------------------------------------------
# The schema the LLM must satisfy
# --------------------------------------------------------------------------

class LLMActivity(BaseModel):
    """One activity block as proposed by the model."""

    place_name: str = Field(..., max_length=200)
    kind: str = Field("sightseeing", max_length=40)
    start_time: str = Field(..., pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    end_time: str = Field(..., pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    description: str = Field(..., max_length=600)
    tips: list[str] = Field(default_factory=list, max_length=4)
    weather_alternative: str | None = Field(default=None, max_length=300)


class LLMDay(BaseModel):
    """One day as proposed by the model."""

    day_number: int = Field(..., ge=1, le=31)
    title: str = Field(..., max_length=120)
    summary: str = Field(..., max_length=600)
    area_focus: str = Field(..., max_length=120)
    activities: list[LLMActivity] = Field(..., min_length=1, max_length=12)
    notes: list[str] = Field(default_factory=list, max_length=4)


class LLMItinerary(BaseModel):
    """The whole plan as proposed by the model."""

    days: list[LLMDay] = Field(..., min_length=1, max_length=31)
    packing_notes: list[str] = Field(default_factory=list, max_length=8)
    local_tips: list[str] = Field(default_factory=list, max_length=8)


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------

def to_place_ref(attraction: Attraction) -> PlaceRef:
    return PlaceRef(
        name=attraction.name,
        category=attraction.category,
        coordinates=Coordinates(lat=attraction.lat, lon=attraction.lon),
        entry_fee_inr=attraction.entry_fee_inr,
        opening_hours=attraction.opening_hours,
        rating=attraction.rating,
        typical_visit_minutes=attraction.visit_minutes,
        provenance=Provenance(
            source=DataSource.CURATED,
            confidence=Confidence.MEDIUM,
            note="From TripWise's maintained dataset. Hours and fees are approximate.",
        ),
    )


def _hop_cost_and_note(distance_km: float, travelers: int) -> tuple[float, str]:
    """Cost and description of getting from one stop to the next."""
    if distance_km <= _WALKABLE_KM:
        return 0.0, f"About {distance_km * 1000:.0f} m on foot"
    cabs = max(1, (travelers + 3) // 4)
    cost = cabs * (_LOCAL_HOP_BASE_INR + _LOCAL_HOP_PER_KM_INR * distance_km)
    return round(cost, 2), (
        f"{distance_km:.1f} km by auto or app cab, shared across {travelers} traveller(s)"
    )


def _destination_summary(destination: Destination) -> DestinationSummary:
    return DestinationSummary(
        id=destination.id,
        name=destination.name,
        region=destination.region,
        state=destination.state,
        coordinates=Coordinates(lat=destination.lat, lon=destination.lon),
        blurb=destination.blurb,
        tags=list(destination.tags),
        provenance=Provenance.curated("Maintained destination dataset."),
    )


def _minutes_to_clock(minutes: int) -> str:
    minutes = max(0, min(23 * 60 + 59, minutes))
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _clock_to_minutes(clock: str) -> int:
    hours, _, mins = clock.partition(":")
    return int(hours) * 60 + int(mins)


# Some stops only work at one end of the day. A "sunset point" scheduled for
# 09:30 is a plan nobody would follow, so these are pinned to their window and
# ordered within the day accordingly.
_TIME_WINDOWS: dict[str, tuple[int, int]] = {
    "sunrise": (5 * 60 + 15, 6 * 60 + 30),
    "sunset": (17 * 60, 18 * 60 + 30),
}


def time_window(attraction: Attraction) -> tuple[int, int] | None:
    """The part of the day this stop belongs in, if its name pins one."""
    name = attraction.name.lower()
    for keyword, window in _TIME_WINDOWS.items():
        if keyword in name:
            return window
    return None


def order_for_time_of_day(group: list[Attraction]) -> list[Attraction]:
    """Sunrise stops first, sunset stops last, geographic order in between."""
    sunrise = [a for a in group if (w := time_window(a)) and w[0] < 12 * 60]
    sunset = [a for a in group if (w := time_window(a)) and w[0] >= 12 * 60]
    middle = [a for a in group if time_window(a) is None]
    return sunrise + middle + sunset


def _opens_after(attraction: Attraction, minutes: int) -> int:
    """Shift a start time to respect a published opening hour, when parseable.

    Opening-hours strings in the dataset are free text ("09:30-18:00",
    "Open all day"). We only act on the ones we can read, and leave the rest
    alone rather than guessing.
    """
    hours = attraction.opening_hours or ""
    for token in hours.replace("-", " ").split():
        if ":" in token and len(token) <= 5:
            try:
                opening = _clock_to_minutes(token)
            except ValueError:
                continue
            # Only treat it as an opening time if it looks like a morning one.
            if 4 * 60 <= opening <= 12 * 60:
                return max(minutes, opening)
            break
    return minutes


# --------------------------------------------------------------------------
# Deterministic planner
# --------------------------------------------------------------------------

def _meal_blocks(pace: Pace) -> list[tuple[int, int, str, str]]:
    """(start_minutes, duration, title, description) for the day's meals."""
    lunch = 13 * 60 if pace != Pace.PACKED else 13 * 60 + 30
    return [
        (lunch, 60, "Lunch", "Break for a local lunch near your last stop."),
        (20 * 60, 75, "Dinner", "Dinner back near your stay, or at a recommended local spot."),
    ]


def build_deterministic_days(
    *,
    destination: Destination,
    plan: ActivityPlan,
    request: TripRequest,
    food_per_person_per_day: float,
) -> list[ItineraryDay]:
    """Lay out each day's activities around opening hours and meal breaks."""
    base = (destination.lat, destination.lon)
    travelers = request.travelers
    start_of_day = {Pace.RELAXED: 9 * 60 + 30, Pace.BALANCED: 8 * 60 + 30, Pace.PACKED: 7 * 60 + 30}[
        request.pace
    ]
    # Food is budgeted per day; split it across the day's two meal blocks.
    per_meal = round(food_per_person_per_day * travelers / 2, 2)

    days: list[ItineraryDay] = []
    for index in range(request.days):
        raw_group = plan.day_groups[index] if index < len(plan.day_groups) else []
        group = order_for_time_of_day(list(raw_group))
        day_date = request.start_date + timedelta(days=index)
        activities: list[ItineraryActivity] = []
        order = 0
        cursor = start_of_day
        previous_point = base
        travelled = 0.0
        meals = _meal_blocks(request.pace)
        meal_index = 0

        # An early-morning first stop needs the day to start early, which only
        # works on a day that does not begin with arrival and check-in.
        if index > 0 and group:
            first_window = time_window(group[0])
            if first_window is not None and first_window[0] < 12 * 60:
                cursor = first_window[0]

        if index == 0:
            activities.append(
                ItineraryActivity(
                    order=order,
                    kind=ActivityKind.CHECK_IN,
                    title=f"Arrive in {destination.name} and check in",
                    start_time=_minutes_to_clock(cursor),
                    end_time=_minutes_to_clock(cursor + 60),
                    duration_minutes=60,
                    description=(
                        f"Settle in, drop bags and get oriented. {destination.local_transport}"
                    ),
                    tips=["Keep a photo of your booking and ID handy for check-in."],
                )
            )
            order += 1
            cursor += 60

        for attraction in group:
            # Insert a meal if we have passed its slot.
            while meal_index < len(meals) and cursor >= meals[meal_index][0]:
                meal_start, meal_duration, meal_title, meal_text = meals[meal_index]
                slot = max(cursor, meal_start)
                activities.append(
                    ItineraryActivity(
                        order=order,
                        kind=ActivityKind.MEAL,
                        title=meal_title,
                        start_time=_minutes_to_clock(slot),
                        end_time=_minutes_to_clock(slot + meal_duration),
                        duration_minutes=meal_duration,
                        description=meal_text,
                        food_cost_inr=per_meal,
                    )
                )
                order += 1
                cursor = slot + meal_duration
                meal_index += 1

            hop_km = haversine_km(previous_point[0], previous_point[1], attraction.lat, attraction.lon)
            hop_cost, hop_note = _hop_cost_and_note(hop_km, travelers)
            travel_minutes = int(round(hop_km / _LOCAL_SPEED_KMPH * 60)) if hop_km > _WALKABLE_KM else int(
                round(hop_km / 4.5 * 60)
            )
            cursor += travel_minutes
            cursor = _opens_after(attraction, cursor)
            window = time_window(attraction)
            if window is not None and cursor < window[0]:
                # Wait for the right light rather than arriving hours early.
                cursor = window[0]

            activities.append(
                ItineraryActivity(
                    order=order,
                    kind=_activity_kind(attraction.category),
                    title=attraction.name,
                    start_time=_minutes_to_clock(cursor),
                    end_time=_minutes_to_clock(cursor + attraction.visit_minutes),
                    duration_minutes=attraction.visit_minutes,
                    description=(
                        f"{attraction.category.replace('_', ' ').title()} stop. "
                        f"Typical visit {attraction.visit_minutes} minutes. "
                        f"Hours (approx.): {attraction.opening_hours}."
                    ),
                    place=to_place_ref(attraction),
                    distance_from_previous_km=round(hop_km, 2),
                    transport_note=hop_note,
                    transport_cost_inr=hop_cost,
                    entry_cost_inr=round(attraction.entry_fee_inr * travelers, 2),
                    booking_required=attraction.entry_fee_inr > 1000,
                    weather_alternative=_weather_alternative(attraction, destination),
                    tips=_tips_for(attraction),
                )
            )
            order += 1
            cursor += attraction.visit_minutes
            travelled += hop_km
            previous_point = (attraction.lat, attraction.lon)

            if request.pace == Pace.RELAXED and order % 3 == 0:
                activities.append(
                    ItineraryActivity(
                        order=order,
                        kind=ActivityKind.REST,
                        title="Rest and coffee",
                        start_time=_minutes_to_clock(cursor),
                        end_time=_minutes_to_clock(cursor + 45),
                        duration_minutes=45,
                        description="Sit down somewhere shaded. A relaxed pace needs real breaks.",
                    )
                )
                order += 1
                cursor += 45

        # Any meals not yet placed.
        while meal_index < len(meals):
            meal_start, meal_duration, meal_title, meal_text = meals[meal_index]
            slot = max(cursor, meal_start)
            activities.append(
                ItineraryActivity(
                    order=order,
                    kind=ActivityKind.MEAL,
                    title=meal_title,
                    start_time=_minutes_to_clock(slot),
                    end_time=_minutes_to_clock(slot + meal_duration),
                    duration_minutes=meal_duration,
                    description=meal_text,
                    food_cost_inr=per_meal,
                )
            )
            order += 1
            cursor = slot + meal_duration
            meal_index += 1

        if index == request.days - 1 and request.days > 1:
            activities.append(
                ItineraryActivity(
                    order=order,
                    kind=ActivityKind.CHECK_OUT,
                    title="Check out and head home",
                    start_time=_minutes_to_clock(min(cursor, 21 * 60)),
                    end_time=_minutes_to_clock(min(cursor + 60, 22 * 60)),
                    duration_minutes=60,
                    description=(
                        "Collect bags and make your way to the station, stand or airport. "
                        "Leave buffer for local traffic."
                    ),
                )
            )

        focus = group[0].category.replace("_", " ") if group else "flexible day"
        days.append(
            ItineraryDay(
                day_number=index + 1,
                date=day_date,
                title=_day_title(index, request.days, group, destination),
                summary=_day_summary(group, destination, travelled),
                area_focus=(
                    f"{group[0].name} area" if group else f"{destination.name} centre"
                ),
                activities=activities,
                total_travel_km=round(travelled, 2),
                walking_intensity=_walking_intensity(request.pace, len(group)),
                notes=[f"Focus: {focus}."] if group else ["Buffer day -- keep it loose."],
            )
        )
    return days


def _activity_kind(category: str) -> ActivityKind:
    if category == "food":
        return ActivityKind.MEAL
    if category in {"adventure", "activity", "trek", "wellness"}:
        return ActivityKind.ACTIVITY
    return ActivityKind.SIGHTSEEING


def _day_title(index: int, total: int, group: list[Attraction], destination: Destination) -> str:
    if index == 0:
        return f"Arrival and first look at {destination.name}"
    if index == total - 1 and total > 1:
        return "Last morning and departure"
    if group:
        return f"{group[0].name} and nearby"
    return f"Free day in {destination.name}"


def _day_summary(group: list[Attraction], destination: Destination, travelled: float) -> str:
    if not group:
        return (
            f"No fixed plan. Use it to revisit a favourite spot in {destination.name} or rest."
        )
    names = ", ".join(a.name for a in group)
    return (
        f"{len(group)} stop(s) grouped in one part of {destination.name} to keep moving to a "
        f"minimum ({travelled:.1f} km of local travel): {names}."
    )


def _walking_intensity(pace: Pace, stops: int) -> str:
    if pace == Pace.PACKED or stops >= 4:
        return "high"
    if pace == Pace.RELAXED or stops <= 1:
        return "low"
    return "moderate"


def _weather_alternative(attraction: Attraction, destination: Destination) -> str | None:
    """Indoor fallback for an outdoor stop."""
    outdoor = {"beach", "viewpoint", "trek", "waterfall", "garden", "lake", "desert", "nature"}
    if attraction.category not in outdoor:
        return None
    indoor = [
        a.name
        for a in destination.attractions
        if a.category in {"museum", "heritage", "spiritual", "shopping", "art_culture"}
        and a.name != attraction.name
    ]
    if indoor:
        return f"If the weather turns, swap this for {indoor[0]}."
    return "If the weather turns, keep this flexible -- it is an outdoor stop."


def _tips_for(attraction: Attraction) -> list[str]:
    tips: list[str] = []
    if attraction.entry_fee_inr > 500:
        tips.append(
            f"Entry is around Rs {attraction.entry_fee_inr:,.0f} per person -- book online where "
            "possible to skip the queue."
        )
    if attraction.visit_minutes >= 240:
        tips.append("This takes most of a day; start early and carry water.")
    if attraction.category == "spiritual":
        tips.append("Dress modestly; shoes usually come off at the entrance.")
    if attraction.category == "trek":
        tips.append("Proper shoes and an early start matter more than fitness here.")
    return tips


# --------------------------------------------------------------------------
# LLM enhancement
# --------------------------------------------------------------------------

SYSTEM_PROMPT = """You are an expert India travel planner working inside TripWise, a \
budget-first trip planning system.

You will be given a destination, a fixed list of candidate activities (each with \
coordinates, approximate visit duration, approximate opening hours and approximate entry \
fee), the trip length, group size, travel pace and interests.

Your job is to arrange those activities into a day-by-day plan that minimises pointless \
travel, respects opening hours, and reads like advice from someone who knows the place.

Hard rules:
- Use ONLY the activities provided. Do not invent places, tours or venues.
- Refer to each activity by its exact `place_name` as given.
- Group activities that are geographically close into the same day. This matters more than \
any other consideration.
- Do NOT output any prices, fares, costs or totals. The system computes all money itself \
and will ignore any figure you produce.
- Do not claim live availability, confirmed bookings or real-time prices.
- Respect the stated pace: relaxed means fewer stops and real breaks; packed means more.
- Every day from 1 to the trip length must appear exactly once.
- Return ONLY a JSON object, no prose outside it.

JSON shape:
{
  "days": [
    {
      "day_number": 1,
      "title": "short title",
      "summary": "2-3 sentences on the shape of the day",
      "area_focus": "the part of town or region this day covers",
      "activities": [
        {
          "place_name": "exact name from the provided list",
          "kind": "sightseeing|activity|meal|rest|transit|free_time",
          "start_time": "09:30",
          "end_time": "11:00",
          "description": "why this, and what to expect",
          "tips": ["practical tip"],
          "weather_alternative": "what to do instead if the weather turns, or null"
        }
      ],
      "notes": ["anything worth knowing about this day"]
    }
  ],
  "packing_notes": ["destination-specific packing advice"],
  "local_tips": ["how to get around, what to watch for, local etiquette"]
}"""


def build_user_prompt(
    *, destination: Destination, plan: ActivityPlan, request: TripRequest
) -> str:
    """Assemble the grounding context for the model."""
    activities = [
        {
            "place_name": a.name,
            "category": a.category,
            "lat": round(a.lat, 5),
            "lon": round(a.lon, 5),
            "approx_visit_minutes": a.visit_minutes,
            "approx_opening_hours": a.opening_hours,
            "approx_entry_fee_inr": a.entry_fee_inr,
            "rating": a.rating,
        }
        for a in plan.selected
    ]
    suggested = [
        {"day": i + 1, "places": [a.name for a in group]}
        for i, group in enumerate(plan.day_groups)
    ]
    payload = {
        "destination": {
            "name": destination.name,
            "region": destination.region,
            "state": destination.state,
            "lat": destination.lat,
            "lon": destination.lon,
            "character": destination.blurb,
            "tags": list(destination.tags),
            "getting_around": destination.local_transport,
        },
        "trip": {
            "days": request.days,
            "start_date": request.start_date.isoformat(),
            "travelers": request.travelers,
            "pace": request.pace.value,
            "interests": [i.value for i in request.interests],
            "accommodation": request.accommodation.value,
        },
        "available_activities": activities,
        "geographic_clustering_hint": suggested,
    }
    return (
        "Plan this trip.\n\n"
        + json.dumps(payload, indent=2)
        + "\n\nThe clustering hint shows geographically compact groupings our engine found. "
        "Improve on it if you can do better, but do not scatter nearby places across "
        "different days."
    )


def _merge_llm_days(
    *,
    llm: LLMItinerary,
    destination: Destination,
    plan: ActivityPlan,
    request: TripRequest,
    food_per_person_per_day: float,
) -> list[ItineraryDay]:
    """Rebuild days from the model's sequence, re-deriving every number.

    The model supplies ordering, titles and prose. Distances, durations,
    transport costs and entry fees are all recomputed here from our own data,
    which is why a hallucinated price cannot reach the user.
    """
    by_name = {a.name.lower(): a for a in plan.selected}
    travelers = request.travelers
    per_meal = round(food_per_person_per_day * travelers / 2, 2)
    base = (destination.lat, destination.lon)

    days: list[ItineraryDay] = []
    for llm_day in sorted(llm.days, key=lambda d: d.day_number)[: request.days]:
        activities: list[ItineraryActivity] = []
        previous_point = base
        travelled = 0.0
        order = 0
        used_names: list[str] = []

        for proposed in llm_day.activities:
            attraction = by_name.get(proposed.place_name.strip().lower())
            start = _clock_to_minutes(proposed.start_time)
            end = _clock_to_minutes(proposed.end_time)
            duration = max(15, end - start) if end > start else 60

            if attraction is None:
                # Not one of ours -- keep it only as a non-priced filler block,
                # never as a place with a cost or coordinates.
                kind = proposed.kind if proposed.kind in {k.value for k in ActivityKind} else "free_time"
                if kind in {"sightseeing", "activity"}:
                    kind = "free_time"
                activities.append(
                    ItineraryActivity(
                        order=order,
                        kind=ActivityKind(kind),
                        title=proposed.place_name,
                        start_time=proposed.start_time,
                        end_time=proposed.end_time,
                        duration_minutes=duration,
                        description=proposed.description,
                        food_cost_inr=per_meal if kind == "meal" else 0.0,
                        tips=list(proposed.tips),
                        weather_alternative=proposed.weather_alternative,
                    )
                )
                order += 1
                continue

            hop_km = haversine_km(
                previous_point[0], previous_point[1], attraction.lat, attraction.lon
            )
            hop_cost, hop_note = _hop_cost_and_note(hop_km, travelers)
            activities.append(
                ItineraryActivity(
                    order=order,
                    kind=_activity_kind(attraction.category),
                    title=attraction.name,
                    start_time=proposed.start_time,
                    end_time=proposed.end_time,
                    duration_minutes=duration,
                    description=proposed.description,
                    place=to_place_ref(attraction),
                    distance_from_previous_km=round(hop_km, 2),
                    transport_note=hop_note,
                    transport_cost_inr=hop_cost,
                    entry_cost_inr=round(attraction.entry_fee_inr * travelers, 2),
                    booking_required=attraction.entry_fee_inr > 1000,
                    weather_alternative=proposed.weather_alternative
                    or _weather_alternative(attraction, destination),
                    tips=list(proposed.tips) or _tips_for(attraction),
                )
            )
            order += 1
            travelled += hop_km
            previous_point = (attraction.lat, attraction.lon)
            used_names.append(attraction.name)

        if not any(a.kind == ActivityKind.MEAL for a in activities):
            activities.append(
                ItineraryActivity(
                    order=order,
                    kind=ActivityKind.MEAL,
                    title="Meals",
                    start_time="13:00",
                    end_time="14:00",
                    duration_minutes=60,
                    description="Budgeted food for the day.",
                    food_cost_inr=round(food_per_person_per_day * travelers, 2),
                )
            )

        days.append(
            ItineraryDay(
                day_number=llm_day.day_number,
                date=request.start_date + timedelta(days=llm_day.day_number - 1),
                title=llm_day.title,
                summary=llm_day.summary,
                area_focus=llm_day.area_focus,
                activities=activities,
                total_travel_km=round(travelled, 2),
                walking_intensity=_walking_intensity(request.pace, len(used_names)),
                notes=list(llm_day.notes),
            )
        )

    # Fill any day the model skipped so the trip has no gaps.
    present = {d.day_number for d in days}
    for missing in range(1, request.days + 1):
        if missing in present:
            continue
        days.append(
            ItineraryDay(
                day_number=missing,
                date=request.start_date + timedelta(days=missing - 1),
                title=f"Open day in {destination.name}",
                summary="Kept free. Revisit a favourite, or rest before travelling.",
                area_focus=f"{destination.name} centre",
                activities=[
                    ItineraryActivity(
                        order=0,
                        kind=ActivityKind.FREE_TIME,
                        title="Unplanned day",
                        start_time="10:00",
                        end_time="18:00",
                        duration_minutes=480,
                        description="No fixed plan for this day.",
                        food_cost_inr=round(food_per_person_per_day * travelers, 2),
                    )
                ],
                notes=["Added by TripWise because the plan skipped this day."],
            )
        )
    days.sort(key=lambda d: d.day_number)
    return days


@dataclass(slots=True)
class ItineraryResult:
    """An itinerary plus how it was produced."""

    itinerary: Itinerary
    used_llm: bool
    llm_error: str | None = None


async def generate_itinerary(
    *,
    destination: Destination,
    plan: ActivityPlan,
    request: TripRequest,
    cost: CostBreakdown,
    settings: Settings,
    food_per_person_per_day: float,
    allow_llm: bool = True,
) -> ItineraryResult:
    """Produce an itinerary, preferring the LLM but never depending on it."""
    summary = _destination_summary(destination)
    llm_error: str | None = None
    days: list[ItineraryDay] | None = None
    generated_by = "deterministic"

    if allow_llm and settings.llm_enabled:
        try:
            days = await _llm_days(
                destination=destination,
                plan=plan,
                request=request,
                settings=settings,
                food_per_person_per_day=food_per_person_per_day,
            )
            generated_by = f"llm:{settings.active_llm_provider}"
        except (LLMUnavailable, ValidationError, ValueError, json.JSONDecodeError) as exc:
            llm_error = f"{type(exc).__name__}: {exc}"
            logger.warning("LLM itinerary generation failed, using deterministic plan: %s", exc)
            days = None

    if days is None:
        days = build_deterministic_days(
            destination=destination,
            plan=plan,
            request=request,
            food_per_person_per_day=food_per_person_per_day,
        )

    itinerary = Itinerary(
        destination=summary,
        start_date=request.start_date,
        end_date=request.start_date + timedelta(days=request.days - 1),
        travelers=request.travelers,
        days=days,
        cost=cost,
        accommodation_area=f"{destination.name} centre",
        accommodation_coordinates=Coordinates(lat=destination.lat, lon=destination.lon),
        packing_notes=_packing_notes(destination, request),
        local_tips=[destination.local_transport],
        generated_by=generated_by,
        provenance=(
            Provenance(
                source=DataSource.CURATED,
                confidence=Confidence.MEDIUM,
                provider=settings.active_llm_provider if generated_by != "deterministic" else None,
                note=(
                    "Sequencing and prose from a language model; all places, hours and costs "
                    "from TripWise's own data and cost model."
                    if generated_by != "deterministic"
                    else "Built by TripWise's deterministic planner from the curated dataset."
                ),
            )
        ),
        confidence=cost.confidence,
        disclaimer=DISCLAIMER,
    )
    return ItineraryResult(itinerary=itinerary, used_llm=generated_by != "deterministic", llm_error=llm_error)


async def _llm_days(
    *,
    destination: Destination,
    plan: ActivityPlan,
    request: TripRequest,
    settings: Settings,
    food_per_person_per_day: float,
) -> list[ItineraryDay]:
    """Call the model, validating the reply and retrying once with feedback."""
    user_prompt = build_user_prompt(destination=destination, plan=plan, request=request)

    for attempt in (1, 2):
        reply = await complete_json(
            system_prompt=SYSTEM_PROMPT, user_prompt=user_prompt, settings=settings
        )
        try:
            validated = LLMItinerary.model_validate(reply.data)
        except ValidationError as exc:
            if attempt == 2:
                raise
            logger.info("LLM reply failed validation, retrying with feedback")
            user_prompt = (
                user_prompt
                + "\n\nYour previous reply did not match the required schema. "
                + f"Fix these problems and return valid JSON only:\n{exc.errors()[:5]}"
            )
            continue
        return _merge_llm_days(
            llm=validated,
            destination=destination,
            plan=plan,
            request=request,
            food_per_person_per_day=food_per_person_per_day,
        )
    raise LLMUnavailable("model did not return a valid itinerary")


def _packing_notes(destination: Destination, request: TripRequest) -> list[str]:
    notes = ["Photo ID for every traveller -- hotels and trains both require it."]
    tags = set(destination.tags)
    if {"hill_station", "mountains"} & tags:
        notes.append("Layers and a windproof jacket; evenings get cold even in summer.")
    if "beaches" in tags:
        notes.append("Reef-safe sunscreen, a hat and something you can swim in.")
    if "trekking" in tags:
        notes.append("Broken-in shoes, a 1 L water bottle and a basic first-aid kit.")
    if "spiritual" in tags:
        notes.append("Clothing that covers shoulders and knees for temple visits.")
    if destination.id in {"spiti", "leh-ladakh"}:
        notes.append(
            "High altitude: carry prescribed altitude medication and expect slow first days."
        )
    if request.start_date.month in (6, 7, 8, 9):
        notes.append("Monsoon months -- a rain shell and a dry bag for electronics.")
    return notes
