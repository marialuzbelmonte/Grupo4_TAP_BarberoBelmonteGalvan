// Moneda en la que se muestran los precios. Todo se calcula y se guarda en US$;
// acá solo se convierte para mostrar, con la cotización que da el servidor (/api/rates).
// El peso argentino va al dólar blue (venta), y eso se aclara siempre en pantalla.
import { escapeHtml } from "./text.js";
import { api } from "./ui.js";

export const money = {
  code: "USD",
  rate: 1, // unidades de `code` por 1 US$
  rates: null, // respuesta de /api/rates (se pide una sola vez)
};

const listeners = new Set();
const formatters = new Map();
const dateOpts = { day: "numeric", month: "short", year: "numeric" };
// ExchangeRate-API publica a las 00:00 UTC: en hora argentina caería el día anterior.
const utcDate = new Intl.DateTimeFormat("es-AR", { ...dateOpts, timeZone: "UTC" });
const arDate = new Intl.DateTimeFormat("es-AR", { ...dateOpts, timeZone: "America/Argentina/Buenos_Aires" });
const rateFmt = new Intl.NumberFormat("es-AR", { maximumFractionDigits: 2 });

export const onCurrencyChange = (fn) => listeners.add(fn);

export async function loadRates() {
  if (!money.rates) money.rates = await (await api("/rates")).json();
  return money.rates;
}

/** Cambia la moneda. Si no hay cotización disponible, queda en dólares y lanza el error. */
export async function setCurrency(code) {
  let rate = 1;
  if (code !== "USD") {
    const { rates } = await loadRates();
    if (!rates[code]) throw new Error(`No hay cotización para ${code}.`);
    rate = rates[code];
  }
  if (code === money.code && rate === money.rate) return;
  Object.assign(money, { code, rate });
  listeners.forEach((fn) => fn());
}

function formatter() {
  // Monedas con valores grandes (pesos, yenes…): sin centavos.
  const key = `${money.code}:${money.rate >= 100}`;
  if (!formatters.has(key)) {
    formatters.set(key, new Intl.NumberFormat("es-AR", {
      style: "currency", currency: money.code, minimumFractionDigits: 0, maximumFractionDigits: money.rate >= 100 ? 0 : 2,
    }));
  }
  return formatters.get(key);
}

export const fmtMoney = (usd) => formatter().format(usd * money.rate);
export const fmtPrice = (usd) => (usd === 0 ? "Gratis" : fmtMoney(usd));

/** Cartel con la cotización usada, o "" si se muestra en dólares. */
export function exchangeNoteHtml() {
  if (money.code === "USD" || !money.rates) return "";
  const blue = money.code === "ARS" ? money.rates.ars : null;
  const origin = blue ?? money.rates;
  const when = origin.updated ? `, ${(blue ? arDate : utcDate).format(new Date(origin.updated))}` : "";
  const title = blue ? "Cotización dólar blue" : `Precios en ${money.code}`;
  const text = blue
    ? `1 US$ = ${fmtMoney(1)} (precio de venta del blue, ${origin.source}${when}). Los costos se estiman en dólares y se pasan a pesos con esa cotización.`
    : `1 US$ = ${rateFmt.format(money.rate)} ${money.code} (${origin.source}${when}). Cotización de referencia.`;
  return `<p class="fx-note${blue ? " is-blue" : ""}" role="note"><strong>${escapeHtml(title)}</strong> ${escapeHtml(text)}</p>`;
}
