"""Análisis de rutas: lo comparten la pantalla de Configuración y el asistente de IA."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from urllib.parse import urlencode

from app.config import load_config, save_config
from app.db import excel_repo
from app.services.fechas import a_float, formato_crc
from app.services.tarifas import actualizar_viajes_pendientes_de_ruta

SIN_ESTACION = "Estación no disponible"

# Reglas que la IA puede citar. Deben coincidir con el comportamiento real de la app.
REGLAS_DE_RUTAS = [
    "Una ruta es un par ordenado: estación de salida → estación de llegada. "
    "A → B y B → A son rutas distintas y cada una se registra por separado.",
    "Varias rutas pueden llegar a la misma estación (A → C y B → C) o salir de la misma estación. "
    "Lo único que no se repite es el mismo par exacto salida → llegada: si se guarda otra vez, "
    "se actualiza el código y el precio de la ruta que ya existía.",
    "Cada ruta guarda un código, un precio vigente y una moneda (CRC o USD). "
    "Varias rutas pueden compartir el mismo código: el sistema avisa, pero lo permite.",
    "Al cambiar el código o el precio de una ruta se actualizan los viajes pendientes de esa ruta; "
    "los viajes ya enviados a la contadora conservan su precio.",
    "Un viaje solo se puede guardar si su ruta (salida y llegada) está registrada con código y precio. "
    "Si no, la app pide configurarla en Configuración → Códigos de rutas.",
    "Solo las estaciones activas se pueden elegir al registrar rutas y viajes. "
    "Una estación archivada conserva su historial y se reactiva con el botón Reactivar en Estaciones.",
    "Una ruta con la misma estación de salida y de llegada es válida.",
    "La categoría del viaje (Viaje completo, Desvío o Ruptura) se elige en cada viaje; no depende de la ruta.",
]

DONDE_REGISTRAR = [
    "Estaciones: menú Estaciones → botón «Nueva estación». Una estación debe existir y estar activa antes de usarla en una ruta.",
    "Rutas: menú Configuración → sección «Códigos de rutas». Elegir Salida y Llegada, escribir Código, Precio y Moneda "
    "y pulsar «Guardar o actualizar ruta». Debajo de los selectores la pantalla indica si la ruta es nueva o ya existe.",
    "Para corregir una ruta: botón «Editar» en la tabla de rutas. "
    "Para quitarla: botón «Eliminar» (los viajes históricos conservan su código y precio).",
    "Viajes: menú Viajes → nuevo viaje. Al elegir salida y llegada, el código y el precio se llenan solos desde la ruta registrada.",
]


def normalizar(texto) -> str:
    """Minúsculas y sin tildes, para comparar nombres escritos de forma distinta."""
    base = unicodedata.normalize("NFD", str(texto or ""))
    return "".join(c for c in base if not unicodedata.combining(c)).casefold()


def estaciones_por_id() -> dict[str, dict]:
    estaciones = {}
    for fila in excel_repo.leer("Empresas"):
        id_estacion = str(fila.get("ID_Empresa") or "")
        if id_estacion:
            estaciones[id_estacion] = {
                "id": id_estacion,
                "nombre": str(fila.get("NombreEmpresa") or id_estacion),
                "estado": str(fila.get("Estado") or "Activo"),
            }
    return estaciones


def analizar_rutas(cfg: dict | None = None) -> list[dict]:
    """Rutas configuradas, con nombres legibles, códigos compartidos y problemas detectados."""
    cfg = cfg or load_config()
    estaciones = estaciones_por_id()
    rutas = []
    for cruda in cfg.get("rutas_configuradas") or []:
        salida_id = str(cruda.get("salida") or "")
        llegada_id = str(cruda.get("llegada") or "")
        salida = estaciones.get(salida_id)
        llegada = estaciones.get(llegada_id)
        nombre_salida = salida["nombre"] if salida else SIN_ESTACION
        nombre_llegada = llegada["nombre"] if llegada else SIN_ESTACION
        codigo = str(cruda.get("codigo") or "").strip()
        moneda = str(cruda.get("moneda") or "CRC")
        precio = cruda.get("precio", "")

        problemas = []
        if not codigo:
            problemas.append("No tiene código: no se puede usar al registrar viajes.")
        if precio in (None, "") or a_float(precio, -1) <= 0:
            problemas.append("No tiene un precio vigente válido: los viajes de esta ruta no se guardan hasta configurarlo.")
        if moneda not in {"CRC", "USD"}:
            problemas.append(f"La moneda «{moneda}» no es válida (use CRC o USD).")
        for rol, estacion in (("salida", salida), ("llegada", llegada)):
            if estacion is None:
                problemas.append(f"La estación de {rol} ya no existe.")
            elif estacion["estado"] != "Activo":
                problemas.append(
                    f"La estación de {rol} «{estacion['nombre']}» está archivada: no aparece al registrar viajes."
                )

        rutas.append(
            {
                "salida_id": salida_id,
                "llegada_id": llegada_id,
                "salida": nombre_salida,
                "llegada": nombre_llegada,
                "etiqueta": f"{nombre_salida} → {nombre_llegada}",
                "codigo": codigo,
                "precio": precio,
                "moneda": moneda,
                "problemas": problemas,
            }
        )

    rutas.sort(key=lambda r: (r["salida"].casefold(), r["llegada"].casefold()))
    por_codigo: dict[str, list[dict]] = {}
    for ruta in rutas:
        if ruta["codigo"]:
            por_codigo.setdefault(ruta["codigo"].casefold(), []).append(ruta)
    for ruta in rutas:
        ruta["comparte_con"] = [
            otra["etiqueta"]
            for otra in por_codigo.get(ruta["codigo"].casefold(), [])
            if otra is not ruta
        ]
    return rutas


def texto_precio(precio, moneda: str) -> str:
    if precio in (None, ""):
        return "Sin configurar"
    return formato_crc(precio) if moneda == "CRC" else f"${a_float(precio):,.2f}"


def validar_datos_ruta(codigo: str, precio_raw: str, moneda: str) -> list[str]:
    """Errores del código, el precio y la moneda de una ruta."""
    errores = []
    if not codigo or len(codigo) > 50 or any(c in codigo for c in "\r\n"):
        errores.append("El código de ruta es obligatorio y debe tener hasta 50 caracteres.")
    if not str(precio_raw).strip() or a_float(precio_raw, -1) <= 0:
        errores.append("El precio de la ruta debe ser un número mayor a cero.")
    if moneda not in {"CRC", "USD"}:
        errores.append("La moneda de la ruta debe ser CRC o USD.")
    return errores


def validar_ruta(salida: str, llegada: str, codigo: str, precio_raw: str, moneda: str) -> list[str]:
    """Errores de una ruta antes de guardarla. Lo usan el formulario y los cambios que propone la IA."""
    errores = []
    if not salida or excel_repo.leer_por_id("Empresas", salida) is None:
        errores.append("Seleccione una estación de salida válida.")
    if not llegada or excel_repo.leer_por_id("Empresas", llegada) is None:
        errores.append("Seleccione una estación de llegada válida.")
    return errores + validar_datos_ruta(codigo, precio_raw, moneda)


def guardar_ruta(salida: str, llegada: str, codigo: str, precio: float, moneda: str) -> dict:
    """Crea o actualiza la ruta (ya validada) y reprecia sus viajes pendientes."""
    rutas = [dict(r) for r in load_config().get("rutas_configuradas") or []]
    codigo_compartido = any(
        str(r.get("codigo") or "").casefold() == codigo.casefold()
        and (str(r.get("salida")) != salida or str(r.get("llegada")) != llegada)
        for r in rutas
    )
    antes = None
    for ruta in rutas:
        if str(ruta.get("salida")) == salida and str(ruta.get("llegada")) == llegada:
            antes = dict(ruta)
            ruta["codigo"] = codigo
            ruta["precio"] = round(precio, 2)
            ruta["moneda"] = moneda
            break
    else:
        rutas.append(
            {"salida": salida, "llegada": llegada, "codigo": codigo, "precio": round(precio, 2), "moneda": moneda}
        )
    save_config({"rutas_configuradas": rutas})
    viajes = actualizar_viajes_pendientes_de_ruta(salida, llegada, codigo=codigo, precio=precio, moneda=moneda)
    return {
        "creada": antes is None,
        "antes": antes,
        "codigo_compartido": codigo_compartido,
        "viajes_actualizados": viajes,
    }


def eliminar_ruta(salida: str, llegada: str) -> dict | None:
    """Quita la ruta configurada y la devuelve (None si no existía). Los viajes históricos no cambian."""
    rutas = load_config().get("rutas_configuradas") or []
    es_esa = lambda r: str(r.get("salida")) == salida and str(r.get("llegada")) == llegada
    quitada = next((dict(r) for r in rutas if es_esa(r)), None)
    save_config({"rutas_configuradas": [r for r in rutas if not es_esa(r)]})
    return quitada


def viajes_sin_ruta_configurada(rutas: list[dict], limite: int = 10) -> list[dict]:
    """Pares salida → llegada que ya tienen viajes pero no una ruta con código."""
    configuradas = {(r["salida_id"], r["llegada_id"]) for r in rutas if r["codigo"]}
    nombres = {e["id"]: e["nombre"] for e in estaciones_por_id().values()}
    conteo: Counter = Counter()
    for viaje in excel_repo.leer("Viajes"):
        par = (str(viaje.get("ID_Salida") or ""), str(viaje.get("ID_Llegada") or ""))
        if par[0] and par[1] and par not in configuradas:
            conteo[par] += 1
    return [
        {
            "ruta": f"{nombres.get(a, SIN_ESTACION)} → {nombres.get(b, SIN_ESTACION)}",
            "viajes": cantidad,
        }
        for (a, b), cantidad in conteo.most_common(limite)
    ]


def estaciones_mencionadas(texto: str, estaciones: list[dict]) -> list[dict]:
    """Estaciones cuyo nombre aparece en el texto, en orden de aparición y sin repetir."""
    normal = normalizar(texto)
    candidatos = []
    for estacion in estaciones:
        nombre = normalizar(estacion["nombre"]).strip()
        if len(nombre) < 3:
            continue
        for coincidencia in re.finditer(rf"(?<!\w){re.escape(nombre)}(?!\w)", normal):
            candidatos.append((coincidencia.start(), coincidencia.end(), estacion))
    # A igual posición gana el nombre más largo ("Montebello 3" antes que "Montebello").
    candidatos.sort(key=lambda c: (c[0], -(c[1] - c[0])))
    resultado, vistas, ocupado_hasta = [], set(), -1
    for inicio, fin, estacion in candidatos:
        if inicio < ocupado_hasta:
            continue
        ocupado_hasta = fin
        if estacion["id"] not in vistas:
            vistas.add(estacion["id"])
            resultado.append(estacion)
    return resultado


def consulta_de_rutas(pregunta: str, rutas: list[dict] | None = None) -> dict | None:
    """Si la pregunta nombra dos estaciones, resuelve localmente si la ruta existe (en ambos sentidos)."""
    estaciones = list(estaciones_por_id().values())
    mencionadas = estaciones_mencionadas(pregunta, estaciones)
    if len(mencionadas) < 2:
        return None
    rutas = analizar_rutas() if rutas is None else rutas
    primera, segunda = mencionadas[0], mencionadas[1]

    def describir(origen: dict, destino: dict) -> dict:
        ruta = next(
            (r for r in rutas if r["salida_id"] == origen["id"] and r["llegada_id"] == destino["id"]), None
        )
        return {
            "salida": origen["nombre"],
            "llegada": destino["nombre"],
            "registrada": ruta is not None,
            "datos": (
                {k: ruta[k] for k in ("codigo", "precio", "moneda", "comparte_con", "problemas")}
                if ruta
                else None
            ),
            "estaciones_archivadas": [
                e["nombre"] for e in (origen, destino) if e["estado"] != "Activo"
            ],
        }

    return {
        "estaciones_mencionadas": [e["nombre"] for e in mencionadas],
        "nota": "La primera estación nombrada se tomó como salida; revisa también el sentido contrario.",
        "primera_a_segunda": describir(primera, segunda),
        "segunda_a_primera": describir(segunda, primera),
        "otras_rutas_que_llegan_a_la_segunda": [
            r["etiqueta"]
            for r in rutas
            if r["llegada_id"] == segunda["id"] and r["salida_id"] != primera["id"]
        ],
    }


def acciones_para_pregunta(pregunta: str) -> list[dict]:
    """Botones que la app ofrece junto a la respuesta de la IA. Se calculan aquí, nunca con texto del modelo."""
    estaciones = list(estaciones_por_id().values())
    mencionadas = estaciones_mencionadas(pregunta, estaciones)
    rutas = analizar_rutas()
    acciones = []
    if len(mencionadas) >= 2:
        salida, llegada = mencionadas[0], mencionadas[1]
        if salida["estado"] == "Activo" and llegada["estado"] == "Activo":
            registrada = any(
                r["salida_id"] == salida["id"] and r["llegada_id"] == llegada["id"] for r in rutas
            )
            consulta = urlencode({"ruta_salida": salida["id"], "ruta_llegada": llegada["id"]})
            acciones.append(
                {
                    "texto": f"{'Ver' if registrada else 'Registrar'} la ruta {salida['nombre']} → {llegada['nombre']}",
                    "href": f"/configuracion?{consulta}#rutas",
                }
            )
    if "ruta" in normalizar(pregunta):
        acciones.append({"texto": "Ir a Códigos de rutas", "href": "/configuracion#rutas"})
    return acciones
