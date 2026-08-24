"""Fuente única de verdad: columnas, prefijos de ID y valores permitidos."""

from __future__ import annotations

from typing import Any

HOJAS: dict[str, dict[str, Any]] = {
    "Camiones": {
        "id_col": "ID_Camion",
        "id_prefix": "CAM",
        "display": "Placa",
        "columns": [
            "ID_Camion",
            "Placa",
            "Marca",
            "Modelo",
            "Anio",
            "Capacidad",
            "Estado",
            "Notas",
            "ID_ChoferPredeterminado",
        ],
        "required": ["Placa", "Marca", "Estado"],
        "choices": {
            "Estado": ["Activo", "Inactivo"],
        },
    },
    "Seguros": {
        "id_col": "ID_Seguro",
        "id_prefix": "SEG",
        "display": "NumeroPoliza",
        "columns": [
            "ID_Seguro",
            "ID_Camion",
            "TipoSeguro",
            "Aseguradora",
            "NumeroPoliza",
            "Periodicidad",
            "FechaInicio",
            "FechaFin",
            "MontoPrima",
            "Moneda",
            "Estado",
            "FechaUltimoPago",
            "Notas",
        ],
        "required": ["ID_Camion", "TipoSeguro", "Periodicidad", "FechaInicio", "FechaFin", "Estado"],
        "choices": {
            "TipoSeguro": ["Auto", "Carga", "Riesgos Laborales"],
            "Periodicidad": ["Mensual", "Trimestral"],
            "Estado": ["Activo", "Renovado", "Cancelado"],
            "Moneda": ["CRC"],
        },
    },
    "Empresas": {
        "id_col": "ID_Empresa",
        "id_prefix": "EMP",
        "display": "NombreEmpresa",
        "columns": [
            "ID_Empresa",
            "NombreEmpresa",
            "Contacto",
            "Telefono",
            "Email",
            "Notas",
            "Estado",
        ],
        "required": ["NombreEmpresa", "Estado"],
        "choices": {
            "Estado": ["Activo", "Inactivo"],
        },
    },
    "Choferes": {
        "id_col": "ID_Chofer",
        "id_prefix": "CHF",
        "display": "Nombre",
        "columns": [
            "ID_Chofer",
            "Nombre",
            "Telefono",
            "Licencia",
            "Estado",
        ],
        "required": ["Nombre", "Estado"],
        "choices": {
            "Estado": ["Activo", "Inactivo"],
        },
    },
    "Viajes": {
        "id_col": "ID_Viaje",
        "id_prefix": "VIA",
        "display": "ID_Viaje",
        "columns": [
            "ID_Viaje",
            "Desde",
            "Hasta",
            "Semana",
            "ID_Salida",
            "ID_Llegada",
            "ID_Camion",
            "ID_Chofer",
            "Contenedor",
            "Chasis",
            "GuiaTirManifiesto",
            "Estado",
            "FechaRegistro",
            "Fecha",
            "CodigoRuta",
            "Precio",
            "Moneda",
            "EmpresaTrabajo",
            "Categoria",
            "Enviada",
        ],
        "required": ["Fecha", "Semana", "ID_Salida", "ID_Llegada", "EmpresaTrabajo", "ID_Camion", "ID_Chofer", "Precio", "Moneda", "Categoria", "Estado"],
        "choices": {
            "Estado": ["Pendiente", "Enviado a contadora"],
            "Moneda": ["CRC", "USD"],
            "Categoria": ["Viaje completo", "Desvío", "Ruptura"],
        },
    },
    "Gasolina": {
        "id_col": "ID_Registro",
        "id_prefix": "GAS",
        "display": "ID_Registro",
        "columns": [
            "ID_Registro",
            "Fecha",
            "TipoCombustible",
            "CantidadComprada",
            "ID_Camion",
            "NumeroBoleta",
            "ID_Chofer",
            "MontoTotal",
            "PrecioPorLitro",
            "Moneda",
        ],
        "required": [
            "Fecha",
            "TipoCombustible",
            "CantidadComprada",
            "ID_Camion",
            "NumeroBoleta",
            "ID_Chofer",
            "MontoTotal",
        ],
        "choices": {
            "TipoCombustible": ["Diesel", "Super", "Regular"],
            "Moneda": ["CRC"],
        },
    },
}

TIPOS_SEGURO = HOJAS["Seguros"]["choices"]["TipoSeguro"]
PERIODICIDAD_MESES = {"Mensual": 1, "Trimestral": 3}


def columnas(hoja: str) -> list[str]:
    return list(HOJAS[hoja]["columns"])


def fila_vacia(hoja: str) -> dict[str, str]:
    return {col: "" for col in columnas(hoja)}
