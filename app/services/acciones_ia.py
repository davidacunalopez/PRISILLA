"""Cambios que la IA propone y la usuaria confirma.

La IA nunca escribe. Gemini solo "llama herramientas"; aquí cada llamada se convierte en una propuesta que:
1. se valida con las mismas reglas que los formularios,
2. se muestra a la usuaria con el detalle exacto de lo que cambiará,
3. solo se ejecuta cuando ella la confirma, una sola vez, y se vuelve a validar en ese momento,
4. queda registrada en data/ia_cambios.jsonl.

Las acciones viven por área (acciones_rutas, acciones_flota, acciones_viajes); este módulo guarda las propuestas
pendientes y las aplica. Todo lo que viene del modelo (nombres, códigos, precios) se trata como no confiable.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from datetime import datetime

from app.db import excel_repo
from app.db.excel_repo import ExcelBloqueadoError
from app.services.acciones_base import Accion, Dependencia, Plan, PropuestaInvalida, plural
from app.services.acciones_flota import ACCIONES_FLOTA
from app.services.acciones_rutas import ACCIONES_RUTAS
from app.services.acciones_viajes import ACCIONES_VIAJES

VIGENCIA_SEGUNDOS = 30 * 60
MAX_PROPUESTAS = 10
MAX_PENDIENTES = 100

ACCIONES: dict[str, Accion] = {a.nombre: a for a in (*ACCIONES_RUTAS, *ACCIONES_FLOTA, *ACCIONES_VIAJES)}

__all__ = [
    "ACCIONES", "PropuestaInvalida", "aplicar", "aplicar_varias", "descartar",
    "preparar_propuestas", "propuestas_del_lote",
]

_pendientes: dict[str, dict] = {}
_candado = threading.Lock()


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
    plan = Plan()
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
            preparada = accion.preparar(args, plan)
        except PropuestaInvalida as exc:
            resumen = ", ".join(str(v) for v in args.values())[:80]
            vistas.append(_vista({"titulo": f"{accion.etiqueta}: {resumen}"}, error=str(exc)))
            continue
        if accion.planear:
            accion.planear(preparada["datos"], plan)
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
                error=f"Se omitieron {plural(omitidas, 'cambio', 'cambios')}; pídalos en otra pregunta.",
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
