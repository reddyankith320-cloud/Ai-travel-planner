"""Intercity transport pricing.

One rule drives this module: a per-vehicle cost is never multiplied by the
number of travellers, and a per-person cost always is. Each line item records
its `basis`, so the mistake is visible in the output rather than hidden in a
total.

Fares are deterministic estimates unless a licensed provider supplies a quote,
in which case the provider's figure replaces ours and the provenance says so.
There is no legitimate public API for Indian rail or intercity bus fares, so
those stay estimates and we link to the official booking channel instead of
inventing availability.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

from ..data.catalog import Airport, Destination, nearest_airport
from ..schemas.common import BookingLink, Provenance
from ..schemas.enums import (
    AccommodationType,
    Confidence,
    CostBasis,
    CostCategory,
    DataSource,
    TravelMode,
)
from ..schemas.cost import CostLineItem
from . import cost_model as cm
from .geo_math import estimate_road_km, haversine_km


@dataclass(frozen=True, slots=True)
class RouteInfo:
    """Distance and duration for one direction of the journey."""

    road_km: float
    road_hours: float
    provenance: Provenance
    note: str | None = None


@dataclass(slots=True)
class TransportQuote:
    """A fully costed round trip by one mode."""

    mode: TravelMode
    feasible: bool
    one_way_km: float
    one_way_hours: float
    line_items: list[CostLineItem] = field(default_factory=list)
    booking_links: list[BookingLink] = field(default_factory=list)
    provenance: Provenance = field(default_factory=lambda: Provenance.estimated())
    route_note: str | None = None
    transfer_note: str | None = None
    infeasible_reason: str | None = None
    detail: dict[str, object] = field(default_factory=dict)

    @property
    def round_trip_total_inr(self) -> float:
        return round(sum(i.total_inr for i in self.line_items), 2)

    @property
    def round_trip_hours(self) -> float:
        return round(self.one_way_hours * 2, 2)


# --------------------------------------------------------------------------
# Booking links -- official channels only, clearly labelled.
# --------------------------------------------------------------------------

def _rail_links() -> list[BookingLink]:
    return [
        BookingLink(
            label="IRCTC (official booking)",
            url="https://www.irctc.co.in/nget/train-search",
            official=True,
            note="The only authorised channel for Indian Railways reservations.",
        ),
        BookingLink(
            label="NTES train schedules (official)",
            url="https://enquiry.indianrail.gov.in/",
            official=True,
            note="Live running status and timetables from Indian Railways.",
        ),
    ]


def _bus_links(state: str) -> list[BookingLink]:
    state_rtc = {
        "Karnataka": ("KSRTC (official)", "https://ksrtc.karnataka.gov.in/"),
        "Maharashtra": ("MSRTC (official)", "https://public.msrtcors.com/"),
        "Kerala": ("KSRTC Kerala (official)", "https://online.keralartc.com/"),
        "Rajasthan": ("RSRTC (official)", "https://rsrtconline.rajasthan.gov.in/"),
        "Telangana": ("TGSRTC (official)", "https://www.tgsrtcbus.in/"),
        "Andhra Pradesh": ("APSRTC (official)", "https://www.apsrtconline.in/"),
        "Tamil Nadu": ("TNSTC (official)", "https://www.tnstc.in/"),
        "Himachal Pradesh": ("HRTC (official)", "https://online.hrtchp.com/"),
        "Uttarakhand": ("UTC (official)", "https://utconline.uk.gov.in/"),
        "Gujarat": ("GSRTC (official)", "https://www.gsrtc.in/"),
        "West Bengal": ("WBTC (official)", "https://wbtconline.in/"),
        "Odisha": ("OSRTC (official)", "https://osrtconline.in/"),
        "Punjab": ("PUNBUS / PRTC (official)", "https://www.punbusonline.com/"),
        "Madhya Pradesh": ("MP state transport", "https://mptransport.org/"),
        "Goa": ("Kadamba Transport (official)", "https://ktclgoa.com/"),
        "Sikkim": ("Sikkim Nationalised Transport", "https://sikkimsntbus.in/"),
    }
    links: list[BookingLink] = []
    if state in state_rtc:
        label, url = state_rtc[state]
        links.append(
            BookingLink(label=label, url=url, official=True, note="State road transport corporation.")
        )
    links.append(
        BookingLink(
            label="redBus (aggregator)",
            url="https://www.redbus.in/",
            official=False,
            note="Private aggregator covering state and private operators.",
        )
    )
    return links


def _flight_links(origin_iata: str | None, dest_iata: str | None) -> list[BookingLink]:
    links = [
        BookingLink(
            label="Google Flights (fare comparison)",
            url="https://www.google.com/travel/flights",
            official=False,
            note="Compare live fares across airlines, then book with the airline.",
        )
    ]
    if origin_iata and dest_iata:
        links.append(
            BookingLink(
                label=f"Search {origin_iata} to {dest_iata}",
                url=f"https://www.google.com/travel/flights?q=Flights%20from%20{origin_iata}%20to%20{dest_iata}",
                official=False,
                note="Prefilled search. TripWise does not hold flight inventory.",
            )
        )
    return links


# --------------------------------------------------------------------------
# Mode quotes
# --------------------------------------------------------------------------

def quote_train(
    *,
    route: RouteInfo,
    destination: Destination,
    travelers: int,
    accommodation: AccommodationType,
    overrides: cm.ModelOverrides,
) -> TransportQuote:
    """Rail round trip, priced per person on a telescopic fare table."""
    if not destination.railhead.exists:
        return TransportQuote(
            mode=TravelMode.TRAIN,
            feasible=False,
            one_way_km=route.road_km,
            one_way_hours=route.road_hours,
            infeasible_reason=f"{destination.name} has no rail link.",
        )

    travel_class = overrides.train_class or cm.TRAIN_CLASS_BY_ACCOMMODATION[accommodation]
    rail_km = route.road_km * cm.RAIL_ROUTE_FACTOR
    fare = cm.train_fare_inr(route.road_km, travel_class)
    is_ac = travel_class in cm.TRAIN_AC_CLASSES

    items = [
        CostLineItem.build(
            category=CostCategory.INTERCITY_TRANSPORT,
            label=f"Train {travel_class} round trip",
            basis=CostBasis.PER_PERSON_TRIP,
            units=travelers * 2,
            unit_label="one-way ticket",
            unit_price_inr=fare,
            provenance=Provenance(
                source=DataSource.ESTIMATED,
                confidence=Confidence.MEDIUM,
                note=(
                    "Telescopic fare estimate for "
                    f"{rail_km:,.0f} rail km in {travel_class}. No public API exists "
                    "for live Indian Railways fares, so this is not a quote -- check "
                    "IRCTC for the actual fare and availability."
                ),
            ),
            taxable=is_ac,
            tax_rate=cm.GST_AC_TRANSPORT if is_ac else 0.0,
            detail=f"{travelers} traveller(s), both directions",
        )
    ]

    # Reaching the railhead from the destination centre, both ways.
    if destination.railhead.distance_km > 3:
        items.append(
            CostLineItem.build(
                category=CostCategory.INTERCITY_TRANSPORT,
                label=f"Transfer: {destination.railhead.name} station to {destination.name}",
                basis=CostBasis.PER_GROUP_TRIP,
                units=2,
                unit_label="transfer",
                unit_price_inr=cm.transfer_cost_inr(destination.railhead.distance_km, travelers),
                provenance=Provenance.estimated(
                    f"Shared cab over {destination.railhead.distance_km:.0f} km, both directions."
                ),
                detail=f"Shared across {travelers} traveller(s)",
            )
        )

    hours = rail_km / cm.TRAIN_AVG_SPEED_KMPH
    return TransportQuote(
        mode=TravelMode.TRAIN,
        feasible=True,
        one_way_km=round(rail_km, 1),
        one_way_hours=round(hours, 2),
        line_items=items,
        booking_links=_rail_links(),
        provenance=Provenance.estimated("Rail fare and journey time modelled, not quoted."),
        route_note=f"Nearest railhead: {destination.railhead.name} ({destination.railhead.distance_km:.0f} km out)",
        transfer_note=destination.local_transport,
        detail={"train_class": travel_class, "rail_km": round(rail_km, 1)},
    )


def quote_bus(
    *,
    route: RouteInfo,
    destination: Destination,
    travelers: int,
    overrides: cm.ModelOverrides,
) -> TransportQuote:
    """Bus round trip, priced per person."""
    fare, is_ac = cm.bus_fare_inr(route.road_km)
    service = "AC sleeper" if is_ac else "ordinary/semi-deluxe"

    items = [
        CostLineItem.build(
            category=CostCategory.INTERCITY_TRANSPORT,
            label=f"Bus ({service}) round trip",
            basis=CostBasis.PER_PERSON_TRIP,
            units=travelers * 2,
            unit_label="one-way ticket",
            unit_price_inr=fare,
            provenance=Provenance(
                source=DataSource.ESTIMATED,
                confidence=Confidence.MEDIUM,
                note=(
                    f"Estimate for {route.road_km:,.0f} km of {service} service. "
                    "Intercity bus fares in India are not available through an open "
                    "API, so compare on the operator's own site before booking."
                ),
            ),
            taxable=is_ac,
            tax_rate=cm.GST_AC_TRANSPORT if is_ac else 0.0,
            detail=f"{travelers} traveller(s), both directions",
        )
    ]

    hours = route.road_km / cm.BUS_AVG_SPEED_KMPH
    return TransportQuote(
        mode=TravelMode.BUS,
        feasible=True,
        one_way_km=route.road_km,
        one_way_hours=round(hours, 2),
        line_items=items,
        booking_links=_bus_links(destination.state),
        provenance=Provenance.estimated("Bus fare and journey time modelled, not quoted."),
        route_note=f"{route.road_km:,.0f} km by road ({route.provenance.source.value} distance)",
        transfer_note=destination.local_transport,
        detail={"service": service},
    )


def quote_car(
    *,
    route: RouteInfo,
    destination: Destination,
    travelers: int,
    days: int,
    overrides: cm.ModelOverrides,
) -> TransportQuote:
    """Car round trip, priced PER VEHICLE and shared across the group.

    This is the case that most often gets double counted. Units here are
    vehicles, never travellers.
    """
    label, mileage, capacity = cm.car_profile(travelers)
    vehicles = max(1, math.ceil(travelers / capacity))
    round_trip_km = route.road_km * 2
    fuel_price = overrides.fuel_price_per_litre or cm.FUEL_PRICE_PER_LITRE
    toll_rate = overrides.toll_per_km if overrides.toll_per_km is not None else cm.TOLL_PER_KM

    fuel_per_vehicle = (round_trip_km / mileage) * fuel_price
    toll_per_vehicle = round_trip_km * toll_rate

    items = [
        CostLineItem.build(
            category=CostCategory.INTERCITY_TRANSPORT,
            label=f"Fuel for {label} ({round_trip_km:,.0f} km round trip)",
            basis=CostBasis.PER_VEHICLE_TRIP,
            units=vehicles,
            unit_label="vehicle",
            unit_price_inr=fuel_per_vehicle,
            provenance=Provenance.estimated(
                f"{round_trip_km:,.0f} km at {mileage:g} km/l and Rs {fuel_price:,.0f}/litre."
            ),
            detail=f"Shared across {travelers} traveller(s) in {vehicles} vehicle(s)",
        ),
        CostLineItem.build(
            category=CostCategory.INTERCITY_TRANSPORT,
            label="Highway tolls (round trip)",
            basis=CostBasis.PER_VEHICLE_TRIP,
            units=vehicles,
            unit_label="vehicle",
            unit_price_inr=toll_per_vehicle,
            provenance=Provenance.estimated(
                f"National-highway average of Rs {toll_rate:.2f}/km over {round_trip_km:,.0f} km."
            ),
            detail="Actual tolls vary by corridor; some routes have none",
        ),
        CostLineItem.build(
            category=CostCategory.INTERCITY_TRANSPORT,
            label="Parking and road incidentals",
            basis=CostBasis.PER_VEHICLE_TRIP,
            units=vehicles * days,
            unit_label="vehicle-day",
            unit_price_inr=cm.CAR_DAILY_INCIDENTALS,
            provenance=Provenance.estimated("Parking, cleaning and roadside stops."),
        ),
    ]

    speed = cm.car_avg_speed_kmph(destination.terrain)
    hours = route.road_hours if route.provenance.is_live else route.road_km / speed

    return TransportQuote(
        mode=TravelMode.CAR,
        feasible=True,
        one_way_km=route.road_km,
        one_way_hours=round(hours, 2),
        line_items=items,
        booking_links=[],
        provenance=Provenance.estimated(
            "Own or rented vehicle. Running cost only -- rental or self-drive hire is not included."
        ),
        route_note=(
            f"{route.road_km:,.0f} km each way, {destination.terrain.replace('_', ' ')} roads "
            f"at about {speed:.0f} km/h average"
        ),
        transfer_note="Your own vehicle doubles as local transport at the destination.",
        detail={"vehicles": vehicles, "vehicle_class": label, "capacity": capacity},
    )


def quote_flight(
    *,
    route: RouteInfo,
    destination: Destination,
    origin_lat: float,
    origin_lon: float,
    travelers: int,
    start_date: date,
    today: date,
    overrides: cm.ModelOverrides,
    live_fare_inr: float | None = None,
    live_provider: str | None = None,
) -> TransportQuote:
    """Flight round trip, priced per person, plus transfers at both ends."""
    origin_airport_hit = nearest_airport(origin_lat, origin_lon)
    if origin_airport_hit is None:
        return TransportQuote(
            mode=TravelMode.FLIGHT,
            feasible=False,
            one_way_km=route.road_km,
            one_way_hours=route.road_hours,
            infeasible_reason="No commercial airport found near your starting point.",
        )
    origin_airport, origin_airport_km = origin_airport_hit

    if origin_airport_km > cm.MAX_ORIGIN_AIRPORT_KM:
        return TransportQuote(
            mode=TravelMode.FLIGHT,
            feasible=False,
            one_way_km=route.road_km,
            one_way_hours=route.road_hours,
            infeasible_reason=(
                f"The nearest airport ({origin_airport.iata}) is {origin_airport_km:.0f} km away, "
                "which makes flying impractical from here."
            ),
        )

    if not destination.airport.exists or not destination.airport.code:
        return TransportQuote(
            mode=TravelMode.FLIGHT,
            feasible=False,
            one_way_km=route.road_km,
            one_way_hours=route.road_hours,
            infeasible_reason=f"{destination.name} is not served by a nearby airport.",
        )

    dest_airport = _resolve_destination_airport(destination)
    if dest_airport is None:
        return TransportQuote(
            mode=TravelMode.FLIGHT,
            feasible=False,
            one_way_km=route.road_km,
            one_way_hours=route.road_hours,
            infeasible_reason=f"No airport record for {destination.airport.code}.",
        )

    air_km = haversine_km(origin_airport.lat, origin_airport.lon, dest_airport.lat, dest_airport.lon)
    if air_km < cm.FLIGHT_MIN_SENSIBLE_KM:
        return TransportQuote(
            mode=TravelMode.FLIGHT,
            feasible=False,
            one_way_km=route.road_km,
            one_way_hours=route.road_hours,
            infeasible_reason=(
                f"{origin_airport.iata} to {dest_airport.iata} is only {air_km:.0f} km. "
                "Routes that short are rarely flown -- take the train or drive."
            ),
        )

    if origin_airport.iata == dest_airport.iata:
        return TransportQuote(
            mode=TravelMode.FLIGHT,
            feasible=False,
            one_way_km=route.road_km,
            one_way_hours=route.road_hours,
            infeasible_reason="You would fly out of and into the same airport.",
        )

    days_ahead = max(0, (start_date - today).days)
    if live_fare_inr is not None:
        fare = live_fare_inr
        fare_provenance = Provenance.live(
            live_provider or "flight-provider", confidence=Confidence.HIGH
        )
    else:
        fare = cm.flight_fare_inr(air_km, days_ahead, start_date.month)
        fare_provenance = Provenance(
            source=DataSource.ESTIMATED,
            confidence=Confidence.LOW,
            note=(
                f"Modelled from {air_km:,.0f} km, {days_ahead} days' lead time and "
                f"{start_date.strftime('%B')} seasonality. Airfares move daily -- this is "
                "an estimate, not a quote. Check the airline for the real fare."
            ),
        )

    items = [
        CostLineItem.build(
            category=CostCategory.INTERCITY_TRANSPORT,
            label=f"Flight {origin_airport.iata} to {dest_airport.iata} (return)",
            basis=CostBasis.PER_PERSON_TRIP,
            units=travelers * 2,
            unit_label="one-way fare",
            unit_price_inr=fare,
            provenance=fare_provenance,
            taxable=True,
            tax_rate=cm.GST_AC_TRANSPORT,
            detail=f"{travelers} traveller(s), both directions",
        ),
        CostLineItem.build(
            category=CostCategory.INTERCITY_TRANSPORT,
            label=f"Transfers to/from {origin_airport.iata}",
            basis=CostBasis.PER_GROUP_TRIP,
            units=2,
            unit_label="transfer",
            unit_price_inr=cm.transfer_cost_inr(origin_airport_km, travelers),
            provenance=Provenance.estimated(
                f"Shared cab over {origin_airport_km:.0f} km at your end, both directions."
            ),
        ),
    ]

    if destination.airport.distance_km > 3:
        items.append(
            CostLineItem.build(
                category=CostCategory.INTERCITY_TRANSPORT,
                label=f"Transfers {dest_airport.iata} to {destination.name}",
                basis=CostBasis.PER_GROUP_TRIP,
                units=2,
                unit_label="transfer",
                unit_price_inr=cm.transfer_cost_inr(destination.airport.distance_km, travelers),
                provenance=Provenance.estimated(
                    f"Shared cab over {destination.airport.distance_km:.0f} km, both directions."
                ),
            )
        )

    air_hours = air_km / cm.FLIGHT_CRUISE_KMPH + cm.FLIGHT_FIXED_OVERHEAD_HOURS
    transfer_hours = (origin_airport_km + destination.airport.distance_km) / 40.0

    return TransportQuote(
        mode=TravelMode.FLIGHT,
        feasible=True,
        one_way_km=round(air_km, 1),
        one_way_hours=round(air_hours + transfer_hours, 2),
        line_items=items,
        booking_links=_flight_links(origin_airport.iata, dest_airport.iata),
        provenance=fare_provenance,
        route_note=(
            f"{origin_airport.iata} to {dest_airport.iata}, {air_km:,.0f} km. "
            f"Door-to-door includes {cm.FLIGHT_FIXED_OVERHEAD_HOURS:.0f} h of airport time."
        ),
        transfer_note=(
            f"{origin_airport_km:.0f} km to your airport, "
            f"{destination.airport.distance_km:.0f} km from {dest_airport.iata} to town."
        ),
        detail={
            "origin_iata": origin_airport.iata,
            "destination_iata": dest_airport.iata,
            "air_km": round(air_km, 1),
            "days_ahead": days_ahead,
        },
    )


def _resolve_destination_airport(destination: Destination) -> Airport | None:
    from ..data.catalog import airport_by_iata

    if destination.airport.code:
        hit = airport_by_iata(destination.airport.code)
        if hit:
            return hit
    fallback = nearest_airport(destination.lat, destination.lon)
    return fallback[0] if fallback else None


def build_route(
    origin_lat: float,
    origin_lon: float,
    destination: Destination,
    live_route: RouteInfo | None = None,
) -> RouteInfo:
    """Use a live route when one was fetched, otherwise estimate."""
    if live_route is not None:
        return live_route
    road_km = estimate_road_km(
        origin_lat, origin_lon, destination.lat, destination.lon, destination.terrain
    )
    hours = road_km / cm.car_avg_speed_kmph(destination.terrain)
    return RouteInfo(
        road_km=road_km,
        road_hours=round(hours, 2),
        provenance=Provenance.estimated(
            f"Straight-line distance inflated for {destination.terrain.replace('_', ' ')} roads. "
            "Configure a routing provider for real road distances."
        ),
        note="Estimated road distance",
    )
