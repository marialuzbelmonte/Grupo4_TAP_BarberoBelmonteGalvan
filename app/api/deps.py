"""Dependencias compartidas por los endpoints (FastAPI las inyecta con Depends)."""
from __future__ import annotations

import aiohttp
import asyncpg
from fastapi import Depends, HTTPException, Request

from app.services import auth


def http_session(request: Request) -> aiohttp.ClientSession:
    return request.app.state.http


def optional_pool(request: Request) -> asyncpg.Pool | None:
    return request.app.state.db


def db_pool(pool: asyncpg.Pool | None = Depends(optional_pool)) -> asyncpg.Pool:
    if pool is None:
        raise HTTPException(status_code=503, detail="Las cuentas no están disponibles: falta la base de datos.")
    return pool


async def current_user(request: Request, pool: asyncpg.Pool = Depends(db_pool)) -> dict:
    """Usuario de la cookie de sesión; 401 si no hay sesión válida."""
    user_id = auth.user_id_from_token(request.cookies.get(auth.SESSION_COOKIE))
    user = await auth.get_user(pool, user_id) if user_id else None
    if user is None:
        raise HTTPException(status_code=401, detail="Tenés que iniciar sesión.")
    return user
