"""The budget calculator.

Everything about money in TripWise comes together here, and the design has one
guarantee at its centre: **the total is the sum of the line items, always.**
Categories, per-person figures and dashboard shares are all projections of the
same list, so there is no path by which the headline number and the breakdown
can disagree.

Order of assembly:
    1. Collect line items from the transport, accommodation and activity
       services (each already correct about per-person vs per-group basis).
    2. Add daily spend: food and local transport.
    3. Sum to a subtotal.
    4. Apply tax, from each item's own declared rate.
    5. Apply contingency on top of subtotal + tax.

Nothing is counted twice because nothing is counted anywhere else.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import Settings
from ..data.catalog import Destination
from ..schemas.common import Assumption, Provenance
from ..schemas.cost import CostBreakdown, CostLineItem
from ..schemas.enums import (
    AccommodationType,
    BudgetFit,
    Confidence,
    CostBasis,
    CostCategory,
    DataSource,
    TravelMode,
)
from ..schemas.trip import TripRequest
from . import cost_model as cm
from .accommodation import AccommodationQuote
from .activities import ActivityPlan
from .transport import TransportQuote

# Numeric weights for averaging confidence across line items.
_CONFIDENCE_VALUE: dict[Confidence, float] = {
    Confidence.HIGH: 1.0,
    Confidence.MEDIUM: 0.65,
    Confidence.LOW: 0.3,
}

# When travelling by car, the vehicle also serves as local transport, so the
# local-transport allowance drops to fuel and parking around town only.
CAR_LOCAL_TRANSPORT_FACTOR = 0.45


@dataclass(slots=True)
class BudgetInputs:
    """Everything needed to produce a breakdown."""

    request: TripRequest
    destination: Destination
    transport: TransportQuote
    accommodation: AccommodationQuote
    activities: ActivityPlan


def _daily_spend_items(inputs: BudgetInputs, overrides: cm.ModelOverrides) -> list[CostLineItem]:
    """Food and local transport, both per person per day."""
    request = inputs.request
    destination = inputs.destination
    travelers = request.travelers
    days = request.days
    stay_type = inputs.accommodation.accommodation

    food_index = destination.cost_index.get("food", 1.0)
    food_rate = (
        overrides.food_per_person_per_day
        if overrides.food_per_person_per_day is not None
        else cm.FOOD_PER_PERSON_PER_DAY[stay_type] * food_index
    )

    local_index = destination.cost_index.get("local", 1.0)
    sharing = cm.local_sharing_factor(travelers)
    base_local = (
        overrides.local_transport_per_person_per_day
        if overrides.local_transport_per_person_per_day is not None
        else cm.LOCAL_TRANSPORT_PER_PERSON_PER_DAY * local_index * sharing
    )
    car_mode = inputs.transport.mode == TravelMode.CAR
    local_rate = base_local * (CAR_LOCAL_TRANSPORT_FACTOR if car_mode else 1.0)

    items = [
        CostLineItem.build(
            category=CostCategory.FOOD,
            label="Food and drinks",
            basis=CostBasis.PER_PERSON_PER_DAY,
            units=travelers * days,
            unit_label="person-day",
            unit_price_inr=food_rate,
            provenance=Provenance(
                source=DataSource.ESTIMATED,
                confidence=Confidence.MEDIUM,
                note=(
                    f"All meals at a {stay_type.value.replace('_', ' ')} spending level, "
                    f"adjusted by {destination.name}'s food index ({food_index:.2f}). "
                    "Restaurant tax is included in these rates."
                ),
            ),
            detail=f"{travelers} traveller(s) x {days} day(s)",
        ),
        CostLineItem.build(
            category=CostCategory.LOCAL_TRANSPORT,
            label="Local transport at the destination",
            basis=CostBasis.PER_PERSON_PER_DAY,
            units=travelers * days,
            unit_label="person-day",
            unit_price_inr=local_rate,
            provenance=Provenance(
                source=DataSource.ESTIMATED,
                confidence=Confidence.MEDIUM,
                note=(
                    (
                        "Reduced because your own vehicle covers local travel; this is fuel "
                        "and parking around town only. "
                        if car_mode
                        else ""
                    )
                    + f"Autos, local buses and short cabs. Local index {local_index:.2f}, "
                    f"shared-ride factor {sharing:.2f} for a group of {travelers}."
                ),
            ),
            detail=destination.local_transport,
        ),
    ]
    return items


def _assumptions(
    inputs: BudgetInputs, overrides: cm.ModelOverrides, contingency_rate: float
) -> list[Assumption]:
    """Every knob used, surfaced so the user can see and change it."""
    request = inputs.request
    destination = inputs.destination
    stay_type = inputs.accommodation.accommodation
    food_index = destination.cost_index.get("food", 1.0)
    local_index = destination.cost_index.get("local", 1.0)
    sharing = cm.local_sharing_factor(request.travelers)

    out = [
        Assumption(
            key="food_per_person_per_day",
            label="Food and drinks per person per day",
            value=round(
                overrides.food_per_person_per_day
                if overrides.food_per_person_per_day is not None
                else cm.FOOD_PER_PERSON_PER_DAY[stay_type] * food_index,
                2,
            ),
            unit="INR",
            provenance=Provenance.estimated(f"Local food index {food_index:.2f}"),
        ),
        Assumption(
            key="local_transport_per_person_per_day",
            label="Local transport per person per day",
            value=round(
                overrides.local_transport_per_person_per_day
                if overrides.local_transport_per_person_per_day is not None
                else cm.LOCAL_TRANSPORT_PER_PERSON_PER_DAY * local_index * sharing,
                2,
            ),
            unit="INR",
            provenance=Provenance.estimated(
                f"Local index {local_index:.2f}, shared-ride factor {sharing:.2f}"
            ),
        ),
        Assumption(
            key="accommodation_per_night",
            label=f"Accommodation per {inputs.accommodation.unit_label}",
            value=inputs.accommodation.unit_price_inr,
            unit="INR",
            provenance=inputs.accommodation.provenance,
        ),
        Assumption(
            key="accommodation_units",
            label=f"{inputs.accommodation.unit_label.replace('-night', '').title()}s booked",
            value=float(inputs.accommodation.units),
            unit="count",
            editable=False,
            provenance=Provenance.estimated(
                f"{cm.UNIT_OCCUPANCY[stay_type]} traveller(s) per "
                f"{inputs.accommodation.unit_label.replace('-night', '')}"
            ),
        ),
        Assumption(
            key="nights",
            label="Nights of accommodation",
            value=float(request.nights),
            unit="count",
            editable=False,
            provenance=Provenance.estimated(f"{request.days}-day trip = {request.nights} night(s)"),
        ),
        Assumption(
            key="contingency_rate",
            label="Emergency contingency",
            value=round(contingency_rate * 100, 2),
            unit="%",
            provenance=Provenance.estimated("Buffer for delays, price moves and the unexpected"),
        ),
        Assumption(
            key="activity_buffer_per_person_per_day",
            label="Unlisted activities allowance per person per day",
            value=round(
                overrides.activity_buffer_per_person_per_day
                if overrides.activity_buffer_per_person_per_day is not None
                else cm.ACTIVITY_BUFFER_PER_PERSON_PER_DAY[request.pace],
                2,
            ),
            unit="INR",
            provenance=Provenance.estimated(f"{request.pace.value.title()} pace"),
        ),
    ]

    if inputs.transport.mode == TravelMode.CAR:
        out.extend(
            [
                Assumption(
                    key="fuel_price_per_litre",
                    label="Fuel price",
                    value=round(overrides.fuel_price_per_litre or cm.FUEL_PRICE_PER_LITRE, 2),
                    unit="INR/litre",
                    provenance=Provenance.estimated("National average; varies by state"),
                ),
                Assumption(
                    key="toll_per_km",
                    label="Highway tolls",
                    value=round(
                        overrides.toll_per_km
                        if overrides.toll_per_km is not None
                        else cm.TOLL_PER_KM,
                        2,
                    ),
                    unit="INR/km",
                    provenance=Provenance.estimated("National-highway average"),
                ),
            ]
        )
    if inputs.transport.mode == TravelMode.TRAIN:
        out.append(
            Assumption(
                key="train_class",
                label=f"Rail class ({inputs.transport.detail.get('train_class', 'SL')})",
                value=0.0,
                unit="class",
                provenance=Provenance.estimated(
                    "Implied by your accommodation preference; override to re-price"
                ),
            )
        )
    return out


def budget_fit_for(utilisation: float, stretch_tolerance: float) -> BudgetFit:
    """Classify how a total sits against the budget."""
    if utilisation <= 0.70:
        return BudgetFit.COMFORTABLE
    if utilisation <= 0.92:
        return BudgetFit.GOOD
    if utilisation <= 1.0:
        return BudgetFit.TIGHT
    if utilisation <= 1.0 + stretch_tolerance:
        return BudgetFit.STRETCH
    return BudgetFit.OVER


def compute_breakdown(inputs: BudgetInputs, settings: Settings) -> CostBreakdown:
    """Produce the complete, reconciling cost breakdown for one destination."""
    request = inputs.request
    overrides = cm.ModelOverrides.from_mapping(request.assumption_overrides)
    contingency_rate = (
        request.contingency_rate
        if request.contingency_rate is not None
        else (overrides.contingency_rate if overrides.contingency_rate is not None else settings.contingency_rate)
    )

    # 1-2. Gather every line item exactly once, from the service that owns it.
    line_items: list[CostLineItem] = [
        *inputs.transport.line_items,
        *inputs.accommodation.line_items,
        *inputs.activities.line_items,
        *_daily_spend_items(inputs, overrides),
    ]

    # 3. Subtotal.
    subtotal = round(sum(item.total_inr for item in line_items), 2)

    # 4. Tax, from each item's own declared rate -- never a blanket percentage.
    taxes = round(
        sum(item.total_inr * item.tax_rate for item in line_items if item.taxable), 2
    )

    # 5. Contingency on the taxed subtotal.
    contingency = round((subtotal + taxes) * contingency_rate, 2)

    total = round(subtotal + taxes + contingency, 2)

    travelers = max(1, request.travelers)
    days = max(1, request.days)
    utilisation = total / request.budget_inr if request.budget_inr else float("inf")

    # Provenance rollup, weighted by what each item actually costs.
    live_value = sum(i.total_inr for i in line_items if i.provenance.is_live)
    live_share = round(live_value / subtotal, 4) if subtotal else 0.0
    weighted_confidence = (
        sum(_CONFIDENCE_VALUE[i.provenance.confidence] * i.total_inr for i in line_items) / subtotal
        if subtotal
        else 0.65
    )
    if weighted_confidence >= 0.85:
        confidence = Confidence.HIGH
    elif weighted_confidence >= 0.55:
        confidence = Confidence.MEDIUM
    else:
        confidence = Confidence.LOW

    return CostBreakdown(
        currency=settings.default_currency,
        travelers=travelers,
        days=days,
        nights=request.nights,
        line_items=line_items,
        assumptions=_assumptions(inputs, overrides, contingency_rate),
        subtotal_inr=subtotal,
        taxes_inr=taxes,
        contingency_inr=contingency,
        contingency_rate=contingency_rate,
        total_inr=total,
        per_traveler_inr=round(total / travelers, 2),
        per_day_inr=round(total / days, 2),
        per_traveler_per_day_inr=round(total / (travelers * days), 2),
        budget_inr=request.budget_inr,
        remaining_inr=round(max(0.0, request.budget_inr - total), 2),
        overrun_inr=round(max(0.0, total - request.budget_inr), 2),
        budget_utilisation=round(utilisation, 4),
        budget_fit=budget_fit_for(utilisation, settings.stretch_budget_tolerance),
        confidence=confidence,
        live_data_share=live_share,
        freshness_note=_freshness_note(live_share, inputs),
    )


def _freshness_note(live_share: float, inputs: BudgetInputs) -> str:
    """One honest sentence about where these numbers came from."""
    if live_share >= 0.6:
        return (
            f"{live_share:.0%} of this estimate is backed by live provider quotes fetched just now; "
            "the rest is modelled."
        )
    if live_share > 0:
        return (
            f"{live_share:.0%} of this estimate comes from live provider quotes. The remainder is "
            "modelled from reference rates and is not a quote."
        )
    volatile = inputs.transport.mode == TravelMode.FLIGHT
    base = (
        "Every figure here is modelled from TripWise's reference rates for India "
        f"(set {cm.REFERENCE_DATE:%B %Y}) -- none of it is a live quote."
    )
    if volatile:
        return base + " Airfares in particular move daily; verify before you budget on them."
    return base + " Verify fares and room rates before booking."


def minimum_viable_cost(request: TripRequest) -> float:
    """Cheapest plausible on-the-ground cost, ignoring intercity transport.

    Used to explain a shortfall: if even this exceeds the budget, the problem
    is the trip's shape, not the destination choice.
    """
    travelers, days, nights = request.travelers, request.days, request.nights
    food = cm.FOOD_PER_PERSON_PER_DAY[AccommodationType.HOSTEL] * travelers * days
    local = (
        cm.LOCAL_TRANSPORT_PER_PERSON_PER_DAY
        * cm.local_sharing_factor(travelers)
        * travelers
        * days
    )
    stay = cm.ACCOMMODATION_PER_NIGHT[AccommodationType.HOSTEL] * travelers * nights
    return round(food + local + stay, 2)
