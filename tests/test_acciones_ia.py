"""Cambios que la IA propone: nada se guarda hasta que la usuaria confirma."""

from __future__ import annotations

import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from google.genai import types

from app import config as app_config
from app.db import excel_repo, plantilla_base
from app.main import app
from app.routers import viajes as viajes_router
from app.services import acciones_ia
from app.services.ia import preguntar_gemini


def _respuesta_gemini(texto: str, *llamadas: tuple[str, dict]):
    """Respuesta real del SDK: texto opcional más llamadas a herramientas."""
    partes = [types.Part(text=texto)] if texto else []
    partes += [types.Part(function_call=types.FunctionCall(name=n, args=a)) for n, a in llamadas]
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=partes))]
    )


class AccionesIaTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        raiz = Path(self.tmp.name)
        self.excel = raiz / "data" / "basedatos.xlsx"
        self.patches = [
            patch.dict(os.environ, {"GEMINI_API_KEY": "", "GEMINI_MODEL": "", "GEMINI_ENABLED": ""}),
            patch.object(app_config, "ENV_PATH", raiz / ".env"),
            patch.object(app_config, "CONFIG_PATH", raiz / "config.json"),
            patch.object(plantilla_base, "DATA_DIR", self.excel.parent),
            patch.object(plantilla_base, "EXCEL_PATH", self.excel),
            patch.object(excel_repo, "EXCEL_PATH", self.excel),
            patch.object(excel_repo, "BACKUP_DIR", raiz / "data" / "backups"),
            patch.object(viajes_router, "EXPORT_DIR", raiz / "data" / "exportaciones"),
        ]
        for activo in self.patches:
            activo.start()
        plantilla_base._verificado = False
        plantilla_base.asegurar_libro()
        acciones_ia._pendientes.clear()
        self.cliente = TestClient(app)
        app_config.save_gemini_settings(api_key="clave-prueba-valida", model="gemini-3.6-flash", enabled=True)
        self.bitacora = self.excel.parent / "ia_cambios.jsonl"

    def tearDown(self):
        acciones_ia._pendientes.clear()
        for activo in reversed(self.patches):
            activo.stop()
        plantilla_base._verificado = True
        self.tmp.cleanup()

    def _estacion(self, nombre: str, estado: str = "Activo") -> str:
        return excel_repo.insertar("Empresas", {"NombreEmpresa": nombre, "Estado": estado})["ID_Empresa"]

    def _rutas(self) -> dict[tuple[str, str], dict]:
        return {(r["salida"], r["llegada"]): r for r in app_config.load_config()["rutas_configuradas"]}

    def _preguntar(self, texto: str, *llamadas: tuple[str, dict]):
        cliente = MagicMock()
        cliente.models.generate_content.return_value = _respuesta_gemini(texto, *llamadas)
        with patch("google.genai.Client", return_value=cliente):
            return preguntar_gemini("pregunta de prueba")

    def _viaje(self, salida: str, llegada: str, *, enviada: bool, precio: float = 1000) -> dict:
        return excel_repo.insertar(
            "Viajes",
            {
                "Fecha": "2026-08-15",
                "ID_Salida": salida,
                "ID_Llegada": llegada,
                "CodigoRuta": "10",
                "Precio": precio,
                "Moneda": "CRC",
                "Enviada": enviada,
                "Estado": "Enviado a contadora" if enviada else "Pendiente",
            },
        )

    def _bitacora_lineas(self) -> list[dict]:
        if not self.bitacora.exists():
            return []
        return [json.loads(l) for l in self.bitacora.read_text(encoding="utf-8").splitlines()]

    GUARDAR = ("guardar_ruta", {"salida": "Alfa", "llegada": "Beta", "codigo": "10", "precio": 1000, "moneda": "CRC"})

    # --- Nada se guarda sin confirmar ---

    def test_proponer_no_escribe_nada_y_confirmar_si(self):
        a, b = self._estacion("Alfa"), self._estacion("Beta")
        respuesta = self._preguntar("Propongo crear la ruta.", self.GUARDAR)
        self.assertEqual(respuesta.texto, "Propongo crear la ruta.")
        (propuesta,) = respuesta.propuestas
        self.assertEqual(propuesta["titulo"], "Crear la ruta Alfa → Beta")
        self.assertEqual(self._rutas(), {})  # solo propuesta
        self.assertEqual(self._bitacora_lineas(), [])

        pagina = self.cliente.post(f"/ia/cambios/{propuesta['token']}/confirmar")
        self.assertEqual(pagina.status_code, 200)
        self.assertIn("Ruta creada: Alfa → Beta", pagina.text)
        self.assertEqual(self._rutas()[(a, b)]["codigo"], "10")
        self.assertEqual(self._rutas()[(a, b)]["precio"], 1000.0)

    def test_solo_herramientas_sin_texto_igual_pide_confirmacion(self):
        self._estacion("Alfa")
        self._estacion("Beta")
        respuesta = self._preguntar("", self.GUARDAR)
        self.assertIn("revise y confirme", respuesta.texto)
        self.assertEqual(len(respuesta.propuestas), 1)

    def test_cada_propuesta_sirve_una_sola_vez(self):
        self._estacion("Alfa")
        self._estacion("Beta")
        (propuesta,) = self._preguntar("", self.GUARDAR).propuestas
        primera = acciones_ia.aplicar(propuesta["token"])
        segunda = acciones_ia.aplicar(propuesta["token"])
        self.assertTrue(primera["ok"])
        self.assertFalse(segunda["ok"])
        self.assertIn("ya se aplicó, se descartó o venció", segunda["mensaje"])
        self.assertEqual(len(self._rutas()), 1)

    def test_token_inventado_o_vencido_no_aplica_nada(self):
        self._estacion("Alfa")
        self._estacion("Beta")
        (propuesta,) = self._preguntar("", self.GUARDAR).propuestas
        self.assertFalse(acciones_ia.aplicar("token-inventado")["ok"])
        acciones_ia._pendientes[propuesta["token"]]["expira"] = 0
        self.assertFalse(acciones_ia.aplicar(propuesta["token"])["ok"])
        self.assertEqual(self._rutas(), {})

    def test_descartar_no_guarda_y_consume_la_propuesta(self):
        self._estacion("Alfa")
        self._estacion("Beta")
        (propuesta,) = self._preguntar("", self.GUARDAR).propuestas
        pagina = self.cliente.post(f"/ia/cambios/{propuesta['token']}/descartar")
        self.assertIn("No se guardó nada", pagina.text)
        self.assertFalse(acciones_ia.aplicar(propuesta["token"])["ok"])
        self.assertEqual(self._rutas(), {})

    # --- Validación: las mismas reglas que los formularios ---

    def test_propuestas_invalidas_no_se_pueden_confirmar(self):
        self._estacion("Alfa")
        self._estacion("Beta")
        self._estacion("Vieja", "Inactivo")
        casos = {
            "precio cero": {"salida": "Alfa", "llegada": "Beta", "codigo": "1", "precio": 0},
            "precio negativo": {"salida": "Alfa", "llegada": "Beta", "codigo": "1", "precio": -5},
            "precio texto": {"salida": "Alfa", "llegada": "Beta", "codigo": "1", "precio": "abc"},
            "sin código": {"salida": "Alfa", "llegada": "Beta", "codigo": " ", "precio": 10},
            "moneda": {"salida": "Alfa", "llegada": "Beta", "codigo": "1", "precio": 10, "moneda": "EUR"},
            "estación inexistente": {"salida": "Alfa", "llegada": "Fantasma", "codigo": "1", "precio": 10},
            "estación archivada": {"salida": "Alfa", "llegada": "Vieja", "codigo": "1", "precio": 10},
            "sin salida": {"llegada": "Beta", "codigo": "1", "precio": 10},
        }
        for nombre, args in casos.items():
            with self.subTest(nombre):
                (vista,) = acciones_ia.preparar_propuestas([("guardar_ruta", args)])
                self.assertEqual(vista["token"], "", nombre)
                self.assertTrue(vista["error"], nombre)
        self.assertEqual(acciones_ia._pendientes, {})
        self.assertEqual(self._rutas(), {})

    def test_el_error_sugiere_nombres_parecidos(self):
        self._estacion("Carrandí")
        (vista,) = acciones_ia.preparar_propuestas(
            [("guardar_ruta", {"salida": "Carrand", "llegada": "Carrandí", "codigo": "1", "precio": 10})]
        )
        self.assertIn("¿Quiso decir: Carrandí?", vista["error"])

    def test_herramientas_desconocidas_se_ignoran_y_hay_tope(self):
        self._estacion("Alfa")
        self._estacion("Beta")
        llamadas = [("borrar_todo", {})] + [
            ("guardar_ruta", {"salida": "Alfa", "llegada": "Beta", "codigo": str(i), "precio": 10 + i})
            for i in range(1, 8)
        ]
        vistas = acciones_ia.preparar_propuestas(llamadas)
        self.assertEqual([v["titulo"] for v in vistas if v["token"]], ["Crear la ruta Alfa → Beta"] * 4)
        self.assertNotIn("borrar_todo", str(vistas))
        self.assertTrue(vistas[-1]["error"].startswith("Se omitieron"))
        self.assertEqual(self._rutas(), {})

    def test_volver_a_validar_al_confirmar(self):
        self._estacion("Alfa")
        b = self._estacion("Beta")
        (propuesta,) = self._preguntar("", self.GUARDAR).propuestas
        excel_repo.actualizar("Empresas", b, {"Estado": "Inactivo"})  # cambió el mundo entre proponer y confirmar
        resultado = acciones_ia.aplicar(propuesta["token"])
        self.assertFalse(resultado["ok"])
        self.assertIn("está archivada", resultado["mensaje"])
        self.assertEqual(self._rutas(), {})

    # --- Actualizar y eliminar rutas ---

    def test_actualizar_muestra_antes_despues_y_repricia_solo_pendientes(self):
        a, b = self._estacion("Alfa"), self._estacion("Beta")
        self.cliente.post(
            "/configuracion/rutas",
            data={"ID_Salida": a, "ID_Llegada": b, "Codigo": "10", "Precio": "1000", "Moneda": "CRC"},
        )
        pendiente = self._viaje(a, b, enviada=False)
        enviado = self._viaje(a, b, enviada=True)
        (vista,) = self._preguntar(
            "", ("guardar_ruta", {"salida": "Alfa", "llegada": "Beta", "codigo": "11", "precio": 2000})
        ).propuestas
        detalle = " | ".join(vista["detalles"])
        self.assertEqual(vista["titulo"], "Actualizar la ruta Alfa → Beta")
        self.assertIn("Código: 10 → 11", detalle)
        self.assertIn("1 viaje pendiente", detalle)
        self.assertEqual(self._rutas()[(a, b)]["codigo"], "10")  # todavía sin cambios

        resultado = acciones_ia.aplicar(vista["token"])
        self.assertTrue(resultado["ok"])
        self.assertIn("Viajes pendientes actualizados: 1", resultado["mensaje"])
        self.assertEqual(
            (excel_repo.leer_por_id("Viajes", pendiente["ID_Viaje"])["CodigoRuta"],
             float(excel_repo.leer_por_id("Viajes", pendiente["ID_Viaje"])["Precio"])),
            ("11", 2000.0),
        )
        self.assertEqual(float(excel_repo.leer_por_id("Viajes", enviado["ID_Viaje"])["Precio"]), 1000.0)

    def test_sin_cambios_no_se_propone(self):
        a, b = self._estacion("Alfa"), self._estacion("Beta")
        self.cliente.post(
            "/configuracion/rutas",
            data={"ID_Salida": a, "ID_Llegada": b, "Codigo": "10", "Precio": "1000", "Moneda": "CRC"},
        )
        (vista,) = acciones_ia.preparar_propuestas([self.GUARDAR])
        self.assertIn("no hay nada que cambiar", vista["error"])

    def test_eliminar_ruta_es_destructiva_y_conserva_los_viajes(self):
        a, b = self._estacion("Alfa"), self._estacion("Beta")
        self.cliente.post(
            "/configuracion/rutas",
            data={"ID_Salida": a, "ID_Llegada": b, "Codigo": "10", "Precio": "1000", "Moneda": "CRC"},
        )
        viaje = self._viaje(a, b, enviada=False)
        respuesta = self._preguntar("", ("eliminar_ruta", {"salida": "Alfa", "llegada": "Beta"}))
        (vista,) = respuesta.propuestas
        self.assertTrue(vista["destructiva"])
        self.assertEqual(len(self._rutas()), 1)  # aún está

        self.assertTrue(acciones_ia.aplicar(vista["token"])["ok"])
        self.assertEqual(self._rutas(), {})
        self.assertEqual(excel_repo.leer_por_id("Viajes", viaje["ID_Viaje"])["CodigoRuta"], "10")
        (linea,) = self._bitacora_lineas()
        self.assertEqual((linea["accion"], linea["aplicado"]), ("eliminar_ruta", True))
        self.assertEqual(linea["antes"]["codigo"], "10")  # permite reconstruir la ruta

    def test_eliminar_ruta_inexistente_no_se_propone(self):
        self._estacion("Alfa")
        self._estacion("Beta")
        (vista,) = acciones_ia.preparar_propuestas([("eliminar_ruta", {"salida": "Alfa", "llegada": "Beta"})])
        self.assertIn("no está registrada", vista["error"])

    # --- Estaciones ---

    def test_crear_estacion_y_rechazar_duplicadas_sin_importar_tildes(self):
        self._estacion("Carrandí")
        (duplicada,) = acciones_ia.preparar_propuestas([("crear_estacion", {"nombre": "  carrandi "})])
        self.assertIn("Ya existe una estación con ese nombre", duplicada["error"])
        (vista,) = acciones_ia.preparar_propuestas([("crear_estacion", {"nombre": "Nueva   Estación"})])
        self.assertEqual(vista["titulo"], "Crear la estación Nueva Estación")  # espacios normalizados
        self.assertTrue(acciones_ia.aplicar(vista["token"])["ok"])
        self.assertIn("Nueva Estación", [e["NombreEmpresa"] for e in excel_repo.leer("Empresas")])
        (larga,) = acciones_ia.preparar_propuestas([("crear_estacion", {"nombre": "x" * 101})])
        self.assertTrue(larga["error"])

    def test_archivar_y_reactivar_estacion(self):
        a, b = self._estacion("Alfa"), self._estacion("Beta")
        self.cliente.post(
            "/configuracion/rutas",
            data={"ID_Salida": a, "ID_Llegada": b, "Codigo": "10", "Precio": "1000", "Moneda": "CRC"},
        )
        (archivar,) = acciones_ia.preparar_propuestas([("archivar_estacion", {"nombre": "Beta"})])
        self.assertIn("1 ruta la usa", " ".join(archivar["detalles"]))
        self.assertEqual(excel_repo.leer_por_id("Empresas", b)["Estado"], "Activo")
        self.assertTrue(acciones_ia.aplicar(archivar["token"])["ok"])
        self.assertEqual(excel_repo.leer_por_id("Empresas", b)["Estado"], "Inactivo")
        (otra_vez,) = acciones_ia.preparar_propuestas([("archivar_estacion", {"nombre": "Beta"})])
        self.assertIn("ya está archivada", otra_vez["error"])
        (reactivar,) = acciones_ia.preparar_propuestas([("reactivar_estacion", {"nombre": "Beta"})])
        self.assertTrue(acciones_ia.aplicar(reactivar["token"])["ok"])
        self.assertEqual(excel_repo.leer_por_id("Empresas", b)["Estado"], "Activo")

    # --- Lotes con dependencias ---

    def test_estacion_nueva_y_ruta_en_el_mismo_lote_confirmando_todo_en_orden(self):
        self._estacion("Alfa")
        respuesta = self._preguntar(
            "Primero la estación y luego la ruta.",
            ("crear_estacion", {"nombre": "Gamma"}),
            ("guardar_ruta", {"salida": "Alfa", "llegada": "Gamma", "codigo": "7", "precio": 500}),
        )
        estacion, ruta = respuesta.propuestas
        self.assertIn("se crea en otro cambio", " ".join(ruta["detalles"]))
        resultados = acciones_ia.aplicar_varias([estacion["token"], ruta["token"]])
        self.assertEqual([r["ok"] for r in resultados], [True, True])
        self.assertEqual(self._rutas()[(self._id("Alfa"), self._id("Gamma"))]["codigo"], "7")

    def _id(self, nombre: str) -> str:
        return next(e["ID_Empresa"] for e in excel_repo.leer("Empresas") if e["NombreEmpresa"] == nombre)

    def test_confirmar_la_ruta_antes_que_la_estacion_la_deja_pendiente(self):
        self._estacion("Alfa")
        estacion, ruta = self._preguntar(
            "",
            ("crear_estacion", {"nombre": "Gamma"}),
            ("guardar_ruta", {"salida": "Alfa", "llegada": "Gamma", "codigo": "7", "precio": 500}),
        ).propuestas
        resultados = acciones_ia.aplicar_varias([ruta["token"], estacion["token"]])
        self.assertEqual(len(resultados), 1)  # se detiene en el primer fallo
        self.assertFalse(resultados[0]["ok"])
        self.assertIn("Primero confirme el cambio que crea la estación «Gamma»", resultados[0]["mensaje"])
        pendientes = acciones_ia.propuestas_del_lote(resultados[0]["lote"])
        self.assertEqual([p["titulo"] for p in pendientes], [estacion["titulo"], ruta["titulo"]])  # orden original
        self.assertEqual(self._bitacora_lineas(), [])  # un intento diferido no es un cambio
        self.assertTrue(acciones_ia.aplicar(estacion["token"])["ok"])
        self.assertTrue(acciones_ia.aplicar(ruta["token"])["ok"])

    # --- Pantalla ---

    def test_pantalla_muestra_cambios_y_confirmar_todos_aplica_el_lote(self):
        self._estacion("Alfa")
        cliente = MagicMock()
        cliente.models.generate_content.return_value = _respuesta_gemini(
            "Propongo dos cambios.",
            ("crear_estacion", {"nombre": "Gamma"}),
            ("guardar_ruta", {"salida": "Alfa", "llegada": "Gamma", "codigo": "7", "precio": 500}),
        )
        with patch("google.genai.Client", return_value=cliente):
            pagina = self.cliente.post("/ia/preguntar", data={"pregunta": "Agrega la ruta de Alfa a Gamma"})
        self.assertEqual(pagina.status_code, 200)
        self.assertIn("Cambios propuestos", pagina.text)
        self.assertIn("Nada se guarda hasta que usted lo confirme", pagina.text)
        self.assertIn("Confirmar todos en orden (2)", pagina.text)
        self.assertEqual(self._rutas(), {})

        tokens = re.findall(r'name="token" value="([^"]+)"', pagina.text)
        self.assertEqual(len(tokens), 2)
        final = self.cliente.post("/ia/cambios/confirmar-todos", data={"token": tokens})
        self.assertIn("Estación creada: Gamma", final.text)
        self.assertIn("Ruta creada: Alfa → Gamma", final.text)
        self.assertEqual(len(self._rutas()), 1)

    def test_al_confirmar_o_descartar_uno_siguen_visibles_los_demas_del_lote(self):
        self._estacion("Alfa")
        cliente = MagicMock()
        cliente.models.generate_content.return_value = _respuesta_gemini(
            "Dos estaciones.", ("crear_estacion", {"nombre": "Gamma"}), ("crear_estacion", {"nombre": "Delta"})
        )
        with patch("google.genai.Client", return_value=cliente):
            pagina = self.cliente.post("/ia/preguntar", data={"pregunta": "Crea Gamma y Delta"})
        gamma, delta = re.findall(r'action="/ia/cambios/([^/"]+)/confirmar"', pagina.text)

        tras_confirmar = self.cliente.post(f"/ia/cambios/{gamma}/confirmar")
        self.assertIn("Estación creada: Gamma", tras_confirmar.text)
        self.assertIn("Crear la estación Delta", tras_confirmar.text)  # sigue pendiente, no hay que volver a preguntar
        self.assertNotIn(f"/ia/cambios/{gamma}/confirmar", tras_confirmar.text)  # ya aplicada: sin botón
        self.assertIn(f"/ia/cambios/{delta}/confirmar", tras_confirmar.text)

        tras_descartar = self.cliente.post(f"/ia/cambios/{delta}/descartar")
        self.assertIn("No se guardó nada", tras_descartar.text)
        self.assertNotIn("Cambios propuestos", tras_descartar.text)
        self.assertEqual([e["NombreEmpresa"] for e in excel_repo.leer("Empresas")], ["Alfa", "Gamma"])

    def test_eliminar_exige_confirmacion_extra_en_el_navegador(self):
        a, b = self._estacion("Alfa"), self._estacion("Beta")
        self.cliente.post(
            "/configuracion/rutas",
            data={"ID_Salida": a, "ID_Llegada": b, "Codigo": "10", "Precio": "1000", "Moneda": "CRC"},
        )
        cliente = MagicMock()
        cliente.models.generate_content.return_value = _respuesta_gemini(
            "Propongo eliminarla.", ("eliminar_ruta", {"salida": "Alfa", "llegada": "Beta"})
        )
        with patch("google.genai.Client", return_value=cliente):
            pagina = self.cliente.post("/ia/preguntar", data={"pregunta": "Elimina la ruta de Alfa a Beta"})
        self.assertIn("Elimina datos", pagina.text)
        self.assertIn("return confirm(", pagina.text)
        self.assertEqual(len(self._rutas()), 1)

    def test_texto_malicioso_del_modelo_se_muestra_escapado(self):
        self._estacion("Alfa")
        malo = "<script>alert(1)</script>"
        cliente = MagicMock()
        cliente.models.generate_content.return_value = _respuesta_gemini(malo, ("crear_estacion", {"nombre": malo}))
        with patch("google.genai.Client", return_value=cliente):
            pagina = self.cliente.post("/ia/preguntar", data={"pregunta": "x"})
        self.assertNotIn(malo, pagina.text)
        self.assertIn("&lt;script&gt;", pagina.text)


if __name__ == "__main__":
    unittest.main()
