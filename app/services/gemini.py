"""Cliente mínimo de la API REST de Gemini con salida JSON estructurada."""
from __future__ import annotations

import asyncio
import json
import logging
import time

import aiohttp

from app.config import settings

log = logging.getLogger(__name__)

API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "title": {"type": "STRING"},
        "summary": {"type": "STRING"},
        "days": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "day": {"type": "INTEGER"},
                    "theme": {"type": "STRING"},
                    "stops": {
                        "type": "ARRAY",
                        "items": {
                            "type": "OBJECT",
                            "properties": {
                                "id": {"type": "STRING"},
                                "time": {"type": "STRING"},
                                "duration_min": {"type": "INTEGER"},
                                "tip": {"type": "STRING"},
                            },
                            "required": ["id", "time", "duration_min"],
                        },
                    },
                },
                "required": ["day", "theme", "stops"],
            },
        },
        "tips": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["title", "summary", "days"],
}

SYSTEM_PROMPT = """Sos un planificador de viajes experto. Recibís atracciones YA ORDENADAS
geográficamente por una heurística (Vecino Más Cercano + 2-opt) y ya repartidas por día.
Reglas:
- Usá únicamente los ids recibidos; cada id debe aparecer exactamente una vez.
- Respetá el orden geográfico. Solo podés mover una parada a un día contiguo si mejora
  claramente el plan (por ejemplo, un museo que cierra ese día).
- Asigná horarios de inicio realistas en formato HH:MM (24 h), considerando los horarios de
  apertura informados, la duración de cada visita y los traslados (distancia en km desde la
  parada anterior). Ubicá restaurantes cerca de las 13:00 o de las 20:30 y cafés a media mañana o tarde.
- "theme": título corto y evocador para cada día (máx. 6 palabras).
- "tip": un consejo concreto por parada (máx. 25 palabras). No inventes precios ni datos.
- "tips": entre 3 y 5 consejos generales del destino.
- Todo el texto en español rioplatense neutro."""


class GeminiError(RuntimeError):
    pass


def build_prompt(destination: str, hotel_name: str, day_groups: list[list[dict]]) -> str:
    payload = {
        "destino": destination,
        "hotel_de_partida": hotel_name,
        "dias": [{"dia": i + 1, "paradas": stops} for i, stops in enumerate(day_groups)],
    }
    return "Armá el itinerario a partir de estos datos:\n" + json.dumps(payload, ensure_ascii=False, indent=1)


async def generate(session: aiohttp.ClientSession, prompt: str) -> dict:
    if not settings.gemini_api_key:
        raise GeminiError("Falta GEMINI_API_KEY")
    body = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.6,
            "responseMimeType": "application/json",
            "responseSchema": RESPONSE_SCHEMA,
        },
    }
    errors: list[str] = []
    deadline = time.monotonic() + TOTAL_BUDGET
    for model in settings.gemini_models:
        for attempt in range(RETRIES_PER_MODEL + 1):
            remaining = deadline - time.monotonic()
            if remaining < 3:
                log.warning("Gemini superó el tiempo máximo: %s", "; ".join(errors))
                raise GeminiError("Gemini tardó demasiado en responder")
            try:
                result = await _call(session, model, body, timeout=min(CALL_TIMEOUT, remaining))
                log.info("Itinerario generado con %s", model)
                return result
            except _Transient as exc:  # 429/500/504/timeout: reintentar, luego pasar al siguiente modelo
                errors.append(f"{model}: {exc}")
                log.warning("Gemini %s falló (intento %d): %s", model, attempt + 1, exc)
                if attempt < RETRIES_PER_MODEL:
                    await asyncio.sleep(RETRY_DELAY * (attempt + 1))
            except _Unavailable as exc:  # 404 (retirado) o 503 (saturado): reintentar no sirve, siguiente
                errors.append(f"{model}: {exc}")
                log.warning("Gemini %s no disponible: %s", model, exc)
                break
    log.warning("Gemini agotó todos los modelos: %s", "; ".join(errors))
    raise GeminiError("los servidores de Gemini están saturados en este momento")


RETRIES_PER_MODEL = 1
RETRY_DELAY = 1.5
TOTAL_BUDGET = 45  # segundos máximos sumando todos los intentos
CALL_TIMEOUT = 25  # segundos máximos por intento (nunca más que lo que queda del total)
TRANSIENT_STATUS = {429, 500, 504}


class _Transient(Exception):
    pass


class _Unavailable(Exception):
    pass


async def _call(session: aiohttp.ClientSession, model: str, body: dict, timeout: float) -> dict:
    headers = {"x-goog-api-key": settings.gemini_api_key}
    try:
        async with session.post(
            API_URL.format(model=model), json=body, headers=headers, timeout=aiohttp.ClientTimeout(total=timeout)
        ) as resp:
            data = await resp.json(content_type=None)
            status = resp.status
    except asyncio.TimeoutError as exc:
        raise _Transient("tiempo de espera agotado") from exc
    except (aiohttp.ClientError, ValueError) as exc:
        raise _Transient(repr(exc)) from exc

    if status != 200:
        message = (data.get("error") or {}).get("message", f"HTTP {status}")[:200]
        if status in (404, 503):
            raise _Unavailable(f"HTTP {status}: {message}")
        if status in TRANSIENT_STATUS:
            raise _Transient(f"HTTP {status}: {message}")
        raise GeminiError(f"Gemini respondió HTTP {status}: {message}")  # 400/403: clave o pedido inválido

    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(text)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise _Transient("respuesta sin JSON válido") from exc
