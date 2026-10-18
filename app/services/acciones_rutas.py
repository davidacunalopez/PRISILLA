"""Cambios de IA sobre rutas y estaciones."""

from __future__ import annotations

from app.db import excel_repo
from app.services.acciones_base import (
    TIPOS,
    Accion,
    Plan,
    PropuestaInvalida,
    plural,
    resolver,
    resolver_al_ejecutar,
    texto,
)
from app.services.fechas import a_float
from app.services.formularios import validar
from app.services.rutas import (
    analizar_rutas,
    eliminar_ruta,
    estaciones_por_id,
    guardar_ruta,
    normalizar,
    texto_precio,
    validar_datos_ruta,
    validar_ruta,
)
from app.services.tarifas import contar_viajes_pendientes_de_ruta

_EST = TIPOS["Empresas"]


def _par(rutas: list[dict], salida_id: str, llegada_id: str) -> dict | None:
    return next((r for r in rutas if r["salida_id"] == salida_id and r["llegada_id"] == llegada_id), None)


# --- guardar_ruta ---

def _preparar_guardar_ruta(args: dict, plan: Plan) -> dict:
    salida = resolver("Empresas", args.get("salida"), activa=True, planeadas=plan.estaciones)
    llegada = resolver("Empresas", args.get("llegada"), activa=True, planeadas=plan.estaciones)
    codigo = str(args.get("codigo") or "").strip()
    moneda = str(args.get("moneda") or "CRC").strip().upper()
    precio_raw = "" if args.get("precio") is None else str(args.get("precio")).strip()
    errores = validar_datos_ruta(codigo, precio_raw, moneda)
    if errores:
        raise PropuestaInvalida(" ".join(errores))
    precio = round(a_float(precio_raw), 2)
    etiqueta = f"{salida['nombre']} → {llegada['nombre']}"
    rutas = analizar_rutas()
    existente = _par(rutas, salida["id"], llegada["id"]) if salida["id"] and llegada["id"] else None

    detalles = []
    if existente:
        igual_codigo = existente["codigo"] == codigo
        igual_precio = a_float(existente["precio"], -1) == precio and existente["moneda"] == moneda
        if igual_codigo and igual_precio:
            raise PropuestaInvalida(
                f"La ruta {etiqueta} ya está registrada exactamente con ese código y precio; no hay nada que cambiar."
            )
        titulo = f"Actualizar la ruta {etiqueta}"
        detalles.append(f"Código: {existente['codigo']} → {codigo}" if not igual_codigo else f"Código: {codigo} (sin cambio)")
        if not igual_precio:
            detalles.append(
                f"Precio: {texto_precio(existente['precio'], existente['moneda'])} → {texto_precio(precio, moneda)}"
            )
        pendientes = contar_viajes_pendientes_de_ruta(salida["id"], llegada["id"])
        if pendientes:
            detalles.append(
                f"Se actualizarán {plural(pendientes, 'viaje pendiente', 'viajes pendientes')} de esta ruta; "
                "los ya enviados conservan su precio."
            )
    else:
        titulo = f"Crear la ruta {etiqueta}"
        detalles += [f"Código: {codigo}", f"Precio: {texto_precio(precio, moneda)}"]
        if salida.get("planeada") or llegada.get("planeada"):
            detalles.append("Una de las estaciones se crea en otro cambio de esta lista: confírmelo primero.")
    compartido = [
        r["etiqueta"]
        for r in rutas
        if r["codigo"].casefold() == codigo.casefold()
        and not (r["salida_id"] == salida["id"] and r["llegada_id"] == llegada["id"])
    ]
    if compartido:
        detalles.append(f"Aviso: el código {codigo} también lo usan {', '.join(compartido)} (está permitido).")
    return {
        "titulo": titulo,
        "detalles": detalles,
        "datos": {
            "salida": salida["nombre"],
            "salida_id": salida["id"],
            "llegada": llegada["nombre"],
            "llegada_id": llegada["id"],
            "codigo": codigo,
            "precio": precio,
            "moneda": moneda,
            "nueva": existente is None,
        },
    }


def _planear_guardar_ruta(datos: dict, plan: Plan) -> None:
    if datos["nueva"]:  # una ruta que ya existe se resuelve sola; solo se anticipan las que todavía no existen
        clave = (_EST.clave(datos["salida"]), _EST.clave(datos["llegada"]))
        plan.rutas[clave] = {"codigo": datos["codigo"], "precio": datos["precio"], "moneda": datos["moneda"]}


def _ejecutar_guardar_ruta(datos: dict) -> tuple[str, dict | None]:
    salida = resolver_al_ejecutar("Empresas", datos["salida_id"], datos["salida"])
    llegada = resolver_al_ejecutar("Empresas", datos["llegada_id"], datos["llegada"])
    errores = validar_ruta(salida["id"], llegada["id"], datos["codigo"], str(datos["precio"]), datos["moneda"])
    if errores:
        raise PropuestaInvalida(" ".join(errores))
    resultado = guardar_ruta(salida["id"], llegada["id"], datos["codigo"], datos["precio"], datos["moneda"])
    etiqueta = f"{salida['nombre']} → {llegada['nombre']}"
    resumen = f"código {datos['codigo']}, {texto_precio(datos['precio'], datos['moneda'])}"
    if resultado["creada"]:
        return f"Ruta creada: {etiqueta} ({resumen}).", None
    mensaje = f"Ruta actualizada: {etiqueta} ({resumen})."
    if resultado["viajes_actualizados"]:
        mensaje += f" Viajes pendientes actualizados: {resultado['viajes_actualizados']}."
    return mensaje, resultado["antes"]


# --- eliminar_ruta ---

def _preparar_eliminar_ruta(args: dict, plan: Plan) -> dict:
    salida = resolver("Empresas", args.get("salida"), activa=False)
    llegada = resolver("Empresas", args.get("llegada"), activa=False)
    etiqueta = f"{salida['nombre']} → {llegada['nombre']}"
    ruta = _par(analizar_rutas(), salida["id"], llegada["id"])
    if ruta is None:
        raise PropuestaInvalida(f"La ruta {etiqueta} no está registrada; no hay nada que eliminar.")
    detalles = [
        f"Se quita del catálogo la ruta {etiqueta} (código {ruta['codigo']}, {texto_precio(ruta['precio'], ruta['moneda'])}).",
        "Los viajes ya registrados conservan su código y precio.",
        "No se podrán registrar viajes nuevos en esta ruta hasta volver a crearla.",
    ]
    pendientes = contar_viajes_pendientes_de_ruta(salida["id"], llegada["id"])
    if pendientes:
        detalles.append(f"Hay {plural(pendientes, 'viaje pendiente', 'viajes pendientes')} en esta ruta; no cambian.")
    return {
        "titulo": f"Eliminar la ruta {etiqueta}",
        "detalles": detalles,
        "datos": {"salida_id": salida["id"], "llegada_id": llegada["id"], "etiqueta": etiqueta},
    }


def _ejecutar_eliminar_ruta(datos: dict) -> tuple[str, dict | None]:
    quitada = eliminar_ruta(datos["salida_id"], datos["llegada_id"])
    if quitada is None:
        raise PropuestaInvalida(f"La ruta {datos['etiqueta']} ya no estaba registrada.")
    return f"Ruta eliminada: {datos['etiqueta']}.", quitada


# --- estaciones ---

def _nombre_estacion(args: dict) -> str:
    nombre = texto(args.get("nombre"))
    if not nombre or len(nombre) > 100:
        raise PropuestaInvalida("El nombre de la estación es obligatorio y puede tener hasta 100 caracteres.")
    return nombre


def _validar_estacion_nueva(nombre: str) -> None:
    errores = validar("Empresas", {"NombreEmpresa": nombre, "Estado": "Activo"})
    if any(normalizar(e["nombre"]) == normalizar(nombre) for e in estaciones_por_id().values()):
        errores.append("Ya existe una estación con ese nombre.")
    if errores:
        raise PropuestaInvalida(" ".join(dict.fromkeys(errores)))


def _preparar_crear_estacion(args: dict, plan: Plan) -> dict:
    nombre = _nombre_estacion(args)
    _validar_estacion_nueva(nombre)
    return {
        "titulo": f"Crear la estación {nombre}",
        "detalles": [f"Nombre: {nombre}", "Quedará activa y disponible para rutas y viajes."],
        "datos": {"nombre": nombre},
    }


def _planear_crear_estacion(datos: dict, plan: Plan) -> None:
    plan.estaciones.add(_EST.clave(datos["nombre"]))


def _ejecutar_crear_estacion(datos: dict) -> tuple[str, dict | None]:
    _validar_estacion_nueva(datos["nombre"])
    fila = excel_repo.insertar("Empresas", {"NombreEmpresa": datos["nombre"], "Estado": "Activo"})
    return f"Estación creada: {datos['nombre']} ({fila['ID_Empresa']}).", None


def _preparar_archivar_estacion(args: dict, plan: Plan) -> dict:
    estacion = resolver("Empresas", args.get("nombre"), activa=False)
    if estacion["estado"] != "Activo":
        raise PropuestaInvalida(f"La estación «{estacion['nombre']}» ya está archivada.")
    usos = sum(1 for r in analizar_rutas() if estacion["id"] in (r["salida_id"], r["llegada_id"]))
    detalles = ["Conserva su historial; se puede reactivar cuando quiera."]
    if usos:
        detalles.append(
            f"{plural(usos, 'ruta la usa', 'rutas la usan')}: dejarán de poder elegirse al registrar viajes mientras esté archivada."
        )
    return {
        "titulo": f"Archivar la estación {estacion['nombre']}",
        "detalles": detalles,
        "datos": {"id": estacion["id"], "nombre": estacion["nombre"]},
    }


def _ejecutar_archivar_estacion(datos: dict) -> tuple[str, dict | None]:
    estacion = resolver("Empresas", datos["id"], activa=False)
    if estacion["estado"] != "Activo":
        raise PropuestaInvalida(f"La estación «{estacion['nombre']}» ya estaba archivada.")
    excel_repo.actualizar("Empresas", estacion["id"], {"Estado": "Inactivo"})
    return f"Estación archivada: {estacion['nombre']}.", {"Estado": "Activo"}


def _preparar_reactivar_estacion(args: dict, plan: Plan) -> dict:
    estacion = resolver("Empresas", args.get("nombre"), activa=False)
    if estacion["estado"] == "Activo":
        raise PropuestaInvalida(f"La estación «{estacion['nombre']}» ya está activa.")
    return {
        "titulo": f"Reactivar la estación {estacion['nombre']}",
        "detalles": ["Volverá a aparecer al registrar rutas y viajes."],
        "datos": {"id": estacion["id"], "nombre": estacion["nombre"]},
    }


def _ejecutar_reactivar_estacion(datos: dict) -> tuple[str, dict | None]:
    estacion = resolver("Empresas", datos["id"], activa=False)
    if estacion["estado"] == "Activo":
        raise PropuestaInvalida(f"La estación «{estacion['nombre']}» ya estaba activa.")
    excel_repo.actualizar("Empresas", estacion["id"], {"Estado": "Activo"})
    return f"Estación reactivada: {estacion['nombre']}.", {"Estado": "Inactivo"}


ACCIONES_RUTAS = (
    Accion(
        "guardar_ruta",
        "Guardar ruta",
        "Propone crear la ruta salida → llegada o, si ya existe, actualizar su código y precio. "
        "Úsala solo si la usuaria pidió registrar o cambiar una ruta y ya dio el código y el precio. "
        "Las dos estaciones deben existir (nombre exacto de rutas.estaciones) y estar activas.",
        {
            "salida": ("string", "Nombre exacto de la estación de salida."),
            "llegada": ("string", "Nombre exacto de la estación de llegada."),
            "codigo": ("string", "Código de la ruta, tal como lo dio la usuaria."),
            "precio": ("number", "Precio vigente de la ruta, mayor a cero."),
            "moneda": ("string", "CRC o USD. Usa CRC si la usuaria no indicó otra."),
        },
        ("salida", "llegada", "codigo", "precio"),
        False,
        _preparar_guardar_ruta,
        _ejecutar_guardar_ruta,
        _planear_guardar_ruta,
    ),
    Accion(
        "eliminar_ruta",
        "Eliminar ruta",
        "Propone quitar del catálogo una ruta registrada. Úsala solo si la usuaria pidió eliminarla de forma explícita.",
        {
            "salida": ("string", "Nombre exacto de la estación de salida."),
            "llegada": ("string", "Nombre exacto de la estación de llegada."),
        },
        ("salida", "llegada"),
        True,
        _preparar_eliminar_ruta,
        _ejecutar_eliminar_ruta,
    ),
    Accion(
        "crear_estacion",
        "Crear estación",
        "Propone crear una estación nueva y activa. Úsala cuando la usuaria pida agregarla, o cuando una ruta "
        "que ella pidió necesite una estación que todavía no existe (propón primero la estación y luego la ruta).",
        {"nombre": ("string", "Nombre de la estación.")},
        ("nombre",),
        False,
        _preparar_crear_estacion,
        _ejecutar_crear_estacion,
        _planear_crear_estacion,
    ),
    Accion(
        "archivar_estacion",
        "Archivar estación",
        "Propone archivar una estación activa (conserva su historial). Solo si la usuaria lo pidió explícitamente.",
        {"nombre": ("string", "Nombre exacto de la estación.")},
        ("nombre",),
        False,
        _preparar_archivar_estacion,
        _ejecutar_archivar_estacion,
    ),
    Accion(
        "reactivar_estacion",
        "Reactivar estación",
        "Propone reactivar una estación archivada. Solo si la usuaria lo pidió explícitamente.",
        {"nombre": ("string", "Nombre exacto de la estación.")},
        ("nombre",),
        False,
        _preparar_reactivar_estacion,
        _ejecutar_reactivar_estacion,
    ),
)
