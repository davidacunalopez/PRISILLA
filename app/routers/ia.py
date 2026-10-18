from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.config import gemini_settings
from app.services import acciones_ia
from app.services.ia import IaNoConfiguradaError, IaServicioError, preguntar_gemini
from app.services.rutas import acciones_para_pregunta, estaciones_por_id

router = APIRouter(prefix="/ia")


def _sugerencias_rutas() -> list[str]:
    """Ejemplos con nombres reales de estaciones para que la usuaria solo cambie lo necesario."""
    activas = sorted(
        (e["nombre"] for e in estaciones_por_id().values() if e["estado"] == "Activo"), key=str.casefold
    )
    a, b = (activas + ["la estación A", "la estación B"])[:2]
    return [
        f"¿Tenemos registrada una ruta de {a} a {b}?",
        f"¿Cómo registro la ruta de {a} a {b}?",
        "¿Por qué no me deja guardar un viaje en una ruta?",
        "Revisa mis rutas: ¿hay códigos repetidos o rutas con problemas?",
    ]


def _ctx(
    *,
    pregunta: str = "",
    respuesta: str = "",
    errores: list[str] | None = None,
    acciones: list[dict] | None = None,
    propuestas: list[dict] | None = None,
    resultados: list[dict] | None = None,
) -> dict:
    cfg = gemini_settings()
    return {
        "titulo": "Pregúntale a la IA",
        "activo": "ia",
        "pregunta": pregunta,
        "respuesta": respuesta,
        "acciones": acciones or [],
        "propuestas": propuestas or [],
        "resultados": resultados or [],
        "sugerencias_rutas": _sugerencias_rutas(),
        "errores": errores or [],
        "ia_lista": bool(cfg["enabled"] and cfg["api_key"]),
        "modelo": cfg["model"],
    }


def _pagina(request: Request, **contexto):
    return request.app.state.templates.TemplateResponse(request, "ia.html", _ctx(**contexto))


@router.get("", response_class=HTMLResponse)
async def pagina(request: Request):
    # `q` permite que otras pantallas dejen una pregunta lista (no se envía sola).
    pregunta = str(request.query_params.get("q") or "").strip()[:1000]
    return _pagina(request, pregunta=pregunta)


@router.post("/preguntar", response_class=HTMLResponse)
async def preguntar(request: Request):
    form = await request.form()
    pregunta_usuario = str(form.get("pregunta") or "").strip()
    try:
        respuesta = await asyncio.to_thread(preguntar_gemini, pregunta_usuario)
    except (ValueError, IaNoConfiguradaError) as exc:
        return request.app.state.templates.TemplateResponse(
            request, "ia.html", _ctx(pregunta=pregunta_usuario, errores=[str(exc)]), status_code=422
        )
    except IaServicioError as exc:
        return request.app.state.templates.TemplateResponse(
            request, "ia.html", _ctx(pregunta=pregunta_usuario, errores=[str(exc)]), status_code=502
        )
    return _pagina(
        request,
        pregunta=pregunta_usuario,
        respuesta=respuesta.texto,
        propuestas=respuesta.propuestas,
        acciones=acciones_para_pregunta(pregunta_usuario),
    )


# Los cambios solo se aplican aquí, con un clic explícito de la usuaria sobre una propuesta ya mostrada.

@router.post("/cambios/confirmar-todos", response_class=HTMLResponse)
async def confirmar_todos(request: Request):
    form = await request.form()
    tokens = [str(t) for t in form.getlist("token")]
    resultados = await asyncio.to_thread(acciones_ia.aplicar_varias, tokens)
    lote = next((r["lote"] for r in resultados if r["lote"]), "")
    return _pagina(request, resultados=resultados, propuestas=acciones_ia.propuestas_del_lote(lote))


@router.post("/cambios/{token}/confirmar", response_class=HTMLResponse)
async def confirmar(request: Request, token: str):
    resultado = await asyncio.to_thread(acciones_ia.aplicar, token)
    return _pagina(
        request, resultados=[resultado], propuestas=acciones_ia.propuestas_del_lote(resultado["lote"])
    )


@router.post("/cambios/{token}/descartar", response_class=HTMLResponse)
async def descartar(request: Request, token: str):
    lote = acciones_ia.descartar(token)
    return _pagina(
        request,
        resultados=[
            {
                "ok": lote is not None,
                "titulo": "",
                "mensaje": "Cambio descartado. No se guardó nada."
                if lote is not None
                else "Ese cambio ya no estaba pendiente.",
            }
        ],
        propuestas=acciones_ia.propuestas_del_lote(lote or ""),
    )
