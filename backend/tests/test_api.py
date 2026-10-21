"""API tests: contracts, validation, error handling and saved trips.

These run against the real app with providers disabled, which is also the
demo-mode path -- so they double as proof that the product works end to end
with no credentials at all.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

BASE = {
    "origin": {"coordinates": {"lat": 17.385, "lon": 78.486}, "label": "Hyderabad"},
    "budget_inr": 35_000,
    "travelers": 2,
    "days": 4,
    "travel_mode": "any",
    "accommodation": "budget_hotel",
    "pace": "balanced",
    "interests": ["beaches", "heritage"],
}


def body(**overrides) -> dict:
    payload = dict(BASE)
    payload["start_date"] = (date.today() + timedelta(days=45)).isoformat()
    payload.update(overrides)
    return payload


class TestHealthAndMeta:
    def test_health(self, api_client):
        response = api_client.get("/api/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_root_points_at_the_docs(self, api_client):
        assert api_client.get("/").status_code == 200

    def test_openapi_schema_builds(self, api_client):
        response = api_client.get("/openapi.json")
        assert response.status_code == 200
        assert "/api/recommendations" in response.json()["paths"]

    def test_config_reports_demo_mode_without_keys(self, api_client):
        payload = api_client.get("/api/meta/config").json()
        assert payload["demo_mode"] is True
        assert payload["demo_mode_note"]
        assert payload["destination_count"] > 30

    def test_config_never_returns_a_secret_shaped_value(self, api_client):
        raw = api_client.get("/api/meta/config").text.lower()
        for forbidden in ("api_key", "secret", "sk-ant", "aizasy", "password"):
            assert forbidden not in raw

    def test_options_lists_form_choices(self, api_client):
        payload = api_client.get("/api/meta/options").json()
        assert len(payload["travel_modes"]) == 5
        assert len(payload["interests"]) >= 15
        hostel = next(
            a for a in payload["accommodation_types"] if a["value"] == "hostel"
        )
        assert hostel["priced_per"] == "bed"

    def test_ranking_weights_are_published_and_sum_to_one(self, api_client):
        payload = api_client.get("/api/meta/ranking").json()
        assert payload["weights_sum"] == pytest.approx(1.0)
        assert len(payload["weights"]) >= 6

    def test_destination_catalogue_is_browsable(self, api_client):
        payload = api_client.get("/api/meta/destinations").json()
        assert payload["source"] == "curated"
        assert len(payload["destinations"]) > 30
        assert all("lat" in d and "lon" in d for d in payload["destinations"])


class TestRecommendations:
    def test_happy_path(self, api_client):
        response = api_client.post("/api/recommendations", json=body())
        assert response.status_code == 200
        payload = response.json()
        assert payload["recommendations"]
        assert payload["origin_resolved"]
        assert payload["ranking_weights"]

    def test_response_carries_a_data_disclaimer(self, api_client):
        payload = api_client.post("/api/recommendations", json=body()).json()
        assert "does not sell tickets" in payload["meta"]["data_disclaimer"]

    def test_every_total_reconciles_with_its_breakdown(self, api_client):
        payload = api_client.post("/api/recommendations", json=body()).json()
        for recommendation in payload["recommendations"]:
            cost = recommendation["cost"]
            assert sum(c["total_inr"] for c in cost["categories"]) == pytest.approx(
                cost["total_inr"], abs=0.05
            )
            assert sum(i["total_inr"] for i in cost["line_items"]) == pytest.approx(
                cost["subtotal_inr"], abs=0.05
            )

    def test_no_recommendation_exceeds_the_budget(self, api_client):
        payload = api_client.post("/api/recommendations", json=body()).json()
        for recommendation in payload["recommendations"]:
            assert recommendation["cost"]["total_inr"] <= 35_000

    def test_return_transport_is_included(self, api_client):
        payload = api_client.post("/api/recommendations", json=body()).json()
        for recommendation in payload["recommendations"]:
            assert recommendation["outbound"]["distance_km"] > 0
            assert recommendation["inbound"]["distance_km"] > 0
            assert recommendation["intercity_cost_inr"] > 0

    def test_every_cost_figure_is_labelled_with_provenance(self, api_client):
        payload = api_client.post("/api/recommendations", json=body()).json()
        for recommendation in payload["recommendations"]:
            for item in recommendation["cost"]["line_items"]:
                assert item["provenance"]["source"] in {
                    "live", "cached", "estimated", "curated", "sample"
                }
                assert item["provenance"]["confidence"] in {"high", "medium", "low"}

    def test_demo_mode_claims_no_live_data(self, api_client):
        payload = api_client.post("/api/recommendations", json=body()).json()
        for recommendation in payload["recommendations"]:
            assert recommendation["cost"]["live_data_share"] == 0.0
            for item in recommendation["cost"]["line_items"]:
                assert item["provenance"]["source"] != "live"

    def test_scores_reconcile_with_their_components(self, api_client):
        payload = api_client.post("/api/recommendations", json=body()).json()
        for recommendation in payload["recommendations"]:
            score = recommendation["score"]
            assert sum(c["weighted"] for c in score["components"]) == pytest.approx(
                score["total"], abs=0.002
            )

    def test_results_change_with_the_budget(self, api_client):
        lean = api_client.post("/api/recommendations", json=body(budget_inr=20_000)).json()
        rich = api_client.post("/api/recommendations", json=body(budget_inr=250_000)).json()
        assert [r["destination"]["id"] for r in lean["recommendations"]] != [
            r["destination"]["id"] for r in rich["recommendations"]
        ]

    def test_results_change_with_the_mode(self, api_client):
        by_train = api_client.post(
            "/api/recommendations", json=body(travel_mode="train", budget_inr=70_000)
        ).json()
        by_flight = api_client.post(
            "/api/recommendations", json=body(travel_mode="flight", budget_inr=70_000)
        ).json()
        assert all(
            r["outbound"]["mode"] == "train" for r in by_train["recommendations"]
        )
        assert all(
            r["outbound"]["mode"] == "flight" for r in by_flight["recommendations"]
        )

    def test_limit_is_respected(self, api_client):
        payload = api_client.post("/api/recommendations", json=body(limit=2)).json()
        assert len(payload["recommendations"]) <= 2

    def test_manual_origin_without_a_geocoder_is_a_clear_422(self, api_client):
        """No coordinates and no geocoder means we must say so, not guess."""
        payload = body()
        payload["origin"] = {"label": "Somewhere Unresolvable"}
        response = api_client.post("/api/recommendations", json=payload)
        assert response.status_code == 422
        assert "geocoder" in response.json()["detail"].lower() or "location" in response.json()["detail"].lower()

    def test_booking_links_are_present_and_labelled(self, api_client):
        payload = api_client.post(
            "/api/recommendations", json=body(travel_mode="train")
        ).json()
        links = payload["recommendations"][0]["outbound"]["booking_links"]
        assert links
        assert all("official" in link for link in links)


class TestShortfall:
    def test_impossible_budget_returns_200_with_an_explanation(self, api_client):
        response = api_client.post("/api/recommendations", json=body(budget_inr=9_500))
        assert response.status_code == 200
        payload = response.json()
        assert payload["recommendations"] == []
        assert payload["shortfall"] is not None
        assert payload["shortfall"]["additional_budget_required_inr"] > 0
        assert payload["shortfall"]["levers"]

    def test_rejections_are_explained(self, api_client):
        payload = api_client.post("/api/recommendations", json=body(budget_inr=15_000)).json()
        assert payload["rejected"]
        for rejection in payload["rejected"]:
            assert rejection["reason"]


class TestValidation:
    def test_missing_body_is_422(self, api_client):
        assert api_client.post("/api/recommendations", json={}).status_code == 422

    def test_empty_origin_is_422(self, api_client):
        payload = body()
        payload["origin"] = {}
        assert api_client.post("/api/recommendations", json=payload).status_code == 422

    def test_zero_budget_is_422(self, api_client):
        assert api_client.post(
            "/api/recommendations", json=body(budget_inr=0)
        ).status_code == 422

    def test_negative_budget_is_422(self, api_client):
        assert api_client.post(
            "/api/recommendations", json=body(budget_inr=-5000)
        ).status_code == 422

    def test_absurdly_low_budget_explains_the_arithmetic(self, api_client):
        response = api_client.post("/api/recommendations", json=body(budget_inr=100))
        assert response.status_code == 422
        assert "at least" in response.text.lower()

    def test_past_start_date_is_422(self, api_client):
        past = (date.today() - timedelta(days=10)).isoformat()
        assert api_client.post(
            "/api/recommendations", json=body(start_date=past)
        ).status_code == 422

    def test_end_before_start_is_422(self, api_client):
        payload = body(
            start_date=(date.today() + timedelta(days=30)).isoformat(),
            end_date=(date.today() + timedelta(days=20)).isoformat(),
        )
        assert api_client.post("/api/recommendations", json=payload).status_code == 422

    def test_end_date_redefines_days(self, api_client):
        payload = body(
            start_date=(date.today() + timedelta(days=30)).isoformat(),
            end_date=(date.today() + timedelta(days=34)).isoformat(),
            days=2,  # contradicts the dates; the dates must win
        )
        response = api_client.post("/api/recommendations", json=payload)
        assert response.status_code == 200
        assert response.json()["request"]["days"] == 5

    def test_zero_travelers_is_422(self, api_client):
        assert api_client.post(
            "/api/recommendations", json=body(travelers=0)
        ).status_code == 422

    def test_too_many_travelers_is_422(self, api_client):
        assert api_client.post(
            "/api/recommendations", json=body(travelers=500)
        ).status_code == 422

    def test_too_many_days_is_422(self, api_client):
        assert api_client.post("/api/recommendations", json=body(days=90)).status_code == 422

    def test_invalid_enum_is_422(self, api_client):
        assert api_client.post(
            "/api/recommendations", json=body(travel_mode="teleport")
        ).status_code == 422

    def test_invalid_interest_is_422(self, api_client):
        assert api_client.post(
            "/api/recommendations", json=body(interests=["time travel"])
        ).status_code == 422

    def test_out_of_range_coordinates_are_422(self, api_client):
        payload = body()
        payload["origin"] = {"coordinates": {"lat": 999, "lon": 999}}
        assert api_client.post("/api/recommendations", json=payload).status_code == 422

    def test_unknown_field_is_rejected(self, api_client):
        """`extra="forbid"` is what stops a client passing a destination."""
        assert api_client.post(
            "/api/recommendations", json=body(destination="Goa")
        ).status_code == 422


class TestGeoEndpoints:
    def test_reverse_without_a_geocoder_is_honest(self, api_client):
        response = api_client.get("/api/geo/reverse", params={"lat": 17.385, "lon": 78.486})
        assert response.status_code == 200
        payload = response.json()
        assert payload["geocoder_configured"] is False
        assert "17.385N" in payload["label"]
        assert payload["coordinates"]["lat"] == pytest.approx(17.385)

    def test_reverse_validates_coordinates(self, api_client):
        assert api_client.get(
            "/api/geo/reverse", params={"lat": 120, "lon": 78}
        ).status_code == 422

    def test_search_without_a_geocoder_returns_404_with_guidance(self, api_client):
        response = api_client.get("/api/geo/search", params={"q": "Hyderabad"})
        assert response.status_code == 404
        assert "GEOAPIFY_API_KEY" in response.json()["detail"]

    def test_search_rejects_a_too_short_query(self, api_client):
        assert api_client.get("/api/geo/search", params={"q": "a"}).status_code == 422


class TestItineraryEndpoint:
    def _first_destination(self, api_client) -> str:
        payload = api_client.post("/api/recommendations", json=body()).json()
        return payload["recommendations"][0]["destination"]["id"]

    def test_happy_path(self, api_client):
        destination_id = self._first_destination(api_client)
        response = api_client.post(
            "/api/itinerary", json={"destination_id": destination_id, "trip": body()}
        )
        assert response.status_code == 200
        itinerary = response.json()
        assert len(itinerary["days"]) == 4
        assert itinerary["generated_by"] == "deterministic"

    def test_itinerary_cost_reconciles(self, api_client):
        destination_id = self._first_destination(api_client)
        itinerary = api_client.post(
            "/api/itinerary", json={"destination_id": destination_id, "trip": body()}
        ).json()
        cost = itinerary["cost"]
        assert sum(c["total_inr"] for c in cost["categories"]) == pytest.approx(
            cost["total_inr"], abs=0.05
        )

    def test_days_are_geographically_grouped(self, api_client):
        destination_id = self._first_destination(api_client)
        itinerary = api_client.post(
            "/api/itinerary", json={"destination_id": destination_id, "trip": body()}
        ).json()
        for day in itinerary["days"]:
            assert day["area_focus"]
            # A sane day does not criss-cross a region.
            assert day["total_travel_km"] < 220

    def test_unknown_destination_is_404(self, api_client):
        response = api_client.post(
            "/api/itinerary", json={"destination_id": "atlantis", "trip": body()}
        )
        assert response.status_code == 404

    def test_excluding_an_activity_changes_the_plan_and_the_cost(self, api_client):
        destination_id = self._first_destination(api_client)
        first = api_client.post(
            "/api/itinerary", json={"destination_id": destination_id, "trip": body()}
        ).json()
        titles = [
            a["title"]
            for day in first["days"]
            for a in day["activities"]
            if a["place"] is not None
        ]
        assert titles
        dropped = titles[0]
        second = api_client.post(
            "/api/itinerary",
            json={
                "destination_id": destination_id,
                "trip": body(),
                "excluded_place_names": [dropped],
            },
        ).json()
        remaining = [
            a["title"]
            for day in second["days"]
            for a in day["activities"]
            if a["place"] is not None
        ]
        assert dropped not in remaining
        assert second["cost"]["total_inr"] != first["cost"]["total_inr"]

    def test_assumption_override_changes_the_itinerary_cost(self, api_client):
        destination_id = self._first_destination(api_client)
        base_cost = api_client.post(
            "/api/itinerary", json={"destination_id": destination_id, "trip": body()}
        ).json()["cost"]["total_inr"]
        pricey = api_client.post(
            "/api/itinerary",
            json={
                "destination_id": destination_id,
                "trip": body(assumption_overrides={"food_per_person_per_day": 2000}),
            },
        ).json()["cost"]["total_inr"]
        assert pricey > base_cost


class TestSavedTrips:
    def _itinerary(self, api_client):
        recommendations = api_client.post("/api/recommendations", json=body()).json()
        top = recommendations["recommendations"][0]
        itinerary = api_client.post(
            "/api/itinerary",
            json={"destination_id": top["destination"]["id"], "trip": body()},
        ).json()
        return top, itinerary

    def test_save_list_get_delete(self, api_client, owner_headers):
        top, itinerary = self._itinerary(api_client)
        payload = {
            "title": "Long weekend",
            "destination_id": top["destination"]["id"],
            "destination_name": top["destination"]["name"],
            "trip": body(),
            "itinerary": itinerary,
            "notes": "Booked leave already.",
        }
        created = api_client.post("/api/trips", json=payload, headers=owner_headers)
        assert created.status_code == 201
        trip_id = created.json()["id"]

        listed = api_client.get("/api/trips", headers=owner_headers)
        assert listed.status_code == 200
        assert any(t["id"] == trip_id for t in listed.json())

        fetched = api_client.get(f"/api/trips/{trip_id}", headers=owner_headers)
        assert fetched.status_code == 200
        assert fetched.json()["snapshot"]["days"]
        assert fetched.json()["request"]["budget_inr"] == 35_000

        deleted = api_client.delete(f"/api/trips/{trip_id}", headers=owner_headers)
        assert deleted.status_code == 204
        assert api_client.get(
            f"/api/trips/{trip_id}", headers=owner_headers
        ).status_code == 404

    def test_owner_token_is_required(self, api_client):
        assert api_client.get("/api/trips").status_code == 400

    def test_short_owner_token_is_rejected(self, api_client):
        assert api_client.get(
            "/api/trips", headers={"X-TripWise-Owner": "abc"}
        ).status_code == 400

    def test_trips_are_scoped_to_their_owner(self, api_client, owner_headers):
        top, itinerary = self._itinerary(api_client)
        created = api_client.post(
            "/api/trips",
            json={
                "title": "Mine",
                "destination_id": top["destination"]["id"],
                "destination_name": top["destination"]["name"],
                "trip": body(),
                "itinerary": itinerary,
            },
            headers=owner_headers,
        )
        trip_id = created.json()["id"]
        other = {"X-TripWise-Owner": "someoneelse0123456789"}
        assert api_client.get("/api/trips", headers=other).json() == []
        # Another owner must not be able to read or delete it.
        assert api_client.get(f"/api/trips/{trip_id}", headers=other).status_code == 404
        assert api_client.delete(f"/api/trips/{trip_id}", headers=other).status_code == 404

    def test_saved_total_matches_the_itinerary(self, api_client, owner_headers):
        top, itinerary = self._itinerary(api_client)
        created = api_client.post(
            "/api/trips",
            json={
                "title": "Check totals",
                "destination_id": top["destination"]["id"],
                "destination_name": top["destination"]["name"],
                "trip": body(),
                "itinerary": itinerary,
            },
            headers=owner_headers,
        ).json()
        assert created["total_estimate_inr"] == pytest.approx(itinerary["cost"]["total_inr"])
        assert created["per_traveler_inr"] == pytest.approx(
            itinerary["cost"]["per_traveler_inr"]
        )

    def test_deleting_a_missing_trip_is_404(self, api_client, owner_headers):
        assert api_client.delete(
            "/api/trips/does-not-exist", headers=owner_headers
        ).status_code == 404


class TestErrorHandling:
    def test_unknown_route_is_404(self, api_client):
        assert api_client.get("/api/nope").status_code == 404

    def test_wrong_method_is_405(self, api_client):
        assert api_client.get("/api/recommendations").status_code == 405

    def test_cors_headers_are_scoped_not_wildcard(self, api_client):
        response = api_client.options(
            "/api/recommendations",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
            },
        )
        allowed = response.headers.get("access-control-allow-origin")
        assert allowed != "*"
