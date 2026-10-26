"""Punto de entrada: API REST en /api y cliente estático en /."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import aiohttp
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import db
from app.api import accounts
from app.api.routes import router
from app.config import settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Una sola sesión HTTP compartida: reutiliza conexiones TCP/TLS entre pedidos.
    connector = aiohttp.TCPConnector(limit=40, ttl_dns_cache=300)
    timeout = aiohttp.ClientTimeout(total=settings.http_timeout)
    headers = {"User-Agent": settings.user_agent}
    app.state.db = await db.connect()
    try:
        async with aiohttp.ClientSession(connector=connector, timeout=timeout, headers=headers) as session:
            app.state.http = session
            yield
    finally:
        if app.state.db is not None:
            await app.state.db.close()


app = FastAPI(title="Rumbo · Generador de itinerarios", version="1.0.0", lifespan=lifespan)
app.include_router(router, prefix="/api")
app.include_router(accounts.router, prefix="/api")


@app.middleware("http")
async def revalidate_static(request: Request, call_next):
    # Los estáticos se revalidan con ETag en cada carga: tras un deploy nadie
    # queda con CSS/JS viejos, y si no cambiaron el servidor responde 304.
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")
