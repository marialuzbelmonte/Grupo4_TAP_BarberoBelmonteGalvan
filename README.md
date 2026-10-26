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
- **Cuentas de usuario**: registro e inicio de sesión (contraseñas con Argon2id, sesión con JWT en
  cookie `httpOnly`). Con sesión iniciada se pueden guardar itinerarios y volver a abrirlos o
  descargarlos desde "Mis itinerarios". La app se puede usar igual sin cuenta.
- **Búsqueda full-text en PostgreSQL**: zona de ejemplo **Rafaela, Santa Fe** indexada localmente
  (`tsvector` + índice GIN, español sin tildes, raíces de palabras y ranking por relevancia). En
  Rafaela los lugares salen de la base, sin depender de APIs externas.
- **Pedidos abiertos a la IA**: en vez de tildar un lugar, se puede pedir "alguna heladería" o "un
  lugar para comer hamburguesas". Gemini interpreta el pedido, el buscador trae candidatos reales y
  Gemini elige entre ellos (nunca inventa lugares).

## Stack

| Capa | Tecnología |
|---|---|
| Backend | Python 3.12 · FastAPI · aiohttp (asíncrono) · Pydantic · ReportLab |
| Base de datos | PostgreSQL 17 (asyncpg) · búsqueda full-text (`tsvector`, GIN, `unaccent`) |
| Seguridad | Argon2id (argon2-cffi) · JWT HS256 (PyJWT) en cookie `httpOnly` + `SameSite=Lax` |
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
  db.py              # pool de PostgreSQL, esquema y carga inicial de zonas
  schema.sql         # tablas, índice GIN y configuración de búsqueda en español
  api/
    routes.py        # lugares, itinerario, búsqueda full-text, pedidos a la IA
    accounts.py      # registro, sesión e itinerarios guardados
    deps.py          # dependencias: sesión HTTP, base de datos, usuario actual
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
    auth.py          # hash de contraseñas, JWT, límite de intentos
    places.py        # índice local y búsqueda full-text
    suggest.py       # pedidos abiertos (RAG: Gemini + buscador)
data/rafaela.json    # zona indexada de ejemplo (se carga sola en la base)
static/              # index.html, css/, js/ (app, map, text, ui, account)
scripts/
  benchmark_tsp.py   # fuerza bruta vs. heurística
  index_zone.py      # (re)indexa una ciudad en data/*.json y en la base
tests/
```

## API

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/api/health` | Estado y si Gemini está configurado |
| GET | `/api/categories` | Categorías disponibles |
| GET | `/api/geocode?q=&near_lat=&near_lon=` | Busca lugares (opcionalmente cerca de un punto) |
| GET | `/api/attractions?lat=&lon=&radius=&categories=&q=` | Atracciones alrededor del hotel (de la base si la zona está indexada) |
| GET | `/api/places/search?zone=&q=&lat=&lon=&radius=` | Búsqueda full-text rankeada en una zona indexada |
| POST | `/api/suggest` | Pedido abierto: la IA elige lugares reales |
| POST | `/api/itinerary` | Genera itinerario + presupuesto |
| POST | `/api/itinerary/pdf` | Devuelve el itinerario en PDF |
| POST | `/api/auth/register` · `/api/auth/login` · `/api/auth/logout` | Cuentas y sesión |
| GET | `/api/auth/me` | Usuario de la sesión (o `null`) |
| GET · POST | `/api/me/itineraries` | Listar / guardar itinerarios del usuario |
| GET · DELETE | `/api/me/itineraries/{id}` | Ver / borrar uno (solo el dueño) |

Documentación interactiva en `/docs`.

## Cómo correrlo

### Con Docker (recomendado)

```bash
cp .env.example .env        # y completar GEMINI_API_KEY y JWT_SECRET
docker compose up --build
```

Abrir <http://localhost:8000>. Compose levanta también PostgreSQL; al arrancar, la app crea las
tablas y carga el índice de Rafaela.

### Sin Docker

```bash
python -m venv .venv
.venv\Scripts\activate       # Windows  (Linux/macOS: source .venv/bin/activate)
pip install -r requirements-dev.txt
docker compose up -d db          # solo PostgreSQL (puerto 5433 en la compu)
uvicorn app.main:app --reload    # desarrollo, con recarga automática
python -m app                    # como en producción (lee el puerto de $PORT)
```

### Tests y benchmark

```bash
docker compose exec db createdb -U rumbo rumbo_test   # una vez: base para los tests de integración
pytest
python -m scripts.benchmark_tsp
```

Los tests de integración usan `TEST_DATABASE_URL` (por defecto la base `rumbo_test` del compose) y
se saltean solos si no hay PostgreSQL disponible.

### Reindexar una zona

```bash
python -m scripts.index_zone rafaela --db
```

Descarga todos los lugares de la zona (OSM + Wikidata + Commons), actualiza `data/rafaela.json` y,
con `--db`, también la base indicada en `DATABASE_URL`.

## Despliegue en Railway

1. Crear un proyecto en Railway desde este repositorio de GitHub.
2. Railway detecta `railway.json` y construye con el `Dockerfile` (healthcheck en `/api/health`).
3. Agregar una base: **New → Database → PostgreSQL** dentro del mismo proyecto.
4. En **Variables** del servicio web cargar:
   - `GEMINI_API_KEY`
   - `DATABASE_URL` = `${{Postgres.DATABASE_URL}}` (referencia a la base del paso 3)
   - `JWT_SECRET` = un texto largo y aleatorio (`python -c "import secrets; print(secrets.token_urlsafe(48))"`)
5. Generar un dominio público en **Settings → Networking**.

Al arrancar, la app crea las tablas y carga el índice de Rafaela sola.

El `Procfile` queda como alternativa si se usa el builder Nixpacks/Railpack en lugar de Docker.

## Variables de entorno

| Variable | Default | Uso |
|---|---|---|
| `GEMINI_API_KEY` | — | Clave de la API de Gemini (sin ella se usa la heurística local) |
| `GEMINI_MODEL` | `gemini-flash-lite-latest,gemini-flash-latest,gemini-3.8-flash` | Modelos de Gemini en orden de preferencia (si uno falla se usa el siguiente) |
| `CONTACT_EMAIL` | — | Contacto opcional agregado al User-Agent |
| `MAX_CONCURRENCY` | `10` | Pedidos simultáneos máximos a Wikimedia |
| `HTTP_TIMEOUT` | `30` | Timeout general de las APIs externas (s) |
| `OVERPASS_URLS` | 3 servidores públicos | Lista separada por comas |
| `DATABASE_URL` | — | PostgreSQL. Sin ella la app funciona, pero sin cuentas ni índice local |
| `JWT_SECRET` | aleatorio por proceso | Firma de las sesiones. **Obligatoria en producción** (si no, se cierran en cada reinicio) |
| `SESSION_DAYS` | `7` | Duración de la sesión |
| `COOKIE_SECURE` | `true` en Railway | Cookie de sesión solo por HTTPS |

## Créditos de datos

Mapas y lugares © colaboradores de [OpenStreetMap](https://www.openstreetmap.org/copyright) (ODbL).
Datos de Wikidata (CC0) e imágenes de Wikimedia Commons con la licencia y autoría que indica cada foto.
