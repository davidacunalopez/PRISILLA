"""La IA propone cambios en camiones, choferes y viajes; nada se guarda sin confirmar y rigen las reglas de los formularios."""

from __future__ import annotations

import json
import os
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
from app.services.ia import construir_contexto, preguntar_gemini


def _respuesta(texto: str, *llamadas: tuple[str, dict]):
    partes = [types.Part(text=texto)] if texto else []
    partes += [types.Part(function_call=types.FunctionCall(name=n, args=a)) for n, a in llamadas]
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=partes))]
    )


class RegistrosIaTest(unittest.TestCase):
    VIAJE = {
        "fecha": "2026-08-15", "salida": "Alfa", "llegada": "Beta", "camion": "ABC123",
        "chofer": "Luis", "empresa_trabajo": "TCC",
    }

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

    # --- Datos de apoyo ---

    def _estacion(self, nombre, estado="Activo"):
        return excel_repo.insertar("Empresas", {"NombreEmpresa": nombre, "Estado": estado})["ID_Empresa"]

    def _camion(self, placa, marca="Volvo", estado="Activo", chofer_id=""):
        return excel_repo.insertar(
            "Camiones", {"Placa": placa, "Marca": marca, "Estado": estado, "ID_ChoferPredeterminado": chofer_id}
        )["ID_Camion"]

    def _chofer(self, nombre, estado="Activo", **extra):
        return excel_repo.insertar("Choferes", {"Nombre": nombre, "Estado": estado, **extra})["ID_Chofer"]

    def _ruta(self, a, b, codigo="10", precio="1000", moneda="CRC"):
        self.cliente.post(
            "/configuracion/rutas",
            data={"ID_Salida": a, "ID_Llegada": b, "Codigo": codigo, "Precio": precio, "Moneda": moneda},
        )

    def _escenario(self):
        """Alfa → Beta (código 10, ₡1000), camión ABC123, chofer Luis y empresa TCC."""
        ids = {
            "a": self._estacion("Alfa"), "b": self._estacion("Beta"),
            "luis": self._chofer("Luis"),
        }
        ids["cam"] = self._camion("ABC123", chofer_id=ids["luis"])
        self._ruta(ids["a"], ids["b"])
        app_config.save_config({"empresas_trabajo": ["TCC"]})
        return ids

    def _viaje(self, ids, **extra):
        datos = {
            "Fecha": "2026-08-15", "ID_Salida": ids["a"], "ID_Llegada": ids["b"], "ID_Camion": ids["cam"],
            "ID_Chofer": ids["luis"], "CodigoRuta": "10", "Precio": 1000, "Moneda": "CRC",
            "EmpresaTrabajo": "TCC", "Categoria": "Viaje completo", "Enviada": False, "Estado": "Pendiente",
            **extra,
        }
        return excel_repo.insertar("Viajes", datos)

    def _proponer(self, *llamadas):
        return acciones_ia.preparar_propuestas(list(llamadas))

    def _una(self, herramienta, /, **args):
        (vista,) = self._proponer((herramienta, args))
        return vista

    def _fila(self, hoja, id_registro):
        return excel_repo.leer_por_id(hoja, id_registro)

    def _bitacora_lineas(self):
        if not self.bitacora.exists():
            return []
        return [json.loads(l) for l in self.bitacora.read_text(encoding="utf-8").splitlines()]

    # ============================================================ camiones

    def test_crear_camion_propone_y_solo_confirmar_escribe_incluida_la_marca_nueva(self):
        vista = self._una("crear_camion", placa="xyz 789", marca="Patito", anio=2018.0, modelo="FH", notas="Nuevo")
        self.assertEqual(vista["titulo"], "Registrar el camión XYZ 789")
        detalle = " | ".join(vista["detalles"])
        self.assertIn("Año: 2018", detalle)
        self.assertIn("La marca «Patito» es nueva", detalle)
        self.assertEqual(excel_repo.leer("Camiones"), [])
        self.assertNotIn("Patito", app_config.load_config()["marcas_camiones"])  # proponer no escribe ni la marca

        resultado = acciones_ia.aplicar(vista["token"])
        self.assertTrue(resultado["ok"], resultado)
        (camion,) = excel_repo.leer("Camiones")
        self.assertEqual((camion["Placa"], camion["Marca"], str(camion["Anio"]), camion["Estado"]),
                         ("XYZ 789", "Patito", "2018", "Activo"))
        self.assertIn("Patito", app_config.load_config()["marcas_camiones"])

    def test_crear_camion_respeta_las_validaciones_del_formulario(self):
        self._camion("ABC123")
        casos = {
            "placa repetida (otra escritura)": {"placa": "abc123", "marca": "Volvo"},
            "sin marca": {"placa": "NEW1", "marca": ""},
            "sin placa": {"placa": "", "marca": "Volvo"},
            "año antiguo": {"placa": "NEW2", "marca": "Volvo", "anio": 1800},
            "año con texto": {"placa": "NEW3", "marca": "Volvo", "anio": "mil"},
            "marca larguísima": {"placa": "NEW4", "marca": "x" * 51},
            "chofer inexistente": {"placa": "NEW5", "marca": "Volvo", "chofer_habitual": "Fantasma"},
        }
        for nombre, args in casos.items():
            with self.subTest(nombre):
                vista = self._una("crear_camion", **args)
                self.assertEqual(vista["token"], "", nombre)
                self.assertTrue(vista["error"], nombre)
        self.assertEqual(len(excel_repo.leer("Camiones")), 1)

    def test_camion_con_chofer_habitual_existente_archivado_y_creado_en_el_mismo_lote(self):
        luis = self._chofer("Luis")
        self._chofer("Vieja", estado="Inactivo")
        vista = self._una("crear_camion", placa="CAM1", marca="Volvo", chofer_habitual="luis")
        self.assertIn("Chofer habitual: Luis", " | ".join(vista["detalles"]))
        self.assertTrue(acciones_ia.aplicar(vista["token"])["ok"])
        self.assertEqual(excel_repo.leer("Camiones")[0]["ID_ChoferPredeterminado"], luis)

        archivado = self._una("crear_camion", placa="CAM2", marca="Volvo", chofer_habitual="Vieja")
        self.assertIn("está archivado", archivado["error"])

        nuevo, camion = self._proponer(
            ("crear_chofer", {"nombre": "Nora"}),
            ("crear_camion", {"placa": "CAM3", "marca": "Volvo", "chofer_habitual": "Nora"}),
        )
        self.assertTrue(camion["token"], camion)
        resultados = acciones_ia.aplicar_varias([nuevo["token"], camion["token"]])
        self.assertEqual([r["ok"] for r in resultados], [True, True])
        nora = next(c["ID_Chofer"] for c in excel_repo.leer("Choferes") if c["Nombre"] == "Nora")
        cam3 = next(c for c in excel_repo.leer("Camiones") if c["Placa"] == "CAM3")
        self.assertEqual(cam3["ID_ChoferPredeterminado"], nora)

    def test_confirmar_el_camion_antes_que_su_chofer_nuevo_lo_deja_pendiente(self):
        chofer, camion = self._proponer(
            ("crear_chofer", {"nombre": "Nora"}),
            ("crear_camion", {"placa": "CAM3", "marca": "Volvo", "chofer_habitual": "Nora"}),
        )
        resultado = acciones_ia.aplicar(camion["token"])
        self.assertFalse(resultado["ok"])
        self.assertIn("Primero confirme el cambio que crea el chofer «Nora»", resultado["mensaje"])
        self.assertEqual(excel_repo.leer("Camiones"), [])
        self.assertEqual(len(acciones_ia.propuestas_del_lote(resultado["lote"])), 2)
        self.assertTrue(chofer["token"])

    def test_editar_camion_muestra_antes_y_despues_y_busca_la_placa_sin_importar_formato(self):
        luis = self._chofer("Luis")
        cam = self._camion("ABC123", marca="Volvo", chofer_id=luis)
        vista = self._una("editar_camion", placa="abc-123", marca="Freightliner", modelo="Cascadia", anio=2020)
        detalle = " | ".join(vista["detalles"])
        self.assertEqual(vista["titulo"], "Cambiar datos del camión ABC123")
        self.assertIn("Marca: Volvo → Freightliner", detalle)
        self.assertIn("Modelo: — → Cascadia", detalle)
        self.assertEqual(self._fila("Camiones", cam)["Marca"], "Volvo")  # aún sin cambios

        self.assertTrue(acciones_ia.aplicar(vista["token"])["ok"])
        fila = self._fila("Camiones", cam)
        self.assertEqual((fila["Marca"], fila["Modelo"], str(fila["Anio"])), ("Freightliner", "Cascadia", "2020"))
        self.assertEqual(fila["ID_ChoferPredeterminado"], luis)  # lo que no se pidió no se toca
        self.assertIn("Freightliner", app_config.load_config()["marcas_camiones"])
        self.assertEqual(self._bitacora_lineas()[-1]["antes"]["Marca"], "Volvo")

    def test_editar_camion_cambiar_y_quitar_chofer_habitual_y_casos_invalidos(self):
        luis, nora = self._chofer("Luis"), self._chofer("Nora")
        cam = self._camion("ABC123", chofer_id=luis)
        self._camion("OTRO1")
        cambio = self._una("editar_camion", placa="ABC123", chofer_habitual="Nora")
        self.assertIn("Chofer habitual: Luis → Nora", cambio["detalles"][0])
        acciones_ia.aplicar(cambio["token"])
        self.assertEqual(self._fila("Camiones", cam)["ID_ChoferPredeterminado"], nora)
        quitar = self._una("editar_camion", placa="ABC123", chofer_habitual="ninguno")
        self.assertIn("Chofer habitual: Nora → —", quitar["detalles"][0])
        acciones_ia.aplicar(quitar["token"])
        self.assertEqual(self._fila("Camiones", cam)["ID_ChoferPredeterminado"], "")

        self.assertIn("nada que cambiar", self._una("editar_camion", placa="ABC123", marca="Volvo")["error"])
        self.assertIn("Ya existe un camión con esa placa", self._una("editar_camion", placa="ABC123", nueva_placa="otro1")["error"])
        self.assertIn("No existe el camión", self._una("editar_camion", placa="NOEXISTE", marca="X")["error"])
        self.assertIn("año válido", self._una("editar_camion", placa="ABC123", anio=1800)["error"])

    def test_archivar_y_reactivar_camion_y_chofer(self):
        luis = self._chofer("Luis")
        cam = self._camion("ABC123", chofer_id=luis)
        archivar = self._una("archivar_chofer", nombre="Luis")
        self.assertIn("chofer habitual de 1 camión", " | ".join(archivar["detalles"]))
        self.assertTrue(acciones_ia.aplicar(archivar["token"])["ok"])
        self.assertEqual(self._fila("Choferes", luis)["Estado"], "Inactivo")
        self.assertIn("ya está archivado", self._una("archivar_chofer", nombre="Luis")["error"])
        self.assertTrue(acciones_ia.aplicar(self._una("reactivar_chofer", nombre="Luis")["token"])["ok"])
        self.assertEqual(self._fila("Choferes", luis)["Estado"], "Activo")
        self.assertIn("ya está activo", self._una("reactivar_chofer", nombre="Luis")["error"])

        self.assertTrue(acciones_ia.aplicar(self._una("archivar_camion", placa="abc123")["token"])["ok"])
        self.assertEqual(self._fila("Camiones", cam)["Estado"], "Inactivo")
        self.assertTrue(acciones_ia.aplicar(self._una("reactivar_camion", placa="ABC123")["token"])["ok"])
        self.assertEqual(self._fila("Camiones", cam)["Estado"], "Activo")

    # ============================================================ choferes

    def test_crear_y_editar_chofer(self):
        vista = self._una("crear_chofer", nombre="Carla Mora", telefono="8888-0000", licencia="B-123")
        self.assertEqual(excel_repo.leer("Choferes"), [])
        self.assertTrue(acciones_ia.aplicar(vista["token"])["ok"])
        (carla,) = excel_repo.leer("Choferes")
        self.assertEqual((carla["Nombre"], carla["Telefono"], carla["Licencia"], carla["Estado"]),
                         ("Carla Mora", "8888-0000", "B-123", "Activo"))

        cambio = self._una("editar_chofer", nombre="carla mora", telefono="7777-1111")
        self.assertIn("Teléfono: 8888-0000 → 7777-1111", cambio["detalles"][0])
        acciones_ia.aplicar(cambio["token"])
        self.assertEqual(self._fila("Choferes", carla["ID_Chofer"])["Telefono"], "7777-1111")
        self.assertEqual(self._fila("Choferes", carla["ID_Chofer"])["Licencia"], "B-123")
        self.assertIn("nada que cambiar", self._una("editar_chofer", nombre="Carla Mora", telefono="7777-1111")["error"])
        self.assertTrue(self._una("crear_chofer", nombre="")["error"])

    def test_nombres_repetidos_avisan_al_crear_y_exigen_el_id_al_buscar(self):
        primero = self._chofer("Luis")
        segundo = self._chofer("Luis")
        creado = self._una("crear_chofer", nombre="luis")
        self.assertIn("ya hay un chofer con ese nombre", " | ".join(creado["detalles"]))
        self.assertTrue(creado["token"])  # los formularios lo permiten: solo se avisa
        ambiguo = self._una("editar_chofer", nombre="Luis", telefono="1")
        self.assertIn("coincide con varios choferes", ambiguo["error"])
        con_id = self._una("editar_chofer", nombre=segundo, telefono="1")
        self.assertTrue(acciones_ia.aplicar(con_id["token"])["ok"])
        self.assertEqual(self._fila("Choferes", segundo)["Telefono"], "1")
        self.assertEqual(self._fila("Choferes", primero)["Telefono"], "")

    # ============================================================ viajes

    def test_registrar_viaje_toma_codigo_y_precio_de_la_ruta_y_solo_confirmar_escribe(self):
        ids = self._escenario()
        vista = self._una("registrar_viaje", **{**self.VIAJE, "empresa_trabajo": "tcc", "contenedor": "C-1"})
        detalle = " | ".join(vista["detalles"])
        self.assertEqual(vista["titulo"], "Registrar un viaje Alfa → Beta (2026-08-15)")
        self.assertIn("código 10", detalle)
        self.assertIn("₡1.000,00", detalle)
        self.assertIn("Empresa: TCC", detalle)  # respeta la escritura del catálogo
        self.assertIn("Contenedor: C-1", detalle)
        self.assertEqual(excel_repo.leer("Viajes"), [])

        resultado = acciones_ia.aplicar(vista["token"])
        self.assertTrue(resultado["ok"], resultado)
        (viaje,) = excel_repo.leer("Viajes")
        self.assertEqual(
            (viaje["ID_Salida"], viaje["ID_Llegada"], viaje["ID_Camion"], viaje["ID_Chofer"], viaje["CodigoRuta"],
             float(viaje["Precio"]), viaje["Moneda"], str(viaje["Semana"]), viaje["Estado"], viaje["Categoria"],
             viaje["EmpresaTrabajo"], viaje["Contenedor"]),
            (ids["a"], ids["b"], ids["cam"], ids["luis"], "10", 1000.0, "CRC", "33", "Pendiente", "Viaje completo",
             "TCC", "C-1"),
        )
        self.assertFalse(str(viaje["Enviada"]).lower() in ("true", "1"))

    def test_el_viaje_de_la_ia_queda_igual_que_el_del_formulario(self):
        ids = self._escenario()
        self.cliente.post(
            "/viajes",
            data={"Fecha": "2026-08-15", "ID_Salida": ids["a"], "ID_Llegada": ids["b"], "ID_Camion": ids["cam"],
                  "ID_Chofer": ids["luis"], "EmpresaTrabajo": "TCC", "Categoria": "Viaje completo",
                  "Precio": "999999", "Moneda": "USD", "CodigoRuta": "MANIPULADO"},
            follow_redirects=False,
        )
        # El modelo intenta imponer precio y código: la herramienta no los acepta y se ignoran.
        vista = self._una("registrar_viaje", **self.VIAJE, precio=1, codigo="MANIPULADO", moneda="USD")
        acciones_ia.aplicar(vista["token"])
        formulario, ia = excel_repo.leer("Viajes")
        for campo in ("ID_Salida", "ID_Llegada", "ID_Camion", "ID_Chofer", "CodigoRuta", "Moneda", "Semana", "Desde",
                      "Hasta", "Estado", "Categoria", "EmpresaTrabajo", "Fecha"):
            self.assertEqual(str(formulario[campo]), str(ia[campo]), campo)
        self.assertEqual(float(formulario["Precio"]), float(ia["Precio"]))
        self.assertEqual((ia["CodigoRuta"], float(ia["Precio"])), ("10", 1000.0))

    def test_proponer_un_viaje_no_escribe_nada_ni_siquiera_la_empresa_nueva(self):
        self._escenario()
        antes = app_config.load_config()["empresas_trabajo"]
        vista = self._una("registrar_viaje", **{**self.VIAJE, "empresa_trabajo": "Empresa Nueva"})
        self.assertIn("«Empresa Nueva» es nueva", " | ".join(vista["detalles"]))
        self.assertEqual(app_config.load_config()["empresas_trabajo"], antes)
        self.assertEqual(excel_repo.leer("Viajes"), [])
        acciones_ia.aplicar(vista["token"])
        self.assertIn("Empresa Nueva", app_config.load_config()["empresas_trabajo"])

    def test_registrar_viaje_respeta_las_validaciones_del_formulario(self):
        self._escenario()
        self._estacion("Gamma")  # sin ruta hacia ninguna parte
        self._camion("VIEJO1", estado="Inactivo")
        casos = {
            "ruta sin registrar": ({**self.VIAJE, "llegada": "Gamma"}, "no tiene un código configurado"),
            "fecha inválida": ({**self.VIAJE, "fecha": "hoy"}, "AAAA-MM-DD"),
            "sin fecha": ({k: v for k, v in self.VIAJE.items() if k != "fecha"}, "AAAA-MM-DD"),
            "categoría inválida": ({**self.VIAJE, "categoria": "Express"}, "La categoría debe ser"),
            "sin empresa": ({**self.VIAJE, "empresa_trabajo": ""}, "Seleccione la empresa"),
            "camión archivado": ({**self.VIAJE, "camion": "VIEJO1"}, "está archivado"),
            "chofer inexistente": ({**self.VIAJE, "chofer": "Fantasma"}, "No existe el chofer"),
            "estación inexistente": ({**self.VIAJE, "salida": "Fantasma"}, "No existe la estación"),
            "empresa de una sola línea": ({**self.VIAJE, "empresa_trabajo": "x" * 101}, "hasta 100 caracteres"),
        }
        for nombre, (args, esperado) in casos.items():
            with self.subTest(nombre):
                vista = self._una("registrar_viaje", **args)
                self.assertEqual(vista["token"], "", nombre)
                self.assertIn(esperado, vista["error"], nombre)
        self.assertEqual(excel_repo.leer("Viajes"), [])

    def test_el_viaje_se_vuelve_a_validar_al_confirmar(self):
        ids = self._escenario()
        vista = self._una("registrar_viaje", **self.VIAJE)
        excel_repo.actualizar("Camiones", ids["cam"], {"Estado": "Inactivo"})  # cambió el mundo entre proponer y confirmar
        resultado = acciones_ia.aplicar(vista["token"])
        self.assertFalse(resultado["ok"])
        self.assertIn("está archivado", resultado["mensaje"])
        self.assertEqual(excel_repo.leer("Viajes"), [])

        excel_repo.actualizar("Camiones", ids["cam"], {"Estado": "Activo"})
        vista = self._una("registrar_viaje", **self.VIAJE)
        self.cliente.post(f"/configuracion/rutas/{ids['a']}/{ids['b']}/eliminar")  # y la ruta desapareció
        resultado = acciones_ia.aplicar(vista["token"])
        self.assertFalse(resultado["ok"])
        self.assertIn("no tiene un código configurado", resultado["mensaje"])
        self.assertEqual(excel_repo.leer("Viajes"), [])

    def test_viaje_repetido_se_avisa_pero_no_se_bloquea(self):
        ids = self._escenario()
        self._viaje(ids)
        vista = self._una("registrar_viaje", **self.VIAJE)
        self.assertIn("ya hay un viaje igual ese día", " | ".join(vista["detalles"]))
        self.assertTrue(vista["token"])

    def test_estacion_ruta_camion_chofer_y_viaje_en_un_solo_lote(self):
        a = self._estacion("Alfa")
        app_config.save_config({"empresas_trabajo": ["TCC"]})
        propuestas = self._proponer(
            ("crear_estacion", {"nombre": "Gamma"}),
            ("guardar_ruta", {"salida": "Alfa", "llegada": "Gamma", "codigo": "77", "precio": 2500}),
            ("crear_chofer", {"nombre": "Nora"}),
            ("crear_camion", {"placa": "NEW1", "marca": "Volvo"}),
            ("registrar_viaje", {"fecha": "2026-08-15", "salida": "Alfa", "llegada": "Gamma", "camion": "NEW1",
                                 "chofer": "Nora", "empresa_trabajo": "TCC"}),
        )
        self.assertEqual([bool(p["token"]) for p in propuestas], [True] * 5, propuestas)
        viaje = propuestas[-1]
        detalle = " | ".join(viaje["detalles"])
        self.assertIn("código 77", detalle)
        self.assertIn("₡2.500,00", detalle)  # el precio sale de la ruta que aún no existe
        self.assertEqual(excel_repo.leer("Viajes"), [])

        resultados = acciones_ia.aplicar_varias([p["token"] for p in propuestas])
        self.assertEqual([r["ok"] for r in resultados], [True] * 5, resultados)
        (guardado,) = excel_repo.leer("Viajes")
        self.assertEqual((guardado["CodigoRuta"], float(guardado["Precio"])), ("77", 2500.0))
        self.assertEqual(guardado["ID_Salida"], a)

    def test_confirmar_el_viaje_antes_que_su_ruta_lo_deja_pendiente(self):
        self._estacion("Alfa")
        self._estacion("Beta")
        self._camion("ABC123")
        self._chofer("Luis")
        app_config.save_config({"empresas_trabajo": ["TCC"]})
        ruta, viaje = self._proponer(
            ("guardar_ruta", {"salida": "Alfa", "llegada": "Beta", "codigo": "10", "precio": 1000}),
            ("registrar_viaje", self.VIAJE),
        )
        resultado = acciones_ia.aplicar(viaje["token"])
        self.assertFalse(resultado["ok"])
        self.assertIn("Primero confirme el cambio que registra la ruta Alfa → Beta", resultado["mensaje"])
        self.assertEqual(excel_repo.leer("Viajes"), [])
        self.assertEqual(self._bitacora_lineas(), [])
        pendientes = acciones_ia.propuestas_del_lote(resultado["lote"])
        self.assertEqual([p["titulo"] for p in pendientes], [ruta["titulo"], viaje["titulo"]])
        self.assertTrue(acciones_ia.aplicar(ruta["token"])["ok"])
        self.assertTrue(acciones_ia.aplicar(viaje["token"])["ok"])
        self.assertEqual(len(excel_repo.leer("Viajes")), 1)

    def test_editar_viaje_muestra_antes_y_despues_y_sigue_a_la_ruta_para_el_precio(self):
        ids = self._escenario()
        otro = self._chofer("Nora")
        viaje = self._viaje(ids, Precio=500)  # precio desactualizado: el formulario también lo recalcularía
        vista = self._una("editar_viaje", id_viaje=viaje["ID_Viaje"], chofer="nora", categoria="Desvío", contenedor="C-9")
        detalle = " | ".join(vista["detalles"])
        self.assertIn("Chofer: Luis → Nora", detalle)
        self.assertIn("Categoría: Viaje completo → Desvío", detalle)
        self.assertIn("Precio: ₡500,00 → ₡1.000,00", detalle)
        self.assertEqual(self._fila("Viajes", viaje["ID_Viaje"])["ID_Chofer"], ids["luis"])

        self.assertTrue(acciones_ia.aplicar(vista["token"])["ok"])
        fila = self._fila("Viajes", viaje["ID_Viaje"])
        self.assertEqual((fila["ID_Chofer"], fila["Categoria"], fila["Contenedor"], float(fila["Precio"])),
                         (otro, "Desvío", "C-9", 1000.0))
        self.assertEqual(self._bitacora_lineas()[-1]["antes"]["ID_Chofer"], ids["luis"])

    def test_editar_viaje_cambiar_fecha_recalcula_la_semana_y_valida(self):
        ids = self._escenario()
        viaje = self._viaje(ids)
        vista = self._una("editar_viaje", id_viaje=viaje["ID_Viaje"], fecha="2026-01-05")
        acciones_ia.aplicar(vista["token"])
        fila = self._fila("Viajes", viaje["ID_Viaje"])
        self.assertEqual((str(fila["Fecha"])[:10], str(fila["Semana"]), str(fila["Desde"])[:10]), ("2026-01-05", "2", "2026-01-05"))
        self.assertIn("AAAA-MM-DD", self._una("editar_viaje", id_viaje=viaje["ID_Viaje"], fecha="ayer")["error"])
        self.assertIn("nada que cambiar", self._una("editar_viaje", id_viaje=viaje["ID_Viaje"], fecha="2026-01-05")["error"])
        self.assertIn("No existe el viaje", self._una("editar_viaje", id_viaje="VIA-9999", categoria="Desvío")["error"])
        self.assertIn("Falta indicar el viaje", self._una("editar_viaje", categoria="Desvío")["error"])

    def test_viaje_enviado_conserva_precio_y_no_cambia_de_ruta(self):
        ids = self._escenario()
        gamma = self._estacion("Gamma")
        self._ruta(ids["a"], gamma, "20", "9999")
        enviado = self._viaje(ids, Enviada=True, Estado="Enviado a contadora", Precio=1000)
        self._ruta(ids["a"], ids["b"], "10", "5000")  # la tarifa sube: el enviado no debe cambiar

        cambio_ruta = self._una("editar_viaje", id_viaje=enviado["ID_Viaje"], llegada="Gamma")
        self.assertIn("Para cambiar la ruta de un viaje enviado", cambio_ruta["error"])

        permitido = self._una("editar_viaje", id_viaje=enviado["ID_Viaje"], contenedor="C-5")
        self.assertIn("ya fue enviado a la contadora", " | ".join(permitido["detalles"]))
        self.assertNotIn("Precio", " | ".join(permitido["detalles"][:-1]))
        acciones_ia.aplicar(permitido["token"])
        fila = self._fila("Viajes", enviado["ID_Viaje"])
        self.assertEqual((fila["Contenedor"], float(fila["Precio"])), ("C-5", 1000.0))
        self.assertEqual(str(fila["Estado"]), "Enviado a contadora")

    def test_editar_viaje_con_camion_archivado_actual_pero_no_a_uno_archivado(self):
        ids = self._escenario()
        excel_repo.actualizar("Camiones", ids["cam"], {"Estado": "Inactivo"})
        viaje = self._viaje(ids)
        permitido = self._una("editar_viaje", id_viaje=viaje["ID_Viaje"], camion="ABC123", contenedor="C-1")
        self.assertTrue(permitido["token"], permitido)  # nombrar el camión que ya tiene no exige que esté activo
        otro = self._camion("OTRO2", estado="Inactivo")
        rechazado = self._una("editar_viaje", id_viaje=viaje["ID_Viaje"], camion="OTRO2")
        self.assertIn("está archivado", rechazado["error"])
        self.assertTrue(otro)

    def test_eliminar_viaje_es_destructivo_avisa_si_fue_enviado_y_queda_en_la_bitacora(self):
        ids = self._escenario()
        viaje = self._viaje(ids, Enviada=True, Estado="Enviado a contadora")
        vista = self._una("eliminar_viaje", id_viaje=viaje["ID_Viaje"])
        self.assertTrue(vista["destructiva"])
        self.assertIn("ya fue enviado a la contadora", " | ".join(vista["detalles"]))
        self.assertEqual(len(excel_repo.leer("Viajes")), 1)
        self.assertTrue(acciones_ia.aplicar(vista["token"])["ok"])
        self.assertEqual(excel_repo.leer("Viajes"), [])
        linea = self._bitacora_lineas()[-1]
        self.assertEqual((linea["accion"], linea["aplicado"]), ("eliminar_viaje", True))
        self.assertEqual(linea["antes"]["CodigoRuta"], "10")  # permite reconstruir el viaje
        self.assertIn("No existe el viaje", self._una("eliminar_viaje", id_viaje=viaje["ID_Viaje"])["error"])

    # ============================================================ contexto y conexión con Gemini

    def test_el_contexto_trae_catalogos_y_viajes_recientes_sin_datos_personales(self):
        ids = self._escenario()
        excel_repo.actualizar("Choferes", ids["luis"], {"Telefono": "8888-0000", "Licencia": "LIC-SECRETA"})
        for i in range(25):
            self._viaje(ids, Fecha=f"2026-08-{i + 1:02d}", Contenedor="CONT-SECRETO", Chasis="CHASIS-SECRETO")
        operacion = construir_contexto()["operacion"]
        self.assertEqual(operacion["camiones"][0]["placa"], "ABC123")
        self.assertEqual(operacion["camiones"][0]["chofer_habitual"], "Luis")
        self.assertEqual(operacion["empresas_de_trabajo"], ["TCC"])
        recientes = operacion["viajes_recientes"]
        self.assertEqual(len(recientes), 20)
        self.assertEqual(recientes[0]["fecha"], "2026-08-25")  # los más nuevos primero
        self.assertTrue(recientes[0]["id"].startswith("VIA-"))
        self.assertEqual((recientes[0]["salida"], recientes[0]["camion"], recientes[0]["chofer"]), ("Alfa", "ABC123", "Luis"))
        contexto = str(construir_contexto())
        for secreto in ("8888-0000", "LIC-SECRETA", "CONT-SECRETO", "CHASIS-SECRETO"):
            self.assertNotIn(secreto, contexto)

    def test_gemini_declara_las_herramientas_nuevas_con_sus_tipos(self):
        cliente = MagicMock()
        cliente.models.generate_content.return_value = _respuesta("Hola.")
        with patch("google.genai.Client", return_value=cliente):
            preguntar_gemini("hola")
        config = cliente.models.generate_content.call_args.kwargs["config"]
        declaraciones = {f.name: f for f in config.tools[0].function_declarations}
        self.assertEqual(declaraciones["crear_camion"].parameters.properties["anio"].type.value, "INTEGER")
        self.assertEqual(
            list(declaraciones["registrar_viaje"].parameters.required),
            ["fecha", "salida", "llegada", "camion", "chofer", "empresa_trabajo"],
        )
        self.assertNotIn("precio", declaraciones["registrar_viaje"].parameters.properties)  # el precio no se pide
        self.assertNotIn("codigo", declaraciones["registrar_viaje"].parameters.properties)
        self.assertIn("nunca los inventes", config.system_instruction)

    def test_de_la_pregunta_a_los_viajes_guardados_con_gemini_simulado(self):
        self._escenario()
        cliente = MagicMock()
        cliente.models.generate_content.return_value = _respuesta(
            "Propongo registrar los dos viajes.",
            ("registrar_viaje", self.VIAJE),
            ("registrar_viaje", {**self.VIAJE, "fecha": "2026-08-16", "categoria": "Desvío"}),
        )
        with patch("google.genai.Client", return_value=cliente):
            pagina = self.cliente.post("/ia/preguntar", data={"pregunta": "Registra los viajes de Alfa a Beta"}).text
        self.assertIn("Registrar un viaje Alfa → Beta (2026-08-15)", pagina)
        self.assertIn("Registrar un viaje Alfa → Beta (2026-08-16)", pagina)
        self.assertEqual(excel_repo.leer("Viajes"), [])
        import re
        tokens = re.findall(r'name="token" value="([^"]+)"', pagina)
        final = self.cliente.post("/ia/cambios/confirmar-todos", data={"token": tokens}).text
        self.assertEqual(final.count("Viaje registrado: VIA-"), 2)
        self.assertEqual(sorted(v["Categoria"] for v in excel_repo.leer("Viajes")), ["Desvío", "Viaje completo"])


if __name__ == "__main__":
    unittest.main()
