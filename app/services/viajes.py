"""Reglas de los viajes: las comparten el formulario y los cambios que propone la IA."""

from __future__ import annotations

from app.config import load_config, save_config
from app.services.fechas import a_float, parse_fecha
from app.services.mensajes import es_verdadero


def mapa_rutas() -> dict[str, dict]:
    return {
        f"{str(r.get('salida') or '')}|{str(r.get('llegada') or '')}": {
            "codigo": str(r.get("codigo") or ""),
            "precio": r.get("precio", ""),
            "moneda": str(r.get("moneda") or "CRC"),
        }
        for r in load_config().get("rutas_configuradas") or []
        if r.get("salida") and r.get("llegada") and r.get("codigo")
    }


def datos_ruta(salida: str, llegada: str) -> dict:
    return mapa_rutas().get(f"{salida}|{llegada}", {})


def esta_enviado(viaje: dict) -> bool:
    return es_verdadero(viaje.get("Enviada")) or viaje.get("Estado") == "Enviado a contadora"


def empresa_trabajo_valida(nombre: str) -> bool:
    return bool(nombre) and len(nombre) <= 100 and not any(c in nombre for c in "\r\n")


def registrar_empresa_trabajo(nombre) -> None:
    """Agrega la empresa al catálogo si es nueva. Es una escritura: nunca debe ocurrir al solo proponer un cambio."""
    nombre = str(nombre or "").strip()
    if not empresa_trabajo_valida(nombre):
        return
    empresas = load_config().get("empresas_trabajo") or []
    if nombre.casefold() not in {e.casefold() for e in empresas}:
        save_config({"empresas_trabajo": empresas + [nombre]})


def preparar_datos(datos: dict, original: dict | None = None, ruta: dict | None = None) -> list[str]:
    """Completa fecha, semana, código y precio del viaje y devuelve los errores de reglas. No escribe nada.

    `ruta` reemplaza la búsqueda en la configuración; solo lo usa la IA para mostrar el resultado de una ruta
    que otro cambio del mismo lote todavía va a crear.
    """
    fecha = parse_fecha(datos.get("Fecha"))
    if fecha:
        datos["Fecha"] = fecha.isoformat()
        datos["Desde"] = fecha.isoformat()
        datos["Hasta"] = fecha.isoformat()
        datos["Semana"] = str(fecha.isocalendar().week)
    errores = []
    salida = str(datos.get("ID_Salida") or "")
    llegada = str(datos.get("ID_Llegada") or "")
    enviada_original = bool(original and esta_enviado(original))
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
    if misma_ruta_historica and original:
        ruta_usada = {
            "codigo": original.get("CodigoRuta"),
            "precio": original.get("Precio"),
            "moneda": original.get("Moneda") or "CRC",
        }
    else:
        ruta_usada = ruta if ruta is not None else datos_ruta(salida, llegada)
    datos["CodigoRuta"] = str(ruta_usada.get("codigo") or "")
    precio_configurado = ruta_usada.get("precio", "")
    moneda_configurada = str(ruta_usada.get("moneda") or "CRC")
    precio = a_float(precio_configurado, -1)
    if precio_configurado not in (None, "") and precio > 0 and moneda_configurada in {"CRC", "USD"}:
        datos["Precio"] = round(precio, 2)
        datos["Moneda"] = moneda_configurada
    else:
        datos["Precio"] = ""
        datos["Moneda"] = moneda_configurada if moneda_configurada in {"CRC", "USD"} else "CRC"
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
    empresa_trabajo = str(datos.get("EmpresaTrabajo") or "").strip()
    datos["EmpresaTrabajo"] = empresa_trabajo
    if not empresa_trabajo:
        errores.append("Seleccione la empresa para la que se realiza el viaje.")
    elif not empresa_trabajo_valida(empresa_trabajo):
        errores.append("La empresa para la que se trabaja debe ocupar una sola línea de hasta 100 caracteres.")
    return errores
