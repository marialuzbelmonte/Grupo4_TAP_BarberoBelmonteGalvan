"""Endpoints REST. Toda la respuesta es JSON salvo la exportación a PDF."""
from __future__ import annotations

import os
import re

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from app.config import settings
from app.models import AttractionsResponse, GeocodeResult, Itinerary, ItineraryRequest
from app.services import attractions, itinerary, osm, pdf, text

router = APIRouter()


def http_session(request: Request) -> aiohttp.ClientSession:
    return request.app.state.http


@router.get("/health")
async def health() -> dict:
    # RAILWAY_GIT_COMMIT_SHA lo define Railway: permite ver qué versión está desplegada.
    return {
        "status": "ok",
        "gemini": bool(settings.gemini_api_key),
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
):
    slugs = [s for s in re.split(r"[,\s]+", categories or "") if s] or list(osm.CATEGORIES)
    unknown = [s for s in slugs if s not in osm.CATEGORIES]
    if unknown:
        raise HTTPException(status_code=422, detail=f"Categorías desconocidas: {', '.join(unknown)}")
    try:
        items, meta = await attractions.search(session, lat, lon, radius, slugs, q)
    except osm.UpstreamError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return AttractionsResponse(attractions=items, meta=meta)


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
