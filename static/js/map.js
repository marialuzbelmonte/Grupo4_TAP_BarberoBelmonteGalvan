// Capa de presentación del mapa (Leaflet sobre teselas de OpenStreetMap).
import { escapeHtml } from "./text.js";

const L = window.L;

export const CATEGORY_STYLE = {
  museos: { glyph: "M", color: "#6B4E71" },
  monumentos: { glyph: "H", color: "#8A5A2B" },
  atracciones: { glyph: "A", color: "#B03A22" },
  parques: { glyph: "P", color: "#56702F" },
  templos: { glyph: "T", color: "#3E5C8A" },
  restaurantes: { glyph: "R", color: "#2F5D62" },
  cafes: { glyph: "C", color: "#7D5510" },
};

export const DAY_COLORS = ["#B03A22", "#2F5D62", "#6B4E71", "#7D5510", "#56702F", "#3E5C8A", "#8A5A2B"];

const pinIcon = (category, selected, label) => {
  const style = CATEGORY_STYLE[category] ?? { glyph: "•", color: "#1D2A33" };
  return L.divIcon({
    className: "pin-wrap",
    html: `<span class="pin${selected ? " is-selected" : ""}" style="--pin:${style.color}"><b>${escapeHtml(label ?? style.glyph)}</b></span>`,
    iconSize: [28, 28],
    iconAnchor: [14, 14],
  });
};

const hotelIcon = L.divIcon({
  className: "pin-wrap",
  html: '<span class="pin-hotel" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M3 20V9l9-5 9 5v11h-6v-6H9v6z" fill="currentColor"/></svg></span>',
  iconSize: [36, 36],
  iconAnchor: [18, 18],
});

export class MapView {
  constructor(element) {
    this.map = L.map(element, { zoomControl: false, worldCopyJump: true }).setView([-34.6037, -58.3816], 4);
    L.control.zoom({ position: "bottomright" }).addTo(this.map);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    }).addTo(this.map);
    this.poiLayer = L.layerGroup().addTo(this.map);
    this.routeLayer = L.layerGroup().addTo(this.map);
    this.markers = new Map();
    this.hotelMarker = null;
    this.radiusCircle = null;
    this.pickHandler = null;
    // El contenedor cambia de tamaño con la bandeja inferior y el layout responsive.
    new ResizeObserver(() => this.map.invalidateSize()).observe(element);
  }

  focusDestination(place) {
    if (place.bbox) {
      const [south, north, west, east] = place.bbox;
      this.map.flyToBounds([[south, west], [north, east]], { duration: 0.9, maxZoom: 14, padding: [20, 20] });
    } else {
      this.map.flyTo([place.lat, place.lon], 13, { duration: 0.9 });
    }
  }

  setHotel(hotel, radius, onMove) {
    const latlng = [hotel.lat, hotel.lon];
    if (!this.hotelMarker) {
      this.hotelMarker = L.marker(latlng, { icon: hotelIcon, draggable: true, keyboard: true, title: "Hotel", zIndexOffset: 1000 }).addTo(this.map);
      this.hotelMarker.on("dragend", () => {
        const { lat, lng } = this.hotelMarker.getLatLng();
        this.radiusCircle?.setLatLng([lat, lng]);
        this.onHotelMove?.({ lat, lon: lng });
      });
    } else {
      this.hotelMarker.setLatLng(latlng);
    }
    this.onHotelMove = onMove;
    this.hotelMarker.bindTooltip(escapeHtml(hotel.name), { direction: "top", offset: [0, -16] });
    this.setRadius(latlng, radius);
    this.map.flyTo(latlng, Math.max(this.map.getZoom(), 14), { duration: 0.7 });
  }

  setRadius(latlng, radius) {
    if (!this.radiusCircle) {
      this.radiusCircle = L.circle(latlng, { radius, className: "radius-ring", interactive: false }).addTo(this.map);
    } else {
      this.radiusCircle.setLatLng(latlng).setRadius(radius);
    }
  }

  setPickMode(active, onPick) {
    const container = this.map.getContainer();
    container.classList.toggle("is-picking", active);
    if (this.pickHandler) this.map.off("click", this.pickHandler);
    this.pickHandler = null;
    if (active) {
      this.pickHandler = (e) => onPick({ lat: e.latlng.lat, lon: e.latlng.lng });
      this.map.on("click", this.pickHandler);
    }
  }

  renderAttractions(list, selected, onFocus) {
    this.poiLayer.clearLayers();
    this.markers.clear();
    for (const a of list) {
      const marker = L.marker([a.lat, a.lon], {
        icon: pinIcon(a.category, selected.has(a.id)),
        title: a.name,
        riseOnHover: true,
      });
      marker.bindTooltip(escapeHtml(a.name), { direction: "top", offset: [0, -12] });
      marker.on("click", () => onFocus(a.id));
      marker.addTo(this.poiLayer);
      this.markers.set(a.id, { marker, attraction: a });
    }
  }

  refreshSelection(selected) {
    for (const [id, { marker, attraction }] of this.markers) {
      marker.setIcon(pinIcon(attraction.category, selected.has(id)));
      marker.setZIndexOffset(selected.has(id) ? 500 : 0);
    }
  }

  pulse(id) {
    const entry = this.markers.get(id);
    if (!entry) return;
    const el = entry.marker.getElement()?.querySelector(".pin");
    if (el) {
      el.classList.remove("is-pulsing");
      void el.offsetWidth;
      el.classList.add("is-pulsing");
    }
  }

  drawRoute(itinerary) {
    this.routeLayer.clearLayers();
    this.map.getContainer().classList.add("has-route");
    const hotel = [itinerary.hotel.lat, itinerary.hotel.lon];
    const bounds = L.latLngBounds([hotel]);
    itinerary.days.forEach((day, i) => {
      if (!day.stops.length) return;
      const color = DAY_COLORS[i % DAY_COLORS.length];
      const path = [hotel, ...day.stops.map((s) => [s.lat, s.lon]), hotel];
      L.polyline(path, { color, weight: 3, opacity: 0.85, dashArray: "2 7", lineCap: "round", interactive: false }).addTo(this.routeLayer);
      day.stops.forEach((s, j) => {
        const m = L.marker([s.lat, s.lon], {
          icon: L.divIcon({
            className: "pin-wrap",
            html: `<span class="pin pin-step" style="--pin:${color}">${day.day}·${j + 1}</span>`,
            iconSize: [34, 22],
            iconAnchor: [17, 11],
          }),
          zIndexOffset: 800,
        });
        m.bindTooltip(`${escapeHtml(s.time)} · ${escapeHtml(s.name)}`, { direction: "top", offset: [0, -10] });
        m.addTo(this.routeLayer);
        bounds.extend([s.lat, s.lon]);
      });
    });
    this.map.flyToBounds(bounds, { padding: [40, 40], duration: 0.8, maxZoom: 16 });
  }

  clearRoute() {
    this.routeLayer.clearLayers();
    this.map.getContainer().classList.remove("has-route");
  }
}
