"""Formato de las respuestas de la IA: Markdown básico a HTML seguro."""

from __future__ import annotations

import unittest

import jinja2
from markupsafe import Markup

from app.services.formato import markdown_a_html


class MarkdownAHtmlTest(unittest.TestCase):
    RESPUESTA_REAL = (
        "Sí, la ruta **APM → Bioban** está registrada en el sistema.\n"
        "\n"
        "Detalles de la ruta:\n"
        "* **Salida:** APM\n"
        "* **Llegada:** Bioban\n"
        "* **Código:** 35145\n"
        "* **Estado / Problemas:** Sin problemas detectados (comparte código con APM → Filisa).\n"
        "\n"
        "Información adicional:\n"
        "* La ruta en sentido contrario (**Bioban → APM**) también está registrada."
    )

    def test_negritas_y_vinietas_de_una_respuesta_real(self):
        html = markdown_a_html(self.RESPUESTA_REAL)
        self.assertIsInstance(html, Markup)
        self.assertIn("<strong>APM → Bioban</strong>", html)
        self.assertIn("<li><strong>Salida:</strong> APM</li>", html)
        self.assertEqual(html.count("<ul"), 2)
        self.assertEqual(html.count("<li>"), 5)
        self.assertNotIn("**", html)
        self.assertNotIn("* ", html)

    def test_listas_numeradas_con_anidacion(self):
        html = markdown_a_html("1. Vaya a **Configuración**.\n2. Pulse *Guardar*:\n   - Salida\n   - Llegada\n3. Listo")
        self.assertEqual(html.count("<ol"), 1)
        self.assertEqual(html.count("<ul"), 1)
        self.assertIn("<em>Guardar</em>:<ul", html)
        self.assertEqual(html.count("<li>"), 5)
        self.assertEqual(html.count("</li>"), 5)
        self.assertEqual(html.count("</ul>"), html.count("<ul"))
        self.assertEqual(html.count("</ol>"), html.count("<ol"))

    def test_una_linea_en_blanco_entre_items_no_parte_la_lista(self):
        html = markdown_a_html("1. Uno\n\n2. Dos\n\n3. Tres")
        self.assertEqual(html.count("<ol"), 1)
        self.assertEqual(html.count("<li>"), 3)

    def test_saltos_de_linea_y_parrafos(self):
        html = markdown_a_html("Línea uno\nlínea dos\n\nOtro párrafo")
        self.assertIn("Línea uno<br>línea dos", html)
        self.assertEqual(html.count("<p "), 2)

    def test_encabezados_se_muestran_como_titulo_en_negrita_sin_almohadilla(self):
        html = markdown_a_html("## Resumen\nTexto")
        self.assertIn("font-semibold", html)
        self.assertNotIn("#", html)

    def test_no_confunde_asteriscos_de_texto_normal(self):
        self.assertNotIn("<em>", markdown_a_html("5 * 3 = 15 y 2*4"))
        self.assertNotIn("<em>", markdown_a_html("usa snake_case_name y a_b"))
        self.assertIn("**sin cerrar", markdown_a_html("Esto **sin cerrar"))
        self.assertNotIn("<strong>", markdown_a_html("Esto **sin cerrar"))

    def test_vacio(self):
        self.assertEqual(markdown_a_html(""), "")
        self.assertEqual(markdown_a_html(None), "")
        self.assertEqual(markdown_a_html("  \n \n"), "")

    # --- Seguridad: el modelo y los datos son contenido no confiable ---

    def test_no_deja_pasar_html_ni_enlaces(self):
        ataques = [
            "<script>alert(1)</script>",
            '<img src=x onerror="alert(1)">',
            "[clic](javascript:alert(1))",
            "**<b onmouseover=alert(1)>x</b>**",
            "* <iframe src=//evil></iframe>",
            "`<script>`",
            "# <svg onload=alert(1)>",
        ]
        for ataque in ataques:
            with self.subTest(ataque):
                html = markdown_a_html(ataque)
                self.assertNotRegex(html, r"<(script|img|iframe|svg|b)\b")
                self.assertNotIn("<a ", html)
                self.assertNotIn('"alert', html)  # ningún atributo inyectado

    def test_filtro_de_jinja_no_escapa_dos_veces_y_si_escapa_lo_peligroso(self):
        entorno = jinja2.Environment(autoescape=True)
        entorno.filters["markdown"] = markdown_a_html
        plantilla = entorno.from_string("{{ texto|markdown }}")
        html = plantilla.render(texto="**hola** <script>x</script> & más")
        self.assertIn("<strong>hola</strong>", html)
        self.assertIn("&lt;script&gt;x&lt;/script&gt;", html)
        self.assertIn("&amp; más", html)
        self.assertNotIn("&amp;lt;", html)


if __name__ == "__main__":
    unittest.main()
