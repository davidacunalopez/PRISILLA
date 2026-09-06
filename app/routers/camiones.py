from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.db import excel_repo
from app.db.excel_repo import ExcelBloqueadoError
from app.db.schema import HOJAS, fila_vacia
from app.config import load_config, save_config
from app.services.formularios import datos_form, validar

router = APIRouter(prefix="/camiones")
HOJA = "Camiones"


def _marcas() -> list[str]:
    configuradas = [str(m).strip() for m in load_config().get("marcas_camiones", []) if str(m).strip()]
    existentes = [str(c.get("Marca") or "").strip() for c in excel_repo.leer(HOJA)]
    unicas = {}
    for marca in configuradas + existentes:
        if marca:
            unicas.setdefault(marca.casefold(), marca)
    return sorted(unicas.values(), key=str.casefold)


def _normalizar_marca(datos: dict, errores: list[str]) -> None:
    """Si el usuario escribió una marca nueva (en vez de elegir una existente), la registra en Configuración."""
    marca = str(datos.get("Marca") or "").strip()
    datos["Marca"] = marca
    if not marca:
        return
    if len(marca) > 50:
        errores.append("La marca no puede tener más de 50 caracteres.")
        return
    if any(c in marca for c in "\r\n"):
        errores.append("La marca debe ocupar una sola línea.")
        return
    if marca.casefold() not in {m.casefold() for m in _marcas()}:
        cfg = load_config()
        marcas = list(cfg.get("marcas_camiones") or [])
        marcas.append(marca)
        save_config({"marcas_camiones": marcas})


def _ctx(request: Request, **extra):
    choferes = [c for c in excel_repo.leer("Choferes") if c.get("Estado") == "Activo"]
    actual = (extra.get("fila") or {}).get("ID_ChoferPredeterminado")
    if actual and not any(str(c.get("ID_Chofer")) == str(actual) for c in choferes):
        encontrado = excel_repo.leer_por_id("Choferes", actual)
        if encontrado:
            choferes.append(encontrado)
    choferes.sort(key=lambda c: str(c.get("Nombre") or "").lower())
    base = {
        "titulo": "Camiones",
        "activo": "camiones",
        "choices": HOJAS[HOJA]["choices"],
        "choferes": choferes,
        "marcas": _marcas(),
    }
    base.update(extra)
    return base


@router.get("", response_class=HTMLResponse)
async def lista(request: Request):
    templates = request.app.state.templates
    filas = excel_repo.leer(HOJA)
    nombres_chofer = excel_repo.mapa_display("Choferes")
    for fila in filas:
        fila["chofer_predeterminado"] = nombres_chofer.get(
            str(fila.get("ID_ChoferPredeterminado") or ""), ""
        )
    filas.sort(key=lambda c: (c.get("Estado") != "Activo", str(c.get("Placa") or "")))
    return templates.TemplateResponse(request, "camiones/index.html", _ctx(request, filas=filas))


@router.get("/nuevo", response_class=HTMLResponse)
async def nuevo(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "camiones/_form.html",
        _ctx(request, fila=fila_vacia(HOJA), accion="/camiones", errores=[]),
    )


@router.get("/{id_camion}/editar", response_class=HTMLResponse)
async def editar(request: Request, id_camion: str):
    templates = request.app.state.templates
    fila = excel_repo.leer_por_id(HOJA, id_camion)
    if not fila:
        return HTMLResponse("Camión no encontrado", status_code=404)
    return templates.TemplateResponse(
        request,
        "camiones/_form.html",
        _ctx(request, fila=fila, accion=f"/camiones/{id_camion}", errores=[]),
    )


@router.post("", response_class=HTMLResponse)
async def crear(request: Request):
    templates = request.app.state.templates
    datos = await datos_form(request, HOJA)
    datos["Placa"] = datos.get("Placa", "").upper()
    errores = validar(HOJA, datos)
    _normalizar_marca(datos, errores)
    if errores:
        return templates.TemplateResponse(
            request,
            "camiones/_form.html",
            _ctx(request, fila={**fila_vacia(HOJA), **datos}, accion="/camiones", errores=errores),
            status_code=422,
        )
    try:
        excel_repo.insertar(HOJA, datos)
    except ExcelBloqueadoError as exc:
        return templates.TemplateResponse(
            request,
            "camiones/_form.html",
            _ctx(request, fila={**fila_vacia(HOJA), **datos}, accion="/camiones", errores=[str(exc)]),
            status_code=409,
        )
    return RedirectResponse("/camiones?ok=creado", status_code=303)


@router.post("/{id_camion}", response_class=HTMLResponse)
async def guardar(request: Request, id_camion: str):
    templates = request.app.state.templates
    datos = await datos_form(request, HOJA)
    datos["Placa"] = datos.get("Placa", "").upper()
    errores = validar(HOJA, datos, id_camion)
    _normalizar_marca(datos, errores)
    fila = {**(excel_repo.leer_por_id(HOJA, id_camion) or fila_vacia(HOJA)), **datos, "ID_Camion": id_camion}
    if errores:
        return templates.TemplateResponse(
            request,
            "camiones/_form.html",
            _ctx(request, fila=fila, accion=f"/camiones/{id_camion}", errores=errores),
            status_code=422,
        )
    try:
        excel_repo.actualizar(HOJA, id_camion, datos)
    except ExcelBloqueadoError as exc:
        return templates.TemplateResponse(
            request,
            "camiones/_form.html",
            _ctx(request, fila=fila, accion=f"/camiones/{id_camion}", errores=[str(exc)]),
            status_code=409,
        )
    return RedirectResponse("/camiones?ok=actualizado", status_code=303)


@router.post("/{id_camion}/archivar")
async def archivar(request: Request, id_camion: str):
    try:
        excel_repo.actualizar(HOJA, id_camion, {"Estado": "Inactivo"})
    except (ExcelBloqueadoError, KeyError):
        return RedirectResponse("/camiones?error=bloqueado", status_code=303)
    return RedirectResponse("/camiones?ok=archivado", status_code=303)


@router.post("/{id_camion}/reactivar")
async def reactivar(request: Request, id_camion: str):
    try:
        excel_repo.actualizar(HOJA, id_camion, {"Estado": "Activo"})
    except (ExcelBloqueadoError, KeyError):
        return RedirectResponse("/camiones?error=bloqueado", status_code=303)
    return RedirectResponse("/camiones?ok=reactivado", status_code=303)
