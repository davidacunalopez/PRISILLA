from __future__ import annotations

import asyncio
import re
from zipfile import BadZipFile

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from openpyxl.utils.exceptions import InvalidFileException

from app.config import gemini_settings, load_config, save_config, save_gemini_settings
from app.db import excel_repo
from app.db.excel_repo import ExcelBloqueadoError
from app.db.plantilla_base import LibroInvalidoError
from app.services.fechas import a_float, formato_crc
from app.services.ia import IaNoConfiguradaError, IaServicioError, probar_gemini
from app.services.tarifas import actualizar_viajes_pendientes_de_ruta

router = APIRouter(prefix="/configuracion")


def _config_visible() -> dict:
    cfg = load_config()
    marcas = list(cfg.get("marcas_camiones") or [])
    existentes = [str(c.get("Marca") or "").strip() for c in excel_repo.leer("Camiones")]
    unicas = {}
    for marca in marcas + existentes:
        if marca:
            unicas.setdefault(marca.casefold(), marca)
    cfg["marcas_camiones"] = sorted(unicas.values(), key=str.casefold)
    return cfg


def _rutas_visibles(cfg: dict | None = None) -> list[dict]:
    cfg = cfg or load_config()
    estaciones = excel_repo.mapa_display("Empresas")
    rutas = []
    for ruta in cfg.get("rutas_configuradas") or []:
        salida = str(ruta.get("salida") or "")
        llegada = str(ruta.get("llegada") or "")
        rutas.append(
            {
                "salida_id": salida,
                "llegada_id": llegada,
                "salida": estaciones.get(salida, "Estación no disponible"),
                "llegada": estaciones.get(llegada, "Estación no disponible"),
                "codigo": str(ruta.get("codigo") or ""),
                "interna": salida == llegada,
                "precio": ruta.get("precio", ""),
                "moneda": str(ruta.get("moneda") or "CRC"),
                "precio_txt": (
                    formato_crc(ruta.get("precio"))
                    if str(ruta.get("moneda") or "CRC") == "CRC" and ruta.get("precio") not in (None, "")
                    else f"${a_float(ruta.get('precio')):,.2f}"
                    if ruta.get("precio") not in (None, "")
                    else "Sin configurar"
                ),
            }
        )
    rutas.sort(key=lambda r: (r["salida"].casefold(), r["llegada"].casefold()))
    return rutas


def _contexto(request: Request, *, errores: list[str] | None = None, **extra) -> dict:
    estaciones = sorted(
        excel_repo.leer("Empresas"),
        key=lambda e: str(e.get("NombreEmpresa") or "").casefold(),
    )
    contexto = {
        "titulo": "Configuración",
        "activo": "configuracion",
        "cfg": _config_visible(),
        "errores": errores or [],
        "guardado": False,
        "importado": False,
        "ia_cfg": gemini_settings(),
        "ia_guardada": False,
        "ia_probada": False,
        "ruta_guardada": False,
        "estaciones": estaciones,
        "estaciones_activas": [e for e in estaciones if e.get("Estado") == "Activo"],
        "rutas_configuradas": _rutas_visibles(),
    }
    contexto.update(extra)
    return contexto


@router.get("", response_class=HTMLResponse)
async def pagina(request: Request):
    return request.app.state.templates.TemplateResponse(
        request,
        "configuracion.html",
        _contexto(
            request,
            guardado=request.query_params.get("guardado") == "1",
            importado=request.query_params.get("importado") == "1",
            ia_guardada=request.query_params.get("ia_guardada") == "1",
            ia_probada=request.query_params.get("ia_probada") == "1",
            ruta_guardada=request.query_params.get("ruta_guardada") == "1",
        ),
    )


@router.post("", response_class=HTMLResponse)
async def guardar(request: Request):
    form = await request.form()
    marcas = []
    vistas = set()
    for linea in str(form.get("marcas_camiones") or "").splitlines():
        marca = linea.strip()
        clave = marca.casefold()
        if marca and clave not in vistas:
            marcas.append(marca)
            vistas.add(clave)
    empresas_trabajo = []
    vistas_empresas = set()
    for linea in str(form.get("empresas_trabajo") or "").splitlines():
        empresa = linea.strip()
        clave = empresa.casefold()
        if empresa and clave not in vistas_empresas:
            empresas_trabajo.append(empresa)
            vistas_empresas.add(clave)
    datos = {
        "nombre_app": str(form.get("nombre_app") or "").strip(),
        "nombre_empresa": str(form.get("nombre_empresa") or "").strip(),
        "moneda": str(form.get("moneda") or "CRC").strip().upper(),
        "marcas_camiones": marcas,
        "empresas_trabajo": empresas_trabajo,
    }
    errores = []
    try:
        datos["umbral_rojo_dias"] = int(str(form.get("umbral_rojo_dias") or "7"))
        datos["umbral_amarillo_dias"] = int(str(form.get("umbral_amarillo_dias") or "28"))
        datos["backups_conservar"] = int(str(form.get("backups_conservar") or "10"))
    except ValueError:
        errores.append("Los umbrales y la cantidad de respaldos deben ser números enteros.")

    if not datos["nombre_app"]:
        errores.append("El nombre de la aplicación es obligatorio.")
    if any(c in datos["nombre_app"] for c in "\r\n") or len(datos["nombre_app"]) > 60:
        errores.append("El nombre de la aplicación debe ocupar una sola línea de hasta 60 caracteres.")
    if any(c in datos["nombre_empresa"] for c in "\r\n") or len(datos["nombre_empresa"]) > 100:
        errores.append("El nombre de la empresa debe ocupar una sola línea de hasta 100 caracteres.")
    if datos.get("umbral_rojo_dias", 0) < 0:
        errores.append("El umbral rojo no puede ser negativo.")
    if datos.get("umbral_amarillo_dias", 0) <= datos.get("umbral_rojo_dias", 0):
        errores.append("El umbral amarillo debe ser mayor que el rojo.")
    if not 1 <= datos.get("backups_conservar", 0) <= 500:
        errores.append("Debe conservar entre 1 y 500 respaldos.")
    if not marcas:
        errores.append("Registre al menos una marca de camión.")
    if len(marcas) > 100 or any(len(marca) > 50 for marca in marcas):
        errores.append("Puede registrar hasta 100 marcas de 50 caracteres cada una.")
    if len(empresas_trabajo) > 100 or any(len(empresa) > 100 for empresa in empresas_trabajo):
        errores.append("Puede registrar hasta 100 empresas de trabajo de 100 caracteres cada una.")

    if errores:
        return request.app.state.templates.TemplateResponse(
            request,
            "configuracion.html",
            _contexto(request, errores=errores, cfg={**_config_visible(), **datos}),
            status_code=422,
        )
    save_config(datos)
    return RedirectResponse("/configuracion?guardado=1", status_code=303)


@router.post("/rutas", response_class=HTMLResponse)
async def guardar_ruta(request: Request):
    form = await request.form()
    salida = str(form.get("ID_Salida") or "").strip()
    llegada = str(form.get("ID_Llegada") or "").strip()
    codigo = str(form.get("Codigo") or "").strip()
    precio_raw = str(form.get("Precio") or "").strip()
    moneda = str(form.get("Moneda") or "CRC").strip().upper()
    precio = a_float(precio_raw, -1)
    errores = []
    if not salida or excel_repo.leer_por_id("Empresas", salida) is None:
        errores.append("Seleccione una estación de salida válida.")
    if not llegada or excel_repo.leer_por_id("Empresas", llegada) is None:
        errores.append("Seleccione una estación de llegada válida.")
    if not codigo or len(codigo) > 50 or any(c in codigo for c in "\r\n"):
        errores.append("El código de ruta es obligatorio y debe tener hasta 50 caracteres.")
    if not precio_raw or precio <= 0:
        errores.append("El precio de la ruta debe ser un número mayor a cero.")
    if moneda not in {"CRC", "USD"}:
        errores.append("La moneda de la ruta debe ser CRC o USD.")

    cfg = load_config()
    rutas = [dict(r) for r in cfg.get("rutas_configuradas") or []]
    if any(
        str(r.get("codigo") or "").casefold() == codigo.casefold()
        and (str(r.get("salida")) != salida or str(r.get("llegada")) != llegada)
        for r in rutas
    ):
        errores.append("Ese código ya está asignado a otra ruta.")
    if errores:
        return request.app.state.templates.TemplateResponse(
            request,
            "configuracion.html",
            _contexto(request, errores=errores),
            status_code=422,
        )

    encontrada = False
    for ruta in rutas:
        if str(ruta.get("salida")) == salida and str(ruta.get("llegada")) == llegada:
            ruta["codigo"] = codigo
            ruta["precio"] = round(precio, 2)
            ruta["moneda"] = moneda
            encontrada = True
            break
    if not encontrada:
        rutas.append(
            {
                "salida": salida,
                "llegada": llegada,
                "codigo": codigo,
                "precio": round(precio, 2),
                "moneda": moneda,
            }
        )
    save_config({"rutas_configuradas": rutas})
    actualizar_viajes_pendientes_de_ruta(
        salida, llegada, codigo=codigo, precio=precio, moneda=moneda
    )
    return RedirectResponse("/configuracion?ruta_guardada=1#rutas", status_code=303)


@router.post("/rutas/{salida}/{llegada}/eliminar")
async def eliminar_ruta(salida: str, llegada: str):
    cfg = load_config()
    rutas = [
        r
        for r in cfg.get("rutas_configuradas") or []
        if not (str(r.get("salida")) == salida and str(r.get("llegada")) == llegada)
    ]
    save_config({"rutas_configuradas": rutas})
    return RedirectResponse("/configuracion#rutas", status_code=303)


@router.post("/ia", response_class=HTMLResponse)
async def guardar_ia(request: Request):
    form = await request.form()
    clave_nueva = str(form.get("gemini_api_key") or "").strip()
    modelo = str(form.get("gemini_model") or "gemini-3.6-flash").strip()
    habilitada = form.get("gemini_enabled") == "1"
    eliminar = form.get("eliminar_clave") == "1"
    if eliminar:
        habilitada = False
    actual = gemini_settings()
    errores = []
    if clave_nueva and not re.fullmatch(r"[A-Za-z0-9._-]{10,500}", clave_nueva):
        errores.append("La API key no tiene un formato válido.")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", modelo):
        errores.append("El nombre del modelo no tiene un formato válido.")
    clave_efectiva = "" if eliminar else (clave_nueva or actual["api_key"])
    if habilitada and not clave_efectiva:
        errores.append("Ingrese una API key antes de habilitar Gemini.")
    if errores:
        return request.app.state.templates.TemplateResponse(
            request,
            "configuracion.html",
            _contexto(
                request,
                errores=errores,
                ia_cfg={**actual, "model": modelo, "enabled": habilitada},
            ),
            status_code=422,
        )
    save_gemini_settings(api_key="" if eliminar else (clave_nueva or None), model=modelo, enabled=habilitada)
    return RedirectResponse("/configuracion?ia_guardada=1#gemini", status_code=303)


@router.post("/ia/probar", response_class=HTMLResponse)
async def probar_ia(request: Request):
    try:
        await asyncio.to_thread(probar_gemini)
    except (IaNoConfiguradaError, IaServicioError) as exc:
        return request.app.state.templates.TemplateResponse(
            request,
            "configuracion.html",
            _contexto(request, errores=[str(exc)]),
            status_code=502,
        )
    return RedirectResponse("/configuracion?ia_probada=1#gemini", status_code=303)


@router.post("/importar-respaldo", response_class=HTMLResponse)
async def importar_respaldo(request: Request, respaldo: UploadFile = File(...)):
    errores = []
    nombre = str(respaldo.filename or "")
    if not nombre.lower().endswith(".xlsx"):
        errores.append("Seleccione un respaldo con extensión .xlsx.")
    if not errores:
        try:
            excel_repo.importar_respaldo(await respaldo.read())
        except (ValueError, LibroInvalidoError, InvalidFileException, BadZipFile, ExcelBloqueadoError, OSError) as exc:
            errores.append(str(exc))
    if errores:
        return request.app.state.templates.TemplateResponse(
            request,
            "configuracion.html",
            _contexto(request, errores=errores),
            status_code=422,
        )
    return RedirectResponse("/configuracion?importado=1", status_code=303)
