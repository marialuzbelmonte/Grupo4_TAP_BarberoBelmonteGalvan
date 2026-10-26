// Utilidades de interfaz compartidas por los módulos del cliente.

export const $ = (sel) => document.querySelector(sel);

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

export async function api(path, options = {}) {
  const res = await fetch(`/api${path}`, {
    headers: options.body ? { "Content-Type": "application/json" } : undefined,
    credentials: "same-origin", // manda la cookie de sesión
    ...options,
  });
  if (!res.ok) {
    let detail = `Error ${res.status}`;
    try {
      const data = await res.json();
      if (typeof data.detail === "string") detail = data.detail;
      else if (Array.isArray(data.detail) && data.detail[0]?.msg) detail = validationMessage(data.detail[0]);
    } catch { /* respuesta sin JSON */ }
    throw new ApiError(detail, res.status);
  }
  return res;
}

// Mensajes de validación de Pydantic, en castellano para los campos de las cuentas.
function validationMessage(error) {
  const field = error.loc?.at(-1);
  if (field === "email") return "Revisá el email: no parece válido.";
  if (field === "password" && error.type === "string_too_short") return "La contraseña tiene que tener al menos 8 caracteres.";
  if (field === "name") return "Escribí tu nombre.";
  return "Revisá los datos ingresados.";
}

let toastTimer;
export function toast(message, kind = "info") {
  const el = $("#toast");
  el.textContent = message;
  el.dataset.kind = kind;
  el.classList.add("is-visible");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("is-visible"), 5200);
}

export function busy(button, isBusy, label) {
  if (isBusy) {
    button.dataset.label = button.textContent;
    button.textContent = label;
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
  } else {
    button.textContent = button.dataset.label ?? button.textContent;
    button.disabled = false;
    button.removeAttribute("aria-busy");
  }
}

export async function downloadPdf(itinerary) {
  const res = await api("/itinerary/pdf", { method: "POST", body: JSON.stringify(itinerary) });
  const url = URL.createObjectURL(await res.blob());
  const match = /filename="([^"]+)"/.exec(res.headers.get("Content-Disposition") ?? "");
  const link = Object.assign(document.createElement("a"), { href: url, download: match ? match[1] : "itinerario.pdf" });
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}
