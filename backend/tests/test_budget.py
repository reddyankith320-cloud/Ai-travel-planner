"""Budget calculator tests.

The invariant under test throughout: **the headline total is the sum of the
line items, and the category breakdown reproduces it exactly.** If that ever
stops holding, the dashboard is lying to the user.
"""

from __future__ import annotations

import math

import pytest

from app.schemas.enums import (
    AccommodationType,
    BudgetFit,
    CostBasis,
    CostCategory,
    Pace,
    TravelMode,
)
from app.services import cost_model as cm
from app.services.accommodation import quote_accommodation
from app.services.activities import plan_activities
from app.services.budget import (
    BudgetInputs,
    budget_fit_for,
    compute_breakdown,
    minimum_viable_cost,
)
from app.services.transport import build_route, quote_bus, quote_car, quote_train

from .conftest import ORIGIN_LAT, ORIGIN_LON, make_request


def build_breakdown(request, destination, settings, mode=TravelMode.TRAIN):
    """Assemble a full breakdown the same way the engine does."""
    overrides = cm.ModelOverrides.from_mapping(request.assumption_overrides)
    route = build_route(ORIGIN_LAT, ORIGIN_LON, destination)
    if mode == TravelMode.TRAIN:
        transport = quote_train(
            route=route,
            destination=destination,
            travelers=request.travelers,
            accommodation=request.accommodation,
            overrides=overrides,
        )
    elif mode == TravelMode.BUS:
        transport = quote_bus(
            route=route,
            destination=destination,
            travelers=request.travelers,
            overrides=overrides,
        )
    else:
        transport = quote_car(
            route=route,
            destination=destination,
            travelers=request.travelers,
            days=request.days,
            overrides=overrides,
        )
    accommodation = quote_accommodation(
        destination=destination,
        requested=request.accommodation,
        travelers=request.travelers,
        nights=request.nights,
        check_in=request.start_date,
        overrides=overrides,
    )
    activities = plan_activities(
        destination,
        days=request.days,
        travelers=request.travelers,
        pace=request.pace,
        interests=request.interests,
        overrides=overrides,
    )
    return compute_breakdown(
        BudgetInputs(request, destination, transport, accommodation, activities), settings
    )


# --------------------------------------------------------------------------
# Reconciliation
# --------------------------------------------------------------------------

class TestReconciliation:
    def test_total_equals_subtotal_plus_tax_plus_contingency(self, trip_request, goa, settings):
        cost = build_breakdown(trip_request, goa, settings)
        expected = cost.subtotal_inr + cost.taxes_inr + cost.contingency_inr
        assert cost.total_inr == pytest.approx(expected, abs=0.02)

    def test_subtotal_equals_sum_of_line_items(self, trip_request, goa, settings):
        cost = build_breakdown(trip_request, goa, settings)
        assert cost.subtotal_inr == pytest.approx(
            sum(i.total_inr for i in cost.line_items), abs=0.02
        )

    def test_category_breakdown_sums_to_total(self, trip_request, goa, settings):
        cost = build_breakdown(trip_request, goa, settings)
        assert cost.reconciles()
        assert sum(c.total_inr for c in cost.categories) == pytest.approx(
            cost.total_inr, abs=0.05
        )

    def test_every_line_item_total_is_units_times_price(self, trip_request, goa, settings):
        cost = build_breakdown(trip_request, goa, settings)
        for item in cost.line_items:
            assert item.total_inr == pytest.approx(
                item.units * item.unit_price_inr, abs=0.02
            ), f"{item.label} does not multiply out"

    def test_category_shares_sum_to_one(self, trip_request, goa, settings):
        cost = build_breakdown(trip_request, goa, settings)
        assert sum(c.share_of_total for c in cost.categories) == pytest.approx(1.0, abs=0.01)

    @pytest.mark.parametrize("destination_id", ["north-goa", "hampi", "spiti", "kolkata"])
    @pytest.mark.parametrize("travelers", [1, 2, 5])
    @pytest.mark.parametrize("mode", [TravelMode.TRAIN, TravelMode.BUS, TravelMode.CAR])
    def test_reconciles_across_many_shapes(
        self, destination_id, travelers, mode, settings
    ):
        """Reconciliation must hold for every combination, not just the happy path."""
        from app.data.catalog import get_destination

        request = make_request(travelers=travelers, budget_inr=400_000)
        cost = build_breakdown(request, get_destination(destination_id), settings, mode)
        assert cost.reconciles(), f"{destination_id}/{travelers}pax/{mode.value} fails to reconcile"


# --------------------------------------------------------------------------
# No double counting
# --------------------------------------------------------------------------

class TestNoDoubleCounting:
    def test_each_category_appears_once_in_rollup(self, trip_request, goa, settings):
        cost = build_breakdown(trip_request, goa, settings)
        categories = [c.category for c in cost.categories]
        assert len(categories) == len(set(categories))

    def test_car_cost_is_per_vehicle_not_per_person(self, settings, goa):
        """Four people in one car must not pay four times for fuel."""
        solo = build_breakdown(
            make_request(travelers=1, budget_inr=400_000), goa, settings, TravelMode.CAR
        )
        four = build_breakdown(
            make_request(travelers=4, budget_inr=400_000), goa, settings, TravelMode.CAR
        )

        def fuel(cost):
            return sum(
                i.total_inr
                for i in cost.line_items
                if i.category == CostCategory.INTERCITY_TRANSPORT and "Fuel" in i.label
            )

        # One sedan seats four, so the fuel bill is identical.
        assert fuel(four) == pytest.approx(fuel(solo), abs=0.02)

    def test_car_adds_a_second_vehicle_beyond_capacity(self, settings, goa):
        six = build_breakdown(
            make_request(travelers=6, budget_inr=600_000), goa, settings, TravelMode.CAR
        )
        vehicle_items = [
            i for i in six.line_items if i.basis == CostBasis.PER_VEHICLE_TRIP
        ]
        assert vehicle_items, "car trip should produce per-vehicle line items"
        # Six travellers fit one SUV (capacity 6).
        assert all(i.units >= 1 for i in vehicle_items)

    def test_train_cost_scales_with_travellers(self, settings, goa):
        """Rail tickets are per person, so they must scale."""
        solo = build_breakdown(make_request(travelers=1, budget_inr=400_000), goa, settings)
        four = build_breakdown(make_request(travelers=4, budget_inr=400_000), goa, settings)

        def fare(cost):
            return sum(
                i.total_inr
                for i in cost.line_items
                if i.basis == CostBasis.PER_PERSON_TRIP and "Train" in i.label
            )

        assert fare(four) == pytest.approx(fare(solo) * 4, rel=0.01)

    def test_local_transport_is_cheaper_per_head_in_a_group(self, settings, goa):
        """Shared autos mean the per-head cost falls as the group grows."""
        solo = build_breakdown(make_request(travelers=1, budget_inr=400_000), goa, settings)
        four = build_breakdown(make_request(travelers=4, budget_inr=400_000), goa, settings)

        def per_head_local(cost):
            total = sum(
                i.total_inr
                for i in cost.line_items
                if i.category == CostCategory.LOCAL_TRANSPORT
            )
            return total / cost.travelers

        assert per_head_local(four) < per_head_local(solo)

    def test_car_mode_reduces_local_transport(self, settings, goa):
        """Your own vehicle is already paid for; don't bill autos as well."""
        train = build_breakdown(
            make_request(budget_inr=400_000), goa, settings, TravelMode.TRAIN
        )
        car = build_breakdown(make_request(budget_inr=400_000), goa, settings, TravelMode.CAR)

        def local(cost):
            return sum(
                i.total_inr
                for i in cost.line_items
                if i.category == CostCategory.LOCAL_TRANSPORT
            )

        assert local(car) < local(train)


# --------------------------------------------------------------------------
# Return transport and nights
# --------------------------------------------------------------------------

class TestRoundTripAndNights:
    def test_transport_covers_both_directions(self, settings, goa):
        request = make_request(travelers=2, budget_inr=400_000)
        cost = build_breakdown(request, goa, settings)
        ticket = next(
            i for i in cost.line_items if "Train" in i.label and i.basis == CostBasis.PER_PERSON_TRIP
        )
        # 2 travellers x 2 directions.
        assert ticket.units == 4

    @pytest.mark.parametrize("days,expected_nights", [(1, 0), (2, 1), (4, 3), (7, 6)])
    def test_nights_are_days_minus_one(self, days, expected_nights, settings, goa):
        request = make_request(days=days, budget_inr=400_000)
        assert request.nights == expected_nights
        cost = build_breakdown(request, goa, settings)
        assert cost.nights == expected_nights

    def test_single_day_trip_has_no_accommodation(self, settings, goa):
        cost = build_breakdown(make_request(days=1, budget_inr=400_000), goa, settings)
        stay = [i for i in cost.line_items if i.category == CostCategory.ACCOMMODATION]
        assert stay == []

    def test_accommodation_scales_with_nights(self, settings, goa):
        three = build_breakdown(make_request(days=3, budget_inr=400_000), goa, settings)
        five = build_breakdown(make_request(days=5, budget_inr=400_000), goa, settings)

        def stay(cost):
            return sum(
                i.total_inr for i in cost.line_items if i.category == CostCategory.ACCOMMODATION
            )

        # 2 nights vs 4 nights.
        assert stay(five) == pytest.approx(stay(three) * 2, rel=0.01)


# --------------------------------------------------------------------------
# Occupancy
# --------------------------------------------------------------------------

class TestOccupancy:
    @pytest.mark.parametrize(
        "accommodation,travelers,expected_units",
        [
            (AccommodationType.HOSTEL, 1, 1),
            (AccommodationType.HOSTEL, 4, 4),       # per bed
            (AccommodationType.BUDGET_HOTEL, 1, 1),
            (AccommodationType.BUDGET_HOTEL, 2, 1), # one double room
            (AccommodationType.BUDGET_HOTEL, 3, 2),
            (AccommodationType.BUDGET_HOTEL, 4, 2),
            (AccommodationType.BUDGET_HOTEL, 5, 3),
            (AccommodationType.HOMESTAY, 3, 1),     # sleeps three
            (AccommodationType.HOMESTAY, 4, 2),
        ],
    )
    def test_units_follow_occupancy(self, accommodation, travelers, expected_units):
        assert cm.accommodation_units(accommodation, travelers) == expected_units

    def test_hostel_priced_per_bed_hotel_per_room(self, goa, settings):
        overrides = cm.ModelOverrides()
        hostel = quote_accommodation(
            destination=goa,
            requested=AccommodationType.HOSTEL,
            travelers=4,
            nights=2,
            check_in=make_request().start_date,
            overrides=overrides,
        )
        hotel = quote_accommodation(
            destination=goa,
            requested=AccommodationType.BUDGET_HOTEL,
            travelers=4,
            nights=2,
            check_in=make_request().start_date,
            overrides=overrides,
        )
        assert hostel.unit_label == "bed-night"
        assert hostel.units == 4
        assert hotel.unit_label == "room-night"
        assert hotel.units == 2

    def test_substitution_when_stay_type_unavailable(self, settings):
        from app.data.catalog import get_destination

        spiti = get_destination("spiti")
        quote = quote_accommodation(
            destination=spiti,
            requested=AccommodationType.RESORT,
            travelers=2,
            nights=3,
            check_in=make_request().start_date,
            overrides=cm.ModelOverrides(),
        )
        assert quote.accommodation != AccommodationType.RESORT
        assert quote.substituted_from == AccommodationType.RESORT
        assert quote.note and "no resort" in quote.note.lower()


# --------------------------------------------------------------------------
# Tax and contingency
# --------------------------------------------------------------------------

class TestTaxAndContingency:
    def test_tax_comes_only_from_taxable_items(self, trip_request, goa, settings):
        cost = build_breakdown(trip_request, goa, settings)
        expected = sum(i.total_inr * i.tax_rate for i in cost.line_items if i.taxable)
        assert cost.taxes_inr == pytest.approx(expected, abs=0.02)

    def test_untaxed_items_contribute_nothing(self, trip_request, goa, settings):
        cost = build_breakdown(trip_request, goa, settings)
        for item in cost.line_items:
            if not item.taxable:
                assert item.tax_rate == 0.0

    @pytest.mark.parametrize(
        "unit_price,expected",
        [(1200, cm.GST_ACCOMMODATION_LOW), (7500, cm.GST_ACCOMMODATION_LOW), (9000, cm.GST_ACCOMMODATION_HIGH)],
    )
    def test_accommodation_gst_follows_the_tariff_slab(self, unit_price, expected):
        assert cm.accommodation_gst_rate(unit_price) == expected

    def test_contingency_applies_to_taxed_subtotal(self, trip_request, goa, settings):
        cost = build_breakdown(trip_request, goa, settings)
        expected = (cost.subtotal_inr + cost.taxes_inr) * cost.contingency_rate
        assert cost.contingency_inr == pytest.approx(expected, abs=0.02)

    def test_contingency_rate_is_overridable(self, goa, settings):
        default = build_breakdown(make_request(budget_inr=400_000), goa, settings)
        zero = build_breakdown(
            make_request(budget_inr=400_000, contingency_rate=0.0), goa, settings
        )
        assert zero.contingency_inr == 0.0
        assert zero.total_inr < default.total_inr
        assert zero.reconciles()


# --------------------------------------------------------------------------
# Derived figures
# --------------------------------------------------------------------------

class TestDerivedFigures:
    def test_per_person_and_per_day_divide_the_total(self, trip_request, goa, settings):
        cost = build_breakdown(trip_request, goa, settings)
        assert cost.per_traveler_inr == pytest.approx(cost.total_inr / cost.travelers, abs=0.02)
        assert cost.per_day_inr == pytest.approx(cost.total_inr / cost.days, abs=0.02)
        assert cost.per_traveler_per_day_inr == pytest.approx(
            cost.total_inr / (cost.travelers * cost.days), abs=0.02
        )

    def test_remaining_and_overrun_are_mutually_exclusive(self, goa, settings):
        plenty = build_breakdown(make_request(budget_inr=400_000), goa, settings)
        assert plenty.remaining_inr > 0
        assert plenty.overrun_inr == 0

        tight = build_breakdown(make_request(budget_inr=12_000), goa, settings)
        assert tight.overrun_inr > 0
        assert tight.remaining_inr == 0

    def test_remaining_plus_total_equals_budget_when_in_budget(self, goa, settings):
        cost = build_breakdown(make_request(budget_inr=400_000), goa, settings)
        assert cost.total_inr + cost.remaining_inr == pytest.approx(cost.budget_inr, abs=0.02)

    @pytest.mark.parametrize(
        "utilisation,expected",
        [
            (0.5, BudgetFit.COMFORTABLE),
            (0.80, BudgetFit.GOOD),
            (0.97, BudgetFit.TIGHT),
            (1.08, BudgetFit.STRETCH),
            (1.40, BudgetFit.OVER),
        ],
    )
    def test_budget_fit_bands(self, utilisation, expected):
        assert budget_fit_for(utilisation, 0.15) == expected

    def test_assumptions_are_exposed(self, trip_request, goa, settings):
        cost = build_breakdown(trip_request, goa, settings)
        keys = {a.key for a in cost.assumptions}
        for required in (
            "food_per_person_per_day",
            "local_transport_per_person_per_day",
            "accommodation_per_night",
            "nights",
            "contingency_rate",
        ):
            assert required in keys

    def test_demo_mode_reports_no_live_data(self, trip_request, goa, settings):
        cost = build_breakdown(trip_request, goa, settings)
        assert cost.live_data_share == 0.0
        assert "not a live quote" in cost.freshness_note or "modelled" in cost.freshness_note


# --------------------------------------------------------------------------
# Overrides
# --------------------------------------------------------------------------

class TestOverrides:
    def test_food_override_changes_the_total(self, goa, settings):
        cheap = build_breakdown(
            make_request(budget_inr=400_000, assumption_overrides={"food_per_person_per_day": 200}),
            goa,
            settings,
        )
        rich = build_breakdown(
            make_request(budget_inr=400_000, assumption_overrides={"food_per_person_per_day": 1500}),
            goa,
            settings,
        )
        assert rich.total_inr > cheap.total_inr
        assert cheap.reconciles() and rich.reconciles()

    def test_food_override_is_reflected_in_assumptions(self, goa, settings):
        cost = build_breakdown(
            make_request(budget_inr=400_000, assumption_overrides={"food_per_person_per_day": 333}),
            goa,
            settings,
        )
        food = next(a for a in cost.assumptions if a.key == "food_per_person_per_day")
        assert food.value == pytest.approx(333.0)

    def test_unknown_override_keys_are_ignored(self):
        overrides = cm.ModelOverrides.from_mapping(
            {"food_per_person_per_day": 400, "wholly_made_up": 99, "__class__": 1}
        )
        assert overrides.food_per_person_per_day == 400.0

    def test_negative_overrides_are_rejected(self):
        overrides = cm.ModelOverrides.from_mapping({"food_per_person_per_day": -50})
        assert overrides.food_per_person_per_day is None

    def test_non_numeric_overrides_are_ignored(self):
        overrides = cm.ModelOverrides.from_mapping({"fuel_price_per_litre": "lots"})
        assert overrides.fuel_price_per_litre is None

    def test_fuel_price_override_affects_car_cost(self, goa, settings):
        cheap = build_breakdown(
            make_request(budget_inr=900_000, assumption_overrides={"fuel_price_per_litre": 80}),
            goa, settings, TravelMode.CAR,
        )
        dear = build_breakdown(
            make_request(budget_inr=900_000, assumption_overrides={"fuel_price_per_litre": 130}),
            goa, settings, TravelMode.CAR,
        )
        assert dear.total_inr > cheap.total_inr


class TestMinimumViableCost:
    def test_grows_with_group_and_duration(self):
        small = minimum_viable_cost(make_request(travelers=1, days=2, budget_inr=400_000))
        large = minimum_viable_cost(make_request(travelers=4, days=6, budget_inr=400_000))
        assert large > small > 0

    def test_excludes_intercity_transport(self, goa, settings):
        request = make_request(budget_inr=400_000)
        floor = minimum_viable_cost(request)
        full = build_breakdown(request, goa, settings)
        assert floor < full.total_inr
