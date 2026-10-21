"""Application configuration.

Every secret is read from the environment -- never from code and never sent to
the browser. See `.env.example` for the full list of variables.

`Settings.provider_status()` is what the rest of the app consults to decide
whether a live provider can be called or whether it must fall back to a
deterministic estimate. That decision is always made server-side.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

Environment = Literal["development", "staging", "production"]

# Values that mean "I have not filled this in yet". A slot left at one of
# these must behave exactly like an absent key, or the app reports itself as
# live and then fails every provider call with a 401.
_PLACEHOLDERS = frozenset(
    {
        "your-key-here",
        "your-key",
        "your-api-key",
        "your-token",
        "your-id",
        "your-secret",
        "changeme",
        "change_me",
        "change-me",
        "replace-me",
        "replaceme",
        "todo",
        "tbd",
        "none",
        "null",
        "xxx",
        "xxxx",
        "...",
    }
)


def _clean_credential(value: str | None, field_name: str = "") -> str | None:
    """Normalise a credential pasted into a .env file.

    Setting up keys is where this app is most likely to be configured wrongly,
    and the failure modes all look identical from the outside. This handles
    the three that actually happen:

    * a slot left empty or holding only whitespace -> treated as absent
    * a value pasted with surrounding quotes -> quotes stripped, because
      dotenv keeps them and the provider then rejects the key
    * a placeholder left in from the example file -> treated as absent, with
      a warning, rather than reported as a configured provider
    """
    if value is None:
        return None

    cleaned = value.strip()
    # dotenv preserves quotes, so `KEY="abc"` arrives as `"abc"`.
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {'"', "'"}:
        cleaned = cleaned[1:-1].strip()

    if not cleaned:
        return None

    lowered = cleaned.lower()
    if lowered in _PLACEHOLDERS or (cleaned.startswith("<") and cleaned.endswith(">")):
        logger.warning(
            "%s still holds the placeholder %r, so it is being ignored. "
            "Replace it with a real value or delete the line.",
            field_name or "A credential",
            cleaned,
        )
        return None

    return cleaned


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
    openai_api_key: str | None = None
    # Deliberately empty. OpenAI model IDs change often enough that any
    # hardcoded default would eventually break, so when this is blank the app
    # asks the API which models the key can actually use and picks one. Set it
    # explicitly to pin a specific model.
    openai_model: str = ""
    # Lets you keep the key in place but turn the model off.
    enable_llm: bool = True
    llm_timeout_seconds: float = 45.0
    llm_max_output_tokens: int = 8000

    # ---- Geocoding / routing / places ------------------------------------
    # Two Google keys, deliberately separate:
    #   server  -> Geocoding, Routes and Places. IP-restricted. Never sent to
    #              the browser.
    #   browser -> Maps JavaScript tiles only. Public by design, so it MUST be
    #              HTTP-referrer restricted in the Google console.
    google_maps_server_key: str | None = None
    google_maps_browser_key: str | None = None

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

    @field_validator(
        "openai_api_key",
        "google_maps_server_key",
        "google_maps_browser_key",
        "osm_contact_email",
        "redis_url",
        mode="before",
    )
    @classmethod
    def _normalise_credential(cls, value: object, info) -> str | None:
        """An unfilled slot must behave exactly like an absent key."""
        if value is None or isinstance(value, str):
            return _clean_credential(value, info.field_name)
        return value

    @field_validator("openai_model", mode="before")
    @classmethod
    def _normalise_model_name(cls, value: object, info) -> str:
        """Model names are plain strings, so an unfilled slot becomes empty.

        `openai_model` has no default on purpose, and an empty value is what
        `active_llm_provider` checks to decide OpenAI is not usable yet.
        """
        if value is None:
            return ""
        if isinstance(value, str):
            return _clean_credential(value, info.field_name) or ""
        return value

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
        """Whether the OpenAI itinerary writer is usable.

        Only the key is required. If OPENAI_MODEL is blank the app resolves a
        current model ID from the API itself, so setup is one line, not two.
        """
        if not self.enable_llm:
            return None
        return "openai" if self.openai_api_key else None

    # Each capability has exactly one paid provider (Google) and one free
    # keyless fallback. Google first when configured; the free service
    # otherwise, and as the fallback if Google fails.
    @property
    def geocoding_providers(self) -> list[str]:
        chain: list[str] = []
        if self.google_maps_server_key:
            chain.append("google")
        if self.enable_nominatim:
            chain.append("nominatim")
        return chain

    @property
    def routing_providers(self) -> list[str]:
        chain: list[str] = []
        if self.google_maps_server_key:
            chain.append("google")
        if self.enable_osrm:
            chain.append("osrm")
        return chain

    @property
    def places_providers(self) -> list[str]:
        chain: list[str] = []
        if self.google_maps_server_key:
            chain.append("google")
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
        return not any([self.google_maps_server_key, self.active_llm_provider])

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
                "model": self.openai_model or None,
            },
            "geocoding": self.geocoding_providers,
            "routing": self.routing_providers,
            "places": self.places_providers,
            "weather": ["open-meteo"] if self.enable_open_meteo else [],
            "images": ["wikimedia"] if self.enable_wikimedia_images else [],
            # No licensed fare or rate API is wired up, so these are always
            # produced by the cost model and labelled as estimates.
            "flights": [],
            "hotels": [],
            # The browser map key is *designed* to be public but must be
            # restricted by HTTP referrer in the provider console.
            "maps_browser_key_present": bool(self.google_maps_browser_key),
            "currency": self.default_currency,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor (FastAPI dependency friendly)."""
    return Settings()
