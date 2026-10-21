"""Server-side OpenAI access for itinerary generation.

Three rules govern how TripWise uses a language model:

1. **It runs server-side only.** The API key never reaches the browser.
2. **It never does arithmetic that matters.** The model proposes structure,
   sequencing and prose. Every rupee is recomputed by `services/budget.py`
   from the model's chosen activities. A hallucinated price cannot reach the
   user because no price the model emits is ever read.
3. **Its output is validated, not trusted.** The reply must parse as JSON and
   satisfy a Pydantic schema. On failure we retry once with the validation
   error fed back, then fall back to the deterministic planner.

Model selection: OpenAI's model IDs change often enough that hardcoding one
guarantees an eventual silent breakage. If `OPENAI_MODEL` is blank, we ask the
API which models this key can use and pick the best available by family, then
cache that for the process. Setting `OPENAI_MODEL` pins a specific model and
skips the lookup.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from ..config import Settings

logger = logging.getLogger(__name__)

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)

# Preference order for automatic model selection, newest family first. Each
# entry is matched as a prefix against the IDs the API reports, so a new point
# release is picked up without a code change.
_MODEL_PREFERENCE: tuple[str, ...] = (
    "gpt-6",
    "gpt-5.6",
    "gpt-5.5",
    "gpt-5",
    "gpt-4.1",
    "gpt-4o",
)

# Never auto-select these: they are not general chat-completion models.
_MODEL_EXCLUDE = (
    "audio", "realtime", "transcribe", "tts", "whisper", "embedding",
    "moderation", "image", "dall-e", "search", "vision-preview",
)

_resolved_model: str | None = None


class LLMUnavailable(RuntimeError):
    """No LLM is configured, or the provider could not be reached."""


@dataclass(frozen=True, slots=True)
class LLMReply:
    """A parsed model response."""

    data: dict[str, Any]
    provider: str
    model: str
    raw_length: int


def extract_json(text: str) -> dict[str, Any]:
    """Pull a JSON object out of a model reply.

    Models sometimes wrap JSON in prose or a fenced block even when told not
    to. Rather than fail the request, strip the wrapper and parse the object.
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```$", "", cleaned.strip())
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        match = _JSON_BLOCK.search(cleaned)
        if not match:
            raise ValueError("model reply contained no JSON object") from None
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("model reply was not a JSON object")
    return parsed


def choose_model(available: list[str]) -> str | None:
    """Pick the best usable chat model from the IDs the API reports.

    Prefers an undated alias (`gpt-5.6`) over a dated snapshot
    (`gpt-5.6-2026-07-01`) so the deployment tracks the provider's current
    pointer, and takes the shortest matching ID as the canonical alias.
    """
    usable = [
        model
        for model in available
        if not any(token in model.lower() for token in _MODEL_EXCLUDE)
    ]
    for prefix in _MODEL_PREFERENCE:
        matches = [m for m in usable if m.startswith(prefix)]
        if matches:
            return sorted(matches, key=lambda m: (len(m), m))[0]
    return None


async def resolve_model(settings: Settings) -> str:
    """The model ID to use, discovering one from the API when unset."""
    global _resolved_model

    if settings.openai_model:
        return settings.openai_model
    if _resolved_model:
        return _resolved_model

    try:
        from openai import AsyncOpenAI
    except ImportError as exc:  # pragma: no cover
        raise LLMUnavailable("the `openai` package is not installed") from exc

    client = AsyncOpenAI(api_key=settings.openai_api_key, timeout=settings.llm_timeout_seconds)
    try:
        listing = await client.models.list()
        available = [model.id for model in listing.data]
    except Exception as exc:  # noqa: BLE001 -- surfaced as a provider failure
        raise LLMUnavailable(
            f"could not list OpenAI models ({type(exc).__name__}). Check the key, or set "
            "OPENAI_MODEL explicitly."
        ) from exc
    finally:
        await client.close()

    chosen = choose_model(available)
    if chosen is None:
        raise LLMUnavailable(
            "no suitable chat model is available to this key. Set OPENAI_MODEL to one of: "
            + ", ".join(sorted(available)[:10])
        )

    _resolved_model = chosen
    logger.info(
        "OPENAI_MODEL not set; selected %r from %d models available to this key. "
        "Set OPENAI_MODEL to pin a specific one.",
        chosen,
        len(available),
    )
    return chosen


def reset_resolved_model() -> None:
    """Clear the cached model choice. Used by tests."""
    global _resolved_model
    _resolved_model = None


async def complete_json(
    *,
    system_prompt: str,
    user_prompt: str,
    settings: Settings,
    max_output_tokens: int | None = None,
) -> LLMReply:
    """Ask OpenAI for a JSON object and parse it."""
    if settings.active_llm_provider is None:
        raise LLMUnavailable(
            "No LLM configured. Set OPENAI_API_KEY to have itineraries written by a model; "
            "without it TripWise uses its deterministic planner."
        )

    try:
        from openai import AsyncOpenAI
    except ImportError as exc:  # pragma: no cover
        raise LLMUnavailable("the `openai` package is not installed") from exc

    model = await resolve_model(settings)
    budget = max_output_tokens or settings.llm_max_output_tokens

    client = AsyncOpenAI(api_key=settings.openai_api_key, timeout=settings.llm_timeout_seconds)
    try:
        completion = await client.chat.completions.create(
            model=model,
            max_completion_tokens=budget,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("OpenAI request failed: %s", type(exc).__name__)
        raise LLMUnavailable(f"OpenAI request failed: {type(exc).__name__}") from exc
    finally:
        await client.close()

    if not completion.choices:
        raise LLMUnavailable("OpenAI returned no choices")
    content = completion.choices[0].message.content or ""
    if not content.strip():
        raise LLMUnavailable("OpenAI returned empty content")

    return LLMReply(
        data=extract_json(content), provider="openai", model=model, raw_length=len(content)
    )
