// Réplica exacta de app/services/text.py y de la fórmula de Haversine del backend.

export function normalize(text) {
  if (!text) return "";
  return text
    .normalize("NFD")
    .replace(/\p{Mn}/gu, "")
    .toLowerCase()
    .replace(/\s+/g, " ")
    .trim();
}

export function matches(query, ...fields) {
  const tokens = normalize(query).split(" ").filter(Boolean);
  if (!tokens.length) return true;
  const haystack = fields.filter(Boolean).map(normalize).join(" ");
  return tokens.every((t) => haystack.includes(t));
}

const R = 6371.0088;
const rad = (d) => (d * Math.PI) / 180;

export function haversineKm(a, b) {
  const dLat = rad(b.lat - a.lat);
  const dLon = rad(b.lon - a.lon);
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(rad(a.lat)) * Math.cos(rad(b.lat)) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(h)));
}

const nf1 = new Intl.NumberFormat("es-AR", { maximumFractionDigits: 1 });
const money = new Intl.NumberFormat("es-AR", { minimumFractionDigits: 0, maximumFractionDigits: 2 });

export const fmtKm = (km) => (km < 1 ? `${Math.round(km * 1000)} m` : `${nf1.format(km)} km`);
export const fmtUsd = (v) => (v === 0 ? "Gratis" : `US$ ${money.format(v)}`);
export const fmtUsdStrict = (v) => `US$ ${money.format(v)}`;
export const fmtRating = (r) => nf1.format(r);

export function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}
