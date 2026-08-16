"""Chequeo diario de pólizas. Pensado para Windows Task Scheduler.

Uso (desde la raíz del proyecto, con el venv):
    .venv\\Scripts\\python.exe scripts\\revisar_vencimientos.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.plantilla_base import asegurar_libro  # noqa: E402
from app.services.alertas import revisar_y_enviar  # noqa: E402


def main() -> int:
    asegurar_libro()
    resultado = revisar_y_enviar()
    print(resultado["mensaje"])
    return 0 if resultado["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
