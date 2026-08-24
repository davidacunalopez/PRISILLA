"""Crea y migra data/basedatos.xlsx sin descartar datos existentes."""

from __future__ import annotations

import shutil
import threading
from pathlib import Path
from zipfile import BadZipFile
from datetime import datetime

from openpyxl import Workbook, load_workbook
from openpyxl.utils.exceptions import InvalidFileException
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableColumn, TableStyleInfo

from app.config import DATA_DIR, EXCEL_PATH
from app.db.schema import HOJAS, columnas
from app.services.fechas import parse_fecha

_VERIFICACION_LOCK = threading.Lock()
_verificado = False


class LibroInvalidoError(RuntimeError):
    """El libro existe, pero su estructura no es segura para la aplicación."""


def asegurar_libro() -> None:
    global _verificado
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with _VERIFICACION_LOCK:
        if _verificado:
            return
        if not EXCEL_PATH.exists():
            crear_libro()
        else:
            try:
                migrar_libro()
            except (BadZipFile, InvalidFileException, EOFError) as exc:
                raise LibroInvalidoError(
                    "basedatos.xlsx está dañado o no es un libro válido. "
                    "Restaure un respaldo desde data/backups."
                ) from exc
        _verificado = True


def migrar_libro(ruta: Path | None = None, crear_backup_previo: bool = True) -> bool:
    """Añade hojas/columnas nuevas conservando el contenido del libro."""
    ruta = ruta or EXCEL_PATH
    wb = load_workbook(ruta)
    cambiado = False
    try:
        for nombre in HOJAS:
            if nombre not in wb.sheetnames:
                raise LibroInvalidoError(
                    f"Falta la hoja {nombre} en basedatos.xlsx. Restaure un respaldo desde data/backups."
                )
            ws = wb[nombre]
            headers = [str(c.value).strip() if c.value is not None else "" for c in ws[1]]
            if nombre == "Viajes" and "Desde" not in headers and "Fecha" in headers:
                fecha_idx = headers.index("Fecha") + 1
                ws.cell(1, fecha_idx, "Desde")
                headers[fecha_idx - 1] = "Desde"
                cambiado = True
            for col in columnas(nombre):
                if col not in headers:
                    migraciones_permitidas = {
                        ("Empresas", "Estado"),
                        ("Camiones", "ID_ChoferPredeterminado"),
                        ("Viajes", "Hasta"),
                        ("Viajes", "Semana"),
                        ("Viajes", "Fecha"),
                        ("Viajes", "CodigoRuta"),
                        ("Viajes", "Precio"),
                        ("Viajes", "Moneda"),
                        ("Viajes", "EmpresaTrabajo"),
                        ("Viajes", "Categoria"),
                        ("Viajes", "Enviada"),
                        ("Gasolina", "TipoCombustible"),
                        ("Gasolina", "NumeroBoleta"),
                        ("Gasolina", "ID_Chofer"),
                    }
                    if (nombre, col) not in migraciones_permitidas:
                        raise LibroInvalidoError(
                            f"Falta la columna {col} en la hoja {nombre}. Restaure un respaldo desde data/backups."
                        )
                    ws.cell(1, len(headers) + 1, col)
                    headers.append(col)
                    cambiado = True
                    if col == "Estado" and nombre == "Empresas":
                        for row in range(2, ws.max_row + 1):
                            if any(ws.cell(row, c).value not in (None, "") for c in range(1, len(headers))):
                                ws.cell(row, len(headers), "Activo")
                    if col == "Hasta" and nombre == "Viajes":
                        desde_idx = headers.index("Desde") + 1
                        for row in range(2, ws.max_row + 1):
                            ws.cell(row, len(headers), ws.cell(row, desde_idx).value)
                    if col == "Semana" and nombre == "Viajes":
                        desde_idx = headers.index("Desde") + 1
                        for row in range(2, ws.max_row + 1):
                            fecha = parse_fecha(ws.cell(row, desde_idx).value)
                            ws.cell(row, len(headers), fecha.isocalendar().week if fecha else "")
                    if col == "Fecha" and nombre == "Viajes":
                        desde_idx = headers.index("Desde") + 1
                        for row in range(2, ws.max_row + 1):
                            ws.cell(row, len(headers), ws.cell(row, desde_idx).value)
                    if col == "Moneda" and nombre == "Viajes":
                        for row in range(2, ws.max_row + 1):
                            if any(ws.cell(row, c).value not in (None, "") for c in range(1, len(headers))):
                                ws.cell(row, len(headers), "CRC")
                    if col == "Categoria" and nombre == "Viajes":
                        salida_idx = headers.index("ID_Salida") + 1
                        llegada_idx = headers.index("ID_Llegada") + 1
                        for row in range(2, ws.max_row + 1):
                            salida = str(ws.cell(row, salida_idx).value or "")
                            llegada = str(ws.cell(row, llegada_idx).value or "")
                            ws.cell(
                                row,
                                len(headers),
                                "Ruptura" if salida and salida == llegada else "Viaje completo",
                            )
                    if col == "Enviada" and nombre == "Viajes":
                        estado_idx = headers.index("Estado") + 1
                        for row in range(2, ws.max_row + 1):
                            estado = str(ws.cell(row, estado_idx).value or "")
                            ws.cell(row, len(headers), estado == "Enviado a contadora")
                    if col == "TipoCombustible" and nombre == "Gasolina":
                        for row in range(2, ws.max_row + 1):
                            if any(ws.cell(row, c).value not in (None, "") for c in range(1, len(headers))):
                                ws.cell(row, len(headers), "Diesel")

            if nombre == "Seguros" and "Periodicidad" in headers:
                periodicidad_idx = headers.index("Periodicidad") + 1
                for row in range(2, ws.max_row + 1):
                    if str(ws.cell(row, periodicidad_idx).value or "").strip() == "Semestral":
                        ws.cell(row, periodicidad_idx, "Trimestral")
                        cambiado = True

            if nombre == "Seguros" and {"FechaFin", "FechaUltimoPago"}.issubset(headers):
                fecha_fin_idx = headers.index("FechaFin") + 1
                fecha_limite_idx = headers.index("FechaUltimoPago") + 1
                for row in range(2, ws.max_row + 1):
                    fecha_fin = ws.cell(row, fecha_fin_idx).value
                    if ws.cell(row, fecha_limite_idx).value != fecha_fin:
                        ws.cell(row, fecha_limite_idx, fecha_fin)
                        cambiado = True

            tabla = next((t for t in ws.tables.values() if t.displayName == nombre), None)
            ref = f"A1:{get_column_letter(len(headers))}{max(1, ws.max_row)}"
            if tabla is None:
                tabla = Table(displayName=nombre, ref=ref)
                tabla.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showRowStripes=True)
                ws.add_table(tabla)
                cambiado = True
            elif tabla.ref != ref:
                tabla.ref = ref
                cambiado = True
            if [c.name for c in tabla.tableColumns] != headers:
                tabla.tableColumns = [TableColumn(id=i, name=header) for i, header in enumerate(headers, 1)]
                cambiado = True

        if cambiado:
            if crear_backup_previo:
                backup_dir = DATA_DIR / "backups"
                backup_dir.mkdir(parents=True, exist_ok=True)
                ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                shutil.copy2(ruta, backup_dir / f"basedatos_pre_migracion_{ts}.xlsx")
            wb.save(ruta)
        return cambiado
    finally:
        wb.close()


def crear_libro() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    primera = True
    header_fill = PatternFill("solid", fgColor="1A2F28")
    header_font = Font(bold=True, color="F4EFE6", name="Calibri")

    for nombre, meta in HOJAS.items():
        ws = wb.active if primera else wb.create_sheet(nombre)
        if primera:
            ws.title = nombre
            primera = False
        cols = columnas(nombre)
        ws.append(cols)
        for idx, _col in enumerate(cols, start=1):
            cell = ws.cell(1, idx)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center")
            ws.column_dimensions[get_column_letter(idx)].width = max(14, len(_col) + 4)

        ref = f"A1:{get_column_letter(len(cols))}1"
        tabla = Table(displayName=nombre, ref=ref)
        tabla.tableStyleInfo = TableStyleInfo(
            name="TableStyleMedium2",
            showFirstColumn=False,
            showLastColumn=False,
            showRowStripes=True,
            showColumnStripes=False,
        )
        ws.add_table(tabla)

    wb.save(EXCEL_PATH)
