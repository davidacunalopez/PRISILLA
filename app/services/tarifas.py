"""Aplicación de tarifas vigentes a viajes todavía no enviados."""

from __future__ import annotations

from app.db import excel_repo
from app.services.mensajes import es_verdadero


def actualizar_viajes_pendientes_de_ruta(
    salida: str, llegada: str, *, codigo: str, precio: float, moneda: str
) -> int:
    cambios = {}
    for viaje in excel_repo.leer("Viajes"):
        enviada = es_verdadero(viaje.get("Enviada")) or viaje.get("Estado") == "Enviado a contadora"
        if (
            not enviada
            and str(viaje.get("ID_Salida") or "") == salida
            and str(viaje.get("ID_Llegada") or "") == llegada
        ):
            cambios[str(viaje.get("ID_Viaje"))] = {
                "CodigoRuta": codigo,
                "Precio": round(precio, 2),
                "Moneda": moneda,
            }
    return excel_repo.actualizar_varios("Viajes", cambios)
