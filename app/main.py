"""Arranque FastAPI + ventana nativa (pywebview). Plan B: navegador por defecto."""

from __future__ import annotations

import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import ROOT, load_config
from app.db.excel_repo import ExcelBloqueadoError
from app.db.plantilla_base import asegurar_libro
from app.routers import camiones, choferes, configuracion, dashboard, empresas, gasolina, ia, seguros, viajes
from app.services.fechas import formato_crc, mostrar_fecha

HOST = "127.0.0.1"
PORT = 8765
URL = f"http://{HOST}:{PORT}"

APP_DIR = Path(__file__).resolve().parent


def crear_app() -> FastAPI:
    asegurar_libro()
    app = FastAPI(title="BITACORA", docs_url=None, redoc_url=None)
    templates = Jinja2Templates(directory=str(APP_DIR / "templates"))
    templates.env.filters["fecha"] = mostrar_fecha
    templates.env.filters["crc"] = formato_crc
    templates.env.globals["configuracion_general"] = load_config
    app.state.templates = templates

    @app.middleware("http")
    async def _proteger_origen(request: Request, call_next):
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origen = request.headers.get("origin")
            permitidos = {URL, f"http://localhost:{PORT}"}
            if origen and origen.rstrip("/") not in permitidos:
                return JSONResponse({"error": "Origen de solicitud no permitido."}, status_code=403)
        return await call_next(request)

    app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")
    app.include_router(dashboard.router)
    app.include_router(camiones.router)
    app.include_router(seguros.router)
    app.include_router(empresas.router)
    app.include_router(choferes.router)
    app.include_router(viajes.router)
    app.include_router(gasolina.router)
    app.include_router(ia.router)
    app.include_router(configuracion.router)

    @app.exception_handler(ExcelBloqueadoError)
    async def _excel_bloqueado(request: Request, exc: ExcelBloqueadoError):
        if "text/html" in (request.headers.get("accept") or ""):
            return templates.TemplateResponse(
                request,
                "error.html",
                {"titulo": "Archivo en uso", "activo": "", "mensaje": str(exc)},
                status_code=409,
            )
        return JSONResponse({"error": str(exc)}, status_code=409)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        if exc.status_code == 404 and "text/html" in (request.headers.get("accept") or ""):
            return templates.TemplateResponse(
                request,
                "error.html",
                {"titulo": "No encontrado", "activo": "", "mensaje": "Esa página no existe."},
                status_code=404,
            )
        return HTMLResponse(exc.detail or "Error", status_code=exc.status_code)

    return app


app = crear_app()


def _esperar_servidor(timeout: float = 12.0) -> bool:
    fin = time.time() + timeout
    while time.time() < fin:
        try:
            urllib.request.urlopen(URL, timeout=0.4)
            return True
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
            time.sleep(0.15)
    return False


def _correr_uvicorn() -> None:
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


def iniciar() -> None:
    hilo = threading.Thread(target=_correr_uvicorn, daemon=True)
    hilo.start()
    if not _esperar_servidor():
        print("No se pudo iniciar el servidor local.", file=sys.stderr)
        sys.exit(1)

    try:
        import webview

        icono = APP_DIR / "static" / "logo.png"
        webview.create_window(
            f"{load_config().get('nombre_app', 'BITACORA')} — {load_config().get('nombre_empresa', 'Transportes')}",
            URL,
            width=1320,
            height=860,
            min_size=(1024, 700),
            icon=str(icono) if icono.exists() else None,
        )
        webview.start()
    except Exception as exc:
        print(f"pywebview no disponible ({exc}). Abriendo el navegador…")
        webbrowser.open(URL)
        try:
            hilo.join()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    # Permite `python -m app.main` desde la raíz del proyecto.
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    iniciar()
