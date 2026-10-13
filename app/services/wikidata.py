"""Wikidata: descripción, imagen, tarifa de entrada y popularidad de una entidad."""
from __future__ import annotations

import asyncio

import aiohttp

from app.services.cache import TTLCache

WIKIDATA_API = "https://www.wikidata.org/w/api.php"

# Monedas (entidad Wikidata -> código, cotización aproximada a USD).
CURRENCIES: dict[str, tuple[str, float]] = {
    "Q4917": ("USD", 1.0),
    "Q4916": ("EUR", 1.08),
    "Q25224": ("GBP", 1.27),
    "Q25344": ("CHF", 1.13),
    "Q8146": ("JPY", 0.0067),
    "Q199578": ("ARS", 0.001),
    "Q173117": ("BRL", 0.18),
    "Q4730": ("MXN", 0.055),
    "Q200050": ("CLP", 0.0011),
    "Q209792": ("UYU", 0.025),
    "Q244819": ("COP", 0.00025),
    "Q204656": ("PEN", 0.27),
    "Q1104069": ("CAD", 0.73),
    "Q259502": ("AUD", 0.66),
    "Q122922": ("SEK", 0.095),
    "Q25417": ("DKK", 0.145),
    "Q132643": ("NOK", 0.093),
    "Q131473": ("CZK", 0.043),
    "Q123213": ("PLN", 0.25),
    "Q172872": ("TRY", 0.03),
}

_cache = TTLCache(ttl_seconds=24 * 3600, maxsize=4096)


def _first_value(claims: dict, prop: str):
    for claim in claims.get(prop, []):
        snak = claim.get("mainsnak", {})
        if snak.get("snaktype") == "value":
            return snak.get("datavalue", {}).get("value")
    return None


def parse_entity(entity: dict) -> dict:
    claims = entity.get("claims", {})
    descriptions = entity.get("descriptions", {})
    description = (descriptions.get("es") or descriptions.get("en") or {}).get("value")

    fee = None
    raw_fee = _first_value(claims, "P2555")  # P2555 = tarifa
    if isinstance(raw_fee, dict) and "amount" in raw_fee:
        unit = raw_fee.get("unit", "").rsplit("/", 1)[-1]
        if unit in CURRENCIES:
            code, rate = CURRENCIES[unit]
            amount = abs(float(raw_fee["amount"]))
            fee = {"amount": amount, "currency": code, "usd": round(amount * rate, 2)}

    return {
        "description": description,
        "image": _first_value(claims, "P18"),  # nombre de archivo en Commons
        "fee": fee,
        "website": _first_value(claims, "P856"),
        "heritage": bool(claims.get("P1435")),  # declarado patrimonio
        "sitelinks": len(entity.get("sitelinks", {})),  # artículos en Wikipedias
    }


async def fetch_entity(session: aiohttp.ClientSession, qid: str) -> dict | None:
    if (cached := _cache.get(qid)) is not None:
        return cached
    params = {
        "action": "wbgetentities",
        "ids": qid,
        "props": "claims|descriptions|sitelinks",
        "languages": "es|en",
        "format": "json",
    }
    try:
        async with session.get(WIKIDATA_API, params=params) as resp:
            if resp.status != 200:
                return None
            data = await resp.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
        return None
    entity = data.get("entities", {}).get(qid)
    if not entity or "missing" in entity:
        return None
    parsed = parse_entity(entity)
    _cache.set(qid, parsed)
    return parsed
