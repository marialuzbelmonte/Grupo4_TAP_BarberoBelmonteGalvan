"""Configuración de la aplicación leída desde variables de entorno."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

# Si un modelo está saturado (503) o fue retirado (404) se prueba el siguiente.
# "gemini-flash-latest" es un alias que Google mantiene apuntando al Flash vigente.
# El último es un modelo "lite": menos capaz pero con más disponibilidad.
DEFAULT_GEMINI_MODELS = "gemini-3.8-flash,gemini-flash-latest,gemini-3.6-flash,gemini-3.5-flash,gemini-flash-lite-latest"

REPO_URL = "https://github.com/marialuzbelmonte/Grupo4_TAP_BarberoBelmonteGalvan"


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str
    gemini_models: tuple[str, ...]  # en orden de preferencia
    user_agent: str
    overpass_urls: tuple[str, ...]
    http_timeout: float
    max_concurrency: int


def load_settings() -> Settings:
    # Wikimedia y Nominatim rechazan (HTTP 403) clientes sin datos de contacto:
    # https://meta.wikimedia.org/wiki/User-Agent_policy
    # Overpass, en cambio, responde 406 si el agente menciona "aiohttp/".
    contact = _env("CONTACT_EMAIL")
    user_agent = f"RumboItinerarios/1.0 ({REPO_URL}{'; ' + contact if contact else ''})"
    overpass = _env(
        "OVERPASS_URLS",
        "https://overpass-api.de/api/interpreter,"
        "https://lz4.overpass-api.de/api/interpreter,"
        "https://overpass.kumi.systems/api/interpreter",
    )
    return Settings(
        gemini_api_key=_env("GEMINI_API_KEY"),
        gemini_models=tuple(m.strip() for m in _env("GEMINI_MODEL", DEFAULT_GEMINI_MODELS).split(",") if m.strip()),
        user_agent=user_agent,
        overpass_urls=tuple(u.strip() for u in overpass.split(",") if u.strip()),
        http_timeout=float(_env("HTTP_TIMEOUT", "30")),
        max_concurrency=int(_env("MAX_CONCURRENCY", "10")),
    )


settings = load_settings()
