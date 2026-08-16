"""Carga config.json y .env. Las rutas se resuelven desde la raíz del proyecto."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
BACKUP_DIR = DATA_DIR / "backups"
EXPORT_DIR = DATA_DIR / "exportaciones"
EXCEL_PATH = DATA_DIR / "basedatos.xlsx"
CONFIG_PATH = ROOT / "config.json"
ENV_PATH = ROOT / ".env"

load_dotenv(ENV_PATH)

DEFAULT_CONFIG = {
    "nombre_app": "BITACORA",
    "nombre_empresa": "Transportes",
    "umbral_amarillo_dias": 28,
    "umbral_rojo_dias": 7,
    "backups_conservar": 10,
    "moneda": "CRC",
    "marcas_camiones": [],
    "empresas_trabajo": [],
    "rutas_configuradas": [],
}


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return dict(DEFAULT_CONFIG)
    with CONFIG_PATH.open(encoding="utf-8") as fh:
        guardado = json.load(fh)
    return {**DEFAULT_CONFIG, **guardado}


def save_config(data: dict) -> None:
    current = load_config()
    current.update(data)
    temporal = CONFIG_PATH.with_name(f".{CONFIG_PATH.stem}_{uuid.uuid4().hex}.tmp.json")
    with temporal.open("w", encoding="utf-8") as fh:
        json.dump(current, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(temporal, CONFIG_PATH)


def smtp_settings() -> dict:
    return {
        "host": os.getenv("SMTP_HOST", "").strip(),
        "port": int(os.getenv("SMTP_PORT") or 587),
        "user": os.getenv("SMTP_USER", "").strip(),
        "password": os.getenv("SMTP_PASSWORD", "").strip(),
        "to": os.getenv("ALERT_EMAIL_TO", "").strip(),
        "from_addr": (os.getenv("ALERT_EMAIL_FROM") or os.getenv("SMTP_USER") or "").strip(),
    }


def gemini_settings() -> dict:
    """Configuración privada de Gemini cargada desde .env."""
    clave = os.getenv("GEMINI_API_KEY", "").strip()
    return {
        "api_key": clave,
        "model": (os.getenv("GEMINI_MODEL") or "gemini-3.6-flash").strip(),
        "enabled": (os.getenv("GEMINI_ENABLED") or "0").strip().lower()
        in {"1", "true", "yes", "si", "sí"},
    }


def save_gemini_settings(*, api_key: str | None, model: str, enabled: bool) -> None:
    """Actualiza solo las variables de Gemini sin tocar las credenciales SMTP."""
    valores = {
        "GEMINI_MODEL": model,
        "GEMINI_ENABLED": "1" if enabled else "0",
    }
    if api_key is not None:
        valores["GEMINI_API_KEY"] = api_key

    lineas = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    pendientes = set(valores)
    nuevas = []
    for linea in lineas:
        clave = linea.split("=", 1)[0].strip() if "=" in linea and not linea.lstrip().startswith("#") else ""
        if clave in valores:
            nuevas.append(f"{clave}={valores[clave]}")
            pendientes.discard(clave)
        else:
            nuevas.append(linea)
    if pendientes and nuevas and nuevas[-1].strip():
        nuevas.append("")
    for clave in ("GEMINI_API_KEY", "GEMINI_MODEL", "GEMINI_ENABLED"):
        if clave in pendientes:
            nuevas.append(f"{clave}={valores[clave]}")

    temporal = ENV_PATH.with_name(f".{ENV_PATH.stem}_{uuid.uuid4().hex}.tmp")
    temporal.write_text("\n".join(nuevas).rstrip() + "\n", encoding="utf-8")
    os.replace(temporal, ENV_PATH)
    for clave, valor in valores.items():
        if valor:
            os.environ[clave] = valor
        else:
            os.environ.pop(clave, None)
