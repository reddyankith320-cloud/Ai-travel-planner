"""Server-side LLM access for itinerary generation.

Three rules govern how TripWise uses a language model:

1. **It runs server-side only.** The API key never reaches the browser.
2. **It never does arithmetic that matters.** The model proposes structure,
   sequencing and prose. Every rupee is recomputed by `services/budget.py`
   from the model's chosen activities. A hallucinated price cannot reach the
   user because no price the model emits is ever used.
3. **Its output is validated, not trusted.** The reply must parse as JSON and
   satisfy a Pydantic schema. On failure we retry once with the validation
   error fed back, then fall back to the deterministic planner.

Anthropic and OpenAI are both supported. `LLM_PROVIDER=auto` prefers Anthropic
when both keys are present.
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


async def complete_json(
    *,
    system_prompt: str,
    user_prompt: str,
    settings: Settings,
    max_output_tokens: int | None = None,
) -> LLMReply:
    """Ask the configured LLM for a JSON object and parse it."""
    provider = settings.active_llm_provider
    if provider is None:
        raise LLMUnavailable(
            "No LLM configured. Set ANTHROPIC_API_KEY, or OPENAI_API_KEY together with "
            "OPENAI_MODEL."
        )

    budget = max_output_tokens or settings.llm_max_output_tokens
    if provider == "anthropic":
        text, model = await _anthropic(system_prompt, user_prompt, settings, budget)
    else:
        text, model = await _openai(system_prompt, user_prompt, settings, budget)

    return LLMReply(
        data=extract_json(text), provider=provider, model=model, raw_length=len(text)
    )


async def _anthropic(
    system_prompt: str, user_prompt: str, settings: Settings, max_tokens: int
) -> tuple[str, str]:
    try:
        from anthropic import AsyncAnthropic
    except ImportError as exc:  # pragma: no cover
        raise LLMUnavailable("the `anthropic` package is not installed") from exc

    client = AsyncAnthropic(
        api_key=settings.anthropic_api_key, timeout=settings.llm_timeout_seconds
    )
    try:
        message = await client.messages.create(
            model=settings.anthropic_model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
    except Exception as exc:  # noqa: BLE001 -- surfaced as a provider failure
        logger.warning("Anthropic request failed: %s", type(exc).__name__)
        raise LLMUnavailable(f"Anthropic request failed: {type(exc).__name__}") from exc
    finally:
        await client.close()

    chunks = [
        block.text for block in message.content if getattr(block, "type", None) == "text"
    ]
    if not chunks:
        raise LLMUnavailable("Anthropic returned no text content")
    return "".join(chunks), settings.anthropic_model


async def _openai(
    system_prompt: str, user_prompt: str, settings: Settings, max_tokens: int
) -> tuple[str, str]:
    try:
        from openai import AsyncOpenAI
    except ImportError as exc:  # pragma: no cover
        raise LLMUnavailable("the `openai` package is not installed") from exc

    if not settings.openai_model:
        raise LLMUnavailable(
            "OPENAI_MODEL is not set. Model IDs change often -- list the current ones with "
            "`GET https://api.openai.com/v1/models` and set one."
        )

    client = AsyncOpenAI(api_key=settings.openai_api_key, timeout=settings.llm_timeout_seconds)
    try:
        completion = await client.chat.completions.create(
            model=settings.openai_model,
            max_completion_tokens=max_tokens,
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
    return content, settings.openai_model
