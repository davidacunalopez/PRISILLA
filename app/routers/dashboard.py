from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.db import excel_repo
from app.services.alertas import resumen_alertas
from app.services.fechas import a_float, formato_crc, mostrar_fecha, parse_fecha
from app.services.mensajes import es_verdadero

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    templates = request.app.state.templates
    alertas = resumen_alertas()
    camiones = excel_repo.leer("Camiones")
    activos = [c for c in camiones if str(c.get("Estado", "")).strip() == "Activo"]
    viajes = excel_repo.leer("Viajes")
    viajes.sort(key=lambda v: str(v.get("Fecha") or v.get("Desde") or ""), reverse=True)
    empresas = excel_repo.mapa_display("Empresas")
    recientes = []
    for v in viajes[:6]:
        recientes.append(
            {
                **v,
                "fecha_txt": mostrar_fecha(v.get("Fecha") or v.get("Desde")),
                "salida": empresas.get(str(v.get("ID_Salida", "")), v.get("ID_Salida", "")),
                "llegada": empresas.get(str(v.get("ID_Llegada", "")), v.get("ID_Llegada", "")),
            }
        )
    gasolina = excel_repo.leer("Gasolina")
    total_gas = 0.0
    for g in gasolina:
        try:
            total_gas += float(g.get("MontoTotal") or 0)
        except (TypeError, ValueError):
            pass

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "titulo": "Tablero",
            "activo": "dashboard",
            "alertas": alertas,
            "n_camiones": len(activos),
            "n_viajes": len(viajes),
            "n_pendientes": sum(1 for v in viajes if v.get("Estado") == "Pendiente"),
            "total_gas": formato_crc(total_gas) if gasolina else "—",
            "recientes": recientes,
            "semana": _resumen_semana(viajes),
        },
    )


def _resumen_semana(viajes: list[dict]) -> dict:
    """Viajes de la semana ISO en curso, comparando el año además del número."""
    anio, numero, _ = date.today().isocalendar()
    de_la_semana = []
    for viaje in viajes:
        fecha = parse_fecha(viaje.get("Fecha") or viaje.get("Desde"))
        if fecha and fecha.isocalendar()[:2] == (anio, numero):
            de_la_semana.append(viaje)

    pendientes = [
        viaje
        for viaje in de_la_semana
        if not (es_verdadero(viaje.get("Enviada")) or viaje.get("Estado") == "Enviado a contadora")
    ]
    total_crc = sum(
        a_float(v.get("Precio")) for v in de_la_semana if str(v.get("Moneda") or "CRC") == "CRC"
    )
    total_usd = sum(a_float(v.get("Precio")) for v in de_la_semana if str(v.get("Moneda")) == "USD")
    return {
        "numero": numero,
        "registrados": len(de_la_semana),
        "pendientes": len(pendientes),
        "total_crc_txt": formato_crc(total_crc),
        "total_usd_txt": f"${total_usd:,.2f}" if total_usd else "",
    }
