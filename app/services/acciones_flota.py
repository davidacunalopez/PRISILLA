"""Cambios de IA sobre camiones y choferes. Validan con las mismas reglas que sus formularios."""

from __future__ import annotations

from app.db import excel_repo
from app.services.acciones_base import (
    TIPOS,
    Accion,
    Plan,
    PropuestaInvalida,
    datos_de_fila,
    plural,
    registros,
    resolver,
    resolver_al_ejecutar,
    sin_repetidos,
    texto,
)
from app.services.camiones import errores_marca, marcas_disponibles, registrar_marca
from app.services.formularios import validar

_CAM = TIPOS["Camiones"]
_CHF = TIPOS["Choferes"]
_SIN_CHOFER = {"", "ninguno", "ninguna", "sin chofer", "quitar", "nadie"}
NUEVO = "(nuevo)"  # id provisional de un chofer que se crea en el mismo lote

ETIQUETAS_CAMION = {
    "Placa": "Placa", "Marca": "Marca", "Modelo": "Modelo", "Anio": "Año", "Capacidad": "Capacidad",
    "Notas": "Notas", "ID_ChoferPredeterminado": "Chofer habitual",
}
ARGS_CAMION = {"nueva_placa": "Placa", "marca": "Marca", "modelo": "Modelo", "anio": "Anio",
               "capacidad": "Capacidad", "notas": "Notas"}
ETIQUETAS_CHOFER = {"Nombre": "Nombre", "Telefono": "Teléfono", "Licencia": "Licencia"}
ARGS_CHOFER = {"nuevo_nombre": "Nombre", "telefono": "Telefono", "licencia": "Licencia"}


def _fila(hoja: str, id_registro: str) -> dict:
    fila = excel_repo.leer_por_id(hoja, id_registro)
    if fila is None:
        raise PropuestaInvalida("Ese registro ya no existe.")
    return fila


def _nombres(hoja: str) -> dict[str, str]:
    return excel_repo.mapa_display(hoja)


# ---------------------------------------------------------------- camiones

def _chofer_habitual(valor, plan: Plan | None) -> dict | None:
    """None = no se mencionó; {"id": ""} sin 'planeada' = quitar el chofer habitual."""
    if valor is None:
        return None
    nombre = texto(valor)
    if nombre.casefold() in _SIN_CHOFER:
        return {"id": "", "nombre": ""}
    chofer = resolver("Choferes", nombre, activa=True, planeadas=plan.choferes if plan else frozenset())
    # Un chofer que crea otro cambio del lote todavía no tiene id: se marca para no confundirlo con "quitar".
    return {**chofer, "id": NUEVO} if chofer.get("planeada") else chofer


def _validar_camion(datos: dict, id_actual: str = "") -> None:
    errores = validar("Camiones", datos, id_actual) + errores_marca(datos.get("Marca", ""))
    if errores:
        raise PropuestaInvalida(sin_repetidos(errores))


def _detalle_campos(datos: dict, etiquetas: dict, omitir: tuple[str, ...] = ()) -> list[str]:
    return [f"{etiquetas[c]}: {datos[c]}" for c in etiquetas if datos.get(c) and c not in omitir]


def _preparar_crear_camion(args: dict, plan: Plan) -> dict:
    chofer = _chofer_habitual(args.get("chofer_habitual"), plan)
    if chofer is not None and not chofer["nombre"]:
        chofer = None
    datos = {
        "Placa": texto(args.get("placa")).upper(),
        "Marca": texto(args.get("marca")),
        "Modelo": texto(args.get("modelo")),
        "Anio": texto(args.get("anio")),
        "Capacidad": texto(args.get("capacidad")),
        "Notas": texto(args.get("notas")),
        "Estado": "Activo",
        "ID_ChoferPredeterminado": "" if chofer is None or chofer["id"] == NUEVO else chofer["id"],
    }
    _validar_camion(datos)
    detalles = _detalle_campos(datos, ETIQUETAS_CAMION, omitir=("ID_ChoferPredeterminado",))
    if chofer:
        detalles.append(f"Chofer habitual: {chofer['nombre']}")
    if datos["Marca"].casefold() not in {m.casefold() for m in marcas_disponibles()}:
        detalles.append(f"La marca «{datos['Marca']}» es nueva y se agregará al catálogo de marcas.")
    return {
        "titulo": f"Registrar el camión {datos['Placa']}",
        "detalles": detalles,
        "datos": {
            "form": datos,
            "chofer": chofer["nombre"] if chofer else "",
            "chofer_id": "" if chofer is None or chofer["id"] == NUEVO else chofer["id"],
        },
    }


def _planear_crear_camion(datos: dict, plan: Plan) -> None:
    plan.camiones.add(_CAM.clave(datos["form"]["Placa"]))


def _ejecutar_crear_camion(datos: dict) -> tuple[str, dict | None]:
    form = dict(datos["form"])
    if datos["chofer"]:
        form["ID_ChoferPredeterminado"] = resolver_al_ejecutar("Choferes", datos["chofer_id"], datos["chofer"])["id"]
    _validar_camion(form)
    registrar_marca(form["Marca"])
    fila = excel_repo.insertar("Camiones", form)
    return f"Camión registrado: {form['Placa']} ({fila['ID_Camion']}).", None


def _calcular_edicion_camion(camion_id: str, args: dict, chofer: dict | None) -> tuple[dict, dict, dict]:
    """(fila actual, cambios por columna, datos completos ya mezclados) para un camión."""
    fila = _fila("Camiones", camion_id)
    cambios = {}
    for arg, columna in ARGS_CAMION.items():
        if args.get(arg) is None:
            continue
        nuevo = texto(args[arg])
        if columna == "Placa":
            nuevo = nuevo.upper()
        if nuevo != str(fila.get(columna) or ""):
            cambios[columna] = nuevo
    if chofer is not None and chofer["id"] != str(fila.get("ID_ChoferPredeterminado") or ""):
        cambios["ID_ChoferPredeterminado"] = chofer["id"]
    datos = {**datos_de_fila("Camiones", fila), **cambios}
    return fila, cambios, datos


def _preparar_editar_camion(args: dict, plan: Plan) -> dict:
    camion = resolver("Camiones", args.get("placa"), activa=False)
    chofer = _chofer_habitual(args.get("chofer_habitual"), plan)
    fila, cambios, datos = _calcular_edicion_camion(camion["id"], args, chofer)
    if not cambios:
        raise PropuestaInvalida(f"El camión {camion['nombre']} ya tiene esos datos; no hay nada que cambiar.")
    _validar_camion({**datos, "ID_ChoferPredeterminado": "" if datos.get("ID_ChoferPredeterminado") == NUEVO else datos.get("ID_ChoferPredeterminado", "")}, camion["id"])
    nombres = _nombres("Choferes")
    detalles = []
    for columna, nuevo in cambios.items():
        antes, despues = str(fila.get(columna) or ""), nuevo
        if columna == "ID_ChoferPredeterminado":
            antes, despues = nombres.get(antes, ""), (chofer["nombre"] if chofer else "")
        detalles.append(f"{ETIQUETAS_CAMION[columna]}: {antes or '—'} → {despues or '—'}")
    if "Marca" in cambios and cambios["Marca"].casefold() not in {m.casefold() for m in marcas_disponibles()}:
        detalles.append(f"La marca «{cambios['Marca']}» es nueva y se agregará al catálogo de marcas.")
    return {
        "titulo": f"Cambiar datos del camión {camion['nombre']}",
        "detalles": detalles,
        "datos": {
            "id": camion["id"],
            "placa": camion["nombre"],
            "args": {a: args[a] for a in ARGS_CAMION if args.get(a) is not None},
            "chofer": (chofer["nombre"] if chofer else None),
            "chofer_id": (None if chofer is None else ("" if chofer["id"] == NUEVO else chofer["id"])),
            "chofer_dado": chofer is not None,
        },
    }


def _ejecutar_editar_camion(datos: dict) -> tuple[str, dict | None]:
    chofer = None
    if datos["chofer_dado"]:
        chofer = {"id": "", "nombre": ""}
        if datos["chofer"]:
            chofer = resolver_al_ejecutar("Choferes", datos["chofer_id"], datos["chofer"])
    fila, cambios, completo = _calcular_edicion_camion(datos["id"], datos["args"], chofer)
    if not cambios:
        raise PropuestaInvalida(f"El camión {datos['placa']} ya tiene esos datos.")
    _validar_camion(completo, datos["id"])
    if "Marca" in cambios:
        registrar_marca(cambios["Marca"])
    excel_repo.actualizar("Camiones", datos["id"], cambios)
    return f"Camión actualizado: {completo['Placa']}.", {c: fila.get(c) for c in cambios}


# ---------------------------------------------------------------- choferes

def _validar_chofer(datos: dict, id_actual: str = "") -> None:
    errores = validar("Choferes", datos, id_actual)
    if errores:
        raise PropuestaInvalida(sin_repetidos(errores))


def _preparar_crear_chofer(args: dict, plan: Plan) -> dict:
    datos = {
        "Nombre": texto(args.get("nombre")),
        "Telefono": texto(args.get("telefono")),
        "Licencia": texto(args.get("licencia")),
        "Estado": "Activo",
    }
    _validar_chofer(datos)
    detalles = _detalle_campos(datos, ETIQUETAS_CHOFER)
    if any(_CHF.clave(c["nombre"]) == _CHF.clave(datos["Nombre"]) for c in registros("Choferes")):
        detalles.append("Aviso: ya hay un chofer con ese nombre; se creará otro (los formularios lo permiten).")
    return {"titulo": f"Registrar al chofer {datos['Nombre']}", "detalles": detalles, "datos": {"form": datos}}


def _planear_crear_chofer(datos: dict, plan: Plan) -> None:
    plan.choferes.add(_CHF.clave(datos["form"]["Nombre"]))


def _ejecutar_crear_chofer(datos: dict) -> tuple[str, dict | None]:
    _validar_chofer(datos["form"])
    fila = excel_repo.insertar("Choferes", datos["form"])
    return f"Chofer registrado: {datos['form']['Nombre']} ({fila['ID_Chofer']}).", None


def _calcular_edicion_chofer(chofer_id: str, args: dict) -> tuple[dict, dict, dict]:
    fila = _fila("Choferes", chofer_id)
    cambios = {}
    for arg, columna in ARGS_CHOFER.items():
        if args.get(arg) is None:
            continue
        nuevo = texto(args[arg])
        if nuevo != str(fila.get(columna) or ""):
            cambios[columna] = nuevo
    return fila, cambios, {**datos_de_fila("Choferes", fila), **cambios}


def _preparar_editar_chofer(args: dict, plan: Plan) -> dict:
    chofer = resolver("Choferes", args.get("nombre"), activa=False)
    fila, cambios, datos = _calcular_edicion_chofer(chofer["id"], args)
    if not cambios:
        raise PropuestaInvalida(f"El chofer {chofer['nombre']} ya tiene esos datos; no hay nada que cambiar.")
    _validar_chofer(datos, chofer["id"])
    detalles = [
        f"{ETIQUETAS_CHOFER[c]}: {str(fila.get(c) or '') or '—'} → {nuevo or '—'}" for c, nuevo in cambios.items()
    ]
    return {
        "titulo": f"Cambiar datos del chofer {chofer['nombre']}",
        "detalles": detalles,
        "datos": {"id": chofer["id"], "nombre": chofer["nombre"], "args": {a: args[a] for a in ARGS_CHOFER if args.get(a) is not None}},
    }


def _ejecutar_editar_chofer(datos: dict) -> tuple[str, dict | None]:
    fila, cambios, completo = _calcular_edicion_chofer(datos["id"], datos["args"])
    if not cambios:
        raise PropuestaInvalida(f"El chofer {datos['nombre']} ya tiene esos datos.")
    _validar_chofer(completo, datos["id"])
    excel_repo.actualizar("Choferes", datos["id"], cambios)
    return f"Chofer actualizado: {completo['Nombre']}.", {c: fila.get(c) for c in cambios}


# ------------------------------------------------- archivar / reactivar

def _acciones_estado(hoja: str, sufijo: str, parametro: str, descripcion: str) -> tuple[Accion, Accion]:
    tipo = TIPOS[hoja]

    def preparar(archivar: bool):
        def _preparar(args: dict, plan: Plan) -> dict:
            registro = resolver(hoja, args.get(parametro), activa=False)
            if archivar and registro["estado"] != "Activo":
                raise PropuestaInvalida(f"{tipo.articulo.capitalize()} {tipo.singular} «{registro['nombre']}» ya está {tipo.archivado}.")
            if not archivar and registro["estado"] == "Activo":
                raise PropuestaInvalida(f"{tipo.articulo.capitalize()} {tipo.singular} «{registro['nombre']}» ya está activo.")
            if archivar:
                detalles = ["Conserva su historial; se puede reactivar cuando quiera."]
                if hoja == "Camiones":
                    detalles.append("Dejará de aparecer al elegir camión en viajes y seguros nuevos.")
                else:
                    habituales = [
                        c for c in excel_repo.leer("Camiones")
                        if str(c.get("ID_ChoferPredeterminado") or "") == registro["id"]
                    ]
                    detalles.append("Dejará de aparecer al elegir chofer en viajes y gasolina nuevos.")
                    if habituales:
                        detalles.append(
                            f"Es el chofer habitual de {plural(len(habituales), 'camión', 'camiones')}; siguen asignados."
                        )
            else:
                detalles = ["Volverá a aparecer al registrar viajes."]
            verbo = "Archivar" if archivar else "Reactivar"
            return {
                "titulo": f"{verbo} {tipo.articulo} {tipo.singular} {registro['nombre']}",
                "detalles": detalles,
                "datos": {"id": registro["id"], "nombre": registro["nombre"]},
            }

        return _preparar

    def ejecutar(archivar: bool):
        def _ejecutar(datos: dict) -> tuple[str, dict | None]:
            registro = resolver(hoja, datos["id"], activa=False)
            if archivar and registro["estado"] != "Activo":
                raise PropuestaInvalida(f"{tipo.articulo.capitalize()} {tipo.singular} «{registro['nombre']}» ya estaba {tipo.archivado}.")
            if not archivar and registro["estado"] == "Activo":
                raise PropuestaInvalida(f"{tipo.articulo.capitalize()} {tipo.singular} «{registro['nombre']}» ya estaba activo.")
            excel_repo.actualizar(hoja, registro["id"], {"Estado": "Inactivo" if archivar else "Activo"})
            hecho = "archivado" if archivar else "reactivado"
            return f"{tipo.singular.capitalize()} {hecho}: {registro['nombre']}.", {"Estado": "Activo" if archivar else "Inactivo"}

        return _ejecutar

    def accion(archivar: bool) -> Accion:
        verbo = "archivar" if archivar else "reactivar"
        return Accion(
            f"{verbo}_{sufijo}",
            f"{verbo.capitalize()} {tipo.singular}",
            f"Propone {verbo} {tipo.articulo} {tipo.singular}"
            + (" activo (conserva su historial)" if archivar else " archivado")
            + ". Solo si la usuaria lo pidió explícitamente.",
            {parametro: ("string", descripcion)},
            (parametro,),
            False,
            preparar(archivar),
            ejecutar(archivar),
        )

    return accion(True), accion(False)


_ARCHIVAR_CAMION, _REACTIVAR_CAMION = _acciones_estado("Camiones", "camion", "placa", "Placa exacta del camión.")
_ARCHIVAR_CHOFER, _REACTIVAR_CHOFER = _acciones_estado("Choferes", "chofer", "nombre", "Nombre exacto del chofer.")

ACCIONES_FLOTA = (
    Accion(
        "crear_camion",
        "Registrar camión",
        "Propone registrar un camión nuevo. Úsala solo si la usuaria pidió agregarlo y dio al menos la placa y la marca. "
        "La placa no puede repetirse. El chofer habitual es opcional y debe existir.",
        {
            "placa": ("string", "Placa del camión."),
            "marca": ("string", "Marca (por ejemplo Volvo, Freightliner)."),
            "modelo": ("string", "Modelo, si lo dio la usuaria."),
            "anio": ("integer", "Año de fabricación, si lo dio la usuaria."),
            "capacidad": ("string", "Capacidad, si la dio la usuaria."),
            "notas": ("string", "Notas, si las dio la usuaria."),
            "chofer_habitual": ("string", "Nombre exacto del chofer habitual, si lo pidió."),
        },
        ("placa", "marca"),
        False,
        _preparar_crear_camion,
        _ejecutar_crear_camion,
        _planear_crear_camion,
    ),
    Accion(
        "editar_camion",
        "Cambiar datos de camión",
        "Propone cambiar datos de un camión existente. Envía solo los campos que la usuaria quiere cambiar. "
        "Para quitar el chofer habitual usa chofer_habitual con el texto 'ninguno'.",
        {
            "placa": ("string", "Placa actual del camión que se quiere cambiar."),
            "nueva_placa": ("string", "Placa nueva, solo si hay que corregirla."),
            "marca": ("string", "Marca nueva."),
            "modelo": ("string", "Modelo nuevo."),
            "anio": ("integer", "Año nuevo."),
            "capacidad": ("string", "Capacidad nueva."),
            "notas": ("string", "Notas nuevas."),
            "chofer_habitual": ("string", "Nombre exacto del chofer habitual, o 'ninguno' para quitarlo."),
        },
        ("placa",),
        False,
        _preparar_editar_camion,
        _ejecutar_editar_camion,
    ),
    _ARCHIVAR_CAMION,
    _REACTIVAR_CAMION,
    Accion(
        "crear_chofer",
        "Registrar chofer",
        "Propone registrar un chofer nuevo. Úsala solo si la usuaria pidió agregarlo y dio el nombre. "
        "Teléfono y licencia son opcionales: pásalos solo si ella los escribió.",
        {
            "nombre": ("string", "Nombre completo del chofer."),
            "telefono": ("string", "Teléfono, si lo dio la usuaria."),
            "licencia": ("string", "Licencia, si la dio la usuaria."),
        },
        ("nombre",),
        False,
        _preparar_crear_chofer,
        _ejecutar_crear_chofer,
        _planear_crear_chofer,
    ),
    Accion(
        "editar_chofer",
        "Cambiar datos de chofer",
        "Propone cambiar datos de un chofer existente. Envía solo los campos que la usuaria quiere cambiar.",
        {
            "nombre": ("string", "Nombre actual del chofer que se quiere cambiar."),
            "nuevo_nombre": ("string", "Nombre nuevo, solo si hay que corregirlo."),
            "telefono": ("string", "Teléfono nuevo."),
            "licencia": ("string", "Licencia nueva."),
        },
        ("nombre",),
        False,
        _preparar_editar_chofer,
        _ejecutar_editar_chofer,
    ),
    _ARCHIVAR_CHOFER,
    _REACTIVAR_CHOFER,
)
