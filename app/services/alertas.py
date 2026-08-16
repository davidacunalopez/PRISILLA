"""Cálculo de semáforo de seguros y envío de correo de avisos."""

from __future__ import annotations

import smtplib
from html import escape
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.config import load_config, smtp_settings
from app.db import excel_repo
from app.services.fechas import dias_restantes, etiqueta_urgencia, mostrar_fecha, semaforo


def seguros_con_alerta(solo_activos: bool = True) -> list[dict]:
    cfg = load_config()
    camiones = excel_repo.mapa_display("Camiones")
    filas = []
    for seguro in excel_repo.leer("Seguros"):
        if solo_activos and str(seguro.get("Estado", "")).strip() != "Activo":
            continue
        dias = dias_restantes(seguro.get("FechaFin"))
        color = semaforo(seguro.get("FechaFin"), cfg)
        item = dict(seguro)
        item["dias"] = dias
        item["semaforo"] = color
        item["urgencia"] = etiqueta_urgencia(dias)
        item["placa"] = camiones.get(str(seguro.get("ID_Camion", "")), seguro.get("ID_Camion", ""))
        item["fecha_fin_txt"] = mostrar_fecha(seguro.get("FechaFin"))
        filas.append(item)
    filas.sort(key=lambda s: (s["dias"] is None, s["dias"] if s["dias"] is not None else 9999))
    return filas


def resumen_alertas() -> dict:
    todos = seguros_con_alerta()
    return {
        "todos": todos,
        "rojos": [s for s in todos if s["semaforo"] == "rojo"],
        "amarillos": [s for s in todos if s["semaforo"] == "amarillo"],
        "verdes": [s for s in todos if s["semaforo"] == "verde"],
        "vencidos": [s for s in todos if s["dias"] is not None and s["dias"] < 0],
    }


def _cuerpo_html(items: list[dict]) -> str:
    filas = []
    for s in items:
        color = {"rojo": "#b42318", "amarillo": "#b45309", "verde": "#1b7a4e"}.get(s["semaforo"], "#444")
        filas.append(
            f"<tr>"
            f"<td style='padding:8px;border-bottom:1px solid #eee'>{escape(str(s.get('placa','')))}</td>"
            f"<td style='padding:8px;border-bottom:1px solid #eee'>{escape(str(s.get('TipoSeguro','')))}</td>"
            f"<td style='padding:8px;border-bottom:1px solid #eee'>{escape(str(s.get('Aseguradora','')))}</td>"
            f"<td style='padding:8px;border-bottom:1px solid #eee'>{escape(str(s.get('fecha_fin_txt','')))}</td>"
            f"<td style='padding:8px;border-bottom:1px solid #eee;color:{color};font-weight:600'>{escape(str(s.get('urgencia','')))}</td>"
            f"</tr>"
        )
    tabla = "".join(filas) or "<tr><td colspan='5'>Sin pólizas en umbral.</td></tr>"
    return (
        "<div style='font-family:Georgia,serif;color:#1c1917'>"
        f"<h2>{escape(str(load_config().get('nombre_app', 'BITACORA')))} — avisos de seguros</h2>"
        "<p>Pólizas activas a una semana o menos de vencer, o ya vencidas:</p>"
        "<table style='border-collapse:collapse;width:100%;max-width:720px'>"
        "<thead><tr>"
        "<th style='text-align:left;padding:8px;border-bottom:2px solid #1a2f28'>Camión</th>"
        "<th style='text-align:left;padding:8px;border-bottom:2px solid #1a2f28'>Tipo</th>"
        "<th style='text-align:left;padding:8px;border-bottom:2px solid #1a2f28'>Aseguradora</th>"
        "<th style='text-align:left;padding:8px;border-bottom:2px solid #1a2f28'>Vence</th>"
        "<th style='text-align:left;padding:8px;border-bottom:2px solid #1a2f28'>Estado</th>"
        "</tr></thead>"
        f"<tbody>{tabla}</tbody></table>"
        f"<p style='color:#78716c;font-size:13px;margin-top:24px'>Correo automático de {escape(str(load_config().get('nombre_app', 'BITACORA')))}. No responder.</p>"
        "</div>"
    )


def enviar_correo_alertas(items: list[dict] | None = None) -> dict:
    """Envía correo si hay pólizas rojas o amarillas. Retorna {ok, mensaje, enviados}."""
    smtp = smtp_settings()
    if not smtp["host"] or not smtp["user"] or not smtp["password"] or not smtp["to"]:
        return {
            "ok": False,
            "mensaje": "Faltan datos SMTP en el archivo .env (SMTP_HOST, SMTP_USER, SMTP_PASSWORD, ALERT_EMAIL_TO).",
            "enviados": 0,
        }

    if items is None:
        resumen = resumen_alertas()
        items = resumen["rojos"]

    if not items:
        return {"ok": True, "mensaje": "No hay seguros rojos (7 días o menos). No se envió correo.", "enviados": 0}

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"{load_config().get('nombre_app', 'BITACORA')}: {len(items)} seguro(s) crítico(s)"
    msg["From"] = smtp["from_addr"]
    msg["To"] = smtp["to"]
    texto = "\n".join(
        f"{s.get('placa')} | {s.get('TipoSeguro')} | {s.get('fecha_fin_txt')} | {s.get('urgencia')}"
        for s in items
    )
    msg.attach(MIMEText(texto, "plain", "utf-8"))
    msg.attach(MIMEText(_cuerpo_html(items), "html", "utf-8"))

    try:
        with smtplib.SMTP(smtp["host"], smtp["port"], timeout=30) as server:
            server.starttls()
            server.login(smtp["user"], smtp["password"])
            server.sendmail(smtp["from_addr"], [smtp["to"]], msg.as_string())
    except Exception as exc:
        return {"ok": False, "mensaje": f"No se pudo enviar el correo: {exc}", "enviados": 0}

    return {"ok": True, "mensaje": f"Correo enviado a {smtp['to']} con {len(items)} póliza(s).", "enviados": len(items)}


def revisar_y_enviar() -> dict:
    return enviar_correo_alertas()
