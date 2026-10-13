"""Esquemas JSON que intercambian cliente y servidor."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

BUDGET_NOTE = (
    "Nota: Este presupuesto incluye únicamente las estimaciones de las atracciones. "
    "El alojamiento/hotel no está incluido. Los precios son aproximados y pueden estar "
    "sujetos a modificaciones o desactualizados."
)


class Place(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class GeocodeResult(Place):
    display_name: str
    kind: str | None = None
    bbox: list[float] | None = None  # [sur, norte, oeste, este]


class Image(BaseModel):
    url: str
    credit: str | None = None
    license: str | None = None
    page: str | None = None


class Cost(BaseModel):
    amount_usd: float = Field(ge=0)
    source: Literal["wikidata", "osm", "estimado"]
    label: str


class Attraction(BaseModel):
    id: str = Field(max_length=40)
    name: str = Field(max_length=300)
    category: str
    category_label: str
    kind: str | None = Field(default=None, max_length=40)  # tipo OSM concreto: "zoo", "artwork"…
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    description: str = Field(max_length=1000)
    image: Image | None = None
    cost: Cost
    hours: str | None = Field(default=None, max_length=300)
    rating: float = Field(ge=0, le=5)
    distance_km: float = Field(ge=0)
    website: str | None = None
    wikidata: str | None = None


class AttractionsResponse(BaseModel):
    attractions: list[Attraction]
    meta: dict


class ItineraryRequest(BaseModel):
    destination: str = Field(min_length=1, max_length=300)
    days: int = Field(ge=1, le=14)
    travelers: int = Field(default=1, ge=1, le=20)
    hotel: Place
    attractions: list[Attraction] = Field(min_length=1, max_length=40)


class Stop(BaseModel):
    id: str
    name: str
    category: str
    category_label: str
    lat: float
    lon: float
    time: str
    duration_min: int
    tip: str = ""
    hours: str | None = None
    cost_usd: float
    cost_source: str
    from_prev_km: float


class DayPlan(BaseModel):
    day: int
    theme: str
    stops: list[Stop]
    distance_km: float
    subtotal_usd: float


class BudgetRow(BaseModel):
    day: int
    name: str
    category_label: str
    unit_usd: float
    travelers: int
    total_usd: float
    source: str


class Budget(BaseModel):
    currency: str = "USD"
    travelers: int
    rows: list[BudgetRow]
    total_usd: float
    note: str = BUDGET_NOTE


class Itinerary(BaseModel):
    title: str
    summary: str
    destination: str
    hotel: Place
    days: list[DayPlan]
    tips: list[str]
    budget: Budget
    source: Literal["gemini", "heuristica"]
    warning: str | None = None
    route_stats: dict = Field(default_factory=dict)
