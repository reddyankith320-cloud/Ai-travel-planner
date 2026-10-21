"""Enumerations shared across the API surface."""

from __future__ import annotations

from enum import StrEnum


class TravelMode(StrEnum):
    """Preferred intercity travel mode."""

    BUS = "bus"
    TRAIN = "train"
    CAR = "car"
    FLIGHT = "flight"
    ANY = "any"


class AccommodationType(StrEnum):
    """Accommodation preference.

    The distinction matters for costing: hostel pricing is per *bed*, every
    other type is priced per *room*.
    """

    HOSTEL = "hostel"
    BUDGET_HOTEL = "budget_hotel"
    MIDRANGE_HOTEL = "midrange_hotel"
    HOMESTAY = "homestay"
    RESORT = "resort"


class Pace(StrEnum):
    """How densely the itinerary is packed."""

    RELAXED = "relaxed"
    BALANCED = "balanced"
    PACKED = "packed"


class Interest(StrEnum):
    """Interest tags used for destination matching."""

    BEACHES = "beaches"
    MOUNTAINS = "mountains"
    HILL_STATION = "hill_station"
    HERITAGE = "heritage"
    WILDLIFE = "wildlife"
    ADVENTURE = "adventure"
    TREKKING = "trekking"
    SPIRITUAL = "spiritual"
    FOOD = "food"
    NIGHTLIFE = "nightlife"
    SHOPPING = "shopping"
    NATURE = "nature"
    LAKES = "lakes"
    WATERFALLS = "waterfalls"
    DESERT = "desert"
    BACKWATERS = "backwaters"
    ART_CULTURE = "art_culture"
    PHOTOGRAPHY = "photography"
    OFFBEAT = "offbeat"
    WELLNESS = "wellness"


class CostCategory(StrEnum):
    """Budget categories. Every rupee lands in exactly one of these."""

    INTERCITY_TRANSPORT = "intercity_transport"
    ACCOMMODATION = "accommodation"
    FOOD = "food"
    LOCAL_TRANSPORT = "local_transport"
    ACTIVITIES = "activities"
    TAXES_AND_FEES = "taxes_and_fees"
    CONTINGENCY = "contingency"


class CostBasis(StrEnum):
    """How a line item's quantity is counted.

    Recording the basis explicitly is what prevents double counting: a
    per-vehicle cost can never be silently multiplied by traveller count.
    """

    PER_PERSON_TRIP = "per_person_trip"
    PER_PERSON_PER_DAY = "per_person_per_day"
    PER_PERSON_PER_NIGHT = "per_person_per_night"
    PER_GROUP_TRIP = "per_group_trip"
    PER_GROUP_PER_DAY = "per_group_per_day"
    PER_ROOM_PER_NIGHT = "per_room_per_night"
    PER_BED_PER_NIGHT = "per_bed_per_night"
    PER_VEHICLE_TRIP = "per_vehicle_trip"
    DERIVED = "derived"


class DataSource(StrEnum):
    """Provenance of a number or record. Surfaced in the UI, never hidden.

    - LIVE: fetched from a provider API within this request's cache window.
    - CACHED: a previous live provider response still inside its TTL.
    - ESTIMATED: produced by TripWise's deterministic cost model.
    - CURATED: from the maintained destination dataset in this repository.
    - SAMPLE: illustrative demo content; not a real quote.
    """

    LIVE = "live"
    CACHED = "cached"
    ESTIMATED = "estimated"
    CURATED = "curated"
    SAMPLE = "sample"


class Confidence(StrEnum):
    """How much to trust a figure."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class BudgetFit(StrEnum):
    """How a destination's estimated total sits against the stated budget."""

    COMFORTABLE = "comfortable"   # leaves generous headroom
    GOOD = "good"                 # uses the budget well
    TIGHT = "tight"               # very little headroom
    STRETCH = "stretch"           # slightly over, clearly labelled
    OVER = "over"                 # excluded from recommendations


class ActivityKind(StrEnum):
    """Itinerary block types."""

    SIGHTSEEING = "sightseeing"
    MEAL = "meal"
    TRANSIT = "transit"
    REST = "rest"
    ACTIVITY = "activity"
    CHECK_IN = "check_in"
    CHECK_OUT = "check_out"
    FREE_TIME = "free_time"
