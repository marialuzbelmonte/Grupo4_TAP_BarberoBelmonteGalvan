"""Conexión a PostgreSQL (pool de asyncpg), creación del esquema y carga inicial."""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

import asyncpg

from app.config import settings

log = logging.getLogger(__name__)

SCHEMA = Path(__file__).with_name("schema.sql")
SEEDS_DIR = Path(__file__).resolve().parent.parent / "data"


class DatabaseUnavailable(RuntimeError):
    """La función pedida necesita la base de datos y no hay una configurada o disponible."""


async def _init_connection(conn: asyncpg.Connection) -> None:
    # Las columnas JSONB se leen y escriben como objetos de Python.
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


async def connect() -> asyncpg.Pool | None:
    """Crea el pool, aplica el esquema y carga las zonas de ejemplo que falten.

    Si no hay DATABASE_URL o la base no responde, devuelve None: la app sigue
    funcionando, pero sin cuentas, itinerarios guardados ni índice local.
    """
    if not settings.database_url:
        log.warning("DATABASE_URL no configurada: cuentas e índice local deshabilitados")
        return None
    try:
        pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=10, init=_init_connection)
    except (OSError, asyncpg.PostgresError) as exc:
        log.error("No se pudo conectar a PostgreSQL: %r", exc)
        return None
    async with pool.acquire() as conn:
        # Lock de sesión: si arrancan dos instancias a la vez, migran de a una.
        await conn.execute("SELECT pg_advisory_lock(7421)")
        try:
            await conn.execute(SCHEMA.read_text(encoding="utf-8"))
            await load_seeds(conn)
        finally:
            await conn.execute("SELECT pg_advisory_unlock(7421)")
    log.info("PostgreSQL listo")
    return pool


async def load_seeds(conn: asyncpg.Connection) -> None:
    """Carga data/*.json (zonas indexadas) si no están en la base o si el archivo cambió.

    Se compara un hash SHA-256 del archivo con el guardado en `zones.seed_hash`: así,
    reindexar una zona y desplegar alcanza para actualizar la base de producción.
    """
    from app.services import places  # import local: places depende de este módulo

    for path in sorted(SEEDS_DIR.glob("*.json")):
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        seed = json.loads(raw)
        slug = seed["zone"]["slug"]
        if await conn.fetchval("SELECT seed_hash FROM zones WHERE slug = $1", slug) == digest:
            continue
        count = await places.store_zone(conn, seed["zone"], seed["places"])
        await conn.execute("UPDATE zones SET seed_hash = $1 WHERE slug = $2", digest, slug)
        log.info("Zona %s indexada: %d lugares (desde %s)", slug, count, path.name)
