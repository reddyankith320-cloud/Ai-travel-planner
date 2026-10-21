"""Itinerary tests: geographic grouping, time-of-day sense, and LLM containment.

The LLM tests matter most. The claim is that a language model cannot inject a
fabricated price or a fabricated place into a user's plan, and these exercise
that boundary against a deliberately misbehaving fake model.
"""

from __future__ import annotations

import asyncio

import pytest

from app.config import Settings
from app.data.catalog import get_destination
from app.schemas.enums import ActivityKind, Interest, Pace
from app.services import cost_model as cm
from app.services.activities import group_by_day, plan_activities, select_attractions
from app.services.geo_math import haversine_km
from app.services.itinerary import (
    LLMItinerary,
    build_deterministic_days,
    build_user_prompt,
    generate_itinerary,
    order_for_time_of_day,
    time_window,
)

from .conftest import make_request
from .test_budget import build_breakdown

OVERRIDES = cm.ModelOverrides()
FOOD_RATE = 600.0


def make_plan(destination, request):
    return plan_activities(
        destination,
        days=request.days,
        travelers=request.travelers,
        pace=request.pace,
        interests=request.interests,
        overrides=OVERRIDES,
    )


class TestActivitySelection:
    def test_pace_controls_how_much_is_scheduled(self):
        goa = get_destination("north-goa")
        relaxed = select_attractions(goa, days=2, pace=Pace.RELAXED, interests=[])
        packed = select_attractions(goa, days=2, pace=Pace.PACKED, interests=[])
        assert len(packed) >= len(relaxed)

    def test_selection_respects_the_daily_time_budget(self):
        """A day cannot hold three eight-hour treks."""
        meghalaya = get_destination("shillong")
        picked = select_attractions(meghalaya, days=1, pace=Pace.BALANCED, interests=[])
        total_minutes = sum(a.visit_minutes for a in picked)
        assert total_minutes <= cm.ACTIVE_HOURS_PER_DAY[Pace.BALANCED] * 60 or len(picked) == 1

    def test_interests_steer_selection(self):
        goa = get_destination("north-goa")
        beachy = select_attractions(goa, days=3, pace=Pace.BALANCED, interests=[Interest.BEACHES])
        heritage = select_attractions(
            goa, days=3, pace=Pace.BALANCED, interests=[Interest.HERITAGE]
        )
        assert [a.name for a in beachy] != [a.name for a in heritage]

    def test_budget_pressure_favours_free_attractions(self):
        goa = get_destination("north-goa")
        relaxed = select_attractions(
            goa, days=2, pace=Pace.BALANCED, interests=[], budget_pressure=0.0
        )
        squeezed = select_attractions(
            goa, days=2, pace=Pace.BALANCED, interests=[], budget_pressure=1.0
        )
        assert sum(a.entry_fee_inr for a in squeezed) <= sum(a.entry_fee_inr for a in relaxed)

    def test_exclusions_are_honoured(self):
        goa = get_destination("north-goa")
        full = select_attractions(goa, days=3, pace=Pace.PACKED, interests=[])
        dropped = full[0].name
        remaining = select_attractions(
            goa, days=3, pace=Pace.PACKED, interests=[], excluded_names={dropped}
        )
        assert dropped not in {a.name for a in remaining}

    def test_selection_is_deterministic(self):
        goa = get_destination("north-goa")
        first = select_attractions(goa, days=3, pace=Pace.BALANCED, interests=[Interest.BEACHES])
        second = select_attractions(goa, days=3, pace=Pace.BALANCED, interests=[Interest.BEACHES])
        assert [a.name for a in first] == [a.name for a in second]


class TestGeographicGrouping:
    def test_one_group_per_day(self):
        goa = get_destination("north-goa")
        picked = select_attractions(goa, days=3, pace=Pace.BALANCED, interests=[])
        groups = group_by_day(picked, 3, (goa.lat, goa.lon))
        assert len(groups) == 3

    def test_every_attraction_is_used_exactly_once(self):
        goa = get_destination("north-goa")
        picked = select_attractions(goa, days=3, pace=Pace.PACKED, interests=[])
        groups = group_by_day(picked, 3, (goa.lat, goa.lon))
        flattened = [a.name for group in groups for a in group]
        assert sorted(flattened) == sorted(a.name for a in picked)

    def test_grouping_beats_an_arbitrary_split(self):
        """Clustering must produce tighter days than naive sequential slicing."""
        kolkata = get_destination("kolkata")
        picked = select_attractions(kolkata, days=3, pace=Pace.PACKED, interests=[])
        base = (kolkata.lat, kolkata.lon)

        def spread(groups):
            total = 0.0
            for group in groups:
                for a, b in zip(group, group[1:]):
                    total += haversine_km(a.lat, a.lon, b.lat, b.lon)
            return total

        clustered = group_by_day(picked, 3, base)
        size = max(1, len(picked) // 3)
        naive = [picked[i : i + size] for i in range(0, len(picked), size)][:3]
        assert spread(clustered) <= spread(naive) + 1e-6

    def test_balanced_group_sizes(self):
        goa = get_destination("north-goa")
        picked = select_attractions(goa, days=3, pace=Pace.PACKED, interests=[])
        groups = group_by_day(picked, 3, (goa.lat, goa.lon))
        sizes = [len(g) for g in groups]
        assert max(sizes) - min(sizes) <= 2

    def test_empty_selection_still_returns_a_slot_per_day(self):
        groups = group_by_day([], 4, (0.0, 0.0))
        assert len(groups) == 4
        assert all(g == [] for g in groups)


class TestTimeOfDay:
    def test_sunrise_and_sunset_windows_are_detected(self):
        hampi = get_destination("hampi")
        sunrise = next(a for a in hampi.attractions if "sunrise" in a.name.lower())
        sunset = next(a for a in hampi.attractions if "sunset" in a.name.lower())
        assert time_window(sunrise)[0] < 12 * 60
        assert time_window(sunset)[0] >= 12 * 60

    def test_ordering_puts_sunrise_first_and_sunset_last(self):
        hampi = get_destination("hampi")
        picked = list(hampi.attractions)
        ordered = order_for_time_of_day(picked)
        names = [a.name.lower() for a in ordered]
        sunrise_index = next(i for i, n in enumerate(names) if "sunrise" in n)
        sunset_index = next(i for i, n in enumerate(names) if "sunset" in n)
        assert sunrise_index == 0
        assert sunset_index == len(ordered) - 1

    def test_sunset_stop_is_scheduled_in_the_evening(self, settings):
        hampi = get_destination("hampi")
        request = make_request(days=3, budget_inr=400_000, pace=Pace.BALANCED)
        plan = make_plan(hampi, request)
        days = build_deterministic_days(
            destination=hampi, plan=plan, request=request, food_per_person_per_day=FOOD_RATE
        )
        for day in days:
            for activity in day.activities:
                if "sunset" in activity.title.lower():
                    hour = int(activity.start_time.split(":")[0])
                    assert hour >= 16, f"sunset stop scheduled at {activity.start_time}"


class TestDeterministicPlanner:
    @pytest.fixture
    def plan_and_days(self, settings):
        goa = get_destination("north-goa")
        request = make_request(days=4, budget_inr=400_000)
        plan = make_plan(goa, request)
        days = build_deterministic_days(
            destination=goa, plan=plan, request=request, food_per_person_per_day=FOOD_RATE
        )
        return goa, request, plan, days

    def test_one_day_per_trip_day(self, plan_and_days):
        _, request, _, days = plan_and_days
        assert len(days) == request.days
        assert [d.day_number for d in days] == list(range(1, request.days + 1))

    def test_dates_are_sequential_from_the_start_date(self, plan_and_days):
        _, request, _, days = plan_and_days
        for index, day in enumerate(days):
            assert (day.date - request.start_date).days == index

    def test_first_day_includes_arrival(self, plan_and_days):
        _, _, _, days = plan_and_days
        assert any(a.kind == ActivityKind.CHECK_IN for a in days[0].activities)

    def test_last_day_includes_departure(self, plan_and_days):
        _, _, _, days = plan_and_days
        assert any(a.kind == ActivityKind.CHECK_OUT for a in days[-1].activities)

    def test_every_day_has_meals(self, plan_and_days):
        _, _, _, days = plan_and_days
        for day in days:
            assert any(a.kind == ActivityKind.MEAL for a in day.activities)

    def test_activities_are_time_ordered_within_a_day(self, plan_and_days):
        _, _, _, days = plan_and_days
        for day in days:
            # Ignore the departure block, which is pinned to the evening.
            timed = [
                a.start_time
                for a in day.activities
                if a.kind != ActivityKind.CHECK_OUT
            ]
            assert timed == sorted(timed), f"day {day.day_number} is out of order"

    def test_distances_between_stops_are_recorded(self, plan_and_days):
        _, _, _, days = plan_and_days
        stops = [
            a for day in days for a in day.activities if a.place is not None
        ]
        assert stops
        assert all(a.distance_from_previous_km is not None for a in stops)

    def test_entry_costs_scale_with_group_size(self, settings):
        goa = get_destination("north-goa")
        solo = make_request(travelers=1, days=3, budget_inr=400_000)
        group = make_request(travelers=4, days=3, budget_inr=400_000)
        solo_days = build_deterministic_days(
            destination=goa, plan=make_plan(goa, solo), request=solo,
            food_per_person_per_day=FOOD_RATE,
        )
        group_days = build_deterministic_days(
            destination=goa, plan=make_plan(goa, group), request=group,
            food_per_person_per_day=FOOD_RATE,
        )

        def entry(days):
            return sum(a.entry_cost_inr for d in days for a in d.activities)

        assert entry(group_days) == pytest.approx(entry(solo_days) * 4, rel=0.01)

    def test_outdoor_stops_offer_a_weather_alternative(self, plan_and_days):
        _, _, _, days = plan_and_days
        outdoor = [
            a
            for day in days
            for a in day.activities
            if a.place and a.place.category in {"beach", "viewpoint", "waterfall"}
        ]
        if outdoor:
            assert any(a.weather_alternative for a in outdoor)

    def test_relaxed_pace_inserts_rest_blocks(self, settings):
        goa = get_destination("north-goa")
        request = make_request(days=3, pace=Pace.RELAXED, budget_inr=400_000)
        days = build_deterministic_days(
            destination=goa, plan=make_plan(goa, request), request=request,
            food_per_person_per_day=FOOD_RATE,
        )
        assert any(
            a.kind == ActivityKind.REST for day in days for a in day.activities
        )

    def test_single_day_trip_produces_one_day(self, settings):
        goa = get_destination("north-goa")
        request = make_request(days=1, budget_inr=400_000)
        days = build_deterministic_days(
            destination=goa, plan=make_plan(goa, request), request=request,
            food_per_person_per_day=FOOD_RATE,
        )
        assert len(days) == 1


class TestItineraryWithoutLLM:
    def test_demo_mode_still_produces_a_full_itinerary(self, settings):
        goa = get_destination("north-goa")
        request = make_request(days=4, budget_inr=400_000)
        plan = make_plan(goa, request)
        cost = build_breakdown(request, goa, settings)
        result = asyncio.run(
            generate_itinerary(
                destination=goa, plan=plan, request=request, cost=cost,
                settings=settings, food_per_person_per_day=FOOD_RATE,
            )
        )
        assert result.itinerary.generated_by == "deterministic"
        assert not result.used_llm
        assert len(result.itinerary.days) == 4
        assert result.itinerary.activity_count > 0

    def test_itinerary_carries_the_reconciling_cost(self, settings):
        goa = get_destination("north-goa")
        request = make_request(days=4, budget_inr=400_000)
        cost = build_breakdown(request, goa, settings)
        result = asyncio.run(
            generate_itinerary(
                destination=goa, plan=make_plan(goa, request), request=request, cost=cost,
                settings=settings, food_per_person_per_day=FOOD_RATE,
            )
        )
        assert result.itinerary.cost.reconciles()
        assert result.itinerary.cost.total_inr == cost.total_inr

    def test_disclaimer_is_always_present(self, settings):
        goa = get_destination("north-goa")
        request = make_request(days=3, budget_inr=400_000)
        result = asyncio.run(
            generate_itinerary(
                destination=goa, plan=make_plan(goa, request), request=request,
                cost=build_breakdown(request, goa, settings), settings=settings,
                food_per_person_per_day=FOOD_RATE,
            )
        )
        assert "approximate" in result.itinerary.disclaimer.lower()


class TestLLMPrompt:
    def test_prompt_contains_only_real_activities(self, settings):
        goa = get_destination("north-goa")
        request = make_request(days=3, budget_inr=400_000)
        plan = make_plan(goa, request)
        prompt = build_user_prompt(destination=goa, plan=plan, request=request)
        for attraction in plan.selected:
            assert attraction.name in prompt

    def test_prompt_includes_the_clustering_hint(self, settings):
        goa = get_destination("north-goa")
        request = make_request(days=3, budget_inr=400_000)
        prompt = build_user_prompt(
            destination=goa, plan=make_plan(goa, request), request=request
        )
        assert "geographic_clustering_hint" in prompt

    def test_system_prompt_forbids_prices_and_invention(self):
        from app.services.itinerary import SYSTEM_PROMPT

        lowered = SYSTEM_PROMPT.lower()
        assert "do not invent" in lowered
        assert "do not output any prices" in lowered


class TestLLMContainment:
    """The model may shape the plan; it may not introduce facts or money."""

    @pytest.fixture
    def context(self, settings):
        goa = get_destination("north-goa")
        request = make_request(days=2, budget_inr=400_000)
        plan = make_plan(goa, request)
        return goa, request, plan

    def test_fabricated_prices_are_discarded(self, context):
        """A model that emits prices must not change any cost."""
        from app.services.itinerary import _merge_llm_days

        goa, request, plan = context
        first = plan.selected[0]
        payload = {
            "days": [
                {
                    "day_number": 1,
                    "title": "Day one",
                    "summary": "A day.",
                    "area_focus": "North",
                    "activities": [
                        {
                            "place_name": first.name,
                            "kind": "sightseeing",
                            "start_time": "09:00",
                            "end_time": "11:00",
                            # A model trying to assert a price in prose.
                            "description": "Entry is Rs 99,999 and includes a free helicopter.",
                            "tips": [],
                        }
                    ],
                    "notes": [],
                }
            ],
            "packing_notes": [],
            "local_tips": [],
        }
        days = _merge_llm_days(
            llm=LLMItinerary.model_validate(payload), destination=goa, plan=plan,
            request=request, food_per_person_per_day=FOOD_RATE,
        )
        activity = next(a for a in days[0].activities if a.place is not None)
        # The cost comes from our dataset, not from the model's sentence.
        assert activity.entry_cost_inr == pytest.approx(
            first.entry_fee_inr * request.travelers
        )

    def test_invented_places_cannot_become_priced_stops(self, context):
        """A hallucinated venue must not acquire coordinates or a fee."""
        from app.services.itinerary import _merge_llm_days

        goa, request, plan = context
        payload = {
            "days": [
                {
                    "day_number": 1,
                    "title": "Day one",
                    "summary": "A day.",
                    "area_focus": "North",
                    "activities": [
                        {
                            "place_name": "The Entirely Fictional Museum of Goa",
                            "kind": "sightseeing",
                            "start_time": "09:00",
                            "end_time": "11:00",
                            "description": "Does not exist.",
                            "tips": [],
                        }
                    ],
                    "notes": [],
                }
            ],
            "packing_notes": [],
            "local_tips": [],
        }
        days = _merge_llm_days(
            llm=LLMItinerary.model_validate(payload), destination=goa, plan=plan,
            request=request, food_per_person_per_day=FOOD_RATE,
        )
        fabricated = next(
            a for a in days[0].activities if "Fictional" in a.title
        )
        assert fabricated.place is None
        assert fabricated.entry_cost_inr == 0.0
        assert fabricated.transport_cost_inr == 0.0
        # Downgraded so it cannot masquerade as a real sight.
        assert fabricated.kind == ActivityKind.FREE_TIME

    def test_skipped_days_are_filled_in(self, context):
        from app.services.itinerary import _merge_llm_days

        goa, request, plan = context
        payload = {
            "days": [
                {
                    "day_number": 1,
                    "title": "Only day one",
                    "summary": "The model forgot day two.",
                    "area_focus": "North",
                    "activities": [
                        {
                            "place_name": plan.selected[0].name,
                            "kind": "sightseeing",
                            "start_time": "09:00",
                            "end_time": "11:00",
                            "description": "A stop.",
                            "tips": [],
                        }
                    ],
                    "notes": [],
                }
            ],
            "packing_notes": [],
            "local_tips": [],
        }
        days = _merge_llm_days(
            llm=LLMItinerary.model_validate(payload), destination=goa, plan=plan,
            request=request, food_per_person_per_day=FOOD_RATE,
        )
        assert [d.day_number for d in days] == [1, 2]

    def test_malformed_reply_is_rejected_by_the_schema(self):
        with pytest.raises(Exception):
            LLMItinerary.model_validate({"days": "not a list"})

    def test_bad_time_format_is_rejected(self):
        with pytest.raises(Exception):
            LLMItinerary.model_validate(
                {
                    "days": [
                        {
                            "day_number": 1,
                            "title": "t",
                            "summary": "s",
                            "area_focus": "a",
                            "activities": [
                                {
                                    "place_name": "x",
                                    "start_time": "9am",  # not HH:MM
                                    "end_time": "11:00",
                                    "description": "d",
                                }
                            ],
                        }
                    ]
                }
            )

    def test_llm_failure_falls_back_to_the_deterministic_plan(self, context, settings):
        """An unreachable model must degrade, not fail the request."""
        from app.providers import llm as llm_module

        goa, request, plan = context
        configured = Settings(
            anthropic_api_key="sk-ant-fake-for-test",
            enable_nominatim=False, enable_osrm=False,
            enable_overpass=False, enable_open_meteo=False,
        )
        assert configured.llm_enabled

        async def explode(**kwargs):
            raise llm_module.LLMUnavailable("provider is down")

        original = llm_module.complete_json
        import app.services.itinerary as itinerary_module

        itinerary_module.complete_json = explode
        try:
            result = asyncio.run(
                generate_itinerary(
                    destination=goa, plan=plan, request=request,
                    cost=build_breakdown(request, goa, configured),
                    settings=configured, food_per_person_per_day=FOOD_RATE,
                )
            )
        finally:
            itinerary_module.complete_json = original

        assert result.itinerary.generated_by == "deterministic"
        assert result.llm_error and "down" in result.llm_error
        assert len(result.itinerary.days) == request.days

    def test_valid_llm_reply_is_used_and_recosted(self, context, settings):
        """A well-behaved model shapes the plan; we still own the numbers."""
        goa, request, plan = context
        configured = Settings(
            anthropic_api_key="sk-ant-fake-for-test",
            enable_nominatim=False, enable_osrm=False,
            enable_overpass=False, enable_open_meteo=False,
        )
        names = [a.name for a in plan.selected]
        payload = {
            "days": [
                {
                    "day_number": day,
                    "title": f"Day {day}",
                    "summary": "Model-written summary.",
                    "area_focus": "Somewhere",
                    "activities": [
                        {
                            "place_name": name,
                            "kind": "sightseeing",
                            "start_time": "10:00",
                            "end_time": "12:00",
                            "description": "Model-written description.",
                            "tips": ["Model tip."],
                        }
                        for name in names[day - 1 :: 2]
                    ]
                    or [
                        {
                            "place_name": names[0],
                            "kind": "sightseeing",
                            "start_time": "10:00",
                            "end_time": "12:00",
                            "description": "d",
                            "tips": [],
                        }
                    ],
                    "notes": [],
                }
                for day in (1, 2)
            ],
            "packing_notes": ["Model packing note."],
            "local_tips": ["Model local tip."],
        }

        class FakeReply:
            data = payload

        async def fake_complete(**kwargs):
            return FakeReply()

        import app.services.itinerary as itinerary_module

        original = itinerary_module.complete_json
        itinerary_module.complete_json = fake_complete
        try:
            result = asyncio.run(
                generate_itinerary(
                    destination=goa, plan=plan, request=request,
                    cost=build_breakdown(request, goa, configured),
                    settings=configured, food_per_person_per_day=FOOD_RATE,
                )
            )
        finally:
            itinerary_module.complete_json = original

        assert result.used_llm
        assert result.itinerary.generated_by.startswith("llm:")
        # Model prose is kept...
        assert any(
            "Model-written" in a.description
            for day in result.itinerary.days
            for a in day.activities
        )
        # ...but every stop is one of ours, with our costs.
        for day in result.itinerary.days:
            for activity in day.activities:
                if activity.place is not None:
                    assert activity.place.name in names
        assert result.itinerary.cost.reconciles()
