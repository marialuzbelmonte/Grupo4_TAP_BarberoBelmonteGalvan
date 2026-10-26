// Controlador del cliente: estado de la interfaz, eventos y llamadas REST.
import { initAccount, saveItinerary } from "./account.js";
import { CATEGORY_STYLE, DAY_COLORS, MapView } from "./map.js";
import { escapeHtml, fmtKm, fmtRating, fmtUsd, fmtUsdStrict, haversineKm, matches } from "./text.js";
import { $, api, busy, downloadPdf, toast } from "./ui.js";

const state = {
  destination: null, // {name, display_name, lat, lon, bbox}
  hotel: null, // {name, lat, lon}
  hotelMoved: false,
  categories: [], // [{slug, label}]
  attractions: [],
  activeCategories: new Set(),
  query: "",
  selected: new Set(),
  itinerary: null,
  picking: false,
  zone: null, // zona indexada en PostgreSQL (p. ej. Rafaela): la búsqueda va al servidor
  ftsRanks: null, // Map id -> relevancia de la última búsqueda full-text
};

const map = new MapView($("#map"));

// ------------------------------------------------------------------ utilidades

function unlock(step) {
  $(`#step-${step}`).classList.remove("is-locked");
  document.querySelectorAll(".progress li").forEach((li) => {
    li.classList.toggle("is-current", li.dataset.step === step);
    if (li.dataset.step !== step && !li.classList.contains("is-done")) {
      const order = ["destino", "hotel", "explorar", "itinerario"];
      if (order.indexOf(li.dataset.step) < order.indexOf(step)) li.classList.add("is-done");
    }
  });
}

function scrollToStep(step) {
  const el = $(`#step-${step}`);
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  el.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
}

function renderResults(list, container, onPick) {
  container.innerHTML = "";
  if (!list.length) {
    container.innerHTML = '<li class="results-empty">Sin resultados. Probá con otro nombre.</li>';
    return;
  }
  list.forEach((item, i) => {
    const li = document.createElement("li");
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "result";
    btn.style.setProperty("--i", i);
    btn.innerHTML = `<strong>${escapeHtml(item.name)}</strong><span>${escapeHtml(item.display_name)}</span>`;
    btn.addEventListener("click", () => {
      container.innerHTML = "";
      onPick(item);
    });
    li.append(btn);
    container.append(li);
  });
}

// ------------------------------------------------------------------ 01 destino

$("#destForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = $("#destInput").value.trim();
  if (q.length < 2) return;
  const button = e.submitter ?? e.target.querySelector("button");
  busy(button, true, "Buscando…");
  try {
    const list = await (await api(`/geocode?q=${encodeURIComponent(q)}`)).json();
    renderResults(list, $("#destResults"), chooseDestination);
  } catch (err) {
    toast(err.message, "error");
  } finally {
    busy(button, false);
  }
});

function chooseDestination(place) {
  state.destination = place;
  const chosen = $("#destChosen");
  chosen.hidden = false;
  chosen.innerHTML = `<span class="chosen-label">Destino</span> ${escapeHtml(place.display_name)}`;
  map.focusDestination(place);
  unlock("hotel");
  scrollToStep("hotel");
  setTimeout(() => $("#hotelInput").focus({ preventScroll: true }), 400);
}

// ------------------------------------------------------------------ 02 hotel

$("#hotelForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!state.destination) return;
  const q = $("#hotelInput").value.trim();
  if (q.length < 2) return;
  const button = e.submitter ?? e.target.querySelector("button");
  busy(button, true, "Buscando…");
  try {
    const { lat, lon } = state.destination;
    const list = await (await api(`/geocode?q=${encodeURIComponent(q)}&near_lat=${lat}&near_lon=${lon}`)).json();
    renderResults(list, $("#hotelResults"), (item) => setHotel({ name: item.name, lat: item.lat, lon: item.lon }));
  } catch (err) {
    toast(err.message, "error");
  } finally {
    busy(button, false);
  }
});

$("#useCenter").addEventListener("click", () => {
  if (!state.destination) return;
  const { lat, lon, name } = state.destination;
  setHotel({ name: `Centro de ${name}`, lat, lon });
});

$("#pickOnMap").addEventListener("click", () => setPicking(!state.picking));

function setPicking(active) {
  state.picking = active;
  $("#pickOnMap").setAttribute("aria-pressed", String(active));
  const hint = $("#mapHint");
  hint.hidden = !active;
  hint.textContent = "Tocá el mapa donde está tu hotel";
  map.setPickMode(active, (point) => {
    setPicking(false);
    setHotel({ name: "Punto marcado en el mapa", ...point });
  });
}

$("#radius").addEventListener("change", () => {
  if (state.hotel) loadAttractions();
});

function setHotel(hotel) {
  state.hotel = hotel;
  map.clearRoute();
  state.hotelMoved = false;
  $("#refreshHere").hidden = true;
  renderHotelChosen();
  map.setHotel(hotel, Number($("#radius").value), onHotelDragged);
  unlock("explorar");
  loadAttractions();
  scrollToStep("explorar");
}

function renderHotelChosen() {
  const chosen = $("#hotelChosen");
  chosen.hidden = false;
  chosen.innerHTML = `<span class="chosen-label">Base</span> ${escapeHtml(state.hotel.name)}
    <span class="mono">${state.hotel.lat.toFixed(5)}, ${state.hotel.lon.toFixed(5)}</span>`;
}

// Al arrastrar el pin, las distancias se recalculan al instante en el cliente.
function onHotelDragged(point) {
  const name = state.hotel.name.startsWith("Punto") ? state.hotel.name : `${state.hotel.name} (ajustado)`;
  state.hotel = { name, ...point };
  renderHotelChosen();
  for (const a of state.attractions) a.distance_km = haversineKm(state.hotel, a);
  state.attractions.sort((a, b) => a.distance_km - b.distance_km);
  state.hotelMoved = true;
  $("#refreshHere").hidden = false;
  renderGrid();
}

$("#refreshHere").addEventListener("click", () => {
  map.setRadius([state.hotel.lat, state.hotel.lon], Number($("#radius").value));
  $("#refreshHere").hidden = true;
  loadAttractions();
});

// ------------------------------------------------------------------ 03 explorar

async function loadCategories() {
  try {
    state.categories = await (await api("/categories")).json();
  } catch {
    state.categories = Object.keys(CATEGORY_STYLE).map((slug) => ({ slug, label: slug }));
  }
}

async function loadAttractions() {
  const { lat, lon } = state.hotel;
  const radius = $("#radius").value;
  $("#grid").innerHTML = Array.from({ length: 6 }, () => '<div class="card card-skeleton" aria-hidden="true"></div>').join("");
  $("#exploreMeta").textContent = "Buscando lugares cerca del hotel…";
  try {
    const data = await (await api(`/attractions?lat=${lat}&lon=${lon}&radius=${radius}`)).json();
    // Conserva lo elegido que siga existiendo en la nueva búsqueda.
    const previous = new Map(state.attractions.filter((a) => state.selected.has(a.id)).map((a) => [a.id, a]));
    const fresh = data.attractions;
    for (const [id, a] of previous) {
      if (!fresh.some((f) => f.id === id)) fresh.push({ ...a, distance_km: haversineKm(state.hotel, a) });
    }
    state.attractions = fresh.sort((a, b) => a.distance_km - b.distance_km);
    state.zone = data.meta.zone ?? null;
    state.ftsRanks = null;
    if (state.zone && state.query.trim()) await runFullText();
    renderMeta(data.meta);
    renderChips();
    renderGrid();
  } catch (err) {
    $("#grid").innerHTML = "";
    $("#exploreMeta").textContent = "";
    toast(`No pudimos traer atracciones: ${err.message}`, "error");
  }
}

function renderMeta(meta) {
  if (meta.zone) {
    $("#exploreMeta").innerHTML = `
      <span><strong>${meta.count}</strong> lugares</span>
      <span class="meta-note">Índice local de ${escapeHtml(meta.zone_name)}: búsqueda full-text en PostgreSQL</span>
      <span class="mono">${meta.db_ms} ms</span>`;
    return;
  }
  const speedup = meta.enrich_wall_ms > 0 ? meta.enrich_sequential_ms / meta.enrich_wall_ms : 1;
  $("#exploreMeta").innerHTML = `
    <span><strong>${meta.count}</strong> lugares</span>
    <span>${meta.with_wikidata} enriquecidos con Wikidata</span>
    <span class="mono" title="Tiempo real de las consultas concurrentes frente a la suma de cada consulta por separado">
      Wikidata + Commons en ${(meta.enrich_wall_ms / 1000).toFixed(1)} s
      ${speedup > 1.2 ? `(≈ ${speedup.toFixed(1)}× más rápido que en serie)` : ""}
    </span>`;
}

function renderChips() {
  const counts = {};
  for (const a of state.attractions) counts[a.category] = (counts[a.category] ?? 0) + 1;
  const chips = $("#chips");
  chips.innerHTML = "";
  const all = document.createElement("button");
  all.type = "button";
  all.className = "chip";
  all.textContent = "Todo";
  all.setAttribute("aria-pressed", String(state.activeCategories.size === 0));
  all.addEventListener("click", () => {
    state.activeCategories.clear();
    renderChips();
    renderGrid();
  });
  chips.append(all);
  for (const cat of state.categories) {
    if (!counts[cat.slug]) continue;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "chip";
    btn.style.setProperty("--chip", CATEGORY_STYLE[cat.slug]?.color ?? "#1D2A33");
    btn.innerHTML = `${escapeHtml(cat.label)} <span class="chip-count">${counts[cat.slug]}</span>`;
    btn.setAttribute("aria-pressed", String(state.activeCategories.has(cat.slug)));
    btn.addEventListener("click", () => {
      if (state.activeCategories.has(cat.slug)) state.activeCategories.delete(cat.slug);
      else state.activeCategories.add(cat.slug);
      renderChips();
      renderGrid();
    });
    chips.append(btn);
  }
}

let filterTimer;
$("#filterInput").addEventListener("input", (e) => {
  clearTimeout(filterTimer);
  // En zonas indexadas cada búsqueda va a PostgreSQL: se espera un poco más entre teclas.
  filterTimer = setTimeout(async () => {
    state.query = e.target.value;
    if (state.zone) await runFullText();
    renderGrid();
  }, state.zone ? 220 : 120);
});

async function runFullText() {
  const q = state.query.trim();
  const info = $("#ftsInfo");
  if (!q) {
    state.ftsRanks = null;
    info.hidden = true;
    return;
  }
  const { lat, lon } = state.hotel;
  const params = new URLSearchParams({ zone: state.zone, q, lat, lon, radius: $("#radius").value });
  try {
    const data = await (await api(`/places/search?${params}`)).json();
    state.ftsRanks = new Map(data.results.map((r) => [r.id, r.rank]));
    info.hidden = false;
    info.innerHTML = data.tsquery
      ? `PostgreSQL · <code>to_tsquery('${escapeHtml(data.tsquery)}')</code> · ${data.results.length} resultados en ${data.ms} ms, ordenados por relevancia`
      : "Escribí palabras para buscar.";
  } catch {
    state.ftsRanks = null; // si falla el servidor, se filtra en el navegador
    info.hidden = true;
  }
}

function visibleAttractions() {
  const inCategory = (a) => state.activeCategories.size === 0 || state.activeCategories.has(a.category);
  if (state.ftsRanks) {
    // Zona indexada: el orden lo da la relevancia calculada por PostgreSQL (ts_rank_cd).
    return state.attractions
      .filter((a) => inCategory(a) && state.ftsRanks.has(a.id))
      .sort((a, b) => state.ftsRanks.get(b.id) - state.ftsRanks.get(a.id) || a.distance_km - b.distance_km);
  }
  return state.attractions.filter((a) => inCategory(a) && matches(state.query, a.name, a.description, a.category_label));
}

function stars(rating) {
  const full = Math.round(rating);
  return `<span class="stars" aria-hidden="true">${"★".repeat(full)}<span>${"★".repeat(5 - full)}</span></span>`;
}

function cardHtml(a, index) {
  const style = CATEGORY_STYLE[a.category] ?? { glyph: "•", color: "#1D2A33" };
  const checked = state.selected.has(a.id);
  const media = a.image
    ? `<img src="${escapeHtml(a.image.url)}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer">`
    : `<div class="card-ph" aria-hidden="true"><span>${style.glyph}</span></div>`;
  const credit = a.image?.credit
    ? `<a class="card-credit" href="${escapeHtml(a.image.page ?? a.image.url)}" target="_blank" rel="noopener">Foto: ${escapeHtml(a.image.credit)}${a.image.license ? ` · ${escapeHtml(a.image.license)}` : ""}</a>`
    : "";
  const costNote = a.cost.source === "estimado" && a.cost.amount_usd > 0 ? "≈ " : "";
  return `
  <article class="card${checked ? " is-selected" : ""}" data-id="${escapeHtml(a.id)}" style="--cat:${style.color};--i:${Math.min(index, 12)}">
    <div class="card-media">
      ${media}
      <span class="card-cat">${escapeHtml(a.category_label)}</span>
      <span class="card-dist" data-dist>${fmtKm(a.distance_km)}<small> del hotel</small></span>
      ${credit}
    </div>
    <div class="card-body">
      <h3 class="card-title">${escapeHtml(a.name)}</h3>
      <p class="card-desc">${escapeHtml(a.description)}</p>
    </div>
    <dl class="card-facts">
      <div><dt>Costo</dt><dd title="${escapeHtml(a.cost.label)}">${costNote}${fmtUsd(a.cost.amount_usd)}</dd></div>
      <div><dt>Horario</dt><dd title="${escapeHtml(a.hours ?? "")}">${escapeHtml(a.hours ?? "Consultar")}</dd></div>
      <div><dt>Calificación</dt><dd title="Índice de relevancia calculado con Wikidata y OSM">${fmtRating(a.rating)} ${stars(a.rating)}</dd></div>
    </dl>
    <label class="card-pick">
      <input type="checkbox" ${checked ? "checked" : ""} data-pick="${escapeHtml(a.id)}">
      <span class="card-pick-box" aria-hidden="true"></span>
      <span class="card-pick-text">${checked ? "En el itinerario" : "Agregar al itinerario"}</span>
      <span class="sr-only">: ${escapeHtml(a.name)}</span>
    </label>
  </article>`;
}

function renderGrid() {
  const list = visibleAttractions();
  const grid = $("#grid");
  if (!state.attractions.length) {
    grid.innerHTML = '<p class="empty">No encontramos lugares en este radio. Probá ampliarlo o mover el hotel.</p>';
  } else if (!list.length) {
    grid.innerHTML = '<p class="empty">Ningún lugar coincide con el filtro.</p>';
  } else {
    grid.innerHTML = list.map(cardHtml).join("");
  }
  map.renderAttractions(list, state.selected, focusCard);
  updateTray();
}

$("#grid").addEventListener("change", (e) => {
  const id = e.target.dataset.pick;
  if (id) setSelected(id, e.target.checked);
});

function setSelected(id, on) {
  if (on) state.selected.add(id);
  else state.selected.delete(id);
  const card = document.querySelector(`.card[data-id="${CSS.escape(id)}"]`);
  if (card) {
    card.classList.toggle("is-selected", on);
    card.querySelector("input").checked = on;
    card.querySelector(".card-pick-text").textContent = on ? "En el itinerario" : "Agregar al itinerario";
  }
  map.refreshSelection(state.selected);
  updateTray();
}

// ------------------------------------------------------------------ pedidos a la IA

$("#askForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const request = $("#askInput").value.trim();
  if (request.length < 3 || !state.hotel) return;
  const button = e.submitter ?? e.target.querySelector("button");
  const out = $("#askResult");
  busy(button, true, "Buscando…");
  out.innerHTML = '<p class="ask-status">La IA está buscando entre los lugares reales de la zona…</p>';
  try {
    const body = {
      request, lat: state.hotel.lat, lon: state.hotel.lon,
      radius: Number($("#radius").value), exclude: [...state.selected],
    };
    const res = await (await api("/suggest", { method: "POST", body: JSON.stringify(body) })).json();
    renderSuggestions(res);
    $("#askInput").value = "";
  } catch (err) {
    out.innerHTML = `<p class="ask-status">${escapeHtml(err.message)}</p>`;
  } finally {
    busy(button, false);
  }
});

function renderSuggestions(res) {
  const out = $("#askResult");
  const by = res.source === "gemini" ? "Elegido por Gemini" : "Elegido por la búsqueda (Gemini no disponible)";
  if (!res.suggestions.length) {
    out.innerHTML = `<p class="ask-status">No encontré lugares para «${escapeHtml(res.interpreted_as)}» en este radio.
      Probá ampliarlo o pedir otra cosa.</p>`;
    return;
  }
  for (const { attraction } of res.suggestions) {
    // Si el lugar no estaba en la lista (otro filtro u otra búsqueda), se incorpora.
    if (!state.attractions.some((a) => a.id === attraction.id)) state.attractions.push(attraction);
    state.selected.add(attraction.id);
  }
  out.innerHTML = res.suggestions
    .map(
      ({ attraction: a, reason }) => `
      <div class="ask-pick" data-id="${escapeHtml(a.id)}">
        <span class="ask-check" aria-hidden="true">✓</span>
        <div>
          <p class="ask-name">Agregué <strong>${escapeHtml(a.name)}</strong> <span class="mono">· ${fmtKm(a.distance_km)} del hotel</span></p>
          <p class="ask-reason">${escapeHtml(reason)}</p>
          <p class="ask-by mono">${by} · entendí: «${escapeHtml(res.interpreted_as)}» · ${res.searched} candidatos</p>
        </div>
        <div class="ask-actions">
          <button class="btn btn-line btn-small" type="button" data-ask="view">Ver</button>
          <button class="btn btn-line btn-small" type="button" data-ask="remove">Quitar</button>
        </div>
      </div>`,
    )
    .join("");
  renderGrid();
}

$("#askResult").addEventListener("click", (e) => {
  const button = e.target.closest("button[data-ask]");
  if (!button) return;
  const pick = button.closest(".ask-pick");
  if (button.dataset.ask === "view") {
    focusCard(pick.dataset.id);
  } else {
    setSelected(pick.dataset.id, false);
    pick.remove();
  }
});

$("#grid").addEventListener("mouseover", (e) => {
  const card = e.target.closest(".card");
  if (card && card.dataset.id !== state.hovered) {
    state.hovered = card.dataset.id;
    map.pulse(card.dataset.id);
  }
});

function focusCard(id) {
  const card = document.querySelector(`.card[data-id="${CSS.escape(id)}"]`);
  if (!card) return;
  card.scrollIntoView({ behavior: "smooth", block: "center" });
  card.classList.remove("is-flash");
  void card.offsetWidth;
  card.classList.add("is-flash");
  card.querySelector("input")?.focus({ preventScroll: true });
}

// ------------------------------------------------------------------ bandeja

function updateTray() {
  const n = state.selected.size;
  const tray = $("#tray");
  tray.hidden = !state.hotel;
  document.body.classList.toggle("has-tray", !tray.hidden);
  $("#trayCount").textContent = n;
  $("#trayLabel").textContent = n === 1 ? "parada elegida" : "paradas elegidas";
  $("#generate").disabled = n === 0;
}

function clampInput(input) {
  const v = Math.round(Number(input.value));
  input.value = Math.min(Number(input.max), Math.max(Number(input.min), Number.isFinite(v) ? v : Number(input.min)));
  return Number(input.value);
}
["#days", "#travelers"].forEach((sel) => $(sel).addEventListener("change", (e) => clampInput(e.target)));

// ------------------------------------------------------------------ 04 itinerario

$("#generate").addEventListener("click", async (e) => {
  const button = e.currentTarget;
  const chosen = state.attractions.filter((a) => state.selected.has(a.id));
  if (!chosen.length) return;
  if (chosen.length > 40) {
    toast("Elegí hasta 40 paradas por itinerario.", "error");
    return;
  }
  const payload = {
    destination: state.destination.name,
    days: clampInput($("#days")),
    travelers: clampInput($("#travelers")),
    hotel: state.hotel,
    attractions: chosen.map((a) => ({ ...a, distance_km: Math.round(a.distance_km * 1000) / 1000 })),
  };
  busy(button, true, "Armando…");
  unlock("itinerario");
  $("#itinerary").innerHTML = '<div class="itin-loading"><span></span><p>Ordenando la ruta y consultando a Gemini…</p></div>';
  scrollToStep("itinerario");
  try {
    state.itinerary = await (await api("/itinerary", { method: "POST", body: JSON.stringify(payload) })).json();
    renderItinerary(state.itinerary);
    map.drawRoute(state.itinerary);
  } catch (err) {
    $("#itinerary").innerHTML = `<p class="empty">No se pudo armar el itinerario: ${escapeHtml(err.message)}</p>`;
    toast(err.message, "error");
  } finally {
    busy(button, false);
  }
});

function renderItinerary(it) {
  const days = it.days
    .map((day, i) => {
      const color = DAY_COLORS[i % DAY_COLORS.length];
      const stops = day.stops.length
        ? day.stops
            .map(
              (s, j) => `
          <li class="stop">
            <time class="stop-time mono">${escapeHtml(s.time)}</time>
            <div class="stop-body">
              <p class="stop-name"><span class="stop-idx">${day.day}·${j + 1}</span> ${escapeHtml(s.name)}</p>
              <p class="stop-meta mono">${escapeHtml(s.category_label)} · ${s.duration_min} min · ${j === 0 ? "desde el hotel" : "desde la anterior"} ${fmtKm(s.from_prev_km)} · ${fmtUsd(s.cost_usd)}</p>
              ${s.tip ? `<p class="stop-tip">${escapeHtml(s.tip)}</p>` : ""}
            </div>
          </li>`,
            )
            .join("")
        : '<li class="stop stop-free">Día libre para descansar o improvisar.</li>';
      return `
      <article class="day" style="--day:${color};--i:${i}">
        <header class="day-head">
          <span class="day-num">Día ${day.day}</span>
          <h3>${escapeHtml(day.theme)}</h3>
          <p class="mono">${day.stops.length} paradas · ${fmtKm(day.distance_km)} · ${fmtUsdStrict(day.subtotal_usd)} p/p</p>
        </header>
        <ol class="stops">${stops}</ol>
      </article>`;
    })
    .join("");

  const rows = it.budget.rows
    .map(
      (r) => `<tr>
        <td class="mono">${r.day}</td>
        <th scope="row">${escapeHtml(r.name)}<span class="src src-${r.source}">${r.source === "estimado" ? "estimado" : r.source === "wikidata" ? "Wikidata" : "OSM"}</span></th>
        <td class="num">${fmtUsdStrict(r.unit_usd)}</td>
        <td class="num">${r.travelers}</td>
        <td class="num">${fmtUsdStrict(r.total_usd)}</td>
      </tr>`,
    )
    .join("");

  const stats = it.route_stats ?? {};
  const saved = stats.nearest_neighbor_km && stats.two_opt_km < stats.nearest_neighbor_km
    ? ` 2-opt acortó el recorrido de ${fmtKm(stats.nearest_neighbor_km)} a ${fmtKm(stats.two_opt_km)}.` : "";

  $("#itinerary").innerHTML = `
    <div class="itin-head">
      <p class="kicker">${it.source === "gemini" ? "Itinerario generado con Gemini" : "Itinerario armado con la heurística local"}</p>
      <h3 class="itin-title">${escapeHtml(it.title)}</h3>
      <p class="itin-summary">${escapeHtml(it.summary)}</p>
      <p class="itin-route mono">Ruta ordenada con Vecino Más Cercano + 2-opt.${saved}</p>
      ${it.warning ? `<p class="warning" role="note">${escapeHtml(it.warning)}</p>` : ""}
    </div>
    <div class="days">${days}</div>
    ${it.tips.length ? `<aside class="tips"><h4>Consejos</h4><ul>${it.tips.map((t) => `<li>${escapeHtml(t)}</li>`).join("")}</ul></aside>` : ""}
    <section class="budget" aria-labelledby="h-budget">
      <h4 id="h-budget">Presupuesto de atracciones</h4>
      <div class="table-wrap">
        <table>
          <thead><tr><th scope="col">Día</th><th scope="col">Atracción</th><th scope="col" class="num">Unitario</th><th scope="col" class="num">Personas</th><th scope="col" class="num">Total</th></tr></thead>
          <tbody>${rows}</tbody>
          <tfoot><tr><td></td><th scope="row">Total estimado</th><td></td><td></td><td class="num">${fmtUsdStrict(it.budget.total_usd)}</td></tr></tfoot>
        </table>
      </div>
      <p class="budget-note">${escapeHtml(it.budget.note)}</p>
      <div class="itin-actions">
        <button class="btn btn-stamp" type="button" id="exportPdf">Exportar a PDF</button>
        <button class="btn btn-line" type="button" id="saveItin">Guardar en mis itinerarios</button>
      </div>
    </section>`;
  $("#exportPdf").addEventListener("click", exportPdf);
  $("#saveItin").addEventListener("click", (e) => saveItinerary(state.itinerary, e.currentTarget));
}

async function exportPdf(e) {
  const button = e.currentTarget;
  busy(button, true, "Generando PDF…");
  try {
    await downloadPdf(state.itinerary);
  } catch (err) {
    toast(`No se pudo exportar: ${err.message}`, "error");
  } finally {
    busy(button, false);
  }
}

// ------------------------------------------------------------------ arranque

// Abre un itinerario guardado: se muestra igual que uno recién generado.
function showSavedItinerary(it) {
  state.itinerary = it;
  unlock("itinerario");
  renderItinerary(it);
  map.drawRoute(it);
  scrollToStep("itinerario");
}

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && state.picking) setPicking(false);
});
loadCategories();
initAccount({ openItinerary: showSavedItinerary });
