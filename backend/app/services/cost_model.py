"""The deterministic cost model: every rate TripWise prices a trip with.

This module is the single source of truth for money. The LLM never sets a
price, and no provider response is trusted beyond the field it supplies.

All figures are INR and are *reference rates* for India, not live quotes.
They are deliberately gathered in one place so they can be reviewed, tested
and overridden per request. Every rate used in a calculation is echoed back
to the client as an `Assumption`, which is what makes an estimate auditable
rather than a black box.

Rates reflect typical 2026 off-platform prices. Review them each season;
`REFERENCE_DATE` records when they were last set.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

from ..schemas.enums import AccommodationType, Pace, TravelMode

REFERENCE_DATE = date(2026, 10, 1)
CURRENCY = "INR"

# --------------------------------------------------------------------------
# Food, local transport and daily spend
# --------------------------------------------------------------------------

# Per person per day, including all meals and drinks, taxes inclusive.
FOOD_PER_PERSON_PER_DAY: dict[AccommodationType, float] = {
    AccommodationType.HOSTEL: 420.0,
    AccommodationType.BUDGET_HOTEL: 560.0,
    AccommodationType.HOMESTAY: 620.0,
    AccommodationType.MIDRANGE_HOTEL: 900.0,
    AccommodationType.RESORT: 1500.0,
}

# Per person per day for autos, local buses, metro and short cab hops, before
# the group-sharing discount below.
LOCAL_TRANSPORT_PER_PERSON_PER_DAY = 190.0

# Autos and cabs are shared, so cost per head falls as the group grows. These
# are multipliers on the per-person rate, indexed by group size.
LOCAL_SHARING_FACTOR: dict[int, float] = {1: 1.0, 2: 0.78, 3: 0.68, 4: 0.60}
LOCAL_SHARING_FLOOR = 0.55


def local_sharing_factor(travelers: int) -> float:
    """Per-head local transport multiplier for a group of `travelers`."""
    return LOCAL_SHARING_FACTOR.get(travelers, LOCAL_SHARING_FLOOR)


# --------------------------------------------------------------------------
# Accommodation
# --------------------------------------------------------------------------

# Hostels are priced per BED. Everything else is priced per ROOM.
ACCOMMODATION_PER_NIGHT: dict[AccommodationType, float] = {
    AccommodationType.HOSTEL: 650.0,
    AccommodationType.BUDGET_HOTEL: 1650.0,
    AccommodationType.HOMESTAY: 2200.0,
    AccommodationType.MIDRANGE_HOTEL: 3400.0,
    AccommodationType.RESORT: 7200.0,
}

# How many people one priced unit sleeps.
UNIT_OCCUPANCY: dict[AccommodationType, int] = {
    AccommodationType.HOSTEL: 1,        # one bed per traveller
    AccommodationType.BUDGET_HOTEL: 2,
    AccommodationType.HOMESTAY: 3,
    AccommodationType.MIDRANGE_HOTEL: 2,
    AccommodationType.RESORT: 2,
}

# GST on accommodation follows the declared tariff per unit per night.
GST_ACCOMMODATION_LOW = 0.12   # tariff at or below the threshold
GST_ACCOMMODATION_HIGH = 0.18  # tariff above it
GST_ACCOMMODATION_THRESHOLD = 7500.0


def accommodation_units(accommodation: AccommodationType, travelers: int) -> int:
    """Number of priced units (beds or rooms) a group needs."""
    occupancy = UNIT_OCCUPANCY[accommodation]
    return max(1, math.ceil(travelers / occupancy))


def accommodation_unit_label(accommodation: AccommodationType) -> str:
    return "bed-night" if accommodation == AccommodationType.HOSTEL else "room-night"


def accommodation_gst_rate(unit_price: float) -> float:
    return (
        GST_ACCOMMODATION_HIGH
        if unit_price > GST_ACCOMMODATION_THRESHOLD
        else GST_ACCOMMODATION_LOW
    )


# --------------------------------------------------------------------------
# Intercity transport
# --------------------------------------------------------------------------

BUS_BOARDING_FEE = 35.0
BUS_RATE_PER_KM_ORDINARY = 1.45   # non-AC seater, short hops
BUS_RATE_PER_KM_SLEEPER = 2.30    # AC sleeper, overnight runs
BUS_SLEEPER_THRESHOLD_KM = 220.0  # beyond this, an overnight coach is the norm
GST_AC_TRANSPORT = 0.05           # economy air, AC rail, AC bus

# Indian rail fares are telescopic: the rate per km falls as distance grows.
# A lookup table interpolated linearly models that far better than a flat
# per-km rate. Values are approximate all-in fares including reservation and
# superfast charges.
TRAIN_FARE_TABLE: dict[str, list[tuple[float, float]]] = {
    "SL": [(100, 140), (200, 185), (300, 230), (500, 310), (750, 390),
           (1000, 465), (1500, 590), (2000, 710), (2500, 820), (3000, 920)],
    "3A": [(100, 420), (200, 530), (300, 650), (500, 880), (750, 1130),
           (1000, 1360), (1500, 1760), (2000, 2130), (2500, 2470), (3000, 2780)],
    "2A": [(100, 600), (200, 755), (300, 925), (500, 1250), (750, 1605),
           (1000, 1930), (1500, 2500), (2000, 3025), (2500, 3505), (3000, 3950)],
}

# Comfort class implied by the accommodation preference, used as a proxy for
# how the traveller likes to travel. Exposed as an editable assumption.
TRAIN_CLASS_BY_ACCOMMODATION: dict[AccommodationType, str] = {
    AccommodationType.HOSTEL: "SL",
    AccommodationType.BUDGET_HOTEL: "SL",
    AccommodationType.HOMESTAY: "3A",
    AccommodationType.MIDRANGE_HOTEL: "3A",
    AccommodationType.RESORT: "2A",
}
TRAIN_AC_CLASSES = {"3A", "2A"}

# Rail route distances run longer than road distances on many corridors.
RAIL_ROUTE_FACTOR = 1.08
# Average end-to-end speed including halts, by class of service.
TRAIN_AVG_SPEED_KMPH = 52.0
BUS_AVG_SPEED_KMPH = 42.0
CAR_AVG_SPEED_KMPH_PLAINS = 48.0
CAR_AVG_SPEED_KMPH_HILLS = 30.0
CAR_AVG_SPEED_KMPH_HIGH = 24.0

# Self-drive / own-car running costs.
FUEL_PRICE_PER_LITRE = 102.0
CAR_MILEAGE_KMPL_SEDAN = 15.0
CAR_MILEAGE_KMPL_SUV = 12.0
CAR_CAPACITY_SEDAN = 4
CAR_CAPACITY_SUV = 6
TOLL_PER_KM = 1.15
CAR_DAILY_INCIDENTALS = 150.0   # parking, cleaning, minor stops

# Flights, domestic economy.
FLIGHT_BASE_FARE = 1850.0
FLIGHT_RATE_PER_KM = 3.10
FLIGHT_MINIMUM_FARE = 2200.0
FLIGHT_MIN_SENSIBLE_KM = 250.0  # below this, flights are rare and pointless
FLIGHT_FIXED_OVERHEAD_HOURS = 3.0  # check-in, security, taxi, baggage
FLIGHT_CRUISE_KMPH = 700.0

# Advance-purchase multiplier: booking late costs more.
FLIGHT_ADVANCE_MULTIPLIER: list[tuple[int, float]] = [
    (3, 1.70), (7, 1.45), (14, 1.25), (30, 1.08), (60, 1.00)
]
FLIGHT_ADVANCE_MULTIPLIER_FAR = 0.97

# Peak-season loading by month of travel (school holidays, festive season).
SEASON_FARE_MULTIPLIER: dict[int, float] = {
    1: 1.10, 2: 1.00, 3: 1.00, 4: 1.05, 5: 1.18, 6: 1.12,
    7: 1.00, 8: 1.00, 9: 0.95, 10: 1.08, 11: 1.05, 12: 1.22,
}

# Airport / station transfer, costed as a shared cab.
TRANSFER_BASE_FARE = 60.0
TRANSFER_PER_KM = 15.0
TRANSFER_CAB_CAPACITY = 4


def interpolate(table: list[tuple[float, float]], x: float) -> float:
    """Piecewise-linear lookup with flat ends and linear extrapolation above.

    Used for rail fares, where a table of real fares beats a formula.
    """
    if not table:
        raise ValueError("interpolate() needs a non-empty table")
    if x <= table[0][0]:
        # Scale down proportionally for very short hops, with a floor.
        first_x, first_y = table[0]
        return max(first_y * 0.45, first_y * (x / first_x) if first_x else first_y)
    if x >= table[-1][0]:
        # Extrapolate along the last segment's slope.
        (x0, y0), (x1, y1) = table[-2], table[-1]
        slope = (y1 - y0) / (x1 - x0)
        return y1 + slope * (x - x1)
    for (x0, y0), (x1, y1) in zip(table, table[1:]):
        if x0 <= x <= x1:
            ratio = (x - x0) / (x1 - x0)
            return y0 + ratio * (y1 - y0)
    return table[-1][1]


def train_fare_inr(distance_km: float, travel_class: str) -> float:
    """One-way rail fare per person, before tax."""
    table = TRAIN_FARE_TABLE.get(travel_class) or TRAIN_FARE_TABLE["SL"]
    return round(interpolate(table, distance_km * RAIL_ROUTE_FACTOR), 2)


def bus_fare_inr(distance_km: float) -> tuple[float, bool]:
    """One-way bus fare per person, and whether it is an AC service.

    Returns (fare, is_ac). AC attracts GST; ordinary service does not.
    """
    is_ac = distance_km >= BUS_SLEEPER_THRESHOLD_KM
    rate = BUS_RATE_PER_KM_SLEEPER if is_ac else BUS_RATE_PER_KM_ORDINARY
    return round(BUS_BOARDING_FEE + rate * distance_km, 2), is_ac


def flight_advance_multiplier(days_ahead: int) -> float:
    """Fare loading for how far ahead the trip is being booked."""
    for threshold, multiplier in FLIGHT_ADVANCE_MULTIPLIER:
        if days_ahead <= threshold:
            return multiplier
    return FLIGHT_ADVANCE_MULTIPLIER_FAR


def flight_fare_inr(air_distance_km: float, days_ahead: int, month: int) -> float:
    """One-way domestic economy fare per person, before tax.

    This is an *estimate* from distance, lead time and season -- never a quote.
    When a licensed flight API is configured, `providers/flights.py` replaces
    it and relabels the provenance as live.
    """
    base = FLIGHT_BASE_FARE + FLIGHT_RATE_PER_KM * air_distance_km
    loaded = base * flight_advance_multiplier(days_ahead) * SEASON_FARE_MULTIPLIER.get(month, 1.0)
    return round(max(FLIGHT_MINIMUM_FARE, loaded), 2)


def transfer_cost_inr(distance_km: float, travelers: int) -> float:
    """Shared-cab transfer cost for the whole group (one direction)."""
    cabs = max(1, math.ceil(travelers / TRANSFER_CAB_CAPACITY))
    per_cab = TRANSFER_BASE_FARE + TRANSFER_PER_KM * max(0.0, distance_km)
    return round(cabs * per_cab, 2)


def car_profile(travelers: int) -> tuple[str, float, int]:
    """(label, mileage_kmpl, capacity) for the vehicle class a group needs."""
    if travelers <= CAR_CAPACITY_SEDAN:
        return "hatchback/sedan", CAR_MILEAGE_KMPL_SEDAN, CAR_CAPACITY_SEDAN
    return "SUV/MPV", CAR_MILEAGE_KMPL_SUV, CAR_CAPACITY_SUV


def car_avg_speed_kmph(terrain: str) -> float:
    return {
        "plains": CAR_AVG_SPEED_KMPH_PLAINS,
        "hills": CAR_AVG_SPEED_KMPH_HILLS,
        "high_himalaya": CAR_AVG_SPEED_KMPH_HIGH,
    }.get(terrain, CAR_AVG_SPEED_KMPH_PLAINS)


# --------------------------------------------------------------------------
# Activities
# --------------------------------------------------------------------------

# How many ticketed or substantial activities a day holds, by pace.
ACTIVITIES_PER_DAY: dict[Pace, int] = {Pace.RELAXED: 2, Pace.BALANCED: 3, Pace.PACKED: 4}

# Per person per day allowance for experiences not in the curated list
# (local tours, rentals, tickets we do not have a price for).
ACTIVITY_BUFFER_PER_PERSON_PER_DAY: dict[Pace, float] = {
    Pace.RELAXED: 120.0,
    Pace.BALANCED: 200.0,
    Pace.PACKED: 320.0,
}

# Waking hours available for activities, by pace.
ACTIVE_HOURS_PER_DAY: dict[Pace, float] = {
    Pace.RELAXED: 7.0,
    Pace.BALANCED: 9.0,
    Pace.PACKED: 11.0,
}


# --------------------------------------------------------------------------
# Feasibility thresholds
# --------------------------------------------------------------------------

# Round-trip travel should not eat more than this share of the trip's hours,
# or the destination is not worth the journey.
MAX_TRAVEL_SHARE_OF_TRIP = 0.35
# A trip must leave at least this many usable hours on the ground.
MIN_GROUND_HOURS_PER_DAY = 5.0
# Destinations closer than this are "where you already are", not a trip.
MIN_TRIP_DISTANCE_KM = 60.0
# Airports further than this from the origin make flying impractical.
MAX_ORIGIN_AIRPORT_KM = 190.0


@dataclass(frozen=True, slots=True)
class ModelOverrides:
    """Per-request overrides for cost-model knobs.

    Built from `TripRequest.assumption_overrides` so a user can correct an
    assumption ("fuel is 108 here", "we'll eat for 300 a day") and have the
    whole estimate recompute from it.
    """

    food_per_person_per_day: float | None = None
    local_transport_per_person_per_day: float | None = None
    accommodation_per_night: float | None = None
    fuel_price_per_litre: float | None = None
    toll_per_km: float | None = None
    contingency_rate: float | None = None
    activity_buffer_per_person_per_day: float | None = None
    train_class: str | None = None

    @classmethod
    def from_mapping(cls, raw: dict[str, float]) -> "ModelOverrides":
        """Accept only known numeric keys; ignore anything else."""
        allowed = {
            "food_per_person_per_day",
            "local_transport_per_person_per_day",
            "accommodation_per_night",
            "fuel_price_per_litre",
            "toll_per_km",
            "contingency_rate",
            "activity_buffer_per_person_per_day",
        }
        kwargs: dict[str, float] = {}
        for key, value in (raw or {}).items():
            if key in allowed:
                try:
                    numeric = float(value)
                except (TypeError, ValueError):
                    continue
                if numeric >= 0:
                    kwargs[key] = numeric
        train_class = raw.get("train_class") if isinstance(raw, dict) else None
        if isinstance(train_class, str) and train_class in TRAIN_FARE_TABLE:
            return cls(**kwargs, train_class=train_class)  # type: ignore[arg-type]
        return cls(**kwargs)  # type: ignore[arg-type]


def modes_for(preference: TravelMode) -> list[TravelMode]:
    """Expand a preference into the concrete modes to evaluate."""
    if preference == TravelMode.ANY:
        return [TravelMode.TRAIN, TravelMode.BUS, TravelMode.CAR, TravelMode.FLIGHT]
    return [preference]
