"""Tipos de cambio para mostrar los precios en la moneda preferida del usuario.

Los costos se calculan y se guardan siempre en dólares (US$); la conversión es solo
para mostrar. Fuentes (gratis y sin clave), guardadas en memoria unas horas:
  - ExchangeRate-API (open.er-api.com): todas las monedas, una vez por día.
  - DolarApi.com: el peso argentino va al **dólar blue** (precio de venta), que es
    lo que se paga en la práctica, no la cotización oficial.
"""
from __future__ import annotations

import asyncio
import logging
import time

import aiohttp

log = logging.getLogger(__name__)

RATES_URL = "https://open.er-api.com/v6/latest/USD"
BLUE_URL = "https://dolarapi.com/v1/dolares/blue"
SOURCE = "ExchangeRate-API"
SOURCE_URL = "https://www.exchangerate-api.com"
BLUE_SOURCE = "DolarApi.com"
CACHE_SECONDS = 6 * 3600
RETRY_SECONDS = 600  # si las fuentes fallan, se reintenta a los 10 minutos

# Monedas que se pueden elegir en el perfil (código ISO 4217 -> nombre).
CURRENCIES = {
    "USD": "Dólar estadounidense",
    "ARS": "Peso argentino (dólar blue)",
    "EUR": "Euro",
    "GBP": "Libra esterlina",
    "BRL": "Real brasileño",
    "CLP": "Peso chileno",
    "UYU": "Peso uruguayo",
    "PYG": "Guaraní paraguayo",
    "BOB": "Boliviano",
    "PEN": "Sol peruano",
    "COP": "Peso colombiano",
    "MXN": "Peso mexicano",
    "CAD": "Dólar canadiense",
    "JPY": "Yen japonés",
}

_cache: dict = {"at": 0.0, "data": None}
_lock = asyncio.Lock()


class RatesUnavailable(Exception):
    pass


async def _fetch_json(session: aiohttp.ClientSession, url: str) -> dict | None:
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            return await resp.json(content_type=None) if resp.status == 200 else None
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
        log.warning("No se pudo consultar %s: %s", url, exc)
        return None


def _world_rates(payload: dict | None) -> dict | None:
    rates = payload.get("rates") if payload and payload.get("result") == "success" else None
    return {code: float(rates[code]) for code in CURRENCIES if code in rates} if rates else None


def _blue(payload: dict | None) -> dict | None:
    """DolarApi -> {"rate": venta, ...}. Venta: lo que cuesta comprar un dólar."""
    try:
        rate = float(payload["venta"])
    except (TypeError, KeyError, ValueError):
        return None
    if rate <= 0:
        return None
    return {"rate": rate, "kind": "blue", "label": "dólar blue (venta)", "source": BLUE_SOURCE,
            "updated": payload.get("fechaActualizacion")}


async def get_rates(session: aiohttp.ClientSession) -> dict:
    """{"base": "USD", "rates": {código: unidades por 1 US$}, "ars": {datos del blue}, ...}."""
    async with _lock:  # varios pedidos a la vez hacen una sola consulta
        previous = _cache["data"]
        if previous and time.monotonic() - _cache["at"] < CACHE_SECONDS:
            return previous
        world_payload, blue_payload = await asyncio.gather(
            _fetch_json(session, RATES_URL), _fetch_json(session, BLUE_URL)
        )
        fresh_world, fresh_blue = _world_rates(world_payload), _blue(blue_payload)
        # Lo que falle se completa con la última cotización conocida.
        if fresh_world:
            world, updated = fresh_world, world_payload.get("time_last_update_utc")
        elif previous:
            world, updated = dict(previous["rates"]), previous["updated"]
        else:
            raise RatesUnavailable("No hay tipos de cambio disponibles en este momento.")
        blue = fresh_blue or (previous and previous.get("ars"))
        world.pop("ARS", None)  # el peso nunca a la cotización oficial: sin blue, no hay ARS
        if blue:
            world["ARS"] = blue["rate"]
        data = {"base": "USD", "rates": world, "ars": blue, "updated": updated, "source": SOURCE, "source_url": SOURCE_URL}
        complete = fresh_world is not None and fresh_blue is not None
        _cache.update(at=time.monotonic() - (0 if complete else CACHE_SECONDS - RETRY_SECONDS), data=data)
        return data
