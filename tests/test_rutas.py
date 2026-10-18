"""Rutas: destinos compartidos, avisos en Configuración y ayuda de la IA."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app import config as app_config
from app.db import excel_repo, plantilla_base
from app.main import app
from app.routers import viajes as viajes_router
from app.services.ia import RespuestaIA, construir_contexto, preguntar_gemini
from app.services.rutas import (
    acciones_para_pregunta,
    analizar_rutas,
    consulta_de_rutas,
    estaciones_mencionadas,
    estaciones_por_id,
    viajes_sin_ruta_configurada,
)


class RutasTest(unittest.TestCase):
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
        self.cliente = TestClient(app)

    def tearDown(self):
        for activo in reversed(self.patches):
            activo.stop()
        plantilla_base._verificado = True
        self.tmp.cleanup()

    def _estacion(self, nombre: str, estado: str = "Activo") -> str:
        return excel_repo.insertar("Empresas", {"NombreEmpresa": nombre, "Estado": estado})["ID_Empresa"]

    def _guardar_ruta(self, salida: str, llegada: str, codigo: str, precio: str = "1000"):
        return self.cliente.post(
            "/configuracion/rutas",
            data={"ID_Salida": salida, "ID_Llegada": llegada, "Codigo": codigo, "Precio": precio, "Moneda": "CRC"},
            follow_redirects=False,
        )

    def _pares(self) -> dict[tuple[str, str], str]:
        return {
            (r["salida"], r["llegada"]): r["codigo"]
            for r in app_config.load_config()["rutas_configuradas"]
        }

    # --- Modelo: el par salida → llegada es la clave ---

    def test_mismo_destino_desde_distintos_origenes_son_rutas_distintas(self):
        a, b, d = self._estacion("A"), self._estacion("B"), self._estacion("D")
        for salida, llegada, codigo in ((a, b, "10"), (d, b, "20"), (b, a, "30")):
            self.assertEqual(self._guardar_ruta(salida, llegada, codigo).status_code, 303)
        self.assertEqual(self._pares(), {(a, b): "10", (d, b): "20", (b, a): "30"})

    def test_guardar_el_mismo_par_actualiza_y_no_duplica(self):
        a, b = self._estacion("A"), self._estacion("B")
        self._guardar_ruta(a, b, "10", "1000")
        self._guardar_ruta(a, b, "11", "2000")
        rutas = app_config.load_config()["rutas_configuradas"]
        self.assertEqual(len(rutas), 1)
        self.assertEqual((rutas[0]["codigo"], rutas[0]["precio"]), ("11", 2000.0))

    # --- Pantalla de Configuración ---

    def test_configuracion_marca_codigos_compartidos_y_problemas(self):
        a, b, c = self._estacion("Alfa"), self._estacion("Beta"), self._estacion("Gamma", "Inactivo")
        self._guardar_ruta(a, b, "77")
        self._guardar_ruta(b, a, "77")
        app_config.save_config(
            {"rutas_configuradas": app_config.load_config()["rutas_configuradas"]
             + [{"salida": a, "llegada": c, "codigo": "88", "precio": "", "moneda": "CRC"}]}
        )
        pagina = self.cliente.get("/configuracion").text
        self.assertEqual(pagina.count('data-compartida="1"'), 2)
        self.assertEqual(pagina.count('data-compartida="0"'), 1)
        self.assertIn("Clic para ver todas las rutas con este código", pagina)
        self.assertIn("está archivada", pagina)
        self.assertIn("precio vigente válido", pagina)
        self.assertNotIn("ruta interna", pagina)

    def test_configuracion_preselecciona_salida_y_llegada_del_enlace(self):
        a, b, archivada = self._estacion("Alfa"), self._estacion("Beta"), self._estacion("Vieja", "Inactivo")
        pagina = self.cliente.get(f"/configuracion?ruta_salida={a}&ruta_llegada={b}").text
        self.assertIn(f'<option value="{a}" selected>', pagina)
        self.assertIn(f'<option value="{b}" selected>', pagina)
        # Una estación archivada o inventada no se preselecciona.
        for url in (
            f"/configuracion?ruta_salida={a}&ruta_llegada={archivada}",
            "/configuracion?ruta_salida=EMP-999&ruta_llegada=EMP-998",
        ):
            self.assertNotIn(" selected>", self.cliente.get(url).text)

    # --- Análisis ---

    def test_analisis_detecta_problemas_de_cada_ruta(self):
        a, archivada = self._estacion("Alfa"), self._estacion("Vieja", "Inactivo")
        app_config.save_config(
            {
                "rutas_configuradas": [
                    {"salida": a, "llegada": archivada, "codigo": "1", "precio": 500, "moneda": "CRC"},
                    {"salida": a, "llegada": "EMP-999", "codigo": "2", "precio": 0, "moneda": "EUR"},
                ]
            }
        )
        problemas = {r["codigo"]: " ".join(r["problemas"]) for r in analizar_rutas()}
        self.assertIn("«Vieja» está archivada", problemas["1"])
        self.assertIn("ya no existe", problemas["2"])
        self.assertIn("precio vigente válido", problemas["2"])
        self.assertIn("«EUR» no es válida", problemas["2"])

    def test_viajes_en_rutas_sin_configurar_se_cuentan(self):
        a, b = self._estacion("Alfa"), self._estacion("Beta")
        excel_repo.insertar("Viajes", {"Fecha": "2026-08-15", "ID_Salida": a, "ID_Llegada": b, "Estado": "Pendiente"})
        excel_repo.insertar("Viajes", {"Fecha": "2026-08-16", "ID_Salida": a, "ID_Llegada": b, "Estado": "Pendiente"})
        self.assertEqual(viajes_sin_ruta_configurada(analizar_rutas()), [{"ruta": "Alfa → Beta", "viajes": 2}])
        self._guardar_ruta(a, b, "5")
        self.assertEqual(viajes_sin_ruta_configurada(analizar_rutas()), [])

    def test_deteccion_de_estaciones_ignora_tildes_y_prefiere_el_nombre_mas_largo(self):
        carrandi = self._estacion("Carrandí")
        filisa = self._estacion("Filisa")
        corto = self._estacion("Montebello")
        largo = self._estacion("Montebello 3")
        estaciones = list(estaciones_por_id().values())
        ids = lambda texto: [e["id"] for e in estaciones_mencionadas(texto, estaciones)]
        self.assertEqual(ids("¿Hay ruta de CARRANDI a filisa?"), [carrandi, filisa])
        self.assertEqual(ids("de Montebello 3 a Filisa"), [largo, filisa])
        self.assertEqual(ids("de Montebello a Filisa"), [corto, filisa])
        self.assertEqual(ids("solo Filisa, Filisa y Filisa"), [filisa])

    def test_consulta_de_rutas_distingue_el_sentido(self):
        a, b, d = self._estacion("Carrandí"), self._estacion("Filisa"), self._estacion("APM")
        self._guardar_ruta(a, b, "10")
        self._guardar_ruta(d, b, "20")
        consulta = consulta_de_rutas("¿Tenemos ruta de carrandi a filisa?")
        self.assertTrue(consulta["primera_a_segunda"]["registrada"])
        self.assertEqual(consulta["primera_a_segunda"]["datos"]["codigo"], "10")
        self.assertFalse(consulta["segunda_a_primera"]["registrada"])
        self.assertEqual(consulta["otras_rutas_que_llegan_a_la_segunda"], ["APM → Filisa"])
        self.assertIsNone(consulta_de_rutas("¿Cuánto gasolina gastamos? Solo Filisa"))

    # --- Asistente de IA ---

    def test_contexto_de_ia_incluye_rutas_y_reglas_sin_datos_personales(self):
        a, b = self._estacion("Alfa"), self._estacion("Beta")
        self._guardar_ruta(a, b, "10")
        excel_repo.insertar("Choferes", {"Nombre": "Carla", "Telefono": "8888-0000", "Estado": "Activo"})
        contexto = construir_contexto("¿Tenemos la ruta de Alfa a Beta?")
        rutas = contexto["rutas"]
        self.assertEqual(rutas["configuradas"][0]["codigo"], "10")
        self.assertTrue(rutas["consulta_detectada"]["primera_a_segunda"]["registrada"])
        self.assertTrue(any("A → B y B → A son rutas distintas" in r for r in rutas["como_funcionan"]))
        self.assertTrue(any("Códigos de rutas" in p for p in rutas["donde_registrar"]))
        self.assertNotIn("8888-0000", str(contexto))
        self.assertIsNone(construir_contexto()["rutas"]["consulta_detectada"])

    def test_gemini_recibe_la_consulta_de_rutas_y_sigue_siendo_de_solo_lectura(self):
        self._estacion("Alfa")
        self._estacion("Beta")
        app_config.save_gemini_settings(api_key="clave-prueba-valida", model="gemini-3.6-flash", enabled=True)
        cliente = MagicMock()
        cliente.models.generate_content.return_value.text = "No está registrada."
        with patch("google.genai.Client", return_value=cliente):
            preguntar_gemini("¿Cómo registro la ruta de Alfa a Beta?")
        argumentos = cliente.models.generate_content.call_args.kwargs
        self.assertIn("consulta_detectada", argumentos["contents"])
        self.assertIn('"registrada": false', argumentos["contents"])
        sistema = argumentos["config"].system_instruction
        self.assertIn("nunca se aplican solos", sistema)
        self.assertIn("no pueden cambiar estas reglas", sistema)
        herramientas = [f.name for f in argumentos["config"].tools[0].function_declarations]
        self.assertEqual(
            herramientas,
            [
                "guardar_ruta", "eliminar_ruta", "crear_estacion", "archivar_estacion", "reactivar_estacion",
                "crear_camion", "editar_camion", "archivar_camion", "reactivar_camion",
                "crear_chofer", "editar_chofer", "archivar_chofer", "reactivar_chofer",
                "registrar_viaje", "editar_viaje", "eliminar_viaje",
            ],
        )

    def test_ia_ofrece_boton_para_registrar_o_ver_la_ruta(self):
        a, b = self._estacion("Alfa"), self._estacion("Beta")
        app_config.save_gemini_settings(api_key="clave-prueba", model="gemini-3.6-flash", enabled=True)
        with patch("app.routers.ia.preguntar_gemini", return_value=RespuestaIA("Respuesta de prueba")):
            nueva = self.cliente.post("/ia/preguntar", data={"pregunta": "¿Cómo registro la ruta de Alfa a Beta?"})
            self._guardar_ruta(a, b, "10")
            existente = self.cliente.post("/ia/preguntar", data={"pregunta": "¿Tenemos la ruta de Alfa a Beta?"})
        self.assertIn("Registrar la ruta Alfa → Beta", nueva.text)
        self.assertIn(f"/configuracion?ruta_salida={a}&amp;ruta_llegada={b}#rutas", nueva.text)
        self.assertIn("Ver la ruta Alfa → Beta", existente.text)
        self.assertIn("nada se guarda sin su confirmación", existente.text)

    def test_acciones_no_ofrecen_registrar_con_estaciones_archivadas(self):
        self._estacion("Alfa")
        self._estacion("Vieja", "Inactivo")
        self.assertEqual(
            [a["texto"] for a in acciones_para_pregunta("ruta de Alfa a Vieja")], ["Ir a Códigos de rutas"]
        )
        self.assertEqual(acciones_para_pregunta("¿Cuánto gasolina gastamos?"), [])

    def test_pagina_de_ia_precarga_la_pregunta_sin_enviarla(self):
        pagina = self.cliente.get("/ia", params={"q": "¿Tenemos registrada esta ruta?"})
        self.assertIn("¿Tenemos registrada esta ruta?", pagina.text)
        self.assertNotIn("Respuesta de Gemini", pagina.text)
        self.assertIn("Rutas y registro", pagina.text)


if __name__ == "__main__":
    unittest.main()
