"""Parseo y validación de formularios según schema.py."""

from __future__ import annotations

from datetime import date

from fastapi import Request

from app.db import excel_repo
from app.db.schema import HOJAS
from app.services.fechas import a_float, parse_fecha


async def datos_form(request: Request, hoja: str) -> dict:
    form = await request.form()
    cols = HOJAS[hoja]["columns"]
    id_col = HOJAS[hoja]["id_col"]
    datos = {}
    for col in cols:
        if col == id_col:
            continue
        datos[col] = str(form.get(col, "") or "").strip()
    return datos


def validar(hoja: str, datos: dict, id_actual: str = "") -> list[str]:
    errores = []
    required = HOJAS[hoja].get("required") or []
    choices = HOJAS[hoja].get("choices") or {}
    for campo in required:
        if not str(datos.get(campo, "")).strip():
            errores.append(f"El campo {campo} es obligatorio.")
    for campo, opciones in choices.items():
        valor = str(datos.get(campo, "")).strip()
        if valor and valor not in opciones:
            errores.append(f"{campo} no es un valor válido.")

    relaciones = {
        "Camiones": {"ID_ChoferPredeterminado": "Choferes"},
        "Seguros": {"ID_Camion": "Camiones"},
        "Viajes": {
            "ID_Salida": "Empresas",
            "ID_Llegada": "Empresas",
            "ID_Camion": "Camiones",
            "ID_Chofer": "Choferes",
        },
        "Gasolina": {"ID_Camion": "Camiones", "ID_Chofer": "Choferes"},
    }
    for campo, destino in relaciones.get(hoja, {}).items():
        valor = str(datos.get(campo, "")).strip()
        if valor and excel_repo.leer_por_id(destino, valor) is None:
            errores.append(f"{campo} no corresponde a un registro existente.")

    fechas_por_hoja = {
        "Seguros": ["FechaInicio", "FechaFin", "FechaUltimoPago"],
        "Viajes": ["Fecha", "FechaRegistro"],
        "Gasolina": ["Fecha"],
    }
    for campo in fechas_por_hoja.get(hoja, []):
        valor = str(datos.get(campo, "")).strip()
        if valor and parse_fecha(valor) is None:
            errores.append(f"{campo} no contiene una fecha válida.")

    if hoja == "Seguros":
        inicio = parse_fecha(datos.get("FechaInicio"))
        fin = parse_fecha(datos.get("FechaFin"))
        if inicio and fin and fin < inicio:
            errores.append("La fecha final no puede ser anterior a la fecha inicial.")
        monto = str(datos.get("MontoPrima", "")).strip()
        if monto and a_float(monto, -1) < 0:
            errores.append("El monto de la prima no puede ser negativo.")

    if hoja == "Viajes":
        precio = str(datos.get("Precio") or "").strip()
        if precio and a_float(precio, -1) < 0:
            errores.append("El precio de la ruta debe ser un número válido y no negativo.")

    if hoja == "Gasolina":
        if a_float(datos.get("CantidadComprada")) <= 0 or a_float(datos.get("MontoTotal")) <= 0:
            errores.append("Litros y total pagado deben ser mayores a cero.")

    campo_anio = {"Camiones": "Anio"}.get(hoja)
    if campo_anio and str(datos.get(campo_anio, "")).strip():
        try:
            anio = int(str(datos[campo_anio]))
            if not 1900 <= anio <= date.today().year + 1:
                raise ValueError
        except ValueError:
            errores.append(f"{campo_anio} debe ser un año válido entre 1900 y {date.today().year + 1}.")

    unicos = {
        "Camiones": ("Placa", "Ya existe un camión con esa placa."),
        "Empresas": ("NombreEmpresa", "Ya existe una estación con ese nombre."),
        "Gasolina": ("NumeroBoleta", "Ya existe una factura con ese número de boleta."),
    }
    if hoja in unicos:
        campo, mensaje = unicos[hoja]
        buscado = str(datos.get(campo, "")).strip().casefold()
        id_col = HOJAS[hoja]["id_col"]
        if buscado and any(
            str(f.get(campo, "")).strip().casefold() == buscado
            and str(f.get(id_col, "")) != str(id_actual)
            for f in excel_repo.leer(hoja)
        ):
            errores.append(mensaje)
    return errores
