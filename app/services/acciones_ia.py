"""Cambios que la IA propone y la usuaria confirma.

La IA nunca escribe. Gemini solo "llama herramientas"; aquí cada llamada se convierte en una propuesta que:
1. se valida con las mismas reglas que los formularios,
2. se muestra a la usuaria con el detalle exacto de lo que cambiará,
3. solo se ejecuta cuando ella la confirma, una sola vez, y se vuelve a validar en ese momento,
4. queda registrada en data/ia_cambios.jsonl.

Todo lo que viene del modelo (nombres, códigos, precios) se trata como no confiable.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from app.db import excel_repo
from app.db.excel_repo import ExcelBloqueadoError
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

VIGENCIA_SEGUNDOS = 30 * 60
MAX_PROPUESTAS = 5
MAX_PENDIENTES = 100


class PropuestaInvalida(ValueError):
    """El cambio pedido no se puede preparar o ya no se puede aplicar; el texto se muestra a la usuaria."""


class EstacionInexistente(PropuestaInvalida):
    pass


class Dependencia(PropuestaInvalida):
    """Falta confirmar antes otro cambio de la lista; la propuesta sigue pendiente."""


@dataclass(frozen=True)
class Accion:
    nombre: str
    etiqueta: str
    descripcion: str
    parametros: dict[str, tuple[str, str]]  # nombre -> (tipo JSON, descripción)
    requeridos: tuple[str, ...]
    destructiva: bool
    preparar: Callable[[dict, set[str]], dict]  # (args, estaciones que crea este mismo lote) -> vista + datos
    ejecutar: Callable[[dict], tuple[str, dict | None]]  # datos -> (mensaje, estado anterior para la bitácora)


_pendientes: dict[str, dict] = {}
_candado = threading.Lock()


def _plural(n: int, singular: str, plural: str) -> str:
    return f"{n} {singular if n == 1 else plural}"


# --- Búsqueda de estaciones ---

def _estacion(valor, *, activa: bool, planeadas: frozenset[str] | set[str] = frozenset()) -> dict:
    texto = " ".join(str(valor or "").split())
    if not texto:
        raise PropuestaInvalida("Falta indicar la estación.")
    buscado = normalizar(texto)
    todas = list(estaciones_por_id().values())
    exactas = [e for e in todas if normalizar(e["nombre"]) == buscado or e["id"].casefold() == buscado]
    if len(exactas) > 1:
        raise PropuestaInvalida(f"«{texto}» coincide con varias estaciones; use el nombre completo.")
    if exactas:
        estacion = exactas[0]
        if activa and estacion["estado"] != "Activo":
            raise PropuestaInvalida(
                f"La estación «{estacion['nombre']}» está archivada. Reactívela primero (Estaciones → Reactivar)."
            )
        return estacion
    if buscado in planeadas:
        return {"id": "", "nombre": texto, "estado": "Activo", "planeada": True}
    parecidas = [
        e["nombre"]
        for e in todas
        if normalizar(e["nombre"]) and (buscado in normalizar(e["nombre"]) or normalizar(e["nombre"]) in buscado)
    ][:3]
    pista = f" ¿Quiso decir: {', '.join(parecidas)}?" if parecidas else " Primero hay que crearla."
    raise EstacionInexistente(f"No existe la estación «{texto}».{pista}")


def _estacion_al_ejecutar(id_estacion: str, nombre: str) -> dict:
    """Vuelve a buscar la estación al confirmar. Si debía crearla otro cambio de la lista, avisa en vez de fallar."""
    try:
        return _estacion(id_estacion or nombre, activa=True)
    except EstacionInexistente as exc:
        if not id_estacion:
            raise Dependencia(f"Primero confirme el cambio que crea la estación «{nombre}».") from exc
        raise


def _par(rutas: list[dict], salida_id: str, llegada_id: str) -> dict | None:
    return next((r for r in rutas if r["salida_id"] == salida_id and r["llegada_id"] == llegada_id), None)


# --- guardar_ruta ---

def _preparar_guardar_ruta(args: dict, planeadas: set[str]) -> dict:
    salida = _estacion(args.get("salida"), activa=True, planeadas=planeadas)
    llegada = _estacion(args.get("llegada"), activa=True, planeadas=planeadas)
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
                f"Se actualizarán {_plural(pendientes, 'viaje pendiente', 'viajes pendientes')} de esta ruta; "
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
        },
    }


def _ejecutar_guardar_ruta(datos: dict) -> tuple[str, dict | None]:
    salida = _estacion_al_ejecutar(datos["salida_id"], datos["salida"])
    llegada = _estacion_al_ejecutar(datos["llegada_id"], datos["llegada"])
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

def _preparar_eliminar_ruta(args: dict, planeadas: set[str]) -> dict:
    salida = _estacion(args.get("salida"), activa=False)
    llegada = _estacion(args.get("llegada"), activa=False)
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
        detalles.append(f"Hay {_plural(pendientes, 'viaje pendiente', 'viajes pendientes')} en esta ruta; no cambian.")
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
    nombre = " ".join(str(args.get("nombre") or "").split())
    if not nombre or len(nombre) > 100:
        raise PropuestaInvalida("El nombre de la estación es obligatorio y puede tener hasta 100 caracteres.")
    return nombre


def _validar_estacion_nueva(nombre: str) -> None:
    errores = validar("Empresas", {"NombreEmpresa": nombre, "Estado": "Activo"})
    if any(normalizar(e["nombre"]) == normalizar(nombre) for e in estaciones_por_id().values()):
        errores.append("Ya existe una estación con ese nombre.")
    if errores:
        raise PropuestaInvalida(" ".join(dict.fromkeys(errores)))


def _preparar_crear_estacion(args: dict, planeadas: set[str]) -> dict:
    nombre = _nombre_estacion(args)
    _validar_estacion_nueva(nombre)
    return {
        "titulo": f"Crear la estación {nombre}",
        "detalles": [f"Nombre: {nombre}", "Quedará activa y disponible para rutas y viajes."],
        "datos": {"nombre": nombre},
    }


def _ejecutar_crear_estacion(datos: dict) -> tuple[str, dict | None]:
    _validar_estacion_nueva(datos["nombre"])
    fila = excel_repo.insertar("Empresas", {"NombreEmpresa": datos["nombre"], "Estado": "Activo"})
    return f"Estación creada: {datos['nombre']} ({fila['ID_Empresa']}).", None


def _preparar_archivar_estacion(args: dict, planeadas: set[str]) -> dict:
    estacion = _estacion(args.get("nombre"), activa=False)
    if estacion["estado"] != "Activo":
        raise PropuestaInvalida(f"La estación «{estacion['nombre']}» ya está archivada.")
    usos = sum(1 for r in analizar_rutas() if estacion["id"] in (r["salida_id"], r["llegada_id"]))
    detalles = ["Conserva su historial; se puede reactivar cuando quiera."]
    if usos:
        detalles.append(
            f"{_plural(usos, 'ruta la usa', 'rutas la usan')}: dejarán de poder elegirse al registrar viajes mientras esté archivada."
        )
    return {
        "titulo": f"Archivar la estación {estacion['nombre']}",
        "detalles": detalles,
        "datos": {"id": estacion["id"], "nombre": estacion["nombre"]},
    }


def _ejecutar_archivar_estacion(datos: dict) -> tuple[str, dict | None]:
    estacion = _estacion(datos["id"], activa=False)
    if estacion["estado"] != "Activo":
        raise PropuestaInvalida(f"La estación «{estacion['nombre']}» ya estaba archivada.")
    excel_repo.actualizar("Empresas", estacion["id"], {"Estado": "Inactivo"})
    return f"Estación archivada: {estacion['nombre']}.", {"Estado": "Activo"}


def _preparar_reactivar_estacion(args: dict, planeadas: set[str]) -> dict:
    estacion = _estacion(args.get("nombre"), activa=False)
    if estacion["estado"] == "Activo":
        raise PropuestaInvalida(f"La estación «{estacion['nombre']}» ya está activa.")
    return {
        "titulo": f"Reactivar la estación {estacion['nombre']}",
        "detalles": ["Volverá a aparecer al registrar rutas y viajes."],
        "datos": {"id": estacion["id"], "nombre": estacion["nombre"]},
    }


def _ejecutar_reactivar_estacion(datos: dict) -> tuple[str, dict | None]:
    estacion = _estacion(datos["id"], activa=False)
    if estacion["estado"] == "Activo":
        raise PropuestaInvalida(f"La estación «{estacion['nombre']}» ya estaba activa.")
    excel_repo.actualizar("Empresas", estacion["id"], {"Estado": "Activo"})
    return f"Estación reactivada: {estacion['nombre']}.", {"Estado": "Inactivo"}


ACCIONES: dict[str, Accion] = {
    a.nombre: a
    for a in (
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
}


# --- Propuestas pendientes de confirmación ---

def _limpiar() -> None:
    ahora = time.time()
    for token in [t for t, p in _pendientes.items() if p["expira"] < ahora]:
        del _pendientes[token]
    while len(_pendientes) > MAX_PENDIENTES:
        del _pendientes[next(iter(_pendientes))]


def _vista(propuesta: dict, token: str = "", error: str = "") -> dict:
    return {
        "token": token,
        "titulo": propuesta["titulo"],
        "detalles": propuesta.get("detalles", []),
        "destructiva": propuesta.get("destructiva", False),
        "error": error,
    }


def preparar_propuestas(llamadas: list[tuple[str, dict]]) -> list[dict]:
    """Convierte las herramientas que pidió el modelo en propuestas; las inválidas se devuelven con su motivo."""
    vistas = []
    planeadas: set[str] = set()
    repetidas = set()
    lote = secrets.token_urlsafe(8)
    orden = 0
    for nombre, args in llamadas[:MAX_PROPUESTAS]:
        accion = ACCIONES.get(nombre)
        if accion is None:
            continue
        args = dict(args or {})
        clave = (nombre, json.dumps(args, sort_keys=True, default=str))
        if clave in repetidas:
            continue
        repetidas.add(clave)
        try:
            preparada = accion.preparar(args, planeadas)
        except PropuestaInvalida as exc:
            resumen = ", ".join(str(v) for v in args.values())[:80]
            vistas.append(_vista({"titulo": f"{accion.etiqueta}: {resumen}"}, error=str(exc)))
            continue
        if nombre == "crear_estacion":
            planeadas.add(normalizar(preparada["datos"]["nombre"]))
        token = secrets.token_urlsafe(16)
        propuesta = {
            **preparada,
            "accion": nombre,
            "destructiva": accion.destructiva,
            "expira": time.time() + VIGENCIA_SEGUNDOS,
            "lote": lote,
            "orden": orden,
        }
        orden += 1
        with _candado:
            _limpiar()
            _pendientes[token] = propuesta
        vistas.append(_vista(propuesta, token))
    omitidas = len(llamadas) - MAX_PROPUESTAS
    if omitidas > 0:
        vistas.append(
            _vista(
                {"titulo": "Cambios omitidos"},
                error=f"Se omitieron {_plural(omitidas, 'cambio', 'cambios')}; pídalos en otra pregunta.",
            )
        )
    return vistas


def propuestas_del_lote(lote: str) -> list[dict]:
    """Propuestas que siguen pendientes de una misma respuesta de la IA, en su orden original."""
    if not lote:
        return []
    with _candado:
        _limpiar()
        del_lote = sorted(((t, p) for t, p in _pendientes.items() if p["lote"] == lote), key=lambda x: x[1]["orden"])
        return [_vista(p, t) for t, p in del_lote]


def descartar(token: str) -> str | None:
    """Descarta una propuesta; devuelve su lote (None si ya no estaba pendiente)."""
    with _candado:
        propuesta = _pendientes.pop(token, None)
    return None if propuesta is None else propuesta["lote"]


def _bitacora(propuesta: dict, ok: bool, mensaje: str, antes: dict | None) -> None:
    registro = {
        "fecha": datetime.now().isoformat(timespec="seconds"),
        "accion": propuesta["accion"],
        "titulo": propuesta["titulo"],
        "datos": propuesta["datos"],
        "aplicado": ok,
        "mensaje": mensaje,
        "antes": antes,
    }
    try:
        ruta = excel_repo.EXCEL_PATH.parent / "ia_cambios.jsonl"
        ruta.parent.mkdir(parents=True, exist_ok=True)
        with ruta.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(registro, ensure_ascii=False, default=str) + "\n")
    except OSError:
        pass  # la bitácora nunca debe impedir ni deshacer un cambio ya confirmado


def aplicar(token: str) -> dict:
    """Ejecuta una propuesta confirmada. Cada token sirve una sola vez."""
    with _candado:
        _limpiar()
        propuesta = _pendientes.pop(token, None)
    if propuesta is None:
        return {
            "ok": False,
            "titulo": "",
            "lote": "",
            "mensaje": "Esta propuesta ya se aplicó, se descartó o venció. Vuelva a pedírsela a la IA.",
        }
    accion = ACCIONES[propuesta["accion"]]
    antes = None
    try:
        mensaje, antes = accion.ejecutar(propuesta["datos"])
        ok = True
    except Dependencia as exc:
        with _candado:
            _pendientes[token] = propuesta
        return {"ok": False, "titulo": propuesta["titulo"], "lote": propuesta["lote"], "mensaje": str(exc)}
    except PropuestaInvalida as exc:
        ok, mensaje = False, str(exc)
    except (ExcelBloqueadoError, OSError) as exc:
        # Fallo transitorio (por ejemplo, el Excel abierto): se conserva para reintentar.
        with _candado:
            _pendientes[token] = propuesta
        ok, mensaje = False, f"{exc} Puede volver a intentarlo."
    except KeyError:
        ok, mensaje = False, "No se encontró el registro que se iba a modificar."
    _bitacora(propuesta, ok, mensaje, antes)
    return {"ok": ok, "titulo": propuesta["titulo"], "lote": propuesta["lote"], "mensaje": mensaje}


def aplicar_varias(tokens: list[str]) -> list[dict]:
    """Aplica en orden y se detiene en el primer fallo; lo que no se aplicó sigue pendiente."""
    resultados = []
    for token in tokens:
        resultado = aplicar(token)
        resultados.append(resultado)
        if not resultado["ok"]:
            break
    return resultados
