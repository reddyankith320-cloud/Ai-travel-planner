"""Accommodation pricing.

The occupancy rule is the whole point of this module: hostels are priced per
bed, hotels per room. Four travellers in a hostel pay for four beds; four
travellers in budget hotels pay for two rooms. Getting this wrong is the
single biggest source of nonsense in a trip estimate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from ..data.catalog import Destination
from ..schemas.common import BookingLink, Provenance
from ..schemas.enums import (
    AccommodationType,
    Confidence,
    CostBasis,
    CostCategory,
    DataSource,
)
from ..schemas.cost import CostLineItem
from . import cost_model as cm


@dataclass(slots=True)
class AccommodationQuote:
    """Priced lodging for the whole stay."""

    accommodation: AccommodationType
    units: int
    unit_label: str
    unit_price_inr: float
    nights: int
    line_items: list[CostLineItem] = field(default_factory=list)
    booking_links: list[BookingLink] = field(default_factory=list)
    provenance: Provenance = field(default_factory=lambda: Provenance.estimated())
    note: str | None = None
    substituted_from: AccommodationType | None = None

    @property
    def total_inr(self) -> float:
        return round(sum(i.total_inr for i in self.line_items), 2)


# Fallback order when a destination does not offer the requested stay type.
_SUBSTITUTION_ORDER: dict[AccommodationType, list[AccommodationType]] = {
    AccommodationType.HOSTEL: [
        AccommodationType.BUDGET_HOTEL,
        AccommodationType.HOMESTAY,
        AccommodationType.MIDRANGE_HOTEL,
    ],
    AccommodationType.BUDGET_HOTEL: [
        AccommodationType.HOMESTAY,
        AccommodationType.HOSTEL,
        AccommodationType.MIDRANGE_HOTEL,
    ],
    AccommodationType.HOMESTAY: [
        AccommodationType.BUDGET_HOTEL,
        AccommodationType.MIDRANGE_HOTEL,
    ],
    AccommodationType.MIDRANGE_HOTEL: [
        AccommodationType.BUDGET_HOTEL,
        AccommodationType.RESORT,
        AccommodationType.HOMESTAY,
    ],
    AccommodationType.RESORT: [
        AccommodationType.MIDRANGE_HOTEL,
        AccommodationType.BUDGET_HOTEL,
    ],
}


def resolve_stay_type(
    destination: Destination, requested: AccommodationType
) -> tuple[AccommodationType, AccommodationType | None]:
    """Pick an available stay type, returning (chosen, substituted_from)."""
    if destination.supports_stay(requested.value):
        return requested, None
    for alternative in _SUBSTITUTION_ORDER.get(requested, []):
        if destination.supports_stay(alternative.value):
            return alternative, requested
    # Nothing matched the dataset; fall back to whatever the place does have.
    if destination.stay_types:
        return AccommodationType(destination.stay_types[0]), requested
    return requested, None


def quote_accommodation(
    *,
    destination: Destination,
    requested: AccommodationType,
    travelers: int,
    nights: int,
    check_in: date,
    overrides: cm.ModelOverrides,
    live_nightly_inr: float | None = None,
    live_provider: str | None = None,
) -> AccommodationQuote:
    """Price the stay for the whole group."""
    stay_type, substituted_from = resolve_stay_type(destination, requested)
    units = cm.accommodation_units(stay_type, travelers)
    unit_label = cm.accommodation_unit_label(stay_type)

    stay_index = destination.cost_index.get("stay", 1.0)
    if live_nightly_inr is not None:
        unit_price = live_nightly_inr
        provenance = Provenance.live(live_provider or "hotel-provider", confidence=Confidence.HIGH)
    elif overrides.accommodation_per_night is not None:
        unit_price = overrides.accommodation_per_night
        provenance = Provenance(
            source=DataSource.ESTIMATED,
            confidence=Confidence.HIGH,
            note="Nightly rate you supplied.",
        )
    else:
        unit_price = cm.ACCOMMODATION_PER_NIGHT[stay_type] * stay_index
        provenance = Provenance(
            source=DataSource.ESTIMATED,
            confidence=Confidence.MEDIUM,
            note=(
                f"Typical {stay_type.value.replace('_', ' ')} rate for {destination.name} "
                f"(national baseline Rs {cm.ACCOMMODATION_PER_NIGHT[stay_type]:,.0f} "
                f"x local index {stay_index:.2f}). Not a live availability check."
            ),
        )

    items: list[CostLineItem] = []
    if nights > 0:
        basis = (
            CostBasis.PER_BED_PER_NIGHT
            if stay_type == AccommodationType.HOSTEL
            else CostBasis.PER_ROOM_PER_NIGHT
        )
        occupancy = cm.UNIT_OCCUPANCY[stay_type]
        items.append(
            CostLineItem.build(
                category=CostCategory.ACCOMMODATION,
                label=f"{stay_type.value.replace('_', ' ').title()} x {nights} night(s)",
                basis=basis,
                units=units * nights,
                unit_label=unit_label,
                unit_price_inr=unit_price,
                provenance=provenance,
                taxable=True,
                tax_rate=cm.accommodation_gst_rate(unit_price),
                detail=(
                    f"{units} {unit_label.replace('-night', '')}(s) for {travelers} traveller(s) "
                    f"at {occupancy} per {unit_label.replace('-night', '')}"
                ),
            )
        )

    note: str | None = None
    if substituted_from is not None:
        note = (
            f"{destination.name} has no {substituted_from.value.replace('_', ' ')} option in our "
            f"dataset, so this is priced as {stay_type.value.replace('_', ' ')}."
        )
    elif nights == 0:
        note = "Single-day trip -- no overnight stay needed."

    return AccommodationQuote(
        accommodation=stay_type,
        units=units,
        unit_label=unit_label,
        unit_price_inr=round(unit_price, 2),
        nights=nights,
        line_items=items,
        booking_links=_stay_links(destination),
        provenance=provenance,
        note=note,
        substituted_from=substituted_from,
    )


def _stay_links(destination: Destination) -> list[BookingLink]:
    """Search links, not inventory. TripWise does not hold hotel stock."""
    query = f"{destination.name} {destination.state}".replace(" ", "+")
    return [
        BookingLink(
            label="Search stays on Booking.com",
            url=f"https://www.booking.com/searchresults.html?ss={query}",
            official=False,
            note="Prefilled search. Prices and availability are shown on their site.",
        ),
        BookingLink(
            label="Search stays on MakeMyTrip",
            url=f"https://www.makemytrip.com/hotels/?checkin=&city={query}",
            official=False,
            note="Prefilled search for Indian inventory.",
        ),
    ]
