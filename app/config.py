"""Configuración de la aplicación leída desde variables de entorno."""
from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

# Si un modelo está saturado (503), sin cuota (429) o retirado (404) se prueba el siguiente.
# Primero el "lite": medido, responde en ~2 s con buena calidad; los Flash completos
# tardaban 25-90 s en el plan gratuito. Los alias "-latest" los mantiene Google al día.
DEFAULT_GEMINI_MODELS = "gemini-flash-lite-latest,gemini-flash-latest,gemini-3.8-flash"

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
    database_url: str  # vacío = sin base: la app funciona, pero sin cuentas ni índice local
    jwt_secret: str
    cookie_secure: bool
    session_days: int


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
        database_url=_env("DATABASE_URL"),
        jwt_secret=_jwt_secret(),
        # En Railway (HTTPS) la cookie de sesión viaja solo cifrada; en localhost no hay HTTPS.
        cookie_secure=_env("COOKIE_SECURE", "true" if os.getenv("RAILWAY_ENVIRONMENT") else "false") == "true",
        session_days=int(_env("SESSION_DAYS", "7")),
    )


def _jwt_secret() -> str:
    secret = _env("JWT_SECRET")
    if not secret:
        # Sirve para desarrollo; en producción las sesiones se perderían en cada reinicio.
        logging.getLogger(__name__).warning("JWT_SECRET no configurado: se usa uno aleatorio temporal")
        secret = secrets.token_urlsafe(48)
    return secret


settings = load_settings()
