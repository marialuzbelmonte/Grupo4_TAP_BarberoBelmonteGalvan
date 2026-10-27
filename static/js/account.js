// Cuentas de usuario: ingreso, registro, perfil, sesión e itinerarios guardados.
// La sesión vive en una cookie httpOnly que este código no puede leer: para saber
// si hay alguien conectado se le pregunta al servidor (/api/auth/me).
import { fmtMoney, loadRates, money, onCurrencyChange, setCurrency } from "./money.js";
import { escapeHtml } from "./text.js";
import { $, api, busy, downloadPdf, toast } from "./ui.js";

export const session = { user: null, available: false };

let openItinerary = () => {};
let pendingLogin = null; // {resolve} de requireLogin() mientras el diálogo está abierto

const dateFmt = new Intl.DateTimeFormat("es-AR", { day: "numeric", month: "short", year: "numeric" });

export async function initAccount(options) {
  openItinerary = options.openItinerary;
  try {
    const health = await (await api("/health")).json();
    session.available = health.database;
  } catch {
    session.available = false;
  }
  if (session.available) {
    try {
      session.user = await (await api("/auth/me")).json(); // null si no hay sesión
    } catch {
      session.user = null;
    }
  }
  renderAccount();
  bindDialogs();
  onCurrencyChange(renderAccount);
  if (session.user) applyCurrency(session.user.currency);
}

// La moneda del perfil. Si no hay cotización, se sigue mostrando en dólares.
async function applyCurrency(code) {
  try {
    await setCurrency(code ?? "USD");
  } catch {
    await setCurrency("USD");
    toast("No pudimos traer la cotización del día: los precios se muestran en dólares.", "error");
  }
}

function renderAccount() {
  const box = $("#account");
  if (!session.available) {
    box.innerHTML = "";
    return;
  }
  box.innerHTML = session.user
    ? `<button class="account-btn account-saved" type="button" id="openSaved">Mis itinerarios</button>
       <button class="account-btn" type="button" id="openProfile" aria-label="Perfil de ${escapeHtml(session.user.name)}">
         <span class="account-avatar" aria-hidden="true">${escapeHtml(session.user.name.trim().charAt(0).toUpperCase())}</span>
         <span class="account-name">${escapeHtml(session.user.name)}</span>
         <span class="account-cur mono" title="Moneda de los precios">${escapeHtml(money.code)}</span>
       </button>`
    : `<button class="account-btn" type="button" id="openLogin">Ingresar</button>`;
  $("#openLogin")?.addEventListener("click", () => openAuth("login"));
  $("#openSaved")?.addEventListener("click", openSaved);
  $("#openProfile")?.addEventListener("click", openProfile);
}

// ------------------------------------------------------------------ ingreso y registro

function openAuth(tab) {
  showTab(tab);
  for (const id of ["#loginError", "#registerError"]) $(id).textContent = "";
  $("#authDialog").showModal();
  $(tab === "login" ? "#loginEmail" : "#registerName").focus();
}

function showTab(tab) {
  const login = tab === "login";
  $("#tabLogin").setAttribute("aria-selected", String(login));
  $("#tabRegister").setAttribute("aria-selected", String(!login));
  $("#loginForm").hidden = !login;
  $("#registerForm").hidden = login;
}

/** Abre el ingreso si hace falta. Resuelve con el usuario, o null si se cancela. */
export function requireLogin(reason) {
  if (session.user) return Promise.resolve(session.user);
  if (!session.available) {
    toast("Las cuentas no están disponibles en este momento.", "error");
    return Promise.resolve(null);
  }
  $("#authReason").textContent = reason ?? "";
  $("#authReason").hidden = !reason;
  return new Promise((resolve) => {
    pendingLogin = { resolve };
    openAuth("login");
  });
}

function finishLogin(user) {
  session.user = user;
  renderAccount();
  applyCurrency(user.currency);
  $("#authDialog").close();
  toast(`¡Hola, ${user.name}!`);
  pendingLogin?.resolve(user);
  pendingLogin = null;
}

async function submitAuth(e, path, fields, errorEl) {
  e.preventDefault();
  const button = e.submitter ?? e.target.querySelector("button[type=submit]");
  const body = Object.fromEntries(fields.map(([key, sel]) => [key, $(sel).value]));
  $(errorEl).textContent = "";
  busy(button, true, "Un momento…");
  try {
    finishLogin(await (await api(path, { method: "POST", body: JSON.stringify(body) })).json());
    e.target.reset();
  } catch (err) {
    $(errorEl).textContent = err.message;
  } finally {
    busy(button, false);
  }
}

async function logout() {
  await api("/auth/logout", { method: "POST" }).catch(() => {});
  session.user = null;
  $("#profileDialog").close();
  renderAccount();
  await setCurrency("USD");
  toast("Cerraste sesión.");
}

// ------------------------------------------------------------------ perfil

async function openProfile() {
  const { name, email } = session.user;
  $("#profileName").value = name;
  $("#profileEmail").value = email;
  $("#profileError").textContent = "";
  const select = $("#profileCurrency");
  select.innerHTML = `<option value="${escapeHtml(money.code)}">${escapeHtml(money.code)}</option>`;
  $("#profileDialog").showModal();
  try {
    const { currencies, rates } = await loadRates();
    select.innerHTML = currencies
      .filter((c) => rates[c.code])
      .map((c) => `<option value="${c.code}"${c.code === session.user.currency ? " selected" : ""}>${c.code} · ${escapeHtml(c.name)}</option>`)
      .join("");
  } catch {
    select.innerHTML = '<option value="USD">USD · Dólar estadounidense</option>';
    $("#profileError").textContent = "No pudimos traer las cotizaciones: por ahora solo hay dólares.";
  }
  updateCurrencyHint();
}

const CURRENCY_HINT = "Los costos se estiman en dólares y se convierten con la cotización del día.";
const BLUE_HINT = "Los pesos argentinos se calculan al dólar blue (precio de venta), no al oficial.";

function updateCurrencyHint() {
  $("#currencyHint").textContent = $("#profileCurrency").value === "ARS" ? BLUE_HINT : CURRENCY_HINT;
}

async function saveProfile(e) {
  e.preventDefault();
  const button = e.submitter ?? e.target.querySelector("button[type=submit]");
  const body = { name: $("#profileName").value.trim(), currency: $("#profileCurrency").value };
  $("#profileError").textContent = "";
  busy(button, true, "Guardando…");
  try {
    session.user = await (await api("/me", { method: "PATCH", body: JSON.stringify(body) })).json();
    await applyCurrency(session.user.currency);
    renderAccount();
    $("#profileDialog").close();
    toast(money.code === "USD" ? "Perfil guardado."
      : money.code === "ARS" ? "Perfil guardado. Los precios se muestran en pesos, al dólar blue."
      : `Perfil guardado. Los precios se muestran en ${money.code}.`);
  } catch (err) {
    $("#profileError").textContent = err.message;
  } finally {
    busy(button, false);
  }
}

// ------------------------------------------------------------------ mis itinerarios

export async function saveItinerary(itinerary, button) {
  if (!(await requireLogin("Iniciá sesión para guardar este itinerario."))) return;
  busy(button, true, "Guardando…");
  try {
    await api("/me/itineraries", { method: "POST", body: JSON.stringify(itinerary) });
    button.textContent = "Guardado ✓";
    button.dataset.label = "Guardado ✓";
    toast("Itinerario guardado en tu cuaderno.");
  } catch (err) {
    toast(err.message, "error");
  } finally {
    busy(button, false);
  }
}

async function openSaved() {
  const list = $("#savedList");
  list.innerHTML = '<li class="saved-empty">Cargando…</li>';
  $("#savedDialog").showModal();
  try {
    renderSaved(await (await api("/me/itineraries")).json());
  } catch (err) {
    list.innerHTML = `<li class="saved-empty">${escapeHtml(err.message)}</li>`;
  }
}

function renderSaved(items) {
  const list = $("#savedList");
  if (!items.length) {
    list.innerHTML = '<li class="saved-empty">Todavía no guardaste itinerarios. Armá uno y tocá <strong>Guardar</strong>.</li>';
    return;
  }
  list.innerHTML = items
    .map(
      (it) => `
      <li class="saved-item" data-id="${it.id}">
        <div class="saved-info">
          <p class="saved-title">${escapeHtml(it.title)}</p>
          <p class="saved-meta mono">${escapeHtml(it.destination)} · ${it.days} ${it.days === 1 ? "día" : "días"} · ${it.stops} paradas · ${fmtMoney(it.total_usd)} · ${dateFmt.format(new Date(it.created_at))}</p>
        </div>
        <div class="saved-actions">
          <button class="btn btn-ink btn-small" type="button" data-action="open">Ver</button>
          <button class="btn btn-line btn-small" type="button" data-action="pdf">PDF</button>
          <button class="btn btn-line btn-small btn-danger" type="button" data-action="delete" aria-label="Eliminar ${escapeHtml(it.title)}">Eliminar</button>
        </div>
      </li>`,
    )
    .join("");
}

async function onSavedAction(e) {
  const button = e.target.closest("button[data-action]");
  if (!button) return;
  const item = button.closest(".saved-item");
  const id = item.dataset.id;
  try {
    if (button.dataset.action === "delete") {
      if (!confirm("¿Eliminar este itinerario? No se puede deshacer.")) return;
      await api(`/me/itineraries/${id}`, { method: "DELETE" });
      item.remove();
      if (!$("#savedList").children.length) renderSaved([]);
      return;
    }
    busy(button, true, "…");
    const itinerary = await (await api(`/me/itineraries/${id}`)).json();
    if (button.dataset.action === "pdf") {
      await downloadPdf(itinerary, money.code);
    } else {
      $("#savedDialog").close();
      openItinerary(itinerary);
    }
  } catch (err) {
    toast(err.message, "error");
  } finally {
    if (button.isConnected) busy(button, false);
  }
}

// ------------------------------------------------------------------ eventos

function bindDialogs() {
  $("#tabLogin").addEventListener("click", () => showTab("login"));
  $("#tabRegister").addEventListener("click", () => showTab("register"));
  $("#loginForm").addEventListener("submit", (e) =>
    submitAuth(e, "/auth/login", [["email", "#loginEmail"], ["password", "#loginPassword"]], "#loginError"),
  );
  $("#registerForm").addEventListener("submit", (e) =>
    submitAuth(e, "/auth/register", [["name", "#registerName"], ["email", "#registerEmail"], ["password", "#registerPassword"]], "#registerError"),
  );
  $("#authDialog").addEventListener("close", () => {
    pendingLogin?.resolve(null); // se cerró sin ingresar
    pendingLogin = null;
  });
  $("#savedList").addEventListener("click", onSavedAction);
  $("#profileForm").addEventListener("submit", saveProfile);
  $("#profileCurrency").addEventListener("change", updateCurrencyHint);
  $("#logout").addEventListener("click", logout);
  $("#profileSaved").addEventListener("click", () => {
    $("#profileDialog").close();
    openSaved();
  });
  // Clic en el fondo oscuro cierra el diálogo.
  for (const dialog of document.querySelectorAll("dialog.modal")) {
    dialog.addEventListener("click", (e) => {
      if (e.target === dialog) dialog.close();
    });
  }
}
