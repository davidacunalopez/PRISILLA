from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.db import excel_repo
from app.db.excel_repo import ExcelBloqueadoError
from app.db.schema import HOJAS, PERIODICIDAD_MESES, fila_vacia
from app.services.alertas import seguros_con_alerta
from app.services.fechas import add_months, formato_crc, iso, parse_fecha
from app.services.formularios import datos_form, validar

router = APIRouter(prefix="/seguros")
HOJA = "Seguros"


def _enriquecer(filas: list[dict]) -> list[dict]:
    camiones = excel_repo.mapa_display("Camiones")
    alertas = {s["ID_Seguro"]: s for s in seguros_con_alerta(solo_activos=False)}
    out = []
    for f in filas:
        extra = alertas.get(str(f.get("ID_Seguro", "")), {})
        out.append(
            {
                **f,
                "placa": camiones.get(str(f.get("ID_Camion", "")), f.get("ID_Camion", "")),
                "semaforo": extra.get("semaforo", "gris"),
                "urgencia": extra.get("urgencia", ""),
                "dias": extra.get("dias"),
                "monto_txt": formato_crc(f.get("MontoPrima")),
            }
        )
    return out


def _ctx(request: Request, **extra):
    camiones = [c for c in excel_repo.leer("Camiones") if c.get("Estado") == "Activo"]
    if not camiones:
        camiones = excel_repo.leer("Camiones")
    actual = (extra.get("fila") or {}).get("ID_Camion")
    if actual and not any(str(c.get("ID_Camion")) == str(actual) for c in camiones):
        encontrado = excel_repo.leer_por_id("Camiones", actual)
        if encontrado:
            camiones.append(encontrado)
    base = {
        "titulo": "Seguros",
        "activo": "seguros",
        "choices": HOJAS[HOJA]["choices"],
        "camiones": camiones,
    }
    base.update(extra)
    return base


@router.get("", response_class=HTMLResponse)
async def lista(request: Request, estado: str = "Activo"):
    templates = request.app.state.templates
    filas = excel_repo.leer(HOJA)
    if estado and estado != "Todos":
        filas = [f for f in filas if f.get("Estado") == estado]
    filas = _enriquecer(filas)
    filas.sort(key=lambda s: (s.get("dias") is None, s.get("dias") if s.get("dias") is not None else 9999))
    return templates.TemplateResponse(
        request, "seguros/index.html", _ctx(request, filas=filas, filtro_estado=estado or "Activo")
    )


@router.get("/nuevo", response_class=HTMLResponse)
async def nuevo(request: Request, camion: str = ""):
    templates = request.app.state.templates
    fila = fila_vacia(HOJA)
    fila["Estado"] = "Activo"
    fila["Moneda"] = "CRC"
    fila["ID_Camion"] = camion
    return templates.TemplateResponse(
        request,
        "seguros/_form.html",
        _ctx(request, fila=fila, accion="/seguros", errores=[]),
    )


@router.get("/{id_seguro}/editar", response_class=HTMLResponse)
async def editar(request: Request, id_seguro: str):
    templates = request.app.state.templates
    fila = excel_repo.leer_por_id(HOJA, id_seguro)
    if not fila:
        return HTMLResponse("Seguro no encontrado", status_code=404)
    fila["FechaInicio"] = iso(fila.get("FechaInicio"))
    fila["FechaFin"] = iso(fila.get("FechaFin"))
    fila["FechaUltimoPago"] = iso(fila.get("FechaUltimoPago"))
    return templates.TemplateResponse(
        request,
        "seguros/_form.html",
        _ctx(request, fila=fila, accion=f"/seguros/{id_seguro}", errores=[]),
    )


@router.post("", response_class=HTMLResponse)
async def crear(request: Request):
    templates = request.app.state.templates
    datos = await datos_form(request, HOJA)
    if not datos.get("Moneda"):
        datos["Moneda"] = "CRC"
    errores = validar(HOJA, datos)
    if errores:
        return templates.TemplateResponse(
            request,
            "seguros/_form.html",
            _ctx(request, fila={**fila_vacia(HOJA), **datos}, accion="/seguros", errores=errores),
            status_code=422,
        )
    try:
        excel_repo.insertar(HOJA, datos)
    except ExcelBloqueadoError as exc:
        return templates.TemplateResponse(
            request,
            "seguros/_form.html",
            _ctx(request, fila={**fila_vacia(HOJA), **datos}, accion="/seguros", errores=[str(exc)]),
            status_code=409,
        )
    return RedirectResponse("/seguros", status_code=303)


@router.post("/{id_seguro}", response_class=HTMLResponse)
async def guardar(request: Request, id_seguro: str):
    templates = request.app.state.templates
    datos = await datos_form(request, HOJA)
    errores = validar(HOJA, datos, id_seguro)
    fila = {**(excel_repo.leer_por_id(HOJA, id_seguro) or fila_vacia(HOJA)), **datos, "ID_Seguro": id_seguro}
    if errores:
        return templates.TemplateResponse(
            request,
            "seguros/_form.html",
            _ctx(request, fila=fila, accion=f"/seguros/{id_seguro}", errores=errores),
            status_code=422,
        )
    try:
        excel_repo.actualizar(HOJA, id_seguro, datos)
    except ExcelBloqueadoError as exc:
        return templates.TemplateResponse(
            request,
            "seguros/_form.html",
            _ctx(request, fila=fila, accion=f"/seguros/{id_seguro}", errores=[str(exc)]),
            status_code=409,
        )
    return RedirectResponse("/seguros", status_code=303)


@router.post("/{id_seguro}/renovar")
async def renovar(request: Request, id_seguro: str):
    actual = excel_repo.leer_por_id(HOJA, id_seguro)
    if not actual or actual.get("Estado") != "Activo":
        return RedirectResponse("/seguros", status_code=303)
    fin = parse_fecha(actual.get("FechaFin"))
    meses = PERIODICIDAD_MESES.get(str(actual.get("Periodicidad") or "Semestral"), 6)
    if fin is None:
        return RedirectResponse("/seguros?error=fecha", status_code=303)
    inicio_nuevo = fin + timedelta(days=1)
    fin_nuevo = add_months(inicio_nuevo, meses) - timedelta(days=1)
    nuevo = {
        "ID_Camion": actual.get("ID_Camion", ""),
        "TipoSeguro": actual.get("TipoSeguro", ""),
        "Aseguradora": actual.get("Aseguradora", ""),
        "NumeroPoliza": actual.get("NumeroPoliza", ""),
        "Periodicidad": actual.get("Periodicidad", ""),
        "FechaInicio": inicio_nuevo.isoformat(),
        "FechaFin": fin_nuevo.isoformat(),
        "MontoPrima": actual.get("MontoPrima", ""),
        "Moneda": actual.get("Moneda") or "CRC",
        "Estado": "Activo",
        "FechaUltimoPago": "",
        "Notas": f"Renovación de {actual.get('ID_Seguro', '')}",
    }
    try:
        excel_repo.actualizar_e_insertar(HOJA, id_seguro, {"Estado": "Renovado"}, nuevo)
    except ExcelBloqueadoError:
        return RedirectResponse("/seguros?error=bloqueado", status_code=303)
    return RedirectResponse("/seguros", status_code=303)


@router.post("/{id_seguro}/eliminar")
async def borrar(request: Request, id_seguro: str):
    try:
        excel_repo.eliminar(HOJA, id_seguro)
    except (ExcelBloqueadoError, KeyError):
        return RedirectResponse("/seguros?error=bloqueado", status_code=303)
    return RedirectResponse("/seguros", status_code=303)
