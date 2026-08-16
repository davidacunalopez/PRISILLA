r"""Restaura un respaldo verificado y conserva una copia del libro actual.

Uso desde la raíz del proyecto, con la aplicación cerrada:
    .venv\Scripts\python.exe scripts\restaurar_backup.py data\backups\basedatos_YYYYMMDD_HHMMSS_ffffff.xlsx
"""

from __future__ import annotations

import os
import shutil
import sys
import uuid
from datetime import datetime
from pathlib import Path

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import BACKUP_DIR, EXCEL_PATH  # noqa: E402


def main() -> int:
    if len(sys.argv) != 2:
        print("Indique la ruta de un respaldo .xlsx.", file=sys.stderr)
        return 2
    origen = Path(sys.argv[1]).resolve()
    carpeta_permitida = BACKUP_DIR.resolve()
    if origen.suffix.lower() != ".xlsx" or carpeta_permitida not in origen.parents or not origen.is_file():
        print("El archivo debe ser un .xlsx existente dentro de data/backups.", file=sys.stderr)
        return 2

    prueba = load_workbook(origen, read_only=True)
    prueba.close()
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    seguridad = BACKUP_DIR / f"basedatos_antes_restaurar_{ts}.xlsx"
    shutil.copy2(EXCEL_PATH, seguridad)

    temporal = EXCEL_PATH.with_name(f".{EXCEL_PATH.stem}_{uuid.uuid4().hex}.restore.xlsx")
    try:
        shutil.copy2(origen, temporal)
        os.replace(temporal, EXCEL_PATH)
    finally:
        temporal.unlink(missing_ok=True)
    print(f"Restaurado: {origen.name}")
    print(f"Copia del libro anterior: {seguridad.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
