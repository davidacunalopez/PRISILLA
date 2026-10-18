"""Cambios de IA sobre viajes. Aplican las mismas reglas que el formulario (services/viajes y validar).

El código y el precio de un viaje nunca los decide la IA: salen de la ruta registrada, igual que en el formulario.
"""

from __future__ import annotations

from datetime import date

from app.config import load_config
from app.db import excel_repo
from app.db.schema import HOJAS
from app.services.acciones_base import (
    TIPOS,
    Accion,
    Dependencia,
    Plan,
    PropuestaInvalida,
    datos_de_fila,
    resolver,
    resolver_al_ejecutar,
    sin_repetidos,
    texto,
)
from app.services.fechas import a_float, iso, parse_fecha
from app.services.formularios import validar
from app.services.rutas import normalizar, texto_precio
from app.services.viajes import datos_ruta, esta_enviado, preparar_datos, registrar_empresa_trabajo

HOJA = "Viajes"
CATEGORIAS = HOJAS[HOJA]["choices"]["Categoria"]
NUEVO = "(nuevo)"  # id provisional de algo que otro cambio del mismo lote todavía va a crear
_EST = TIPOS["Empresas"]
_ENTIDADES = (("salida", "Empresas", "ID_Salida"), ("llegada", "Empresas", "ID_Llegada"),
              ("camion", "Camiones", "ID_Camion"), ("chofer", "Choferes", "ID_Chofer"))
ARGS_TEXTO = {"contenedor": "Contenedor", "chasis": "Chasis", "guia": "GuiaTirManifiesto"}
ETIQUETAS = {
    "Fecha": "Fecha", "ID_Salida": "Salida", "ID_Llegada": "Llegada", "ID_Camion": "Camión", "ID_Chofer": "Chofer",
    "EmpresaTrabajo": "Empresa", "Categoria": "Categoría", "Contenedor": "Contenedor", "Chasis": "Chasis",
    "GuiaTirManifiesto": "Guía", "CodigoRuta": "Código de ruta", "Precio": "Precio",
}


# --- Interpretación de lo que pide el modelo ---

def _fecha(valor) -> date:
    fecha = parse_fecha(valor)
    if fecha is None:
        raise PropuestaInvalida("La fecha debe tener el formato AAAA-MM-DD (por ejemplo 2026-10-18).")
    return fecha


def _categoria(valor) -> str:
    nombre = texto(valor)
    if not nombre:
        return "Viaje completo"
    for categoria in CATEGORIAS:
        if normalizar(categoria) == normalizar(nombre):
            return categoria
    raise PropuestaInvalida(f"La categoría debe ser una de: {', '.join(CATEGORIAS)}.")


def _empresa_trabajo(valor) -> str:
    """Respeta la escritura del catálogo (TCC y tcc son la misma empresa) para no partir los informes."""
    nombre = texto(valor)
    for existente in load_config().get("empresas_trabajo") or []:
        if normalizar(existente) == normalizar(nombre):
            return existente
    return nombre


def _provisional(entidad: dict) -> str:
    return NUEVO if entidad.get("planeada") else entidad["id"]


def _resolver_entidades(args: dict, plan: Plan) -> dict[str, dict]:
    return {
        clave: resolver(hoja, args.get(clave), activa=True, planeadas=plan.de(hoja))
        for clave, hoja, _col in _ENTIDADES
    }


def _ruta_planeada(entidades: dict, plan: Plan | None) -> dict | None:
    """La ruta que otro cambio del lote va a crear, solo si todavía no existe."""
    if plan is None or datos_ruta(entidades["salida"]["id"], entidades["llegada"]["id"]):
        return None
    clave = (_EST.clave(entidades["salida"]["nombre"]), _EST.clave(entidades["llegada"]["nombre"]))
    return plan.rutas.get(clave)


def _errores_viaje(datos: dict, *, original: dict | None = None, id_actual: str = "", ruta: dict | None = None) -> list[str]:
    """Las mismas comprobaciones del formulario: reglas del viaje + validación general de la hoja."""
    reglas = preparar_datos(datos, original, ruta)
    formales = validar(HOJA, datos, id_actual)
    provisionales = {col for _c, _h, col in _ENTIDADES if datos.get(col) == NUEVO}
    formales = [e for e in formales if not any(e.startswith(f"{col} no corresponde") for col in provisionales)]
    return list(dict.fromkeys(reglas + formales))


def _armar_nuevo(fecha: date, entidades: dict, empresa: str, categoria: str, args: dict) -> dict:
    return {
        "Fecha": fecha.isoformat(),
        "ID_Salida": _provisional(entidades["salida"]),
        "ID_Llegada": _provisional(entidades["llegada"]),
        "ID_Camion": _provisional(entidades["camion"]),
        "ID_Chofer": _provisional(entidades["chofer"]),
        "EmpresaTrabajo": empresa,
        "Categoria": categoria,
        "Contenedor": texto(args.get("contenedor")),
        "Chasis": texto(args.get("chasis")),
        "GuiaTirManifiesto": texto(args.get("guia")),
        "FechaRegistro": date.today().isoformat(),
        "Estado": "Pendiente",
        "Enviada": False,
    }


def _resumen(datos: dict, entidades: dict) -> list[str]:
    lineas = [
        f"Fecha: {datos['Fecha']} (semana {datos['Semana']})",
        f"Ruta: {entidades['salida']['nombre']} → {entidades['llegada']['nombre']} · código {datos['CodigoRuta']} · "
        f"{texto_precio(datos['Precio'], datos['Moneda'])} (los toma de la ruta registrada)",
        f"Camión: {entidades['camion']['nombre']}",
        f"Chofer: {entidades['chofer']['nombre']}",
        f"Empresa: {datos['EmpresaTrabajo']}",
        f"Categoría: {datos['Categoria']}",
    ]
    for etiqueta, columna in (("Contenedor", "Contenedor"), ("Chasis", "Chasis"), ("Guía", "GuiaTirManifiesto")):
        if datos.get(columna):
            lineas.append(f"{etiqueta}: {datos[columna]}")
    return lineas


def _avisos_nuevo(datos: dict, entidades: dict) -> list[str]:
    avisos = []
    if datos["EmpresaTrabajo"].casefold() not in {e.casefold() for e in load_config().get("empresas_trabajo") or []}:
        avisos.append(f"La empresa «{datos['EmpresaTrabajo']}» es nueva y se agregará al catálogo de empresas.")
    if any(e.get("planeada") for e in entidades.values()):
        avisos.append("Algo de este viaje se crea en otro cambio de esta lista: confírmelos en orden.")
    claves = ("Fecha", "ID_Salida", "ID_Llegada", "ID_Camion", "ID_Chofer")
    if NUEVO not in {datos[c] for c in claves}:
        iguales = [
            v for v in excel_repo.leer(HOJA)
            if iso(v.get("Fecha") or v.get("Desde")) == datos["Fecha"]
            and all(str(v.get(c) or "") == datos[c] for c in claves[1:])
        ]
        if iguales:
            avisos.append(
                f"Aviso: ya hay un viaje igual ese día (mismo camión, chofer y ruta): {iguales[0].get('ID_Viaje')}. "
                "Confirme solo si es otro viaje."
            )
    return avisos


def _exigir_ruta_confirmada(d: dict, salida_id: str, llegada_id: str, salida: dict, llegada: dict) -> None:
    """Si la ruta debía crearla otro cambio de la lista y aún no está, este viaje espera en vez de fallar."""
    if d.get("ruta_pendiente") and not datos_ruta(salida_id, llegada_id):
        raise Dependencia(
            f"Primero confirme el cambio que registra la ruta {salida['nombre']} → {llegada['nombre']}."
        )


# --- registrar_viaje ---

def _preparar_registrar_viaje(args: dict, plan: Plan) -> dict:
    fecha = _fecha(args.get("fecha"))
    entidades = _resolver_entidades(args, plan)
    empresa = _empresa_trabajo(args.get("empresa_trabajo"))
    categoria = _categoria(args.get("categoria"))
    datos = _armar_nuevo(fecha, entidades, empresa, categoria, args)
    ruta = _ruta_planeada(entidades, plan)
    errores = _errores_viaje(datos, ruta=ruta)
    if errores:
        raise PropuestaInvalida(sin_repetidos(errores))
    etiqueta = f"{entidades['salida']['nombre']} → {entidades['llegada']['nombre']}"
    guardado = {"ruta_pendiente": ruta is not None, "fecha": datos["Fecha"], "empresa": empresa, "categoria": categoria,
                "contenedor": datos["Contenedor"], "chasis": datos["Chasis"], "guia": datos["GuiaTirManifiesto"]}
    for clave, _hoja, _col in _ENTIDADES:
        guardado[clave] = entidades[clave]["nombre"]
        guardado[f"{clave}_id"] = "" if entidades[clave].get("planeada") else entidades[clave]["id"]
    return {
        "titulo": f"Registrar un viaje {etiqueta} ({datos['Fecha']})",
        "detalles": _resumen(datos, entidades) + ["Queda pendiente de envío a la contadora."] + _avisos_nuevo(datos, entidades),
        "datos": guardado,
    }


def _ejecutar_registrar_viaje(d: dict) -> tuple[str, dict | None]:
    entidades = {clave: resolver_al_ejecutar(hoja, d[f"{clave}_id"], d[clave]) for clave, hoja, _col in _ENTIDADES}
    _exigir_ruta_confirmada(d, entidades["salida"]["id"], entidades["llegada"]["id"], entidades["salida"], entidades["llegada"])
    datos = _armar_nuevo(
        _fecha(d["fecha"]), entidades, d["empresa"], d["categoria"],
        {"contenedor": d["contenedor"], "chasis": d["chasis"], "guia": d["guia"]},
    )
    errores = _errores_viaje(datos)
    if errores:
        raise PropuestaInvalida(sin_repetidos(errores))
    registrar_empresa_trabajo(datos["EmpresaTrabajo"])
    fila = excel_repo.insertar(HOJA, datos)
    return (
        f"Viaje registrado: {fila['ID_Viaje']} · {entidades['salida']['nombre']} → {entidades['llegada']['nombre']}, "
        f"{datos['Fecha']}, camión {entidades['camion']['nombre']}.",
        None,
    )


# --- editar_viaje ---

def _buscar_viaje(valor) -> dict:
    id_viaje = texto(valor)
    if not id_viaje:
        raise PropuestaInvalida("Falta indicar el viaje (su ID, por ejemplo VIA-0001).")
    for viaje in excel_repo.leer(HOJA):
        if str(viaje.get("ID_Viaje") or "").casefold() == id_viaje.casefold():
            return viaje
    raise PropuestaInvalida(f"No existe el viaje «{id_viaje}».")


def _cambio_de_entidad(hoja: str, valor, id_actual: str, plan: Plan) -> dict | None:
    """None si la usuaria nombró lo que el viaje ya tiene (aunque esté archivado); si es otro, debe estar activo."""
    encontrada = resolver(hoja, valor, activa=False, planeadas=plan.de(hoja))
    if encontrada.get("id") and encontrada["id"] == id_actual:
        return None
    return resolver(hoja, valor, activa=True, planeadas=plan.de(hoja))


def _calcular_edicion(original: dict, pedidos: dict, entidades: dict, ruta: dict | None) -> tuple[dict, list[str]]:
    """Viaje ya mezclado con los cambios pedidos y sus errores. `entidades`: ya resueltas (None = sin cambio)."""
    datos = datos_de_fila(HOJA, original)
    datos["Fecha"] = iso(original.get("Fecha") or original.get("Desde"))
    datos["Categoria"] = datos.get("Categoria") or "Viaje completo"
    if pedidos.get("fecha") is not None:
        datos["Fecha"] = _fecha(pedidos["fecha"]).isoformat()
    for clave, _hoja, columna in _ENTIDADES:
        if entidades.get(clave) is not None:
            datos[columna] = _provisional(entidades[clave])
    if pedidos.get("empresa_trabajo") is not None:
        datos["EmpresaTrabajo"] = _empresa_trabajo(pedidos["empresa_trabajo"])
    if pedidos.get("categoria") is not None:
        datos["Categoria"] = _categoria(pedidos["categoria"])
    for arg, columna in ARGS_TEXTO.items():
        if pedidos.get(arg) is not None:
            datos[columna] = texto(pedidos[arg])
    enviado = esta_enviado(original)
    datos["Enviada"] = enviado
    datos["Estado"] = "Enviado a contadora" if enviado else "Pendiente"
    errores = _errores_viaje(datos, original=original, id_actual=str(original["ID_Viaje"]), ruta=ruta)
    return datos, errores


def _igual(columna: str, antes, despues) -> bool:
    if columna == "Precio":
        return abs(a_float(antes) - a_float(despues)) < 0.005
    if columna == "Fecha":
        return iso(antes) == iso(despues)
    return texto(antes) == texto(despues)


# Lo que puede cambiar al editar: lo visible más lo que se deriva de la fecha y de la ruta.
EDITABLES = tuple(ETIQUETAS) + ("Desde", "Hasta", "Semana", "Moneda")


def _diferencias(original: dict, datos: dict) -> dict:
    return {
        columna: datos[columna]
        for columna in EDITABLES
        if columna in datos and not _igual(columna, original.get(columna), datos[columna])
    }


def _preparar_editar_viaje(args: dict, plan: Plan) -> dict:
    original = _buscar_viaje(args.get("id_viaje"))
    entidades = {}
    for clave, hoja, columna in _ENTIDADES:
        entidades[clave] = (
            _cambio_de_entidad(hoja, args[clave], str(original.get(columna) or ""), plan)
            if args.get(clave) is not None
            else None
        )
    pedidos = {k: args.get(k) for k in ("fecha", "empresa_trabajo", "categoria", *ARGS_TEXTO)}
    ruta = None
    if entidades["salida"] or entidades["llegada"]:
        salida = entidades["salida"] or {"id": str(original.get("ID_Salida") or ""), "nombre": ""}
        llegada = entidades["llegada"] or {"id": str(original.get("ID_Llegada") or ""), "nombre": ""}
        nombres = excel_repo.mapa_display("Empresas")
        for lado, ent in (("salida", salida), ("llegada", llegada)):
            if not ent["nombre"]:
                ent["nombre"] = nombres.get(ent["id"], "")
        ruta = _ruta_planeada({"salida": salida, "llegada": llegada}, plan)
    datos, errores = _calcular_edicion(original, pedidos, entidades, ruta)
    if errores:
        raise PropuestaInvalida(sin_repetidos(errores))
    cambios = _diferencias(original, datos)
    visibles = [c for c in ETIQUETAS if c in cambios]
    if not visibles:
        raise PropuestaInvalida(f"El viaje {original['ID_Viaje']} ya tiene esos datos; no hay nada que cambiar.")
    nombres = {
        "ID_Salida": excel_repo.mapa_display("Empresas"), "ID_Llegada": excel_repo.mapa_display("Empresas"),
        "ID_Camion": excel_repo.mapa_display("Camiones"), "ID_Chofer": excel_repo.mapa_display("Choferes"),
    }
    nuevos = {col: ent["nombre"] for (clave, _h, col) in _ENTIDADES if (ent := entidades.get(clave))}
    detalles = []
    for columna in visibles:
        if columna in nombres:
            antes = nombres[columna].get(str(original.get(columna) or ""), "")
            despues = nuevos.get(columna, "")
        elif columna == "Precio":
            antes = texto_precio(original.get("Precio"), str(original.get("Moneda") or "CRC"))
            despues = texto_precio(datos["Precio"], datos["Moneda"])
        else:
            antes, despues = texto(original.get(columna)), texto(datos[columna])
        detalles.append(f"{ETIQUETAS[columna]}: {antes or '—'} → {despues or '—'}")
    if esta_enviado(original):
        detalles.append("Este viaje ya fue enviado a la contadora: conserva su precio y su ruta no puede cambiar.")
    if pedidos.get("empresa_trabajo") is not None and datos["EmpresaTrabajo"].casefold() not in {
        e.casefold() for e in load_config().get("empresas_trabajo") or []
    }:
        detalles.append(f"La empresa «{datos['EmpresaTrabajo']}» es nueva y se agregará al catálogo de empresas.")
    guardado = {
        "ruta_pendiente": ruta is not None,
        "id": str(original["ID_Viaje"]),
        "pedidos": {k: v for k, v in pedidos.items() if v is not None},
        "entidades": {
            clave: None if ent is None else {"id": "" if ent.get("planeada") else ent["id"], "nombre": ent["nombre"]}
            for clave, ent in entidades.items()
        },
    }
    return {"titulo": f"Cambiar el viaje {original['ID_Viaje']}", "detalles": detalles, "datos": guardado}


def _ejecutar_editar_viaje(d: dict) -> tuple[str, dict | None]:
    original = _buscar_viaje(d["id"])
    entidades = {}
    for clave, hoja, _col in _ENTIDADES:
        pedida = d["entidades"].get(clave)
        entidades[clave] = None if pedida is None else resolver_al_ejecutar(hoja, pedida["id"], pedida["nombre"])
    nombres = excel_repo.mapa_display("Empresas")
    ids = {
        lado: (entidades[lado] or {}).get("id") or str(original.get(columna) or "")
        for lado, columna in (("salida", "ID_Salida"), ("llegada", "ID_Llegada"))
    }
    _exigir_ruta_confirmada(
        d, ids["salida"], ids["llegada"],
        {"nombre": (entidades["salida"] or {}).get("nombre") or nombres.get(ids["salida"], "")},
        {"nombre": (entidades["llegada"] or {}).get("nombre") or nombres.get(ids["llegada"], "")},
    )
    datos, errores = _calcular_edicion(original, d["pedidos"], entidades, None)
    if errores:
        raise PropuestaInvalida(sin_repetidos(errores))
    cambios = _diferencias(original, datos)
    if not any(c in cambios for c in ETIQUETAS):
        raise PropuestaInvalida(f"El viaje {d['id']} ya tiene esos datos.")
    if d["pedidos"].get("empresa_trabajo") is not None:
        registrar_empresa_trabajo(datos["EmpresaTrabajo"])
    excel_repo.actualizar(HOJA, d["id"], cambios)
    return f"Viaje actualizado: {d['id']}.", {c: original.get(c) for c in cambios}


# --- eliminar_viaje ---

def _preparar_eliminar_viaje(args: dict, plan: Plan) -> dict:
    viaje = _buscar_viaje(args.get("id_viaje"))
    estaciones = excel_repo.mapa_display("Empresas")
    camiones, choferes = excel_repo.mapa_display("Camiones"), excel_repo.mapa_display("Choferes")
    ruta = f"{estaciones.get(str(viaje.get('ID_Salida') or ''), '?')} → {estaciones.get(str(viaje.get('ID_Llegada') or ''), '?')}"
    detalles = [
        f"Viaje {viaje['ID_Viaje']} del {iso(viaje.get('Fecha') or viaje.get('Desde'))}: {ruta}, "
        f"camión {camiones.get(str(viaje.get('ID_Camion') or ''), '?')}, "
        f"chofer {choferes.get(str(viaje.get('ID_Chofer') or ''), '?')}, "
        f"{texto_precio(viaje.get('Precio'), str(viaje.get('Moneda') or 'CRC'))}.",
        "Se borra de la bitácora; la app no puede deshacerlo (solo queda el respaldo automático del Excel).",
    ]
    if esta_enviado(viaje):
        detalles.append("Atención: este viaje ya fue enviado a la contadora.")
    return {
        "titulo": f"Eliminar el viaje {viaje['ID_Viaje']}",
        "detalles": detalles,
        "datos": {"id": str(viaje["ID_Viaje"])},
    }


def _ejecutar_eliminar_viaje(d: dict) -> tuple[str, dict | None]:
    viaje = _buscar_viaje(d["id"])
    excel_repo.eliminar(HOJA, d["id"])
    return f"Viaje eliminado: {d['id']}.", {k: v for k, v in viaje.items() if v not in (None, "")}


_PARAMS_VIAJE = {
    "fecha": ("string", "Fecha del viaje, AAAA-MM-DD. Si la usuaria dice «hoy», usa fecha_actual del resumen."),
    "salida": ("string", "Nombre exacto de la estación de salida."),
    "llegada": ("string", "Nombre exacto de la estación de llegada."),
    "camion": ("string", "Placa exacta del camión."),
    "chofer": ("string", "Nombre exacto del chofer."),
    "empresa_trabajo": ("string", "Empresa para la que se hace el viaje (prefiere una de empresas_de_trabajo)."),
    "categoria": ("string", "Viaje completo, Desvío o Ruptura. Viaje completo si la usuaria no dijo otra."),
    "contenedor": ("string", "Contenedor, si la usuaria lo dio."),
    "chasis": ("string", "Chasis, si la usuaria lo dio."),
    "guia": ("string", "Guía, TIR o manifiesto, si la usuaria lo dio."),
}

ACCIONES_VIAJES = (
    Accion(
        "registrar_viaje",
        "Registrar viaje",
        "Propone registrar un viaje nuevo. NUNCA pidas ni inventes código o precio: salen de la ruta registrada. "
        "Si la ruta no existe, propón antes guardar_ruta (necesitas el código y el precio de la usuaria); si el camión, "
        "el chofer o una estación no existen, propón antes crearlos. Úsala solo si la usuaria dio fecha, salida, "
        "llegada, camión, chofer y empresa.",
        _PARAMS_VIAJE,
        ("fecha", "salida", "llegada", "camion", "chofer", "empresa_trabajo"),
        False,
        _preparar_registrar_viaje,
        _ejecutar_registrar_viaje,
    ),
    Accion(
        "editar_viaje",
        "Cambiar viaje",
        "Propone cambiar datos de un viaje ya registrado, identificado por su ID (de viajes_recientes o el que dé la "
        "usuaria). Envía solo los campos que ella quiere cambiar. El precio no se cambia aquí: sigue a la ruta.",
        {"id_viaje": ("string", "ID del viaje, por ejemplo VIA-0007."), **{k: v for k, v in _PARAMS_VIAJE.items()}},
        ("id_viaje",),
        False,
        _preparar_editar_viaje,
        _ejecutar_editar_viaje,
    ),
    Accion(
        "eliminar_viaje",
        "Eliminar viaje",
        "Propone eliminar un viaje de la bitácora. Úsala solo si la usuaria pidió eliminarlo de forma explícita y "
        "conoces su ID (viajes_recientes).",
        {"id_viaje": ("string", "ID del viaje, por ejemplo VIA-0007.")},
        ("id_viaje",),
        True,
        _preparar_eliminar_viaje,
        _ejecutar_eliminar_viaje,
    ),
)
