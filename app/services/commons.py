"""Wikimedia Commons: miniatura y créditos de una imagen."""
from __future__ import annotations

import asyncio
import html
import re

import aiohttp

from app.services.cache import TTLCache

COMMONS_API = "https://commons.wikimedia.org/w/api.php"
_TAGS = re.compile(r"<[^>]+>")
_cache = TTLCache(ttl_seconds=24 * 3600, maxsize=4096)


def _plain(value: str | None) -> str | None:
    if not value:
        return None
    text = html.unescape(_TAGS.sub("", value)).strip()
    return text[:120] or None


def filename_from_tag(value: str | None) -> str | None:
    """Etiqueta OSM `wikimedia_commons`: 'File:Foo.jpg' -> 'Foo.jpg'."""
    if not value or not value.lower().startswith("file:"):
        return None
    return value.split(":", 1)[1]


async def fetch_thumbnail(session: aiohttp.ClientSession, filename: str, width: int = 640) -> dict | None:
    key = (filename, width)
    if (cached := _cache.get(key)) is not None:
        return cached
    params = {
        "action": "query",
        "titles": f"File:{filename}",
        "prop": "imageinfo",
        "iiprop": "url|extmetadata",
        "iiurlwidth": str(width),
        "format": "json",
    }
    try:
        async with session.get(COMMONS_API, params=params) as resp:
            if resp.status != 200:
                return None
            data = await resp.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
        return None

    for page in data.get("query", {}).get("pages", {}).values():
        info = (page.get("imageinfo") or [None])[0]
        if not info:
            continue
        meta = info.get("extmetadata", {})
        result = {
            "url": info.get("thumburl") or info.get("url"),
            "credit": _plain(meta.get("Artist", {}).get("value")),
            "license": _plain(meta.get("LicenseShortName", {}).get("value")),
            "page": info.get("descriptionurl"),
        }
        if result["url"]:
            _cache.set(key, result)
            return result
    return None
