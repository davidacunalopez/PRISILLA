from __future__ import annotations

import os
import asyncio
from datetime import date
from urllib.parse import parse_qsl, quote, urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.config import EXPORT_DIR, gemini_settings, load_config
from app.db import excel_repo
from app.db.excel_repo import ExcelBloqueadoError
from app.db.schema import HOJAS, fila_vacia
from app.services.exportar import nombre_export, viajes_csv, viajes_xlsx
from app.services.fechas import a_float, formato_crc, iso, mostrar_fecha, parse_fecha
from app.services.formularios import datos_form, validar
from app.services.ia import IaNoConfiguradaError, IaServicioError, redactar_mensaje_contadora_ia
from app.services.mensajes import es_verdadero, generar_mensaje_contadora

router = APIRouter(prefix="/viajes")
HOJA = "Viajes"


def _fecha_viaje(fila: dict) -> str:
    return iso(fila.get("Fecha") or fila.get("Desde"))


def _mapa_rutas() -> dict[str, dict]:
    return {
        f"{str(r.get('salida') or '')}|{str(r.get('llegada') or '')}": {
            "codigo": str(r.get("codigo") or ""),
            "precio": r.get("precio", ""),
            "moneda": str(r.get("moneda") or "CRC"),
        }
        for r in load_config().get("rutas_configuradas") or []
        if r.get("salida") and r.get("llegada") and r.get("codigo")
    }


def _datos_ruta(salida: str, llegada: str) -> dict:
    return _mapa_rutas().get(f"{salida}|{llegada}", {})


def _precio_txt(precio, moneda: str) -> str:
    numero = a_float(precio)
    if precio in (None, ""):
        return "—"
    return formato_crc(numero) if moneda == "CRC" else f"${numero:,.2f}"


def _preparar_datos(datos: dict, original: dict | None = None) -> list[str]:
    fecha = parse_fecha(datos.get("Fecha"))
    if fecha:
        datos["Fecha"] = fecha.isoformat()
        datos["Desde"] = fecha.isoformat()
        datos["Hasta"] = fecha.isoformat()
        datos["Semana"] = str(fecha.isocalendar().week)
    errores = []
    salida = str(datos.get("ID_Salida") or "")
    llegada = str(datos.get("ID_Llegada") or "")
    enviada_original = bool(
        original
        and (es_verdadero(original.get("Enviada")) or original.get("Estado") == "Enviado a contadora")
    )
    if enviada_original and original and (
        str(original.get("ID_Salida") or "") != salida
        or str(original.get("ID_Llegada") or "") != llegada
    ):
        errores.append(
            "Para cambiar la ruta de un viaje enviado, primero desmarque la casilla Enviada en la bitácora."
        )
    misma_ruta_historica = bool(
        original
        and enviada_original
        and str(original.get("ID_Salida") or "") == salida
        and str(original.get("ID_Llegada") or "") == llegada
        and original.get("CodigoRuta")
        and original.get("Precio") not in (None, "")
    )
    ruta = (
        {
            "codigo": original.get("CodigoRuta"),
            "precio": original.get("Precio"),
            "moneda": original.get("Moneda") or "CRC",
        }
        if misma_ruta_historica and original
        else _datos_ruta(salida, llegada)
    )
    datos["CodigoRuta"] = str(ruta.get("codigo") or "")
    precio_configurado = ruta.get("precio", "")
    moneda_configurada = str(ruta.get("moneda") or "CRC")
    precio = a_float(precio_configurado, -1)
    if precio_configurado not in (None, "") and precio > 0 and moneda_configurada in {"CRC", "USD"}:
        datos["Precio"] = round(precio, 2)
        datos["Moneda"] = moneda_configurada
    else:
        datos["Precio"] = ""
        datos["Moneda"] = moneda_configurada if moneda_configurada in {"CRC", "USD"} else "CRC"
    if salida and llegada and salida == llegada:
        datos["Categoria"] = "Ruptura"
    elif datos.get("Categoria") == "Ruptura":
        errores.append("La categoría Ruptura solo puede utilizarse cuando la salida y la llegada son la misma estación.")
    if datos.get("ID_Salida") and datos.get("ID_Llegada") and not datos.get("CodigoRuta"):
        errores.append(
            "La ruta seleccionada no tiene un código configurado. "
            "Agréguelo en Configuración → Códigos de rutas."
        )
    elif datos.get("CodigoRuta") and not datos.get("Precio"):
        errores.append(
            "La ruta seleccionada no tiene un precio vigente configurado. "
            "Agréguelo en Configuración → Códigos de rutas."
        )
    empresas_trabajo = load_config().get("empresas_trabajo") or []
    if not datos.get("EmpresaTrabajo"):
        errores.append("Seleccione la empresa para la que se realiza el viaje.")
    elif datos.get("EmpresaTrabajo") not in empresas_trabajo:
        errores.append("La empresa para la que se trabaja no es una opción configurada.")
    return errores


def _catalogos(incluir: dict | None = None):
    incluir = incluir or {}
    camiones = [c for c in excel_repo.leer("Camiones") if c.get("Estado") == "Activo"] or excel_repo.leer("Camiones")
    todos_choferes = excel_repo.leer("Choferes")
    choferes_disponibles = {str(c.get("ID_Chofer")) for c in todos_choferes if c.get("Estado") == "Activo"}
    choferes = [c for c in todos_choferes if c.get("Estado") == "Activo"] or todos_choferes
    empresas = [e for e in excel_repo.leer("Empresas") if e.get("Estado", "Activo") == "Activo"] or excel_repo.leer("Empresas")
    for lista, hoja, campo in (
        (camiones, "Camiones", "ID_Camion"),
        (choferes, "Choferes", "ID_Chofer"),
    ):
        actual = incluir.get(campo)
        if actual and not any(str(x.get(campo)) == str(actual) for x in lista):
            encontrado = excel_repo.leer_por_id(hoja, actual)
            if encontrado:
                lista.append(encontrado)
    for campo in ("ID_Salida", "ID_Llegada"):
        actual = incluir.get(campo)
        if actual and not any(str(x.get("ID_Empresa")) == str(actual) for x in empresas):
            encontrado = excel_repo.leer_por_id("Empresas", actual)
            if encontrado:
                empresas.append(encontrado)
    for camion in camiones:
        predeterminado = str(camion.get("ID_ChoferPredeterminado") or "")
        camion["ID_ChoferDisponible"] = predeterminado if predeterminado in choferes_disponibles else ""
    empresas.sort(key=lambda e: str(e.get("NombreEmpresa") or "").lower())
    return camiones, choferes, empresas


def _enriquecer(filas: list[dict]) -> list[dict]:
    empresas = excel_repo.mapa_display("Empresas")
    camiones = excel_repo.mapa_display("Camiones")
    choferes = excel_repo.mapa_display("Choferes")
    out = []
    for f in filas:
        out.append(
            {
                **f,
                "Fecha": _fecha_viaje(f),
                "fecha_txt": mostrar_fecha(f.get("Fecha") or f.get("Desde")),
                "salida": empresas.get(str(f.get("ID_Salida", "")), f.get("ID_Salida", "")),
                "llegada": empresas.get(str(f.get("ID_Llegada", "")), f.get("ID_Llegada", "")),
                "placa": camiones.get(str(f.get("ID_Camion", "")), f.get("ID_Camion", "")),
                "chofer": choferes.get(str(f.get("ID_Chofer", "")), f.get("ID_Chofer", "")),
                "precio_txt": _precio_txt(f.get("Precio"), str(f.get("Moneda") or "CRC")),
                "Enviada": es_verdadero(f.get("Enviada")) or f.get("Estado") == "Enviado a contadora",
            }
        )
    return out


def _filtrar(filas: list[dict], q: dict) -> list[dict]:
    desde = q.get("desde") or ""
    hasta = q.get("hasta") or ""
    salida = q.get("salida") or ""
    llegada = q.get("llegada") or ""
    contenedor = (q.get("contenedor") or "").strip().casefold()
    chasis = (q.get("chasis") or "").strip().casefold()
    guia = (q.get("guia") or "").strip().casefold()
    semana = str(q.get("semana") or "").strip()
    out = []
    for f in filas:
        viaje_fecha = _fecha_viaje(f)
        if desde and viaje_fecha and viaje_fecha < desde:
            continue
        if hasta and viaje_fecha and viaje_fecha > hasta:
            continue
        if salida and str(f.get("ID_Salida", "")) != salida:
            continue
        if llegada and str(f.get("ID_Llegada", "")) != llegada:
            continue
        if contenedor and contenedor not in str(f.get("Contenedor", "")).casefold():
            continue
        if chasis and chasis not in str(f.get("Chasis", "")).casefold():
            continue
        if guia and guia not in str(f.get("GuiaTirManifiesto", "")).casefold():
            continue
        if semana and str(f.get("Semana", "")) != semana:
            continue
        categoria = str(q.get("categoria") or "").strip()
        if categoria and str(f.get("Categoria") or "Viaje completo") != categoria:
            continue
        moneda = str(q.get("moneda") or "").strip()
        if moneda and str(f.get("Moneda") or "CRC") != moneda:
            continue
        empresa_trabajo = str(q.get("empresa_trabajo") or "").strip()
        if empresa_trabajo and str(f.get("EmpresaTrabajo") or "") != empresa_trabajo:
            continue
        codigo = str(q.get("codigo") or "").strip().casefold()
        if codigo and codigo not in str(f.get("CodigoRuta") or "").casefold():
            continue
        precio = a_float(f.get("Precio"))
        precio_min = str(q.get("precio_min") or "").strip()
        precio_max = str(q.get("precio_max") or "").strip()
        if precio_min and precio < a_float(precio_min):
            continue
        if precio_max and precio > a_float(precio_max):
            continue
        enviada = str(q.get("enviada") or "").strip()
        esta_enviada = es_verdadero(f.get("Enviada")) or f.get("Estado") == "Enviado a contadora"
        if enviada == "si" and not esta_enviada:
            continue
        if enviada == "no" and esta_enviada:
            continue
        out.append(f)
    return out


def _ctx(request: Request, **extra):
    camiones, choferes, empresas = _catalogos(extra.get("fila"))
    base = {
        "titulo": "Viajes",
        "activo": "viajes",
        "choices": HOJAS[HOJA]["choices"],
        "camiones": camiones,
        "choferes": choferes,
        "empresas": empresas,
        "semanas": [{"value": str(i), "label": f"Semana {i}"} for i in range(1, 54)],
        "monedas": HOJAS[HOJA]["choices"]["Moneda"],
        "categorias": HOJAS[HOJA]["choices"]["Categoria"],
        "empresas_trabajo": load_config().get("empresas_trabajo") or [],
        "mapa_rutas": _mapa_rutas(),
        "ia_lista": bool(gemini_settings()["enabled"] and gemini_settings()["api_key"]),
    }
    base.update(extra)
    return base


@router.get("/exportar/{fmt}")
async def exportar(request: Request, fmt: str):
    q = dict(request.query_params)
    filas = _enriquecer(_filtrar(excel_repo.leer(HOJA), q))
    filas.sort(key=_fecha_viaje)
    if fmt == "csv":
        data = viajes_csv(filas)
        filename = nombre_export("csv")
    else:
        data = viajes_xlsx(filas)
        filename = nombre_export("xlsx")
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    (EXPORT_DIR / filename).write_bytes(data)
    return RedirectResponse(f"/viajes?exportado={quote(filename)}", status_code=303)


@router.post("/abrir-exportaciones")
async def abrir_exportaciones(request: Request):
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.startfile(EXPORT_DIR)  # type: ignore[attr-defined]
    except OSError:
        return RedirectResponse("/viajes?error=carpeta", status_code=303)
    return RedirectResponse("/viajes", status_code=303)


@router.get("", response_class=HTMLResponse)
async def lista(request: Request):
    templates = request.app.state.templates
    permitidos = {
        "desde", "hasta", "semana", "salida", "llegada", "contenedor", "chasis", "guia",
        "moneda", "empresa_trabajo", "codigo", "precio_min", "precio_max", "categoria", "enviada",
    }
    q = {k: v for k, v in request.query_params.items() if k in permitidos}
    filas = _enriquecer(_filtrar(excel_repo.leer(HOJA), q))
    filas.sort(key=_fecha_viaje, reverse=True)
    return templates.TemplateResponse(
        request,
        "viajes/index.html",
        _ctx(
            request,
            filas=filas,
            filtros=q,
            query_export=urlencode(q),
            exportado=request.query_params.get("exportado", ""),
            mensaje_generado="",
            errores_mensaje=[],
        ),
    )


@router.post("/generar-mensaje", response_class=HTMLResponse)
async def generar_mensaje(request: Request):
    form = await request.form()
    permitidos = {
        "desde", "hasta", "semana", "salida", "llegada", "contenedor", "chasis", "guia",
        "moneda", "empresa_trabajo", "codigo", "precio_min", "precio_max", "categoria", "enviada",
    }
    q = {k: v for k, v in parse_qsl(str(form.get("query") or "")) if k in permitidos}
    todas = _filtrar(excel_repo.leer(HOJA), q)
    pendientes = [
        fila for fila in todas
        if not (es_verdadero(fila.get("Enviada")) or fila.get("Estado") == "Enviado a contadora")
    ]
    errores = []
    if not pendientes:
        errores.append("No hay viajes pendientes de envío en el filtro actual.")
    if any(not str(fila.get("EmpresaTrabajo") or "").strip() for fila in pendientes):
        errores.append("Todos los viajes deben tener una empresa de trabajo antes de generar el mensaje.")
    if any(not fila.get("CodigoRuta") or fila.get("Precio") in (None, "") for fila in pendientes):
        errores.append("Todos los viajes deben tener código y precio antes de generar el mensaje.")

    enriquecidas = _enriquecer(pendientes)
    mensaje = generar_mensaje_contadora(enriquecidas) if not errores else ""
    if mensaje and form.get("usar_ia") == "1":
        try:
            mensaje = await asyncio.to_thread(redactar_mensaje_contadora_ia, mensaje)
        except (IaNoConfiguradaError, IaServicioError) as exc:
            errores.append(str(exc))
            mensaje = ""
    if mensaje:
        cambios = {
            str(fila.get("ID_Viaje")): {"Enviada": True, "Estado": "Enviado a contadora"}
            for fila in pendientes
        }
        excel_repo.actualizar_varios(HOJA, cambios)

    filas = _enriquecer(_filtrar(excel_repo.leer(HOJA), q))
    filas.sort(key=_fecha_viaje, reverse=True)
    return request.app.state.templates.TemplateResponse(
        request,
        "viajes/index.html",
        _ctx(
            request,
            filas=filas,
            filtros=q,
            query_export=urlencode(q),
            exportado="",
            mensaje_generado=mensaje,
            errores_mensaje=errores,
        ),
        status_code=422 if errores else 200,
    )


@router.get("/nuevo", response_class=HTMLResponse)
async def nuevo(request: Request):
    templates = request.app.state.templates
    fila = fila_vacia(HOJA)
    fila["Fecha"] = date.today().isoformat()
    fila["Semana"] = str(date.today().isocalendar().week)
    fila["Moneda"] = "CRC"
    fila["Categoria"] = "Viaje completo"
    fila["Estado"] = "Pendiente"
    fila["Enviada"] = False
    return templates.TemplateResponse(
        request,
        "viajes/_form.html",
        _ctx(request, fila=fila, accion="/viajes", errores=[]),
    )


@router.get("/{id_viaje}/editar", response_class=HTMLResponse)
async def editar(request: Request, id_viaje: str):
    templates = request.app.state.templates
    fila = excel_repo.leer_por_id(HOJA, id_viaje)
    if not fila:
        return HTMLResponse("Viaje no encontrado", status_code=404)
    fila["Fecha"] = _fecha_viaje(fila)
    fila["Semana"] = str(fila.get("Semana") or "")
    fila["Categoria"] = fila.get("Categoria") or (
        "Ruptura" if fila.get("ID_Salida") and fila.get("ID_Salida") == fila.get("ID_Llegada") else "Viaje completo"
    )
    return templates.TemplateResponse(
        request,
        "viajes/_form.html",
        _ctx(request, fila=fila, accion=f"/viajes/{id_viaje}", errores=[]),
    )


@router.post("", response_class=HTMLResponse)
async def crear(request: Request):
    templates = request.app.state.templates
    datos = await datos_form(request, HOJA)
    errores_reglas = _preparar_datos(datos)
    if not datos.get("FechaRegistro"):
        datos["FechaRegistro"] = date.today().isoformat()
    if not datos.get("Estado"):
        datos["Estado"] = "Pendiente"
    datos["Enviada"] = False
    errores = validar(HOJA, datos) + errores_reglas
    if errores:
        return templates.TemplateResponse(
            request,
            "viajes/_form.html",
            _ctx(request, fila={**fila_vacia(HOJA), **datos}, accion="/viajes", errores=errores),
            status_code=422,
        )
    try:
        excel_repo.insertar(HOJA, datos)
    except ExcelBloqueadoError as exc:
        return templates.TemplateResponse(
            request,
            "viajes/_form.html",
            _ctx(request, fila={**fila_vacia(HOJA), **datos}, accion="/viajes", errores=[str(exc)]),
            status_code=409,
        )
    return RedirectResponse("/viajes", status_code=303)


@router.post("/{id_viaje}", response_class=HTMLResponse)
async def guardar(request: Request, id_viaje: str):
    templates = request.app.state.templates
    original = excel_repo.leer_por_id(HOJA, id_viaje) or fila_vacia(HOJA)
    datos = await datos_form(request, HOJA)
    esta_enviada = es_verdadero(original.get("Enviada")) or original.get("Estado") == "Enviado a contadora"
    datos["Enviada"] = esta_enviada
    datos["Estado"] = "Enviado a contadora" if esta_enviada else "Pendiente"
    errores_reglas = _preparar_datos(datos, original)
    errores = validar(HOJA, datos, id_viaje) + errores_reglas
    fila = {**original, **datos, "ID_Viaje": id_viaje}
    if errores:
        return templates.TemplateResponse(
            request,
            "viajes/_form.html",
            _ctx(request, fila=fila, accion=f"/viajes/{id_viaje}", errores=errores),
            status_code=422,
        )
    try:
        excel_repo.actualizar(HOJA, id_viaje, datos)
    except ExcelBloqueadoError as exc:
        return templates.TemplateResponse(
            request,
            "viajes/_form.html",
            _ctx(request, fila=fila, accion=f"/viajes/{id_viaje}", errores=[str(exc)]),
            status_code=409,
        )
    return RedirectResponse("/viajes", status_code=303)


@router.post("/{id_viaje}/enviar")
async def marcar_enviado(request: Request, id_viaje: str):
    try:
        excel_repo.actualizar(HOJA, id_viaje, {"Enviada": True, "Estado": "Enviado a contadora"})
    except ExcelBloqueadoError:
        return RedirectResponse("/viajes?error=bloqueado", status_code=303)
    return RedirectResponse("/viajes", status_code=303)


@router.post("/{id_viaje}/enviada")
async def cambiar_enviada(request: Request, id_viaje: str):
    form = await request.form()
    enviada = str(form.get("Enviada") or "").lower() == "true"
    cambios = {"Enviada": enviada, "Estado": "Enviado a contadora" if enviada else "Pendiente"}
    if not enviada:
        viaje = excel_repo.leer_por_id(HOJA, id_viaje)
        if viaje:
            ruta = _datos_ruta(str(viaje.get("ID_Salida") or ""), str(viaje.get("ID_Llegada") or ""))
            if ruta.get("codigo") and ruta.get("precio") not in (None, ""):
                cambios.update(
                    CodigoRuta=ruta["codigo"],
                    Precio=round(a_float(ruta["precio"]), 2),
                    Moneda=ruta.get("moneda") or "CRC",
                )
    try:
        excel_repo.actualizar(HOJA, id_viaje, cambios)
    except (ExcelBloqueadoError, KeyError):
        return RedirectResponse("/viajes?error=bloqueado", status_code=303)
    query = str(form.get("query") or "")
    return RedirectResponse(f"/viajes?{query}" if query else "/viajes", status_code=303)


@router.post("/{id_viaje}/eliminar")
async def borrar(request: Request, id_viaje: str):
    try:
        excel_repo.eliminar(HOJA, id_viaje)
    except (ExcelBloqueadoError, KeyError):
        return RedirectResponse("/viajes?error=bloqueado", status_code=303)
    return RedirectResponse("/viajes", status_code=303)
