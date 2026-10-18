"""Marcas de camiones: las comparten el formulario de camiones y los cambios que propone la IA."""

from __future__ import annotations

from app.config import load_config, save_config
from app.db import excel_repo


def marcas_disponibles() -> list[str]:
    configuradas = [str(m).strip() for m in load_config().get("marcas_camiones", []) if str(m).strip()]
    existentes = [str(c.get("Marca") or "").strip() for c in excel_repo.leer("Camiones")]
    unicas = {}
    for marca in configuradas + existentes:
        if marca:
            unicas.setdefault(marca.casefold(), marca)
    return sorted(unicas.values(), key=str.casefold)


def errores_marca(marca: str) -> list[str]:
    """Errores de formato de una marca; no escribe nada."""
    if len(marca) > 50:
        return ["La marca no puede tener más de 50 caracteres."]
    if any(c in marca for c in "\r\n"):
        return ["La marca debe ocupar una sola línea."]
    return []


def registrar_marca(marca: str) -> None:
    """Agrega la marca al catálogo de Configuración si es nueva. Es una escritura."""
    marca = marca.strip()
    if not marca or errores_marca(marca):
        return
    if marca.casefold() not in {m.casefold() for m in marcas_disponibles()}:
        marcas = list(load_config().get("marcas_camiones") or [])
        marcas.append(marca)
        save_config({"marcas_camiones": marcas})
