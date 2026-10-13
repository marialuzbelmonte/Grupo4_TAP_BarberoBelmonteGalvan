# Rumbo · Generador de itinerarios de viaje con IA

Trabajo práctico de **Tópicos Avanzados de Programación** — Grupo 4 (Barbero · Belmonte · Galván).

Aplicación web cliente-servidor para planificar viajes: buscás un destino, fijás tu hotel, elegís
atracciones cercanas desde un *checklist* de tarjetas y el sistema arma un itinerario por días
(ruta optimizada + horarios generados con Gemini), con presupuesto estimado y exportación a PDF.

## Funcionalidades

- **Destino y hotel**: geocodificación con Nominatim (OSM); el hotel se puede buscar, marcar en el
  mapa o arrastrar. Las distancias se recalculan al instante (Haversine).
- **Exploración**: atracciones de OpenStreetMap (Overpass) enriquecidas con Wikidata (descripción,
  tarifa, popularidad) y Wikimedia Commons (foto y créditos). Filtros por categoría y búsqueda de
  texto normalizada (sin tildes, mayúsculas ni espacios de más).
- **Itinerario**: ruta pre-ordenada con Vecino Más Cercano + 2-opt, repartida por días y enviada a
  Gemini para asignar horarios y consejos. Si no hay `GEMINI_API_KEY` o Gemini falla, se arma igual
  con la heurística local.
- **Presupuesto y PDF**: tabla por atracción y viajeros, total estimado y exportación a PDF.

## Stack

| Capa | Tecnología |
|---|---|
| Backend | Python 3.12 · FastAPI · aiohttp (asíncrono) · Pydantic · ReportLab |
| Frontend | HTML5 · CSS3 · JavaScript *vanilla* (módulos ES) · Leaflet.js |
| Datos | OpenStreetMap (Nominatim + Overpass) · Wikidata · Wikimedia Commons |
| IA | Gemini API (REST, salida JSON con esquema) |
| Infraestructura | Docker · docker-compose · Railway |

## Estructura

```
app/
  main.py            # FastAPI: API en /api y cliente estático en /
  config.py          # variables de entorno
  models.py          # esquemas JSON (Pydantic)
  api/routes.py      # endpoints REST
  services/
    text.py          # normalización de texto
    geo.py           # Haversine, Vecino Más Cercano, 2-opt, fuerza bruta
    osm.py           # Nominatim y Overpass (con pedidos "hedged" entre mirrors)
    wikidata.py      # entidades de Wikidata
    commons.py       # miniaturas de Wikimedia Commons
    attractions.py   # cadena OSM -> Wikidata -> Commons en paralelo
    gemini.py        # cliente de Gemini
    itinerary.py     # orquestación, validación de la respuesta del LLM, presupuesto
    pdf.py           # exportación a PDF
static/              # index.html, css/, js/ (app.js, map.js, text.js)
scripts/benchmark_tsp.py
tests/
```

## API

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/api/health` | Estado y si Gemini está configurado |
| GET | `/api/categories` | Categorías disponibles |
| GET | `/api/geocode?q=&near_lat=&near_lon=` | Busca lugares (opcionalmente cerca de un punto) |
| GET | `/api/attractions?lat=&lon=&radius=&categories=&q=` | Atracciones enriquecidas alrededor del hotel |
| POST | `/api/itinerary` | Genera itinerario + presupuesto |
| POST | `/api/itinerary/pdf` | Devuelve el itinerario en PDF |

Documentación interactiva en `/docs`.

## Cómo correrlo

### Con Docker (recomendado)

```bash
cp .env.example .env        # y completar GEMINI_API_KEY
docker compose up --build
```

Abrir <http://localhost:8000>.

### Sin Docker

```bash
python -m venv .venv
.venv\Scripts\activate       # Windows  (Linux/macOS: source .venv/bin/activate)
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
```

### Tests y benchmark

```bash
pytest
python -m scripts.benchmark_tsp
```

## Despliegue en Railway

1. Crear un proyecto en Railway desde este repositorio de GitHub.
2. Railway detecta `railway.json` y construye con el `Dockerfile` (healthcheck en `/api/health`).
3. En **Variables** del servicio cargar `GEMINI_API_KEY` (y opcionalmente `GEMINI_MODEL`, `CONTACT_EMAIL`).
4. Generar un dominio público en **Settings → Networking**.

El `Procfile` queda como alternativa si se usa el builder Nixpacks/Railpack en lugar de Docker.

## Variables de entorno

| Variable | Default | Uso |
|---|---|---|
| `GEMINI_API_KEY` | — | Clave de la API de Gemini (sin ella se usa la heurística local) |
| `GEMINI_MODEL` | `gemini-3.8-flash,gemini-flash-latest,gemini-3.6-flash,gemini-3.5-flash,gemini-flash-lite-latest` | Modelos de Gemini en orden de preferencia (si uno falla se usa el siguiente) |
| `CONTACT_EMAIL` | — | Contacto opcional agregado al User-Agent |
| `MAX_CONCURRENCY` | `10` | Pedidos simultáneos máximos a Wikimedia |
| `HTTP_TIMEOUT` | `30` | Timeout general de las APIs externas (s) |
| `OVERPASS_URLS` | 3 servidores públicos | Lista separada por comas |

## Créditos de datos

Mapas y lugares © colaboradores de [OpenStreetMap](https://www.openstreetmap.org/copyright) (ODbL).
Datos de Wikidata (CC0) e imágenes de Wikimedia Commons con la licencia y autoría que indica cada foto.
