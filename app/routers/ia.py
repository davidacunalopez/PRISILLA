from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.config import gemini_settings
from app.services.ia import IaNoConfiguradaError, IaServicioError, preguntar_gemini

router = APIRouter(prefix="/ia")


def _ctx(*, pregunta: str = "", respuesta: str = "", errores: list[str] | None = None) -> dict:
    cfg = gemini_settings()
    return {
        "titulo": "Pregúntale a la IA",
        "activo": "ia",
        "pregunta": pregunta,
        "respuesta": respuesta,
        "errores": errores or [],
        "ia_lista": bool(cfg["enabled"] and cfg["api_key"]),
        "modelo": cfg["model"],
    }


@router.get("", response_class=HTMLResponse)
async def pagina(request: Request):
    return request.app.state.templates.TemplateResponse(request, "ia.html", _ctx())


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
    return request.app.state.templates.TemplateResponse(
        request, "ia.html", _ctx(pregunta=pregunta_usuario, respuesta=respuesta)
    )
