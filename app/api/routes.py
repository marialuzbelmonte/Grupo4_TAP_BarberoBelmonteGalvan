"""Endpoints REST. Toda la respuesta es JSON salvo la exportación a PDF."""
from __future__ import annotations

import os
import re

import time

import aiohttp
import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.api.deps import db_pool, http_session, optional_pool
from app.config import settings
from app.models import (
    AttractionsResponse,
    GeocodeResult,
    Itinerary,
    ItineraryRequest,
    SuggestRequest,
    SuggestResponse,
)
from app.services import attractions, itinerary, osm, pdf, places, suggest, text

router = APIRouter()


@router.get("/health")
async def health(pool: asyncpg.Pool | None = Depends(optional_pool)) -> dict:
    # RAILWAY_GIT_COMMIT_SHA lo define Railway: permite ver qué versión está desplegada.
    return {
        "status": "ok",
        "gemini": bool(settings.gemini_api_key),
        "database": pool is not None,
        "version": os.getenv("RAILWAY_GIT_COMMIT_SHA", "local")[:7],
    }


@router.get("/categories")
async def categories() -> list[dict]:
    return [{"slug": c.slug, "label": c.label} for c in osm.CATEGORIES.values()]


@router.get("/geocode", response_model=list[GeocodeResult])
async def geocode(
    q: str = Query(min_length=2, max_length=200),
    near_lat: float | None = Query(default=None, ge=-90, le=90),
    near_lon: float | None = Query(default=None, ge=-180, le=180),
    session: aiohttp.ClientSession = Depends(http_session),
):
    near = (near_lat, near_lon) if near_lat is not None and near_lon is not None else None
    try:
        return await osm.geocode(session, q.strip(), near=near)
    except osm.UpstreamError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/attractions", response_model=AttractionsResponse)
async def list_attractions(
    lat: float = Query(ge=-90, le=90),
    lon: float = Query(ge=-180, le=180),
    radius: int = Query(default=3000, ge=300, le=15000, description="Radio en metros"),
    categories: str | None = Query(default=None, description="Slugs separados por coma"),
    q: str | None = Query(default=None, max_length=120, description="Texto libre (se normaliza)"),
    session: aiohttp.ClientSession = Depends(http_session),
    pool: asyncpg.Pool | None = Depends(optional_pool),
):
    slugs = [s for s in re.split(r"[,\s]+", categories or "") if s] or list(osm.CATEGORIES)
    unknown = [s for s in slugs if s not in osm.CATEGORIES]
    if unknown:
        raise HTTPException(status_code=422, detail=f"Categorías desconocidas: {', '.join(unknown)}")
    # Zona indexada (p. ej. Rafaela): los lugares salen de PostgreSQL, sin APIs externas.
    zone = await places.zone_at(pool, lat, lon) if pool else None
    if zone:
        start = time.perf_counter()
        found = await places.nearby(pool, zone["slug"], lat, lon, radius)
        found = [a for a in found if a.category in slugs and text.matches(q, a.name, a.description, a.category_label)]
        meta = {
            "count": len(found), "zone": zone["slug"], "zone_name": zone["name"], "source": "indice",
            "db_ms": round((time.perf_counter() - start) * 1000),
        }
        return AttractionsResponse(attractions=found, meta=meta)
    try:
        items, meta = await attractions.search(session, lat, lon, radius, slugs, q)
    except osm.UpstreamError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return AttractionsResponse(attractions=items, meta=meta)


@router.get("/places/search")
async def search_places(
    zone: str = Query(min_length=1, max_length=40),
    q: str = Query(min_length=1, max_length=120),
    lat: float = Query(ge=-90, le=90),
    lon: float = Query(ge=-180, le=180),
    radius: int = Query(default=3000, ge=300, le=15000),
    pool: asyncpg.Pool = Depends(db_pool),
) -> dict:
    """Búsqueda full-text en una zona indexada: ids ordenados por relevancia."""
    start = time.perf_counter()
    found = await places.search(pool, zone, q, lat, lon, radius, limit=100)
    return {
        "results": [{"id": a.id, "rank": round(rank, 4)} for a, rank in found],
        "tsquery": places.build_tsquery(q),
        "ms": round((time.perf_counter() - start) * 1000, 1),
    }


@router.post("/suggest", response_model=SuggestResponse)
async def suggest_places(
    req: SuggestRequest,
    session: aiohttp.ClientSession = Depends(http_session),
    pool: asyncpg.Pool | None = Depends(optional_pool),
):
    try:
        return await suggest.suggest(session, pool, req)
    except osm.UpstreamError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/itinerary", response_model=Itinerary)
async def create_itinerary(req: ItineraryRequest, session: aiohttp.ClientSession = Depends(http_session)):
    return await itinerary.create(session, req)


@router.post("/itinerary/pdf")
async def export_pdf(data: Itinerary) -> Response:
    content = pdf.render(data)
    filename = re.sub(r"[^a-z0-9]+", "-", text.normalize(data.destination)).strip("-") or "destino"
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="itinerario-{filename}.pdf"'},
    )
