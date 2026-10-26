import os
from dataclasses import replace

import pytest

from app import db
from app.services import gemini, itinerary

# Base para los tests de integración (se crea vacía; el esquema lo aplica la app).
# Con docker compose:  docker compose exec db createdb -U rumbo rumbo_test
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "postgresql://rumbo:rumbo@localhost:5433/rumbo_test")


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """Por defecto los tests no usan Gemini real ni la base de datos de .env."""
    no_gemini = replace(itinerary.settings, gemini_api_key="")
    monkeypatch.setattr(itinerary, "settings", no_gemini)
    monkeypatch.setattr(gemini, "settings", no_gemini)
    monkeypatch.setattr(db, "settings", replace(db.settings, database_url=""))


@pytest.fixture
def database(monkeypatch):
    """Apunta la app a la base de test. Si no hay PostgreSQL disponible, saltea el test."""
    import asyncio

    import asyncpg

    async def reachable() -> bool:
        try:
            conn = await asyncpg.connect(TEST_DATABASE_URL, timeout=3)
        except (OSError, asyncpg.PostgresError, asyncio.TimeoutError):
            return False
        await conn.execute("TRUNCATE users, itineraries RESTART IDENTITY CASCADE") if await conn.fetchval(
            "SELECT to_regclass('public.users') IS NOT NULL"
        ) else None
        await conn.close()
        return True

    if not asyncio.run(reachable()):
        pytest.skip("PostgreSQL de test no disponible (TEST_DATABASE_URL)")
    monkeypatch.setattr(db, "settings", replace(db.settings, database_url=TEST_DATABASE_URL))
    return TEST_DATABASE_URL
