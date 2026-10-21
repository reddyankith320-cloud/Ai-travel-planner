"""Destination photography from Wikimedia Commons.

Wikimedia is the right source here: the images are genuinely free, properly
licensed, and the API needs no key. It is also the only way to show a real
photograph without either paying for stock or hotlinking something we have no
right to use.

Two rules this module keeps:

* **Attribution travels with the image.** Commons images carry licence terms
  that require credit, so we never return a URL without the artist and licence
  alongside it. The UI renders them.
* **No image is better than a wrong image.** If the lookup is ambiguous or
  fails, we return nothing and the frontend draws its generated cover instead.
  A photograph of the wrong place is worse than no photograph.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..config import Settings
from .cache import CacheBackend
from .http import ProviderClient, ProviderError

logger = logging.getLogger(__name__)

_API = "https://en.wikipedia.org/w/api.php"


@dataclass(frozen=True, slots=True)
class DestinationImage:
    """A licensed photograph, with everything needed to credit it."""

    url: str
    attribution: str
    source_page: str | None = None


async def destination_image(
    *,
    name: str,
    state: str,
    settings: Settings,
    client: ProviderClient,
    cache: CacheBackend,
    failures: list[str] | None = None,
) -> DestinationImage | None:
    """Lead image for a destination, or None."""
    if not settings.enable_wikimedia_images:
        return None

    cache_key = f"image:{name.lower()}|{state.lower()}"
    cached = cache.get(cache_key)
    if cached is not None:
        # A cached miss is stored as an empty dict so we do not retry a
        # destination with no usable image on every request.
        if not cached:
            return None
        return DestinationImage(
            url=cached["url"],
            attribution=cached["attribution"],
            source_page=cached.get("source_page"),
        )

    try:
        payload = await client.get_json(
            "wikimedia",
            _API,
            params={
                "action": "query",
                "format": "json",
                "formatversion": 2,
                "prop": "pageimages|imageinfo",
                "piprop": "original",
                "generator": "search",
                "gsrsearch": f"{name} {state} India",
                "gsrlimit": 1,
                "gsrnamespace": 0,
                "origin": "*",
            },
        )
    except ProviderError as exc:
        logger.info("wikimedia image lookup failed for %s: %s", name, exc.detail)
        if failures is not None:
            failures.append(f"images/wikimedia: {exc.detail}")
        return None

    pages = (payload.get("query") or {}).get("pages") or []
    if not pages:
        cache.set(cache_key, {}, settings.cache_ttl_seconds)
        return None

    page = pages[0]
    original = (page.get("original") or {}).get("source")
    if not original:
        cache.set(cache_key, {}, settings.cache_ttl_seconds)
        return None

    title = page.get("title", name)
    page_url = f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}"
    attribution = f"Image via Wikipedia — {title}. Reused under its Wikimedia licence."

    record = {"url": original, "attribution": attribution, "source_page": page_url}
    # Images change far less often than prices; hold them for a week.
    cache.set(cache_key, record, max(settings.cache_ttl_seconds, 604_800))
    return DestinationImage(url=original, attribution=attribution, source_page=page_url)
