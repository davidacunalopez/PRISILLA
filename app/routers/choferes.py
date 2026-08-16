from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.db import excel_repo
from app.db.excel_repo import ExcelBloqueadoError
from app.db.schema import HOJAS, fila_vacia
from app.services.formularios import datos_form, validar

router = APIRouter(prefix="/choferes")
HOJA = "Choferes"


def _ctx(request: Request, **extra):
    base = {"titulo": "Choferes", "activo": "choferes", "choices": HOJAS[HOJA]["choices"]}
    base.update(extra)
    return base


@router.get("", response_class=HTMLResponse)
async def lista(request: Request):
    templates = request.app.state.templates
    filas = excel_repo.leer(HOJA)
    filas.sort(key=lambda c: (c.get("Estado") != "Activo", str(c.get("Nombre") or "").lower()))
    return templates.TemplateResponse(request, "choferes/index.html", _ctx(request, filas=filas))


@router.get("/nuevo", response_class=HTMLResponse)
async def nuevo(request: Request):
    templates = request.app.state.templates
    fila = fila_vacia(HOJA)
    fila["Estado"] = "Activo"
    return templates.TemplateResponse(
        request,
        "choferes/_form.html",
        _ctx(request, fila=fila, accion="/choferes", errores=[]),
    )


@router.get("/{id_chofer}/editar", response_class=HTMLResponse)
async def editar(request: Request, id_chofer: str):
    templates = request.app.state.templates
    fila = excel_repo.leer_por_id(HOJA, id_chofer)
    if not fila:
        return HTMLResponse("Chofer no encontrado", status_code=404)
    return templates.TemplateResponse(
        request,
        "choferes/_form.html",
        _ctx(request, fila=fila, accion=f"/choferes/{id_chofer}", errores=[]),
    )


@router.post("", response_class=HTMLResponse)
async def crear(request: Request):
    templates = request.app.state.templates
    datos = await datos_form(request, HOJA)
    errores = validar(HOJA, datos)
    if errores:
        return templates.TemplateResponse(
            request,
            "choferes/_form.html",
            _ctx(request, fila={**fila_vacia(HOJA), **datos}, accion="/choferes", errores=errores),
            status_code=422,
        )
    try:
        excel_repo.insertar(HOJA, datos)
    except ExcelBloqueadoError as exc:
        return templates.TemplateResponse(
            request,
            "choferes/_form.html",
            _ctx(request, fila={**fila_vacia(HOJA), **datos}, accion="/choferes", errores=[str(exc)]),
            status_code=409,
        )
    return RedirectResponse("/choferes", status_code=303)


@router.post("/{id_chofer}", response_class=HTMLResponse)
async def guardar(request: Request, id_chofer: str):
    templates = request.app.state.templates
    datos = await datos_form(request, HOJA)
    errores = validar(HOJA, datos, id_chofer)
    fila = {**(excel_repo.leer_por_id(HOJA, id_chofer) or fila_vacia(HOJA)), **datos, "ID_Chofer": id_chofer}
    if errores:
        return templates.TemplateResponse(
            request,
            "choferes/_form.html",
            _ctx(request, fila=fila, accion=f"/choferes/{id_chofer}", errores=errores),
            status_code=422,
        )
    try:
        excel_repo.actualizar(HOJA, id_chofer, datos)
    except ExcelBloqueadoError as exc:
        return templates.TemplateResponse(
            request,
            "choferes/_form.html",
            _ctx(request, fila=fila, accion=f"/choferes/{id_chofer}", errores=[str(exc)]),
            status_code=409,
        )
    return RedirectResponse("/choferes", status_code=303)


@router.post("/{id_chofer}/archivar")
async def archivar(request: Request, id_chofer: str):
    try:
        excel_repo.actualizar(HOJA, id_chofer, {"Estado": "Inactivo"})
    except (ExcelBloqueadoError, KeyError):
        return RedirectResponse("/choferes?error=bloqueado", status_code=303)
    return RedirectResponse("/choferes", status_code=303)


@router.post("/{id_chofer}/reactivar")
async def reactivar(request: Request, id_chofer: str):
    try:
        excel_repo.actualizar(HOJA, id_chofer, {"Estado": "Activo"})
    except (ExcelBloqueadoError, KeyError):
        return RedirectResponse("/choferes?error=bloqueado", status_code=303)
    return RedirectResponse("/choferes", status_code=303)
