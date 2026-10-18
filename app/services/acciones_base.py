"""Piezas comunes de los cambios que propone la IA: errores, plan del lote y búsqueda de registros.

Todo lo que viene del modelo (nombres, placas, códigos, precios) es contenido no confiable: aquí solo se usa
para buscar registros reales y para armar propuestas que la usuaria revisa y confirma.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from app.db import excel_repo
from app.db.schema import HOJAS
from app.services.rutas import normalizar


class PropuestaInvalida(ValueError):
    """El cambio pedido no se puede preparar o ya no se puede aplicar; el texto se muestra a la usuaria."""


class RegistroInexistente(PropuestaInvalida):
    pass


class Dependencia(PropuestaInvalida):
    """Falta confirmar antes otro cambio de la lista; la propuesta sigue pendiente."""


@dataclass(frozen=True)
class Tipo:
    """Cómo se nombra un tipo de registro en los mensajes."""

    hoja: str
    singular: str
    plural: str
    articulo: str  # "la" / "el"
    todos: str  # "varias" / "varios"
    archivado: str  # "archivada" / "archivado"
    reactivarlo: str  # "Reactívela" / "Reactívelo"
    menu: str

    def clave(self, texto) -> str:
        """Forma comparable: sin tildes ni mayúsculas (y, en placas, sin espacios ni guiones)."""
        base = normalizar(texto)
        if self.hoja == "Camiones":
            return re.sub(r"[^0-9a-z]", "", base)
        return " ".join(base.split())


TIPOS = {
    "Empresas": Tipo("Empresas", "estación", "estaciones", "la", "varias", "archivada", "Reactívela", "Estaciones"),
    "Camiones": Tipo("Camiones", "camión", "camiones", "el", "varios", "archivado", "Reactívelo", "Camiones"),
    "Choferes": Tipo("Choferes", "chofer", "choferes", "el", "varios", "archivado", "Reactívelo", "Choferes"),
}


@dataclass
class Plan:
    """Lo que otros cambios del mismo lote van a crear, para poder proponer p. ej. estación + ruta + viaje juntos."""

    estaciones: set[str] = field(default_factory=set)
    camiones: set[str] = field(default_factory=set)
    choferes: set[str] = field(default_factory=set)
    rutas: dict[tuple[str, str], dict] = field(default_factory=dict)

    def de(self, hoja: str) -> set[str]:
        return {"Empresas": self.estaciones, "Camiones": self.camiones, "Choferes": self.choferes}[hoja]


@dataclass(frozen=True)
class Accion:
    nombre: str
    etiqueta: str
    descripcion: str
    parametros: dict[str, tuple[str, str]]  # nombre -> (tipo JSON, descripción)
    requeridos: tuple[str, ...]
    destructiva: bool
    preparar: Callable[[dict, Plan], dict]  # (args, plan del lote) -> título, detalles y datos
    ejecutar: Callable[[dict], tuple[str, dict | None]]  # datos -> (mensaje, estado anterior para la bitácora)
    planear: Callable[[dict, Plan], None] | None = None  # qué crea esta acción para los cambios que la siguen


def plural(n: int, singular: str, plural_: str) -> str:
    return f"{n} {singular if n == 1 else plural_}"


def texto(valor) -> str:
    """Texto de un argumento del modelo: sin espacios de más; un número entero llega sin '.0'."""
    if valor is None:
        return ""
    if isinstance(valor, float) and valor.is_integer():
        return str(int(valor))
    return " ".join(str(valor).split())


def registros(hoja: str) -> list[dict]:
    meta = HOJAS[hoja]
    salida = []
    for fila in excel_repo.leer(hoja):
        id_registro = str(fila.get(meta["id_col"]) or "")
        if id_registro:
            salida.append(
                {
                    "id": id_registro,
                    "nombre": str(fila.get(meta["display"]) or id_registro),
                    "estado": str(fila.get("Estado") or "Activo"),
                }
            )
    return salida


def resolver(hoja: str, valor, *, activa: bool, planeadas: frozenset[str] | set[str] = frozenset()) -> dict:
    """Busca un registro por nombre (o id) exacto, sin importar tildes ni mayúsculas. No adivina."""
    tipo = TIPOS[hoja]
    nombre = texto(valor)
    if not nombre:
        raise PropuestaInvalida(f"Falta indicar {tipo.articulo} {tipo.singular}.")
    buscado = tipo.clave(nombre)
    todos = registros(hoja)
    exactos = [r for r in todos if tipo.clave(r["nombre"]) == buscado or r["id"].casefold() == nombre.casefold()]
    if len(exactos) > 1:
        raise PropuestaInvalida(f"«{nombre}» coincide con {tipo.todos} {tipo.plural}; use el nombre completo.")
    if exactos:
        encontrado = exactos[0]
        if activa and encontrado["estado"] != "Activo":
            art = tipo.articulo.capitalize()
            raise PropuestaInvalida(
                f"{art} {tipo.singular} «{encontrado['nombre']}» está {tipo.archivado}. "
                f"{tipo.reactivarlo} primero ({tipo.menu} → Reactivar)."
            )
        return encontrado
    if buscado in planeadas:
        return {"id": "", "nombre": nombre, "estado": "Activo", "planeada": True}
    parecidos = [
        r["nombre"]
        for r in todos
        if tipo.clave(r["nombre"]) and (buscado in tipo.clave(r["nombre"]) or tipo.clave(r["nombre"]) in buscado)
    ][:3]
    pista = f" ¿Quiso decir: {', '.join(parecidos)}?" if parecidos else " Primero hay que crearla."
    if hoja != "Empresas" and not parecidos:
        pista = " Primero hay que registrarlo."
    raise RegistroInexistente(f"No existe {tipo.articulo} {tipo.singular} «{nombre}».{pista}")


def resolver_al_ejecutar(hoja: str, id_registro: str, nombre: str) -> dict:
    """Vuelve a buscar al confirmar. Si debía crearlo otro cambio de la lista, avisa en vez de fallar."""
    try:
        return resolver(hoja, id_registro or nombre, activa=True)
    except RegistroInexistente as exc:
        if not id_registro:
            tipo = TIPOS[hoja]
            raise Dependencia(
                f"Primero confirme el cambio que crea {tipo.articulo} {tipo.singular} «{nombre}»."
            ) from exc
        raise


def datos_de_fila(hoja: str, fila: dict) -> dict:
    """Las columnas de una fila como las enviaría el formulario (texto, sin el id)."""
    meta = HOJAS[hoja]
    return {c: ("" if fila.get(c) is None else str(fila.get(c))) for c in meta["columns"] if c != meta["id_col"]}


def sin_repetidos(errores: list[str]) -> str:
    return " ".join(dict.fromkeys(errores))
