"""Provider-layer tests: secret hygiene, caching, retries, circuit breaking
and the fallback chain.

No test here touches the network. Provider behaviour is exercised against a
fake transport so the failure paths are actually covered rather than assumed.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.config import Settings
from app.providers.cache import MemoryCache, get_cache, reset_cache
from app.providers.http import (
    CircuitBreaker,
    ProviderClient,
    ProviderError,
    RateLimiter,
    redact_url,
)


class TestSecretHygiene:
    @pytest.mark.parametrize(
        "url",
        [
            "https://maps.googleapis.com/maps/api/geocode/json?address=Goa&key=AIzaSyREALKEY",
            "https://places.googleapis.com/v1/places:searchNearby?key=realkey123",
            "https://example.com/x?access_token=abc&token=def&api_key=ghi",
        ],
    )
    def test_credentials_never_appear_in_log_urls(self, url):
        redacted = redact_url(url)
        for secret in ("AIzaSyREALKEY", "realkey123", "abc", "def", "ghi"):
            assert secret not in redacted
        assert "REDACTED" in redacted

    def test_non_secret_params_are_preserved(self):
        redacted = redact_url("https://x.test/y?address=Goa&region=in&key=secret")
        assert "address=Goa" in redacted
        assert "region=in" in redacted

    def test_urls_without_query_are_untouched(self):
        assert redact_url("https://x.test/a/b") == "https://x.test/a/b"

    def test_provider_status_never_leaks_keys(self):
        settings = Settings(
            google_maps_server_key="super-secret-server-key",
            google_maps_browser_key="browser-secret-key",
            openai_api_key="sk-proj-secret",
        )
        serialised = repr(settings.provider_status())
        for secret in (
            "super-secret-server-key", "browser-secret-key", "sk-proj-secret"
        ):
            assert secret not in serialised

    def test_provider_status_reports_capability_not_credentials(self):
        settings = Settings(google_maps_server_key="k", openai_api_key="sk-proj-x")
        status = settings.provider_status()
        # Google leads, and keyless Nominatim stays on as a fallback.
        assert status["geocoding"] == ["google", "nominatim"]
        assert status["llm"]["enabled"] is True
        assert status["demo_mode"] is False

    def test_keyless_fallbacks_can_be_turned_off(self):
        """Production deployments may not want to depend on fair-use tiers."""
        settings = Settings(google_maps_server_key="k", enable_nominatim=False)
        assert settings.geocoding_providers == ["google"]


class TestDemoModeDetection:
    def test_no_keys_means_demo_mode(self):
        settings = Settings(
            enable_nominatim=False, enable_osrm=False,
            enable_overpass=False, enable_open_meteo=False,
        )
        assert settings.demo_mode

    def test_keyless_osm_does_not_leave_demo_mode(self):
        """Free OSM services do not count as a configured deployment."""
        settings = Settings(enable_nominatim=True, enable_osrm=True)
        assert settings.demo_mode
        assert settings.geocoding_providers == ["nominatim"]

    def test_any_credential_leaves_demo_mode(self):
        assert not Settings(google_maps_server_key="x").demo_mode
        assert not Settings(openai_api_key="x").demo_mode

    def test_google_leads_the_chain_with_free_fallback_behind(self):
        """One paid provider per capability, one free fallback behind it."""
        settings = Settings(google_maps_server_key="g", enable_nominatim=True)
        assert settings.geocoding_providers == ["google", "nominatim"]
        assert settings.routing_providers == ["google", "osrm"]
        assert settings.places_providers == ["google", "overpass"]

    def test_openai_needs_only_a_key(self):
        """The model ID is resolved from the API when it is not pinned."""
        assert Settings(openai_api_key="k").active_llm_provider == "openai"
        assert Settings(openai_api_key="k", openai_model="gpt-x").active_llm_provider == "openai"

    def test_llm_can_be_disabled_explicitly(self):
        assert Settings(openai_api_key="k", enable_llm=False).active_llm_provider is None

    def test_no_fare_or_rate_provider_is_claimed(self):
        """Flights and hotels are always modelled, so nothing may claim live."""
        status = Settings(google_maps_server_key="g", openai_api_key="k").provider_status()
        assert status["flights"] == []
        assert status["hotels"] == []


class TestMemoryCache:
    def test_round_trip(self):
        cache = MemoryCache()
        cache.set("k", {"v": 1}, 60)
        assert cache.get("k") == {"v": 1}

    def test_expiry(self):
        cache = MemoryCache()
        cache.set("k", 1, -1)
        assert cache.get("k") is None

    def test_lru_eviction(self):
        cache = MemoryCache(max_entries=2)
        cache.set("a", 1, 60)
        cache.set("b", 2, 60)
        cache.set("c", 3, 60)
        assert cache.get("a") is None
        assert cache.get("c") == 3

    def test_access_refreshes_recency(self):
        cache = MemoryCache(max_entries=2)
        cache.set("a", 1, 60)
        cache.set("b", 2, 60)
        cache.get("a")           # a is now most recent
        cache.set("c", 3, 60)    # evicts b
        assert cache.get("a") == 1
        assert cache.get("b") is None

    def test_stats_track_hits_and_misses(self):
        cache = MemoryCache()
        cache.set("a", 1, 60)
        cache.get("a")
        cache.get("missing")
        stats = cache.stats()
        assert stats["hits"] == 1 and stats["misses"] == 1

    def test_singleton_is_reusable_and_resettable(self):
        reset_cache()
        first = get_cache()
        assert get_cache() is first
        reset_cache()
        assert get_cache() is not first


class TestCircuitBreaker:
    def test_closed_until_the_threshold(self):
        breaker = CircuitBreaker(failure_threshold=3)
        breaker.record_failure()
        assert not breaker.is_open
        breaker.record_failure()
        assert not breaker.is_open
        breaker.record_failure()
        assert breaker.is_open

    def test_success_resets(self):
        breaker = CircuitBreaker(failure_threshold=2)
        breaker.record_failure()
        breaker.record_success()
        breaker.record_failure()
        assert not breaker.is_open

    def test_decisive_failure_opens_immediately(self):
        """A connect-level failure should not need a second attempt."""
        breaker = CircuitBreaker(failure_threshold=5)
        breaker.record_failure(decisive=True)
        assert breaker.is_open

    def test_cooldown_allows_a_probe(self):
        breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=0.0)
        breaker.record_failure()
        assert not breaker.is_open  # cooldown already elapsed


def fake_client(handler: httpx.MockTransport, **kwargs) -> ProviderClient:
    """A ProviderClient whose transport is a scripted fake."""
    client = ProviderClient(timeout_seconds=1.0, backoff_base_seconds=0.0, **kwargs)
    client._client = httpx.AsyncClient(transport=handler)  # noqa: SLF001 -- test seam
    return client


class TestRetryBehaviour:
    def test_success_returns_parsed_json(self):
        def handle(request):
            return httpx.Response(200, json={"ok": True})

        client = fake_client(httpx.MockTransport(handle))
        result = asyncio.run(client.get_json("fake", "https://fake.test/x"))
        assert result == {"ok": True}
        asyncio.run(client.close())

    def test_retries_then_succeeds_on_a_500(self):
        calls = {"n": 0}

        def handle(request):
            calls["n"] += 1
            if calls["n"] < 3:
                return httpx.Response(503)
            return httpx.Response(200, json={"ok": True})

        client = fake_client(httpx.MockTransport(handle), max_retries=2)
        assert asyncio.run(client.get_json("fake", "https://fake.test/x")) == {"ok": True}
        assert calls["n"] == 3
        asyncio.run(client.close())

    def test_does_not_retry_a_400(self):
        """A bad request will not fix itself, and retrying burns quota."""
        calls = {"n": 0}

        def handle(request):
            calls["n"] += 1
            return httpx.Response(400, json={"error": "bad"})

        client = fake_client(httpx.MockTransport(handle), max_retries=3)
        with pytest.raises(ProviderError) as exc:
            asyncio.run(client.get_json("fake", "https://fake.test/x"))
        assert calls["n"] == 1
        assert exc.value.status_code == 400
        asyncio.run(client.close())

    def test_retries_a_429(self):
        calls = {"n": 0}

        def handle(request):
            calls["n"] += 1
            return httpx.Response(429)

        client = fake_client(httpx.MockTransport(handle), max_retries=2)
        with pytest.raises(ProviderError):
            asyncio.run(client.get_json("fake", "https://fake.test/x"))
        assert calls["n"] == 3
        asyncio.run(client.close())

    def test_exhausted_retries_raise_a_safe_error(self):
        def handle(request):
            raise httpx.ConnectError("no route")

        client = fake_client(httpx.MockTransport(handle), max_retries=1)
        with pytest.raises(ProviderError) as exc:
            asyncio.run(client.get_json("fake", "https://fake.test/x"))
        assert "fake" in str(exc.value)
        asyncio.run(client.close())

    def test_invalid_json_is_an_error_not_a_crash(self):
        def handle(request):
            return httpx.Response(200, text="<html>not json</html>")

        client = fake_client(httpx.MockTransport(handle))
        with pytest.raises(ProviderError):
            asyncio.run(client.get_json("fake", "https://fake.test/x"))
        asyncio.run(client.close())

    def test_circuit_opens_after_repeated_transport_failures(self):
        calls = {"n": 0}

        def handle(request):
            calls["n"] += 1
            raise httpx.ConnectError("down")

        client = fake_client(httpx.MockTransport(handle), max_retries=1)
        with pytest.raises(ProviderError):
            asyncio.run(client.get_json("fake", "https://dead.test/x"))
        attempts_after_first = calls["n"]

        # Second call must short-circuit without touching the transport.
        with pytest.raises(ProviderError) as exc:
            asyncio.run(client.get_json("fake", "https://dead.test/x"))
        assert calls["n"] == attempts_after_first
        assert "skipped" in str(exc.value)
        assert "dead.test" in client.open_circuits()
        asyncio.run(client.close())


class TestRateLimiter:
    def test_spaces_calls_apart(self):
        limiter = RateLimiter(min_interval_seconds=0.05)

        async def run():
            import time

            start = time.monotonic()
            await limiter.acquire()
            await limiter.acquire()
            await limiter.acquire()
            return time.monotonic() - start

        assert asyncio.run(run()) >= 0.09

    def test_keyless_hosts_are_throttled_by_default(self):
        from app.providers.http import get_client, shutdown_client

        asyncio.run(shutdown_client())
        client = get_client()
        assert "nominatim.openstreetmap.org" in client._limiters  # noqa: SLF001
        assert "overpass-api.de" in client._limiters  # noqa: SLF001
        asyncio.run(shutdown_client())

    def test_nominatim_user_agent_carries_contact(self):
        from app.providers.http import get_client, shutdown_client

        asyncio.run(shutdown_client())
        client = get_client(contact_email="ops@example.com")
        assert "ops@example.com" in client._user_agent  # noqa: SLF001
        asyncio.run(shutdown_client())


class TestGeocodingFallback:
    def test_falls_through_to_the_next_provider(self):
        """Google failing must not stop the free geocoder from answering."""
        from app.providers import geocoding

        def handle(request):
            if "googleapis" in str(request.url):
                return httpx.Response(500)
            return httpx.Response(
                200,
                json=[
                    {
                        "lat": "15.6",
                        "lon": "73.75",
                        "display_name": "North Goa, Goa, India",
                        "address": {"state": "Goa", "country": "India"},
                    }
                ],
            )

        settings = Settings(google_maps_server_key="g", enable_nominatim=True)
        client = fake_client(httpx.MockTransport(handle), max_retries=0)
        cache = MemoryCache()
        failures: list[str] = []
        result = asyncio.run(
            geocoding.geocode(
                "North Goa", settings=settings, client=client, cache=cache, failures=failures
            )
        )
        assert result is not None
        assert result.coordinates.lat == pytest.approx(15.6)
        assert any("google" in f for f in failures)
        asyncio.run(client.close())

    def test_all_providers_failing_returns_none(self):
        from app.providers import geocoding

        def handle(request):
            return httpx.Response(500)

        settings = Settings(google_maps_server_key="g", enable_nominatim=True)
        client = fake_client(httpx.MockTransport(handle), max_retries=0)
        result = asyncio.run(
            geocoding.geocode(
                "Nowhere", settings=settings, client=client, cache=MemoryCache(), failures=[]
            )
        )
        assert result is None
        asyncio.run(client.close())

    def test_results_are_cached(self):
        from app.providers import geocoding

        calls = {"n": 0}

        def handle(request):
            calls["n"] += 1
            return httpx.Response(
                200,
                json=[{"lat": "15.6", "lon": "73.75", "display_name": "Goa", "address": {}}],
            )

        settings = Settings(enable_nominatim=True)
        client = fake_client(httpx.MockTransport(handle))
        cache = MemoryCache()
        for _ in range(3):
            asyncio.run(
                geocoding.geocode(
                    "Goa", settings=settings, client=client, cache=cache, failures=[]
                )
            )
        assert calls["n"] == 1, "repeat lookups must be served from cache"
        asyncio.run(client.close())

    def test_cached_result_is_labelled_cached(self):
        from app.providers import geocoding
        from app.schemas.enums import DataSource

        def handle(request):
            return httpx.Response(
                200,
                json=[{"lat": "15.6", "lon": "73.75", "display_name": "Goa", "address": {}}],
            )

        settings = Settings(enable_nominatim=True)
        client = fake_client(httpx.MockTransport(handle))
        cache = MemoryCache()
        first = asyncio.run(
            geocoding.geocode("Goa", settings=settings, client=client, cache=cache, failures=[])
        )
        second = asyncio.run(
            geocoding.geocode("Goa", settings=settings, client=client, cache=cache, failures=[])
        )
        assert first.provenance.source == DataSource.LIVE
        assert second.provenance.source == DataSource.CACHED
        asyncio.run(client.close())

    def test_no_geocoder_configured_returns_none(self):
        from app.providers import geocoding

        settings = Settings(enable_nominatim=False)
        assert settings.geocoding_providers == []
        result = asyncio.run(
            geocoding.geocode(
                "Goa",
                settings=settings,
                client=fake_client(httpx.MockTransport(lambda r: httpx.Response(200, json={}))),
                cache=MemoryCache(),
                failures=[],
            )
        )
        assert result is None

    def test_coordinate_description_is_honest(self):
        from app.providers.geocoding import describe_coordinates

        assert describe_coordinates(17.385, 78.486) == "17.385N, 78.486E"


class TestRoutingFallback:
    def test_osrm_response_is_parsed_and_labelled_live(self):
        from app.providers import routing

        def handle(request):
            return httpx.Response(
                200,
                json={
                    "code": "Ok",
                    "routes": [{"distance": 611_000, "duration": 37_800}],
                },
            )

        settings = Settings(enable_osrm=True)
        client = fake_client(httpx.MockTransport(handle))
        result = asyncio.run(
            routing.road_route(
                17.385, 78.486, 15.6, 73.75,
                settings=settings, client=client, cache=MemoryCache(), failures=[],
            )
        )
        assert result.road_km == pytest.approx(611.0)
        assert result.road_hours == pytest.approx(10.5, abs=0.01)
        assert result.provenance.is_live
        asyncio.run(client.close())

    def test_failure_returns_none_so_the_estimate_survives(self):
        from app.providers import routing

        def handle(request):
            raise httpx.ConnectError("blocked")

        settings = Settings(enable_osrm=True)
        client = fake_client(httpx.MockTransport(handle), max_retries=0)
        failures: list[str] = []
        result = asyncio.run(
            routing.road_route(
                17.385, 78.486, 15.6, 73.75,
                settings=settings, client=client, cache=MemoryCache(), failures=failures,
            )
        )
        assert result is None
        assert failures
        asyncio.run(client.close())


class TestPlacesProvider:
    def test_overpass_results_are_mapped_without_inventing_fields(self):
        from app.providers import places

        def handle(request):
            return httpx.Response(
                200,
                json={
                    "elements": [
                        {
                            "lat": 15.55,
                            "lon": 73.75,
                            "tags": {
                                "name": "Baga Beach",
                                "natural": "beach",
                                "opening_hours": "24/7",
                            },
                        },
                        {
                            "lat": 15.49,
                            "lon": 73.77,
                            # No name -- must be dropped, not given a placeholder.
                            "tags": {"historic": "fort"},
                        },
                    ]
                },
            )

        settings = Settings(enable_overpass=True)
        client = fake_client(httpx.MockTransport(handle))
        results = asyncio.run(
            places.nearby_places(
                15.5, 73.75, settings=settings, client=client, cache=MemoryCache(), failures=[]
            )
        )
        assert len(results) == 1
        place = results[0]
        assert place.name == "Baga Beach"
        assert place.category == "beach"
        assert place.opening_hours == "24/7"
        # OSM carries no admission price here, so the field stays empty.
        assert place.entry_fee_inr is None
        assert place.rating is None
        asyncio.run(client.close())

    def test_google_places_leaves_entry_fee_empty(self):
        from app.providers import places

        def handle(request):
            return httpx.Response(
                200,
                json={
                    "places": [
                        {
                            "displayName": {"text": "Fort Aguada"},
                            "location": {"latitude": 15.49, "longitude": 73.77},
                            "types": ["historical_landmark"],
                            "rating": 4.3,
                        }
                    ]
                },
            )

        settings = Settings(google_maps_server_key="g")
        client = fake_client(httpx.MockTransport(handle))
        results = asyncio.run(
            places.nearby_places(
                15.5, 73.75, settings=settings, client=client, cache=MemoryCache(), failures=[]
            )
        )
        assert results[0].rating == 4.3
        assert results[0].entry_fee_inr is None  # Google does not publish fees
        asyncio.run(client.close())


class TestLLMJsonExtraction:
    def test_plain_json(self):
        from app.providers.llm import extract_json

        assert extract_json('{"a": 1}') == {"a": 1}

    def test_fenced_json(self):
        from app.providers.llm import extract_json

        assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}

    def test_json_wrapped_in_prose(self):
        from app.providers.llm import extract_json

        assert extract_json('Sure!\n{"a": 1}\nDone.') == {"a": 1}

    def test_no_json_raises(self):
        from app.providers.llm import extract_json

        with pytest.raises(ValueError):
            extract_json("I cannot help with that.")

    def test_non_object_json_raises(self):
        from app.providers.llm import extract_json

        with pytest.raises(ValueError):
            extract_json("[1, 2, 3]")

    def test_unconfigured_llm_raises_a_clear_error(self):
        from app.providers.llm import LLMUnavailable, complete_json

        settings = Settings()
        with pytest.raises(LLMUnavailable) as exc:
            asyncio.run(
                complete_json(system_prompt="s", user_prompt="u", settings=settings)
            )
        assert "OPENAI_API_KEY" in str(exc.value)
        assert "deterministic planner" in str(exc.value)


class TestWeatherScoring:
    def test_comfortable_weather_scores_high(self):
        from app.providers.weather import _score_climate

        score, label, _ = _score_climate(29.0, 19.0, 0.5, 11)
        assert score == 1.0
        assert label == "Good weather"

    def test_extreme_heat_is_penalised(self):
        from app.providers.weather import _score_climate

        hot, _, note = _score_climate(44.0, 29.0, 0.0, 5)
        mild, _, _ = _score_climate(29.0, 19.0, 0.0, 5)
        assert hot < mild
        assert "44C" in note

    def test_heavy_rain_is_penalised(self):
        from app.providers.weather import _score_climate

        wet, _, note = _score_climate(28.0, 23.0, 30.0, 7)
        dry, _, _ = _score_climate(28.0, 23.0, 1.0, 7)
        assert wet < dry
        assert "rain" in note

    def test_month_end_handles_leap_years(self):
        from app.providers.weather import _month_end

        assert _month_end(2024, 2).day == 29
        assert _month_end(2025, 2).day == 28
        assert _month_end(2025, 12).day == 31


class TestCredentialSlots:
    """An unfilled key slot must behave exactly like an absent key.

    This is the state a `.env` spends most of its life in: half the slots
    filled, half still empty or holding a placeholder. If a blank slot made
    the app report a provider as configured, every call would fail with a 401
    and the UI would stop saying "demo mode" -- the most confusing possible
    outcome for someone setting keys up.
    """

    @pytest.mark.parametrize(
        "raw",
        ["", "   ", "\t", "\n", None],
    )
    def test_blank_slots_are_treated_as_absent(self, raw):
        settings = Settings(google_maps_server_key=raw, enable_nominatim=False)
        assert settings.google_maps_server_key is None
        assert settings.geocoding_providers == []
        assert settings.demo_mode

    @pytest.mark.parametrize(
        "placeholder",
        ["your-key-here", "YOUR-KEY-HERE", "changeme", "replace-me", "TODO", "<paste key>"],
    )
    def test_placeholders_are_treated_as_absent(self, placeholder):
        settings = Settings(google_maps_server_key=placeholder, enable_nominatim=False)
        assert settings.google_maps_server_key is None
        assert settings.demo_mode, "a placeholder must not switch off demo mode"

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("abc123", "abc123"),
            ("  abc123  ", "abc123"),       # stray whitespace from a paste
            ('"abc123"', "abc123"),          # dotenv keeps the quotes
            ("'abc123'", "abc123"),
            ('  "abc123"  ', "abc123"),
        ],
    )
    def test_real_keys_are_cleaned_not_mangled(self, raw, expected):
        """A key pasted with quotes or spaces must still work."""
        settings = Settings(google_maps_server_key=raw)
        assert settings.google_maps_server_key == expected
        assert "google" in settings.geocoding_providers
        assert not settings.demo_mode

    def test_cleaning_applies_to_every_credential_field(self):
        blank = '  "" '
        settings = Settings(
            openai_api_key="your-key-here",
            google_maps_server_key='""',
            google_maps_browser_key="<your browser key>",
            osm_contact_email=blank,
            enable_nominatim=False,
            enable_osrm=False,
            enable_overpass=False,
            enable_open_meteo=False,
            enable_wikimedia_images=False,
        )
        for field in (
            "openai_api_key",
            "google_maps_server_key",
            "google_maps_browser_key",
            "osm_contact_email",
        ):
            assert getattr(settings, field) is None, f"{field} was not cleaned"
        assert settings.demo_mode
        assert settings.active_llm_provider is None

    def test_blank_model_slot_still_yields_a_usable_config(self):
        """The model ID is optional: it is resolved from the API when blank."""
        assert Settings(openai_api_key="sk-x", openai_model="  ").openai_model == ""
        assert Settings(openai_api_key="sk-x", openai_model="  ").active_llm_provider == "openai"
        assert (
            Settings(openai_api_key="sk-x", openai_model="your-key-here").openai_model == ""
        )
        assert Settings(openai_api_key="sk-x", openai_model="gpt-x").openai_model == "gpt-x"

    def test_a_half_filled_file_enables_only_what_is_filled(self):
        """The realistic mid-setup state: one key in, the rest still blank."""
        settings = Settings(
            google_maps_server_key="real-google-key",
            openai_api_key="",
            google_maps_browser_key="your-key-here",
            enable_nominatim=False,
        )
        assert settings.geocoding_providers == ["google"]
        assert settings.google_maps_browser_key is None
        assert settings.active_llm_provider is None
        assert not settings.demo_mode  # one real provider is configured

    def test_status_never_echoes_a_credential_even_when_set(self):
        settings = Settings(
            google_maps_server_key="super-secret-value",
            openai_api_key="sk-proj-secret-value",
        )
        assert "super-secret-value" not in repr(settings.provider_status())
        assert "sk-proj-secret-value" not in repr(settings.provider_status())
