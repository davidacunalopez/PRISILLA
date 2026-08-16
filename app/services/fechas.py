"""Utilidades de fechas, moneda y semáforo. Los días restantes se calculan en vivo."""

from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime
from typing import Any

from app.config import load_config


def parse_fecha(valor: Any) -> date | None:
    if valor is None or valor == "":
        return None
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    texto = str(valor).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(texto[:10], fmt).date()
        except ValueError:
            continue
    return None


def iso(valor: Any) -> str:
    d = parse_fecha(valor)
    return d.isoformat() if d else ""


def mostrar_fecha(valor: Any) -> str:
    d = parse_fecha(valor)
    return d.strftime("%d/%m/%Y") if d else ""


def hoy() -> date:
    return date.today()


def add_months(origen: date, months: int) -> date:
    month = origen.month - 1 + months
    year = origen.year + month // 12
    month = month % 12 + 1
    day = min(origen.day, monthrange(year, month)[1])
    return date(year, month, day)


def dias_restantes(fecha_fin: Any) -> int | None:
    d = parse_fecha(fecha_fin)
    if d is None:
        return None
    return (d - hoy()).days


def semaforo(fecha_fin: Any, umbrales: dict | None = None) -> str:
    dias = dias_restantes(fecha_fin)
    if dias is None:
        return "gris"
    cfg = umbrales or load_config()
    rojo = int(cfg.get("umbral_rojo_dias") or 7)
    amarillo = int(cfg.get("umbral_amarillo_dias") or 28)
    if dias <= rojo:
        return "rojo"
    if dias <= amarillo:
        return "amarillo"
    return "verde"


def etiqueta_urgencia(dias: int | None) -> str:
    if dias is None:
        return "Sin fecha"
    if dias < 0:
        n = abs(dias)
        return f"Venció hace {n} día" if n == 1 else f"Venció hace {n} días"
    if dias == 0:
        return "Vence hoy"
    if dias == 1:
        return "Vence mañana"
    return f"Vence en {dias} días"


def formato_crc(valor: Any) -> str:
    if valor is None or valor == "":
        return "—"
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return str(valor)
    entero = f"{numero:,.2f}"
    # Formato CR aproximado: 1.234.567,89
    entero = entero.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"₡{entero}"


def a_float(valor: Any, default: float = 0.0) -> float:
    if valor is None or valor == "":
        return default
    if isinstance(valor, (int, float)):
        return float(valor)
    texto = str(valor).strip().replace("₡", "").replace(" ", "")
    if "," in texto and "." in texto:
        texto = texto.replace(".", "").replace(",", ".")
    elif "," in texto:
        texto = texto.replace(",", ".")
    try:
        return float(texto)
    except ValueError:
        return default
