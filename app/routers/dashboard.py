from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.db import excel_repo
from app.services.alertas import resumen_alertas
from app.services.fechas import formato_crc, mostrar_fecha

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
        },
    )
