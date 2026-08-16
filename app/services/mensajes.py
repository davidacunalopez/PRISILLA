"""Generación determinista del informe de viajes para la contadora."""

from __future__ import annotations

from collections import defaultdict

from app.services.fechas import a_float


def es_verdadero(valor) -> bool:
    return valor is True or str(valor).strip().lower() in {"true", "1", "si", "sí"}


def _dinero(precio, moneda: str) -> str:
    numero = a_float(precio)
    texto = f"{numero:,.2f}".replace(",", "X").replace(".", ",").replace("X", " ")
    return f"{texto} {'dólares' if moneda == 'USD' else 'colones'}"


def _descripcion(cantidad: int, categoria: str) -> str:
    categoria = categoria or "Viaje completo"
    formas = {
        "Viaje completo": ("viaje completo", "viajes completos"),
        "Desvío": ("desvío", "desvíos"),
        "Ruptura": ("ruptura", "rupturas"),
    }
    singular, plural = formas.get(categoria, (categoria.lower(), categoria.lower()))
    return singular if cantidad == 1 else plural


def generar_mensaje_contadora(filas: list[dict]) -> str:
    """Agrupa por empresa y código sin delegar cifras ni cálculos a la IA."""
    grupos: dict[str, dict[tuple, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for fila in filas:
        empresa = str(fila.get("EmpresaTrabajo") or "Sin empresa especificada")
        clave = (
            str(fila.get("CodigoRuta") or "Sin código"),
            str(fila.get("Categoria") or "Viaje completo"),
            str(fila.get("salida") or fila.get("ID_Salida") or ""),
            str(fila.get("llegada") or fila.get("ID_Llegada") or ""),
            a_float(fila.get("Precio")),
            str(fila.get("Moneda") or "CRC"),
        )
        grupos[empresa][clave].append(fila)

    bloques = []
    for empresa in sorted(grupos, key=str.casefold):
        lineas = [f"Factura a {empresa}"]
        for clave, viajes in sorted(grupos[empresa].items(), key=lambda item: item[0][0].casefold()):
            codigo, categoria, salida, llegada, precio, moneda = clave
            cantidad = len(viajes)
            nombre = _descripcion(cantidad, categoria)
            if categoria == "Ruptura" and salida == llegada:
                trayecto = f"en {salida}"
            else:
                trayecto = f"de {salida} a {llegada}"
            cierre = " cada uno" if cantidad > 1 else ""
            lineas.extend(
                ["", f"**{codigo}**", f"{cantidad} {nombre} {trayecto}, a {_dinero(precio, moneda)}{cierre}."]
            )
        bloques.append("\n".join(lineas))
    return "\n\n".join(bloques)
