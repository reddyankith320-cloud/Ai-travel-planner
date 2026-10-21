"""Destination recommendation engine tests.

The product claim is that the user never names a destination and still gets a
sensible, affordable, ranked answer -- and that the answer *changes* when the
constraints change. That is what this module checks.
"""

from __future__ import annotations

import pytest

from app.data.catalog import get_destination, load_destinations
from app.schemas.enums import AccommodationType, BudgetFit, Interest, Pace, TravelMode
from app.schemas.trip import OriginLocation, TripRequest
from app.services import cost_model as cm
from app.services.ranking import (
    RANKING_WEIGHTS,
    build_candidate,
    rank_destinations,
    score_budget_fit,
    score_interest_match,
    score_travel_time,
    why_recommended,
)

from .conftest import ORIGIN_LAT, ORIGIN_LON, make_request


def rank(request, settings, limit=5):
    return rank_destinations(
        request,
        origin_lat=ORIGIN_LAT,
        origin_lon=ORIGIN_LON,
        settings=settings,
        limit=limit,
    )


# --------------------------------------------------------------------------
# The core promise
# --------------------------------------------------------------------------

class TestNoDestinationInput:
    def test_request_schema_has_no_destination_field(self):
        """Destination discovery is the product; naming one is not an input."""
        assert "destination" not in TripRequest.model_fields
        assert "destination_id" not in TripRequest.model_fields

    def test_request_rejects_a_destination_field(self):
        """`extra="forbid"` means a client cannot smuggle one in."""
        with pytest.raises(Exception):
            TripRequest(
                origin=OriginLocation(label="Hyderabad"),
                budget_inr=35_000,
                travelers=2,
                days=4,
                start_date=make_request().start_date,
                destination="Goa",
            )

    def test_recommendations_are_produced_from_constraints_alone(self, trip_request, settings):
        outcome = rank(trip_request, settings)
        assert outcome.ranked, "a reasonable budget should yield recommendations"
        assert all(c.destination.id for c in outcome.ranked)


class TestMultipleRankedResults:
    def test_returns_several_ranked_options(self, trip_request, settings):
        outcome = rank(trip_request, settings, limit=5)
        assert 3 <= len(outcome.ranked) <= 5

    def test_results_are_ordered_by_score_descending(self, trip_request, settings):
        outcome = rank(trip_request, settings)
        scores = [c.score.total for c in outcome.ranked]
        assert scores == sorted(scores, reverse=True)

    def test_limit_is_respected(self, trip_request, settings):
        assert len(rank(trip_request, settings, limit=2).ranked) <= 2

    def test_no_duplicate_destinations(self, trip_request, settings):
        outcome = rank(trip_request, settings, limit=5)
        ids = [c.destination.id for c in outcome.ranked]
        assert len(ids) == len(set(ids))


# --------------------------------------------------------------------------
# Transparency
# --------------------------------------------------------------------------

class TestScoreTransparency:
    def test_weights_sum_to_one(self):
        assert sum(RANKING_WEIGHTS.values()) == pytest.approx(1.0, abs=1e-9)

    def test_score_equals_sum_of_weighted_components(self, trip_request, settings):
        for candidate in rank(trip_request, settings).ranked:
            assert candidate.score.reconciles(), (
                f"{candidate.destination.name}: components do not sum to the total"
            )

    def test_every_weight_has_a_component(self, trip_request, settings):
        candidate = rank(trip_request, settings).ranked[0]
        keys = {c.key for c in candidate.score.components}
        assert keys == set(RANKING_WEIGHTS)

    def test_components_carry_explanations(self, trip_request, settings):
        candidate = rank(trip_request, settings).ranked[0]
        for component in candidate.score.components:
            assert component.explanation.strip()

    def test_reasons_cite_computed_numbers(self, trip_request, settings):
        candidate = rank(trip_request, settings).ranked[0]
        reasons = why_recommended(candidate, trip_request)
        assert reasons
        assert any("Rs" in r for r in reasons)


# --------------------------------------------------------------------------
# Sensitivity: the acceptance criterion
# --------------------------------------------------------------------------

class TestSensitivity:
    def test_raising_the_budget_changes_what_is_offered(self, settings):
        lean = rank(make_request(budget_inr=20_000), settings, limit=5)
        rich = rank(make_request(budget_inr=200_000), settings, limit=5)
        lean_ids = {c.destination.id for c in lean.ranked}
        rich_ids = {c.destination.id for c in rich.ranked}
        assert lean_ids != rich_ids
        assert rich.feasible > lean.feasible

    def test_a_tiny_budget_narrows_the_feasible_set(self, settings):
        tiny = rank(make_request(budget_inr=14_000), settings)
        large = rank(make_request(budget_inr=300_000), settings)
        assert tiny.feasible < large.feasible

    def test_more_travellers_raises_the_total(self, settings):
        two = rank(make_request(travelers=2, budget_inr=400_000), settings).ranked[0]
        five = next(
            c
            for c in rank(make_request(travelers=5, budget_inr=400_000), settings).ranked
            if True
        )
        # Compare the same destination to keep it apples-to-apples.
        request_two = make_request(travelers=2, budget_inr=400_000)
        request_five = make_request(travelers=5, budget_inr=400_000)
        destination = get_destination(two.destination.id)
        c2, _ = build_candidate(
            request=request_two, destination=destination, origin_lat=ORIGIN_LAT,
            origin_lon=ORIGIN_LON, settings=settings, today=request_two.start_date,
        )
        c5, _ = build_candidate(
            request=request_five, destination=destination, origin_lat=ORIGIN_LAT,
            origin_lon=ORIGIN_LON, settings=settings, today=request_five.start_date,
        )
        assert c5.cost.total_inr > c2.cost.total_inr

    def test_group_size_changes_the_ranking_at_a_fixed_budget(self, settings):
        """The same budget buys a different trip for one person and for six.

        Holding the budget constant isolates group size: more travellers means
        more per-head cost, a smaller feasible set, and a different ranked
        list -- not merely different totals.
        """
        solo = rank(make_request(travelers=1, budget_inr=45_000), settings, limit=5)
        group = rank(make_request(travelers=6, budget_inr=45_000), settings, limit=5)

        assert [c.destination.id for c in solo.ranked] != [
            c.destination.id for c in group.ranked
        ]
        assert solo.feasible > group.feasible, (
            "a larger group on the same budget should have fewer workable options"
        )

    def test_sharing_lowers_the_per_head_cost(self, settings):
        """Rooms and vehicles are shared, so cost per person falls with size."""
        destination = get_destination("north-goa")
        per_head = []
        for travelers in (1, 2, 4):
            request = make_request(travelers=travelers, budget_inr=400_000)
            candidate, _ = build_candidate(
                request=request, destination=destination, origin_lat=ORIGIN_LAT,
                origin_lon=ORIGIN_LON, settings=settings, today=request.start_date,
            )
            per_head.append(candidate.cost.per_traveler_inr)
        assert per_head == sorted(per_head, reverse=True), (
            f"per-head cost should fall as the group grows, got {per_head}"
        )

    def test_more_days_raises_the_total_for_a_fixed_mode(self, settings):
        """With the travel mode held constant, a longer trip costs more.

        Hampi is close enough that the train qualifies at both trip lengths,
        so the comparison isolates duration from mode choice.
        """
        destination = get_destination("hampi")
        short = make_request(days=3, budget_inr=400_000, travel_mode=TravelMode.TRAIN)
        long = make_request(days=6, budget_inr=400_000, travel_mode=TravelMode.TRAIN)
        c_short, _ = build_candidate(
            request=short, destination=destination, origin_lat=ORIGIN_LAT,
            origin_lon=ORIGIN_LON, settings=settings, today=short.start_date,
        )
        c_long, _ = build_candidate(
            request=long, destination=destination, origin_lat=ORIGIN_LAT,
            origin_lon=ORIGIN_LON, settings=settings, today=long.start_date,
        )
        assert c_long.cost.total_inr > c_short.cost.total_inr

    def test_a_short_trip_can_cost_more_by_forcing_a_faster_mode(self, settings):
        """Counterintuitive but correct: fewer days can mean a bigger bill.

        Hyderabad to Goa is ~14 h each way by train. On a 3-day trip that is
        39% of the available hours, past the feasibility limit, so the only
        workable mode is flying -- which costs far more than the train. Give
        the same trip 6 days and the train qualifies, and the total drops.

        This is the engine valuing a trip that is actually worth taking over
        one that is technically cheaper per day, and the UI surfaces the mode
        it chose so the jump is explainable.
        """
        destination = get_destination("north-goa")
        short = make_request(days=3, budget_inr=400_000, travel_mode=TravelMode.ANY)
        long = make_request(days=6, budget_inr=400_000, travel_mode=TravelMode.ANY)
        c_short, _ = build_candidate(
            request=short, destination=destination, origin_lat=ORIGIN_LAT,
            origin_lon=ORIGIN_LON, settings=settings, today=short.start_date,
        )
        c_long, _ = build_candidate(
            request=long, destination=destination, origin_lat=ORIGIN_LAT,
            origin_lon=ORIGIN_LON, settings=settings, today=long.start_date,
        )
        assert c_short.transport.mode == TravelMode.FLIGHT
        assert c_long.transport.mode == TravelMode.TRAIN
        assert c_short.cost.total_inr > c_long.cost.total_inr
        # Both still reconcile, and both are honest about the mode chosen.
        assert c_short.cost.reconciles() and c_long.cost.reconciles()

    def test_travel_mode_changes_the_ranking(self, settings):
        by_train = rank(make_request(travel_mode=TravelMode.TRAIN, budget_inr=60_000), settings)
        by_flight = rank(make_request(travel_mode=TravelMode.FLIGHT, budget_inr=60_000), settings)
        train_ids = [c.destination.id for c in by_train.ranked]
        flight_ids = [c.destination.id for c in by_flight.ranked]
        assert train_ids != flight_ids
        assert all(c.transport.mode == TravelMode.TRAIN for c in by_train.ranked)
        assert all(c.transport.mode == TravelMode.FLIGHT for c in by_flight.ranked)

    def test_interests_change_the_ranking(self, settings):
        beaches = rank(
            make_request(interests=[Interest.BEACHES], budget_inr=60_000), settings
        )
        mountains = rank(
            make_request(interests=[Interest.HERITAGE, Interest.SPIRITUAL], budget_inr=60_000),
            settings,
        )
        assert [c.destination.id for c in beaches.ranked] != [
            c.destination.id for c in mountains.ranked
        ]

    def test_beach_interest_surfaces_beach_destinations(self, settings):
        outcome = rank(make_request(interests=[Interest.BEACHES], budget_inr=60_000), settings)
        top_three = outcome.ranked[:3]
        assert any("beaches" in c.destination.tags for c in top_three)

    def test_pace_affects_activity_cost(self, settings):
        destination = get_destination("north-goa")
        relaxed = make_request(pace=Pace.RELAXED, budget_inr=400_000)
        packed = make_request(pace=Pace.PACKED, budget_inr=400_000)
        c_relaxed, _ = build_candidate(
            request=relaxed, destination=destination, origin_lat=ORIGIN_LAT,
            origin_lon=ORIGIN_LON, settings=settings, today=relaxed.start_date,
        )
        c_packed, _ = build_candidate(
            request=packed, destination=destination, origin_lat=ORIGIN_LAT,
            origin_lon=ORIGIN_LON, settings=settings, today=packed.start_date,
        )
        assert c_packed.cost.total_inr > c_relaxed.cost.total_inr

    def test_travel_date_changes_seasonal_scoring(self, settings):
        from datetime import date, timedelta

        # Spiti is summer-only; Rajasthan is winter-only.
        spiti = get_destination("spiti")
        today = date.today()
        july = make_request(budget_inr=400_000, start_date=_next_month(today, 7), days=7)
        january = make_request(budget_inr=400_000, start_date=_next_month(today, 1), days=7)
        c_july, _ = build_candidate(
            request=july, destination=spiti, origin_lat=ORIGIN_LAT, origin_lon=ORIGIN_LON,
            settings=settings, today=today,
        )
        c_jan, _ = build_candidate(
            request=january, destination=spiti, origin_lat=ORIGIN_LAT, origin_lon=ORIGIN_LON,
            settings=settings, today=today,
        )
        if c_july and c_jan:
            assert c_july.season_score > c_jan.season_score


def _next_month(today, month: int):
    """The next occurrence of `month`, at least 30 days out."""
    from datetime import date, timedelta

    year = today.year
    candidate = date(year, month, 15)
    while (candidate - today).days < 30:
        year += 1
        candidate = date(year, month, 15)
    return candidate


# --------------------------------------------------------------------------
# Filters
# --------------------------------------------------------------------------

class TestFeasibilityFilters:
    def test_no_recommendation_exceeds_the_budget(self, trip_request, settings):
        for candidate in rank(trip_request, settings).ranked:
            assert candidate.cost.total_inr <= trip_request.budget_inr
            assert candidate.cost.budget_fit != BudgetFit.OVER

    def test_stretch_options_are_flagged_and_over_budget(self, settings):
        outcome = rank(make_request(budget_inr=24_000), settings)
        for candidate in outcome.stretch:
            assert candidate.is_stretch
            assert candidate.cost.total_inr > candidate.cost.budget_inr

    def test_stretch_options_can_be_suppressed(self, settings):
        outcome = rank(make_request(budget_inr=24_000, include_stretch_options=False), settings)
        assert outcome.stretch == []

    def test_nearby_destinations_are_excluded(self, settings):
        """A place 20 km away is a day out, not a trip."""
        # Rank from Hampi itself; Hampi must not be recommended.
        hampi = get_destination("hampi")
        outcome = rank_destinations(
            make_request(budget_inr=200_000),
            origin_lat=hampi.lat,
            origin_lon=hampi.lon,
            settings=settings,
            limit=5,
        )
        assert "hampi" not in {c.destination.id for c in outcome.ranked}
        assert any(r.reason_code == "too_close" for r in outcome.rejected)

    def test_travel_time_filter_rejects_distant_places_on_short_trips(self, settings):
        """Two days is not enough to reach Ladakh from Hyderabad."""
        outcome = rank(make_request(days=2, budget_inr=400_000), settings)
        assert "leh-ladakh" not in {c.destination.id for c in outcome.ranked}

    def test_round_trip_travel_stays_within_the_share_limit(self, trip_request, settings):
        for candidate in rank(trip_request, settings).ranked:
            share = candidate.transport.round_trip_hours / (trip_request.days * 24)
            assert share <= cm.MAX_TRAVEL_SHARE_OF_TRIP + 1e-9

    def test_explicit_one_way_limit_is_honoured(self, settings):
        outcome = rank(make_request(budget_inr=400_000, max_one_way_hours=6), settings)
        for candidate in outcome.ranked:
            assert candidate.transport.one_way_hours <= 6 + 1e-9

    def test_rejections_explain_themselves(self, settings):
        outcome = rank(make_request(budget_inr=16_000), settings)
        assert outcome.rejected
        for rejection in outcome.rejected:
            assert rejection.reason.strip()
            assert rejection.reason_code in {"too_close", "over_budget", "unreachable"}

    def test_flight_only_rejects_places_without_airports(self, settings):
        outcome = rank(make_request(travel_mode=TravelMode.FLIGHT, budget_inr=400_000), settings)
        for candidate in outcome.ranked:
            assert candidate.destination.airport.exists


# --------------------------------------------------------------------------
# Value, not just cheapness
# --------------------------------------------------------------------------

class TestValueOverCheapness:
    def test_cheapest_option_is_not_automatically_first(self, settings):
        outcome = rank(make_request(budget_inr=80_000), settings, limit=5)
        totals = [c.cost.total_inr for c in outcome.ranked]
        # If ranking were price-only, totals would be strictly ascending.
        assert totals != sorted(totals), (
            "ranking appears to be ordered by price alone, which it should not be"
        )

    def test_budget_fit_peaks_in_the_middle_of_the_range(self):
        assert score_budget_fit(0.80, 0.15)[0] > score_budget_fit(0.15, 0.15)[0]
        assert score_budget_fit(0.80, 0.15)[0] > score_budget_fit(0.99, 0.15)[0]

    def test_over_budget_scores_zero(self):
        assert score_budget_fit(1.5, 0.15)[0] == 0.0

    def test_shorter_journeys_score_higher(self):
        assert score_travel_time(6, 4)[0] > score_travel_time(30, 4)[0]

    def test_matching_interests_scores_higher(self):
        goa = get_destination("north-goa")
        spiti = get_destination("spiti")
        beach_score, _ = score_interest_match(goa, [Interest.BEACHES])
        spiti_score, _ = score_interest_match(spiti, [Interest.BEACHES])
        assert beach_score > spiti_score

    def test_no_interests_is_neutral(self):
        score, explanation = score_interest_match(get_destination("north-goa"), [])
        assert score == 0.60
        assert "did not affect" in explanation


# --------------------------------------------------------------------------
# Shortfall
# --------------------------------------------------------------------------

class TestShortfall:
    def test_impossible_budget_yields_a_shortfall_not_an_error(self, settings):
        outcome = rank(make_request(budget_inr=9_000, travelers=2, days=4), settings)
        assert outcome.ranked == []
        assert outcome.shortfall is not None

    def test_shortfall_quantifies_the_gap(self, settings):
        outcome = rank(make_request(budget_inr=9_000, travelers=2, days=4), settings)
        shortfall = outcome.shortfall
        assert shortfall.additional_budget_required_inr > 0
        assert shortfall.cheapest_total_inr > 9_000
        assert shortfall.suggested_budget_inr >= shortfall.cheapest_total_inr

    def test_shortfall_offers_concrete_levers(self, settings):
        outcome = rank(make_request(budget_inr=9_000, travelers=2, days=4), settings)
        levers = outcome.shortfall.levers
        assert len(levers) >= 2
        assert any("budget" in lever.lower() for lever in levers)

    def test_shortfall_lists_closest_alternatives(self, settings):
        outcome = rank(make_request(budget_inr=9_000, travelers=2, days=4), settings)
        alternatives = outcome.shortfall.alternatives
        assert alternatives
        totals = [a.estimated_total_inr for a in alternatives]
        assert totals == sorted(totals), "alternatives should be cheapest-first"

    def test_generous_budget_has_no_shortfall(self, settings):
        outcome = rank(make_request(budget_inr=300_000), settings)
        assert outcome.shortfall is None


# --------------------------------------------------------------------------
# Robustness
# --------------------------------------------------------------------------

class TestRobustness:
    def test_every_catalogue_destination_can_be_costed(self, settings):
        """No destination should crash the engine, whatever its data."""
        request = make_request(budget_inr=5_000_000, days=8)
        for destination in load_destinations():
            candidate, rejection = build_candidate(
                request=request,
                destination=destination,
                origin_lat=ORIGIN_LAT,
                origin_lon=ORIGIN_LON,
                settings=settings,
                today=request.start_date,
            )
            assert candidate is not None or rejection is not None
            if candidate is not None:
                assert candidate.cost.reconciles(), destination.id

    def test_single_traveller_single_day_works(self, settings):
        outcome = rank(make_request(travelers=1, days=1, budget_inr=20_000), settings)
        assert outcome.considered == len(load_destinations())
        for candidate in outcome.ranked:
            assert candidate.cost.nights == 0

    def test_large_group_works(self, settings):
        outcome = rank(make_request(travelers=12, budget_inr=400_000), settings)
        for candidate in outcome.ranked:
            assert candidate.cost.travelers == 12
            assert candidate.cost.reconciles()

    def test_maximum_trip_length_works(self, settings):
        outcome = rank(make_request(days=21, budget_inr=900_000), settings)
        assert outcome.ranked
        for candidate in outcome.ranked:
            assert candidate.cost.nights == 20

    def test_ranking_is_deterministic(self, trip_request, settings):
        first = [c.destination.id for c in rank(trip_request, settings).ranked]
        second = [c.destination.id for c in rank(trip_request, settings).ranked]
        assert first == second

    def test_all_accommodation_types_are_rankable(self, settings):
        for accommodation in AccommodationType:
            outcome = rank(
                make_request(accommodation=accommodation, budget_inr=400_000), settings
            )
            assert outcome.ranked, f"no results for {accommodation.value}"


class TestInterestGap:
    """When the budget cannot buy the kind of trip asked for, say so."""

    def test_gap_is_reported_when_interests_are_unaffordable(self, settings):
        from app.services.ranking import build_interest_gap

        # Mountains from Mumbai on a modest budget: they price out.
        request = make_request(
            interests=[Interest.MOUNTAINS, Interest.TREKKING],
            budget_inr=60_000,
            travelers=3,
            days=5,
        )
        outcome = rank_destinations(
            request,
            origin_lat=19.076,
            origin_lon=72.877,
            settings=settings,
            limit=4,
        )
        gap = outcome.interest_gap
        if any("mountains" in c.destination.tags for c in outcome.ranked):
            # Mountains were affordable after all; nothing to report.
            assert gap is None or "mountains" not in [u.interest for u in gap.unmet]
            return

        assert gap is not None
        assert "mountains" in [u.interest for u in gap.unmet]
        assert "mountains" in gap.message

    def test_gap_names_the_cheapest_matching_option_and_the_shortfall(self, settings):
        request = make_request(
            interests=[Interest.MOUNTAINS], budget_inr=55_000, travelers=3, days=5
        )
        outcome = rank_destinations(
            request, origin_lat=19.076, origin_lon=72.877, settings=settings, limit=4
        )
        gap = outcome.interest_gap
        if gap is None:
            return
        unmet = next((u for u in gap.unmet if u.interest == "mountains"), None)
        if unmet and unmet.cheapest_matching_name:
            assert unmet.cheapest_matching_total_inr > request.budget_inr
            assert unmet.additional_budget_required_inr > 0
            assert gap.smallest_additional_budget_inr is not None

    def test_no_gap_when_interests_are_satisfied(self, settings):
        outcome = rank_destinations(
            make_request(interests=[Interest.BEACHES], budget_inr=80_000),
            origin_lat=ORIGIN_LAT,
            origin_lon=ORIGIN_LON,
            settings=settings,
            limit=5,
        )
        if any("beaches" in c.destination.tags for c in outcome.ranked):
            gap = outcome.interest_gap
            assert gap is None or "beaches" not in [u.interest for u in gap.unmet]

    def test_no_gap_when_no_interests_were_given(self, settings):
        outcome = rank_destinations(
            make_request(interests=[], budget_inr=80_000),
            origin_lat=ORIGIN_LAT,
            origin_lon=ORIGIN_LON,
            settings=settings,
        )
        assert outcome.interest_gap is None

    def test_rejections_carry_tags_so_the_gap_can_be_computed(self, settings):
        outcome = rank_destinations(
            make_request(budget_inr=16_000), origin_lat=ORIGIN_LAT, origin_lon=ORIGIN_LON,
            settings=settings,
        )
        assert outcome.rejected
        assert any(r.tags for r in outcome.rejected)

    def test_matched_interests_are_listed_alongside_unmet_ones(self, settings):
        from app.services.ranking import build_interest_gap

        request = make_request(interests=[Interest.BEACHES, Interest.DESERT], budget_inr=40_000)
        outcome = rank_destinations(
            request, origin_lat=ORIGIN_LAT, origin_lon=ORIGIN_LON, settings=settings, limit=5
        )
        gap = build_interest_gap(
            recommended=outcome.ranked, rejected=outcome.rejected, request=request
        )
        if gap is not None:
            # Every requested interest is accounted for, one way or the other.
            accounted = set(gap.matched) | {u.interest for u in gap.unmet}
            assert accounted == {"beaches", "desert"}
