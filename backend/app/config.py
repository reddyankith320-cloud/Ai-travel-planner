"""Application configuration.

Every secret is read from the environment -- never from code and never sent to
the browser. See `.env.example` for the full list of variables.

`Settings.provider_status()` is what the rest of the app consults to decide
whether a live provider can be called or whether it must fall back to a
deterministic estimate. That decision is always made server-side.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "staging", "production"]


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables / `.env`."""

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- Core -------------------------------------------------------------
    app_name: str = "TripWise API"
    environment: Environment = "development"
    log_level: str = "INFO"
    api_prefix: str = "/api"

    # Comma-separated list of origins allowed to call this API.
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # ---- Storage ----------------------------------------------------------
    # Defaults to a local SQLite file so the app runs with zero infrastructure.
    database_url: str = "sqlite+pysqlite:///./tripwise.db"
    redis_url: str | None = None

    # ---- LLM (server-side only) ------------------------------------------
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-5-5"
    openai_api_key: str | None = None
    # Deliberately empty. OpenAI model IDs change often enough that a
    # hardcoded default would silently break; set OPENAI_MODEL to a current ID
    # from `GET https://api.openai.com/v1/models`. Anthropic IDs are stable
    # enough to default.
    openai_model: str = ""
    llm_provider: Literal["auto", "anthropic", "openai", "none"] = "auto"
    llm_timeout_seconds: float = 45.0
    llm_max_output_tokens: int = 8000

    # ---- Geocoding / routing / places ------------------------------------
    google_maps_server_key: str | None = None
    google_maps_browser_key: str | None = None
    geoapify_api_key: str | None = None
    locationiq_api_key: str | None = None
    openrouteservice_api_key: str | None = None
    foursquare_api_key: str | None = None

    # Keyless OSM-based services. Fair-use only: rate limited and cached.
    # Set to False in production unless you self-host or have permission.
    enable_nominatim: bool = True
    enable_osrm: bool = True
    enable_overpass: bool = True
    enable_open_meteo: bool = True
    # Destination photography from Wikipedia/Wikimedia. No key required, and
    # the images are freely licensed -- but attribution is mandatory and the
    # UI renders it. Turn this off to ship without any external images.
    enable_wikimedia_images: bool = True
    nominatim_base_url: str = "https://nominatim.openstreetmap.org"
    osrm_base_url: str = "https://router.project-osrm.org"
    overpass_base_url: str = "https://overpass-api.de/api/interpreter"
    open_meteo_base_url: str = "https://archive-api.open-meteo.com/v1"
    # Required by the Nominatim usage policy; identifies this deployment.
    osm_contact_email: str | None = None

    # ---- Transport / accommodation providers -----------------------------
    amadeus_client_id: str | None = None
    amadeus_client_secret: str | None = None
    amadeus_environment: Literal["test", "production"] = "test"
    hotel_provider: Literal["none", "amadeus"] = "none"
    flight_provider: Literal["none", "amadeus"] = "none"

    # ---- Network behaviour -----------------------------------------------
    http_timeout_seconds: float = 8.0
    http_max_retries: int = 2
    http_backoff_base_seconds: float = 0.4
    cache_ttl_seconds: int = 21_600  # 6h -- within typical provider caching terms
    cache_max_entries: int = 5_000

    # ---- Rate limiting (per client IP) -----------------------------------
    rate_limit_requests: int = 60
    rate_limit_window_seconds: int = 60

    # ---- Cost model knobs (see services/cost_model.py) -------------------
    contingency_rate: float = Field(0.08, ge=0.0, le=0.5)
    stretch_budget_tolerance: float = Field(0.15, ge=0.0, le=0.5)
    default_currency: str = "INR"

    @field_validator("cors_origins")
    @classmethod
    def _strip_origins(cls, value: str) -> str:
        return ",".join(part.strip() for part in value.split(",") if part.strip())

    # ---- Derived helpers -------------------------------------------------
    @property
    def cors_origin_list(self) -> list[str]:
        return [o for o in self.cors_origins.split(",") if o]

    @property
    def llm_enabled(self) -> bool:
        return self.active_llm_provider is not None

    @property
    def active_llm_provider(self) -> str | None:
        """Which LLM provider will actually be used, if any."""
        if self.llm_provider == "none":
            return None
        if self.llm_provider == "anthropic":
            return "anthropic" if self.anthropic_api_key else None
        if self.llm_provider == "openai":
            return "openai" if (self.openai_api_key and self.openai_model) else None
        # auto
        if self.anthropic_api_key:
            return "anthropic"
        if self.openai_api_key and self.openai_model:
            return "openai"
        return None

    @property
    def geocoding_providers(self) -> list[str]:
        """Geocoders in priority order."""
        chain: list[str] = []
        if self.google_maps_server_key:
            chain.append("google")
        if self.geoapify_api_key:
            chain.append("geoapify")
        if self.locationiq_api_key:
            chain.append("locationiq")
        if self.enable_nominatim:
            chain.append("nominatim")
        return chain

    @property
    def routing_providers(self) -> list[str]:
        chain: list[str] = []
        if self.google_maps_server_key:
            chain.append("google")
        if self.openrouteservice_api_key:
            chain.append("openrouteservice")
        if self.geoapify_api_key:
            chain.append("geoapify")
        if self.enable_osrm:
            chain.append("osrm")
        return chain

    @property
    def places_providers(self) -> list[str]:
        chain: list[str] = []
        if self.google_maps_server_key:
            chain.append("google")
        if self.foursquare_api_key:
            chain.append("foursquare")
        if self.geoapify_api_key:
            chain.append("geoapify")
        if self.enable_overpass:
            chain.append("overpass")
        return chain

    @property
    def demo_mode(self) -> bool:
        """True when no credentialed provider is configured at all.

        Demo mode is fully functional: every number is produced by the
        deterministic cost model and labelled as an estimate. Nothing is
        presented as live data.
        """
        return not any(
            [
                self.google_maps_server_key,
                self.geoapify_api_key,
                self.locationiq_api_key,
                self.openrouteservice_api_key,
                self.foursquare_api_key,
                self.amadeus_client_id,
                self.active_llm_provider,
            ]
        )

    def provider_status(self) -> dict[str, object]:
        """Public, secret-free description of what is wired up.

        Safe to return to the browser: it reports *which* providers are
        available, never their credentials.
        """
        return {
            "demo_mode": self.demo_mode,
            "llm": {
                "enabled": self.llm_enabled,
                "provider": self.active_llm_provider,
                "model": (
                    self.anthropic_model
                    if self.active_llm_provider == "anthropic"
                    else self.openai_model
                    if self.active_llm_provider == "openai"
                    else None
                ),
            },
            "geocoding": self.geocoding_providers,
            "routing": self.routing_providers,
            "places": self.places_providers,
            "weather": ["open-meteo"] if self.enable_open_meteo else [],
            "images": ["wikimedia"] if self.enable_wikimedia_images else [],
            "flights": [self.flight_provider] if self.flight_provider != "none" else [],
            "hotels": [self.hotel_provider] if self.hotel_provider != "none" else [],
            # The browser map key is *designed* to be public but must be
            # restricted by HTTP referrer in the provider console.
            "maps_browser_key_present": bool(self.google_maps_browser_key),
            "currency": self.default_currency,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor (FastAPI dependency friendly)."""
    return Settings()
