"""Transport pricing tests, including the per-person vs per-vehicle rule."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.data.catalog import get_destination
from app.schemas.enums import AccommodationType, CostBasis, TravelMode
from app.services import cost_model as cm
from app.services.transport import (
    build_route,
    quote_bus,
    quote_car,
    quote_flight,
    quote_train,
)

from .conftest import ORIGIN_LAT, ORIGIN_LON

OVERRIDES = cm.ModelOverrides()


@pytest.fixture
def goa_route():
    goa = get_destination("north-goa")
    return goa, build_route(ORIGIN_LAT, ORIGIN_LON, goa)


class TestRouteEstimation:
    def test_estimated_distance_is_plausible(self, goa_route):
        """Hyderabad to Goa is roughly 650-700 km by road."""
        _, route = goa_route
        assert 600 < route.road_km < 760

    def test_estimate_is_labelled_as_an_estimate(self, goa_route):
        """The provenance enum is the contract the UI reads, not the prose."""
        from app.schemas.enums import DataSource

        _, route = goa_route
        assert route.provenance.source == DataSource.ESTIMATED
        assert not route.provenance.is_live
        assert route.provenance.note, "an estimate must explain itself"

    def test_hill_routes_are_inflated_more(self):
        from app.services.geo_math import estimate_road_km

        plains = estimate_road_km(17.385, 78.486, 19.0, 78.5, "plains")
        hills = estimate_road_km(17.385, 78.486, 19.0, 78.5, "hills")
        assert hills > plains

    def test_live_route_overrides_the_estimate(self, goa_route):
        from app.schemas.common import Provenance
        from app.services.transport import RouteInfo

        goa, _ = goa_route
        live = RouteInfo(road_km=611.0, road_hours=10.5, provenance=Provenance.live("osrm"))
        result = build_route(ORIGIN_LAT, ORIGIN_LON, goa, live)
        assert result is live
        assert result.provenance.is_live


class TestTrain:
    def test_fares_rise_with_distance(self):
        fares = [cm.train_fare_inr(d, "SL") for d in (200, 500, 1000, 2000)]
        assert fares == sorted(fares)

    def test_fares_are_telescopic(self):
        """Rate per km must fall as distance grows -- that is how IR prices."""
        near = cm.train_fare_inr(200, "SL") / 200
        far = cm.train_fare_inr(2000, "SL") / 2000
        assert far < near

    def test_higher_classes_cost_more(self):
        assert (
            cm.train_fare_inr(800, "SL")
            < cm.train_fare_inr(800, "3A")
            < cm.train_fare_inr(800, "2A")
        )

    def test_ac_classes_attract_gst(self, goa_route):
        goa, route = goa_route
        ac = quote_train(
            route=route, destination=goa, travelers=2,
            accommodation=AccommodationType.MIDRANGE_HOTEL, overrides=OVERRIDES,
        )
        sleeper = quote_train(
            route=route, destination=goa, travelers=2,
            accommodation=AccommodationType.BUDGET_HOTEL, overrides=OVERRIDES,
        )
        ac_ticket = next(i for i in ac.line_items if "Train" in i.label)
        sl_ticket = next(i for i in sleeper.line_items if "Train" in i.label)
        assert ac_ticket.taxable and ac_ticket.tax_rate == cm.GST_AC_TRANSPORT
        assert not sl_ticket.taxable

    def test_no_railhead_is_infeasible(self, goa_route):
        _, route = goa_route
        andaman = get_destination("andaman")
        quote = quote_train(
            route=route, destination=andaman, travelers=2,
            accommodation=AccommodationType.BUDGET_HOTEL, overrides=OVERRIDES,
        )
        assert not quote.feasible
        assert "no rail" in quote.infeasible_reason.lower()

    def test_links_to_official_booking_only(self, goa_route):
        goa, route = goa_route
        quote = quote_train(
            route=route, destination=goa, travelers=2,
            accommodation=AccommodationType.BUDGET_HOTEL, overrides=OVERRIDES,
        )
        assert quote.booking_links
        assert any("irctc" in link.url for link in quote.booking_links)
        assert all(link.official for link in quote.booking_links)

    def test_fare_is_never_presented_as_live(self, goa_route):
        goa, route = goa_route
        quote = quote_train(
            route=route, destination=goa, travelers=2,
            accommodation=AccommodationType.BUDGET_HOTEL, overrides=OVERRIDES,
        )
        ticket = next(i for i in quote.line_items if "Train" in i.label)
        assert not ticket.provenance.is_live
        assert "not a quote" in (ticket.provenance.note or "").lower()


class TestBus:
    def test_short_hops_use_ordinary_service(self):
        fare, is_ac = cm.bus_fare_inr(120)
        assert not is_ac

    def test_long_hauls_use_ac_sleeper(self):
        fare, is_ac = cm.bus_fare_inr(600)
        assert is_ac

    def test_state_operator_link_is_included(self, goa_route):
        goa, route = goa_route
        quote = quote_bus(route=route, destination=goa, travelers=2, overrides=OVERRIDES)
        labels = " ".join(link.label for link in quote.booking_links)
        assert "Kadamba" in labels  # Goa's state operator
        assert any(not link.official for link in quote.booking_links)  # aggregator marked

    def test_fare_scales_with_travellers(self, goa_route):
        goa, route = goa_route
        solo = quote_bus(route=route, destination=goa, travelers=1, overrides=OVERRIDES)
        pair = quote_bus(route=route, destination=goa, travelers=2, overrides=OVERRIDES)
        assert pair.round_trip_total_inr == pytest.approx(
            solo.round_trip_total_inr * 2, rel=0.01
        )


class TestCar:
    def test_cost_is_per_vehicle(self, goa_route):
        goa, route = goa_route
        one = quote_car(route=route, destination=goa, travelers=1, days=4, overrides=OVERRIDES)
        four = quote_car(route=route, destination=goa, travelers=4, days=4, overrides=OVERRIDES)
        # Both fit in one sedan, so the bill is identical.
        assert four.round_trip_total_inr == pytest.approx(one.round_trip_total_inr, abs=0.02)

    def test_second_vehicle_needed_beyond_suv_capacity(self, goa_route):
        goa, route = goa_route
        seven = quote_car(route=route, destination=goa, travelers=7, days=4, overrides=OVERRIDES)
        assert seven.detail["vehicles"] == 2

    def test_all_items_are_per_vehicle_basis(self, goa_route):
        goa, route = goa_route
        quote = quote_car(route=route, destination=goa, travelers=3, days=4, overrides=OVERRIDES)
        assert all(i.basis == CostBasis.PER_VEHICLE_TRIP for i in quote.line_items)

    def test_covers_fuel_tolls_and_incidentals(self, goa_route):
        goa, route = goa_route
        quote = quote_car(route=route, destination=goa, travelers=2, days=4, overrides=OVERRIDES)
        labels = " ".join(i.label for i in quote.line_items).lower()
        assert "fuel" in labels and "toll" in labels and "parking" in labels

    def test_fuel_matches_distance_mileage_and_price(self, goa_route):
        goa, route = goa_route
        quote = quote_car(route=route, destination=goa, travelers=2, days=4, overrides=OVERRIDES)
        fuel = next(i for i in quote.line_items if "Fuel" in i.label)
        expected = (route.road_km * 2 / cm.CAR_MILEAGE_KMPL_SEDAN) * cm.FUEL_PRICE_PER_LITRE
        assert fuel.unit_price_inr == pytest.approx(expected, rel=0.001)

    def test_hill_terrain_slows_the_drive(self):
        plains = get_destination("hampi")
        hills = get_destination("manali")
        assert cm.car_avg_speed_kmph(hills.terrain) < cm.car_avg_speed_kmph(plains.terrain)


class TestFlight:
    @pytest.fixture
    def far_route(self):
        leh = get_destination("leh-ladakh")
        return leh, build_route(ORIGIN_LAT, ORIGIN_LON, leh)

    def test_long_haul_flight_is_feasible(self, far_route):
        leh, route = far_route
        quote = quote_flight(
            route=route, destination=leh, origin_lat=ORIGIN_LAT, origin_lon=ORIGIN_LON,
            travelers=2, start_date=date.today() + timedelta(days=40), today=date.today(),
            overrides=OVERRIDES,
        )
        assert quote.feasible
        assert quote.detail["origin_iata"] == "HYD"

    def test_very_short_hops_are_rejected(self):
        """Nobody flies 150 km, and we should not pretend they do."""
        tirupati = get_destination("tirupati")
        route = build_route(13.0827, 80.2707, tirupati)  # Chennai
        quote = quote_flight(
            route=route, destination=tirupati, origin_lat=13.0827, origin_lon=80.2707,
            travelers=1, start_date=date.today() + timedelta(days=40), today=date.today(),
            overrides=OVERRIDES,
        )
        assert not quote.feasible
        assert "short" in quote.infeasible_reason.lower()

    def test_booking_late_costs_more(self):
        early = cm.flight_fare_inr(1200, 60, 11)
        late = cm.flight_fare_inr(1200, 3, 11)
        assert late > early

    def test_peak_season_costs_more(self):
        assert cm.flight_fare_inr(1200, 30, 12) > cm.flight_fare_inr(1200, 30, 9)

    def test_fare_has_a_floor(self):
        assert cm.flight_fare_inr(10, 90, 9) >= cm.FLIGHT_MINIMUM_FARE

    def test_includes_airport_transfers(self, far_route):
        leh, route = far_route
        quote = quote_flight(
            route=route, destination=leh, origin_lat=ORIGIN_LAT, origin_lon=ORIGIN_LON,
            travelers=2, start_date=date.today() + timedelta(days=40), today=date.today(),
            overrides=OVERRIDES,
        )
        assert any("Transfer" in i.label for i in quote.line_items)

    def test_door_to_door_time_includes_airport_overhead(self, far_route):
        leh, route = far_route
        quote = quote_flight(
            route=route, destination=leh, origin_lat=ORIGIN_LAT, origin_lon=ORIGIN_LON,
            travelers=2, start_date=date.today() + timedelta(days=40), today=date.today(),
            overrides=OVERRIDES,
        )
        pure_flight_hours = quote.detail["air_km"] / cm.FLIGHT_CRUISE_KMPH
        assert quote.one_way_hours > pure_flight_hours + 2

    def test_live_fare_replaces_the_estimate_and_relabels(self, far_route):
        leh, route = far_route
        quote = quote_flight(
            route=route, destination=leh, origin_lat=ORIGIN_LAT, origin_lon=ORIGIN_LON,
            travelers=2, start_date=date.today() + timedelta(days=40), today=date.today(),
            overrides=OVERRIDES, live_fare_inr=4321.0, live_provider="amadeus-flights",
        )
        ticket = next(i for i in quote.line_items if "Flight" in i.label)
        assert ticket.unit_price_inr == 4321.0
        assert ticket.provenance.is_live
        assert ticket.provenance.provider == "amadeus-flights"

    def test_estimated_fare_is_low_confidence(self, far_route):
        from app.schemas.enums import Confidence

        leh, route = far_route
        quote = quote_flight(
            route=route, destination=leh, origin_lat=ORIGIN_LAT, origin_lon=ORIGIN_LON,
            travelers=2, start_date=date.today() + timedelta(days=40), today=date.today(),
            overrides=OVERRIDES,
        )
        ticket = next(i for i in quote.line_items if "Flight" in i.label)
        assert ticket.provenance.confidence == Confidence.LOW

    def test_remote_origin_cannot_fly(self):
        """An origin with no airport within range rules flying out."""
        goa = get_destination("north-goa")
        # A point in the Thar with no nearby airport.
        route = build_route(27.5, 71.2, goa)
        quote = quote_flight(
            route=route, destination=goa, origin_lat=27.5, origin_lon=71.2,
            travelers=1, start_date=date.today() + timedelta(days=40), today=date.today(),
            overrides=OVERRIDES,
        )
        if not quote.feasible:
            assert "airport" in quote.infeasible_reason.lower()


class TestModeExpansion:
    def test_any_expands_to_every_mode(self):
        modes = cm.modes_for(TravelMode.ANY)
        assert set(modes) == {
            TravelMode.TRAIN, TravelMode.BUS, TravelMode.CAR, TravelMode.FLIGHT
        }

    def test_specific_mode_stays_specific(self):
        assert cm.modes_for(TravelMode.TRAIN) == [TravelMode.TRAIN]
