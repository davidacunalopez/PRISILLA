from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.db import excel_repo
from app.db.excel_repo import ExcelBloqueadoError
from app.db.schema import HOJAS, fila_vacia
from app.services.formularios import datos_form, validar

router = APIRouter(prefix="/empresas")
HOJA = "Empresas"


def _ctx(request: Request, **extra):
    base = {"titulo": "Estaciones", "activo": "estaciones", "choices": HOJAS[HOJA]["choices"]}
    base.update(extra)
    return base


@router.get("", response_class=HTMLResponse)
async def lista(request: Request):
    templates = request.app.state.templates
    filas = excel_repo.leer(HOJA)
    filas.sort(key=lambda e: (e.get("Estado") != "Activo", str(e.get("NombreEmpresa") or "").lower()))
    return templates.TemplateResponse(request, "empresas/index.html", _ctx(request, filas=filas))


@router.get("/nuevo", response_class=HTMLResponse)
async def nuevo(request: Request):
    templates = request.app.state.templates
    fila = fila_vacia(HOJA)
    fila["Estado"] = "Activo"
    return templates.TemplateResponse(
        request,
        "empresas/_form.html",
        _ctx(request, fila=fila, accion="/empresas", errores=[]),
    )


@router.get("/{id_empresa}/editar", response_class=HTMLResponse)
async def editar(request: Request, id_empresa: str):
    templates = request.app.state.templates
    fila = excel_repo.leer_por_id(HOJA, id_empresa)
    if not fila:
        return HTMLResponse("Estación no encontrada", status_code=404)
    return templates.TemplateResponse(
        request,
        "empresas/_form.html",
        _ctx(request, fila=fila, accion=f"/empresas/{id_empresa}", errores=[]),
    )


@router.post("", response_class=HTMLResponse)
async def crear(request: Request):
    templates = request.app.state.templates
    datos = await datos_form(request, HOJA)
    errores = validar(HOJA, datos)
    if errores:
        return templates.TemplateResponse(
            request,
            "empresas/_form.html",
            _ctx(request, fila={**fila_vacia(HOJA), **datos}, accion="/empresas", errores=errores),
            status_code=422,
        )
    try:
        excel_repo.insertar(HOJA, datos)
    except ExcelBloqueadoError as exc:
        return templates.TemplateResponse(
            request,
            "empresas/_form.html",
            _ctx(request, fila={**fila_vacia(HOJA), **datos}, accion="/empresas", errores=[str(exc)]),
            status_code=409,
        )
    return RedirectResponse("/empresas", status_code=303)


@router.post("/{id_empresa}", response_class=HTMLResponse)
async def guardar(request: Request, id_empresa: str):
    templates = request.app.state.templates
    datos = await datos_form(request, HOJA)
    errores = validar(HOJA, datos, id_empresa)
    fila = {**(excel_repo.leer_por_id(HOJA, id_empresa) or fila_vacia(HOJA)), **datos, "ID_Empresa": id_empresa}
    if errores:
        return templates.TemplateResponse(
            request,
            "empresas/_form.html",
            _ctx(request, fila=fila, accion=f"/empresas/{id_empresa}", errores=errores),
            status_code=422,
        )
    try:
        excel_repo.actualizar(HOJA, id_empresa, datos)
    except ExcelBloqueadoError as exc:
        return templates.TemplateResponse(
            request,
            "empresas/_form.html",
            _ctx(request, fila=fila, accion=f"/empresas/{id_empresa}", errores=[str(exc)]),
            status_code=409,
        )
    return RedirectResponse("/empresas", status_code=303)


@router.post("/{id_empresa}/archivar")
async def archivar(request: Request, id_empresa: str):
    try:
        excel_repo.actualizar(HOJA, id_empresa, {"Estado": "Inactivo"})
    except (ExcelBloqueadoError, KeyError):
        return RedirectResponse("/empresas?error=bloqueado", status_code=303)
    return RedirectResponse("/empresas", status_code=303)


@router.post("/{id_empresa}/reactivar")
async def reactivar(request: Request, id_empresa: str):
    try:
        excel_repo.actualizar(HOJA, id_empresa, {"Estado": "Activo"})
    except (ExcelBloqueadoError, KeyError):
        return RedirectResponse("/empresas?error=bloqueado", status_code=303)
    return RedirectResponse("/empresas", status_code=303)
