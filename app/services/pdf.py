"""Exportación del itinerario y el presupuesto a PDF (ReportLab)."""
from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.models import Itinerary

INK = colors.HexColor("#1D2A33")
MUTED = colors.HexColor("#56626B")
PAPER = colors.HexColor("#F4EFE6")
RULE = colors.HexColor("#CFC6B6")
ACCENT = colors.HexColor("#A8361F")
SEA = colors.HexColor("#2F5D62")


def _register_fonts() -> tuple[str, str]:
    """DejaVu cubre más alfabetos que Helvetica (nombres en griego, cirílico, etc.)."""
    candidates = [
        Path("/usr/share/fonts/truetype/dejavu"),
        Path(__file__).resolve().parent.parent.parent / "static" / "fonts",
    ]
    for folder in candidates:
        regular, bold = folder / "DejaVuSans.ttf", folder / "DejaVuSans-Bold.ttf"
        if regular.exists() and bold.exists():
            if "DejaVu" not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont("DejaVu", str(regular)))
                pdfmetrics.registerFont(TTFont("DejaVu-Bold", str(bold)))
            return "DejaVu", "DejaVu-Bold"
    return "Helvetica", "Helvetica-Bold"


SYMBOLS = {"USD": "US$", "ARS": "$", "EUR": "€", "GBP": "£", "BRL": "R$"}


def _number(value: float, decimals: int = 2) -> str:
    whole = f"{value:,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return whole[:-3] if decimals and whole.endswith(",00") else whole


@dataclass(frozen=True)
class Money:
    """Moneda en la que se muestran los montos (calculados siempre en US$)."""
    code: str = "USD"
    rate: float = 1.0  # unidades de `code` por 1 US$
    updated: str | None = None
    source: str = "ExchangeRate-API"
    label: str | None = None  # tipo de cotización, p. ej. "dólar blue (venta)"

    def __call__(self, usd: float) -> str:
        # Monedas con valores grandes (pesos, yenes...): sin centavos.
        decimals = 0 if self.rate >= 100 else 2
        return f"{SYMBOLS.get(self.code, self.code)} {_number(usd * self.rate, decimals)}"

    def note(self) -> str | None:
        if self.code == "USD":
            return None
        # ExchangeRate-API publica a las 00:00 UTC (en hora argentina sería el día anterior);
        # el blue se informa con la hora de Buenos Aires.
        day = _date(self.updated, _ARGENTINA if self.label else timezone.utc)
        when = f", {day:%d/%m/%Y}" if day else ""
        rate = f"1 US$ = {_number(self.rate)} {self.code} ({self.source}{when})"
        if self.label:
            return f"Cotización {self.label}: {rate}. Montos convertidos desde dólares; es una referencia."
        return f"Montos convertidos de dólares a {self.code}: {rate}. Es una cotización de referencia."


_ARGENTINA = timezone(timedelta(hours=-3))  # sin horario de verano


def _date(value: str | None, tz: timezone) -> datetime | None:
    """Fecha de la cotización: ISO 8601 (DolarApi) o RFC 2822 (ExchangeRate-API)."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
    return parsed.astimezone(tz) if parsed.tzinfo else parsed


def render(itinerary: Itinerary, money: Money = Money()) -> bytes:
    regular, bold = _register_fonts()
    s = {
        "kicker": ParagraphStyle("kicker", fontName=bold, fontSize=8, leading=10, textColor=ACCENT, spaceAfter=4),
        "title": ParagraphStyle("title", fontName=bold, fontSize=22, leading=26, textColor=INK, spaceAfter=6),
        "body": ParagraphStyle("body", fontName=regular, fontSize=9.5, leading=13, textColor=INK),
        "muted": ParagraphStyle("muted", fontName=regular, fontSize=8.5, leading=11.5, textColor=MUTED),
        "day": ParagraphStyle("day", fontName=bold, fontSize=13, leading=16, textColor=SEA, spaceBefore=10, spaceAfter=4),
        "cell": ParagraphStyle("cell", fontName=regular, fontSize=8.5, leading=11, textColor=INK),
        "cellb": ParagraphStyle("cellb", fontName=bold, fontSize=8.5, leading=11, textColor=INK),
        "right": ParagraphStyle("right", fontName=regular, fontSize=8.5, leading=11, textColor=INK, alignment=TA_RIGHT),
        "note": ParagraphStyle("note", fontName=bold, fontSize=8.5, leading=12, textColor=ACCENT),
    }
    p = lambda text, style="body": Paragraph(escape(text), s[style])  # noqa: E731

    story = [
        p("RUMBO · CUADERNO DE ITINERARIO", "kicker"),
        p(itinerary.title, "title"),
        p(itinerary.summary),
        Spacer(1, 3 * mm),
        p(f"Destino: {itinerary.destination}   ·   Base: {itinerary.hotel.name}   ·   "
          f"Viajeros: {itinerary.budget.travelers}", "muted"),
        Spacer(1, 4 * mm),
    ]

    header_style = [
        ("FONTNAME", (0, 0), (-1, 0), bold),
        ("FONTSIZE", (0, 0), (-1, 0), 7.5),
        ("TEXTCOLOR", (0, 0), (-1, 0), MUTED),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, INK),
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, RULE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ]

    for day in itinerary.days:
        rows = [["HORA", "PARADA", "DURACIÓN", "COSTO p/p"]]
        for stop in day.stops:
            detail = f"<b>{escape(stop.name)}</b> · {escape(stop.category_label)}"
            if stop.tip:
                detail += f"<br/><font color='#56626B'>{escape(stop.tip)}</font>"
            rows.append([
                Paragraph(stop.time, s["cellb"]),
                Paragraph(detail, s["cell"]),
                Paragraph(f"{stop.duration_min} min", s["cell"]),
                Paragraph(money(stop.cost_usd), s["right"]),
            ])
        if not day.stops:
            rows.append(["", p("Día libre", "cell"), "", ""])
        table = Table(rows, colWidths=[16 * mm, 110 * mm, 22 * mm, 26 * mm], repeatRows=1)
        table.setStyle(TableStyle(header_style + [("ALIGN", (3, 0), (3, 0), "RIGHT")]))
        story.append(KeepTogether([
            p(f"Día {day.day} — {day.theme}", "day"),
            p(f"{len(day.stops)} paradas · {f'{day.distance_km:.1f}'.replace('.', ',')} km de recorrido · subtotal {money(day.subtotal_usd)} p/p", "muted"),
            Spacer(1, 2 * mm),
            table,
        ]))

    budget = itinerary.budget
    rows = [["DÍA", "ATRACCIÓN", "UNITARIO", "PERSONAS", "TOTAL"]]
    for r in budget.rows:
        rows.append([
            str(r.day),
            Paragraph(escape(r.name), s["cell"]),
            Paragraph(money(r.unit_usd), s["right"]),
            Paragraph(str(r.travelers), s["right"]),
            Paragraph(money(r.total_usd), s["right"]),
        ])
    rows.append(["", Paragraph("<b>Total estimado</b>", s["cell"]), "", "",
                 Paragraph(f"<b>{money(budget.total_usd)}</b>", s["right"])])
    table = Table(rows, colWidths=[12 * mm, 92 * mm, 24 * mm, 20 * mm, 26 * mm], repeatRows=1)
    table.setStyle(TableStyle(header_style + [
        ("LINEABOVE", (0, -1), (-1, -1), 0.8, INK),
        ("BACKGROUND", (0, -1), (-1, -1), PAPER),
        ("FONTNAME", (0, 1), (0, -1), regular),
        ("FONTSIZE", (0, 1), (0, -1), 8.5),
        ("ALIGN", (2, 0), (-1, 0), "RIGHT"),
    ]))
    story += [Spacer(1, 6 * mm), p("Presupuesto", "day"), table, Spacer(1, 4 * mm), p(budget.note, "note")]
    if money.note():
        story += [Spacer(1, 2 * mm), p(money.note(), "cellb" if money.label else "muted")]

    if itinerary.tips:
        story += [Spacer(1, 4 * mm), p("Consejos", "day")]
        story += [p(f"— {tip}") for tip in itinerary.tips]

    def decorate(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(ACCENT)
        canvas.setLineWidth(2)
        canvas.line(18 * mm, A4[1] - 12 * mm, A4[0] - 18 * mm, A4[1] - 12 * mm)
        canvas.setFont(regular, 7.5)
        canvas.setFillColor(MUTED)
        origin = "Gemini + heurística de ruta" if itinerary.source == "gemini" else "heurística de ruta local"
        canvas.drawString(18 * mm, 10 * mm, f"Generado con Rumbo · {origin} · Datos © OpenStreetMap, Wikidata, Wikimedia Commons")
        canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"{doc.page}")
        canvas.restoreState()

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=18 * mm, bottomMargin=18 * mm, title=itinerary.title, author="Rumbo")
    doc.build(story, onFirstPage=decorate, onLaterPages=decorate)
    return buffer.getvalue()
