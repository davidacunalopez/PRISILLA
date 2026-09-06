from __future__ import annotations

from collections import defaultdict
from datetime import date

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.db import excel_repo
from app.db.excel_repo import ExcelBloqueadoError
from app.db.schema import HOJAS, fila_vacia
from app.services.fechas import a_float, formato_crc, iso, mostrar_fecha, parse_fecha
from app.services.formularios import datos_form, validar

router = APIRouter(prefix="/gasolina")
HOJA = "Gasolina"


def _catalogos(fila: dict | None = None):
    fila = fila or {}
    todos_camiones = excel_repo.leer("Camiones")
    todos_choferes = excel_repo.leer("Choferes")
    camiones = [c for c in todos_camiones if c.get("Estado") == "Activo"] or todos_camiones
    choferes = [c for c in todos_choferes if c.get("Estado") == "Activo"] or todos_choferes
    for lista, todos, id_col, actual in (
        (camiones, todos_camiones, "ID_Camion", fila.get("ID_Camion")),
        (choferes, todos_choferes, "ID_Chofer", fila.get("ID_Chofer")),
    ):
        if actual and not any(str(x.get(id_col)) == str(actual) for x in lista):
            encontrado = next((x for x in todos if str(x.get(id_col)) == str(actual)), None)
            if encontrado:
                lista.append(encontrado)
    disponibles = {str(c.get("ID_Chofer")) for c in todos_choferes if c.get("Estado") == "Activo"}
    for camion in camiones:
        predeterminado = str(camion.get("ID_ChoferPredeterminado") or "")
        camion["ID_ChoferDisponible"] = predeterminado if predeterminado in disponibles else ""
    camiones.sort(key=lambda c: str(c.get("Placa") or ""))
    choferes.sort(key=lambda c: str(c.get("Nombre") or "").lower())
    return camiones, choferes


def _enriquecer(filas: list[dict]) -> list[dict]:
    camiones = excel_repo.mapa_display("Camiones")
    choferes = excel_repo.mapa_display("Choferes")
    return [
        {
            **f,
            "fecha_txt": mostrar_fecha(f.get("Fecha")),
            "placa": camiones.get(str(f.get("ID_Camion", "")), "—"),
            "chofer": choferes.get(str(f.get("ID_Chofer", "")), "Sin asignar"),
            "precio_txt": formato_crc(f.get("PrecioPorLitro")),
            "monto_txt": formato_crc(f.get("MontoTotal")),
        }
        for f in filas
    ]


def _analitica(filas: list[dict]) -> dict:
    camiones = excel_repo.mapa_display("Camiones")
    choferes = excel_repo.mapa_display("Choferes")
    meses: dict[str, dict] = defaultdict(lambda: {"monto": 0.0, "litros": 0.0, "n": 0})
    por_chofer: dict[str, dict] = defaultdict(lambda: {"monto": 0.0, "litros": 0.0, "n": 0})
    por_camion: dict[str, dict] = defaultdict(lambda: {"monto": 0.0, "litros": 0.0, "n": 0})
    por_tipo: dict[str, dict] = defaultdict(lambda: {"monto": 0.0, "litros": 0.0, "n": 0})
    total = litros_total = 0.0

    for fila in filas:
        monto = a_float(fila.get("MontoTotal"))
        litros = a_float(fila.get("CantidadComprada"))
        total += monto
        litros_total += litros
        fecha = parse_fecha(fila.get("Fecha"))
        if fecha:
            clave_mes = fecha.strftime("%Y-%m")
            meses[clave_mes]["monto"] += monto
            meses[clave_mes]["litros"] += litros
            meses[clave_mes]["n"] += 1
        for mapa, clave in (
            (por_chofer, str(fila.get("ID_Chofer") or "")),
            (por_camion, str(fila.get("ID_Camion") or "")),
            (por_tipo, str(fila.get("TipoCombustible") or "Diesel")),
        ):
            mapa[clave]["monto"] += monto
            mapa[clave]["litros"] += litros
            mapa[clave]["n"] += 1

    nombres_meses = "enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre".split()

    def convertir(mapa, etiqueta, ordenar_meses=False, limite=8):
        claves = sorted(mapa) if ordenar_meses else sorted(mapa, key=lambda k: mapa[k]["monto"], reverse=True)
        claves = claves[-12:] if ordenar_meses else claves[:limite]
        maximo = max((mapa[k]["monto"] for k in claves), default=0)
        return [
            {
                "etiqueta": etiqueta(k),
                "monto_txt": formato_crc(mapa[k]["monto"]),
                "litros": f"{mapa[k]['litros']:.2f}",
                "n": mapa[k]["n"],
                "pct": round(mapa[k]["monto"] / maximo * 100, 2) if maximo else 0,
                "participacion": round(mapa[k]["monto"] / total * 100, 1) if total else 0,
                "precio_litro_txt": (
                    formato_crc(mapa[k]["monto"] / mapa[k]["litros"])
                    if mapa[k]["litros"] else "—"
                ),
            }
            for k in claves
        ]

    def etiqueta_mes(clave: str):
        anio, mes = clave.split("-")
        return f"{nombres_meses[int(mes) - 1].capitalize()} {anio}"

    claves_mes = sorted(meses)
    variacion_mes = None
    if len(claves_mes) >= 2 and meses[claves_mes[-2]]["monto"]:
        variacion_mes = round(
            (meses[claves_mes[-1]]["monto"] - meses[claves_mes[-2]]["monto"])
            / meses[claves_mes[-2]]["monto"]
            * 100,
            1,
        )

    return {
        "resumen": {
            "total_txt": formato_crc(total),
            "litros": f"{litros_total:.2f}",
            "precio_promedio_txt": formato_crc(total / litros_total) if litros_total else "—",
            "facturas": len(filas),
            "promedio_factura_txt": formato_crc(total / len(filas)) if filas else "—",
            "variacion_mes": variacion_mes,
            "variacion_mes_txt": (
                f"{abs(variacion_mes):.1f}% {'más' if variacion_mes > 0 else 'menos'}"
                if variacion_mes is not None and variacion_mes != 0
                else "Sin cambio"
                if variacion_mes == 0
                else "Sin comparación"
            ),
            "ultimo_mes": etiqueta_mes(claves_mes[-1]) if claves_mes else "Sin datos",
        },
        "meses": convertir(meses, etiqueta_mes, ordenar_meses=True),
        "choferes": convertir(por_chofer, lambda k: choferes.get(k, "Sin chofer")),
        "camiones": convertir(por_camion, lambda k: camiones.get(k, "Sin vehículo")),
        "tipos": convertir(por_tipo, lambda k: k or "Sin tipo", limite=3),
    }


def _filtrar(filas: list[dict], desde: str, hasta: str) -> list[dict]:
    resultado = []
    for fila in filas:
        fecha = iso(fila.get("Fecha"))
        if desde and fecha and fecha < desde:
            continue
        if hasta and fecha and fecha > hasta:
            continue
        resultado.append(fila)
    return resultado


def _ctx(request: Request, **extra):
    camiones, choferes = _catalogos(extra.get("fila"))
    base = {
        "titulo": "Gasolina",
        "activo": "gasolina",
        "choices": HOJAS[HOJA]["choices"],
        "camiones": camiones,
        "choferes": choferes,
    }
    base.update(extra)
    return base


@router.get("", response_class=HTMLResponse)
async def lista(request: Request):
    desde = str(request.query_params.get("desde") or "")
    hasta = str(request.query_params.get("hasta") or "")
    originales = _filtrar(excel_repo.leer(HOJA), desde, hasta)
    filas = _enriquecer(originales)
    filas.sort(key=lambda g: str(g.get("Fecha") or ""), reverse=True)
    return request.app.state.templates.TemplateResponse(
        request,
        "gasolina/index.html",
        _ctx(
            request,
            filas=filas,
            analitica=_analitica(originales),
            filtros={"desde": desde, "hasta": hasta},
        ),
    )


@router.get("/nuevo", response_class=HTMLResponse)
async def nuevo(request: Request):
    fila = fila_vacia(HOJA)
    fila.update({"Fecha": date.today().isoformat(), "TipoCombustible": "Diesel", "Moneda": "CRC"})
    return request.app.state.templates.TemplateResponse(
        request, "gasolina/_form.html", _ctx(request, fila=fila, accion="/gasolina", errores=[])
    )


@router.get("/{id_registro}/editar", response_class=HTMLResponse)
async def editar(request: Request, id_registro: str):
    fila = excel_repo.leer_por_id(HOJA, id_registro)
    if not fila:
        return HTMLResponse("Factura no encontrada", status_code=404)
    fila["Fecha"] = iso(fila.get("Fecha"))
    fila["TipoCombustible"] = fila.get("TipoCombustible") or "Diesel"
    return request.app.state.templates.TemplateResponse(
        request,
        "gasolina/_form.html",
        _ctx(request, fila=fila, accion=f"/gasolina/{id_registro}", errores=[]),
    )


def _completar(datos: dict) -> dict:
    litros = a_float(datos.get("CantidadComprada"))
    total = a_float(datos.get("MontoTotal"))
    datos["CantidadComprada"] = round(litros, 3) if litros else ""
    datos["MontoTotal"] = round(total, 2) if total else ""
    datos["PrecioPorLitro"] = round(total / litros, 4) if total > 0 and litros > 0 else ""
    datos["TipoCombustible"] = datos.get("TipoCombustible") or "Diesel"
    datos["Moneda"] = "CRC"
    return datos


async def _guardar_formulario(request: Request, id_registro: str = ""):
    datos = _completar(await datos_form(request, HOJA))
    errores = validar(HOJA, datos, id_registro)
    fila = {**(excel_repo.leer_por_id(HOJA, id_registro) or fila_vacia(HOJA)), **datos}
    if id_registro:
        fila["ID_Registro"] = id_registro
    accion = f"/gasolina/{id_registro}" if id_registro else "/gasolina"
    if errores:
        return request.app.state.templates.TemplateResponse(
            request,
            "gasolina/_form.html",
            _ctx(request, fila=fila, accion=accion, errores=errores),
            status_code=422,
        )
    try:
        if id_registro:
            excel_repo.actualizar(HOJA, id_registro, datos)
        else:
            excel_repo.insertar(HOJA, datos)
    except ExcelBloqueadoError as exc:
        return request.app.state.templates.TemplateResponse(
            request,
            "gasolina/_form.html",
            _ctx(request, fila=fila, accion=accion, errores=[str(exc)]),
            status_code=409,
        )
    return RedirectResponse(
        f"/gasolina?ok={'actualizado' if id_registro else 'creado'}", status_code=303
    )


@router.post("", response_class=HTMLResponse)
async def crear(request: Request):
    return await _guardar_formulario(request)


@router.post("/{id_registro}", response_class=HTMLResponse)
async def guardar(request: Request, id_registro: str):
    return await _guardar_formulario(request, id_registro)


@router.post("/{id_registro}/eliminar")
async def borrar(request: Request, id_registro: str):
    try:
        excel_repo.eliminar(HOJA, id_registro)
    except (ExcelBloqueadoError, KeyError):
        return RedirectResponse("/gasolina?error=bloqueado", status_code=303)
    return RedirectResponse("/gasolina?ok=eliminado", status_code=303)
