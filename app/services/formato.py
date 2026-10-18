"""Convierte el Markdown básico que devuelve Gemini en HTML seguro.

Todo el texto se escapa antes de dar formato y solo se generan etiquetas fijas (p, strong, em, code, ul, ol, li, br):
el modelo no puede inyectar HTML, enlaces ni scripts. Soporta negrita, cursiva, código en línea, listas con
viñetas o numeradas (con anidación) y encabezados, que se muestran como títulos en negrita.
"""

from __future__ import annotations

import re
from html import escape

from markupsafe import Markup

_ITEM = re.compile(r"^(?P<sangria>[ \t]*)(?P<marca>[*\-•]|\d+[.)])[ \t]+(?P<texto>.*)$")
_TITULO = re.compile(r"^[ \t]*#{1,6}[ \t]+(?P<texto>.+?)[ \t]*#*[ \t]*$")
_REGLA = re.compile(r"^[ \t]*([-*_])([ \t]*\1){2,}[ \t]*$")
_NEGRITA = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")
_CURSIVA = re.compile(r"(?<![*\w])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?![*\w])")
_CODIGO = re.compile(r"`([^`\n]+)`")

_UL = "my-1.5 list-disc space-y-1 pl-5"
_OL = "my-1.5 list-decimal space-y-1 pl-5"


def _en_linea(texto: str) -> str:
    seguro = escape(texto)
    seguro = _CODIGO.sub(r'<code class="rounded bg-surface2 px-1 py-0.5 text-[0.85em]">\1</code>', seguro)
    seguro = _NEGRITA.sub(r"<strong>\1</strong>", seguro)
    return _CURSIVA.sub(r"<em>\1</em>", seguro)


def _sangria(espacios: str) -> int:
    return len(espacios.replace("\t", "    "))


def markdown_a_html(texto) -> Markup:
    lineas = str(texto or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    salida: list[str] = []
    parrafo: list[str] = []
    listas: list[tuple[str, int]] = []  # (etiqueta, sangría) de las listas abiertas

    def cerrar_parrafo() -> None:
        if parrafo:
            salida.append('<p class="mt-2 first:mt-0">' + "<br>".join(_en_linea(l) for l in parrafo) + "</p>")
            parrafo.clear()

    def cerrar_listas(hasta: int = -1) -> None:
        while listas and listas[-1][1] > hasta:
            salida.append(f"</li></{listas.pop()[0]}>")

    for linea in lineas:
        if not linea.strip():
            cerrar_parrafo()  # las listas siguen abiertas: una línea en blanco no corta una lista
            continue
        if _REGLA.match(linea):
            cerrar_parrafo()
            cerrar_listas()
            continue
        titulo = _TITULO.match(linea)
        if titulo:
            cerrar_parrafo()
            cerrar_listas()
            salida.append(f'<p class="mt-3 font-semibold first:mt-0">{_en_linea(titulo["texto"])}</p>')
            continue
        item = _ITEM.match(linea)
        if item:
            cerrar_parrafo()
            etiqueta = "ol" if item["marca"][0].isdigit() else "ul"
            nivel = _sangria(item["sangria"])
            cerrar_listas(nivel)
            if listas and listas[-1][1] == nivel:
                if listas[-1][0] == etiqueta:
                    salida.append("</li>")
                else:
                    salida.append(f"</li></{listas.pop()[0]}>")
            if not listas or listas[-1][1] < nivel or listas[-1][1] != nivel:
                salida.append(f'<{etiqueta} class="{_OL if etiqueta == "ol" else _UL}">')
                listas.append((etiqueta, nivel))
            salida.append(f"<li>{_en_linea(item['texto'])}")
            continue
        if listas and linea[:1] in " \t":
            salida.append("<br>" + _en_linea(linea.strip()))  # continuación de un elemento de lista
            continue
        cerrar_listas()
        parrafo.append(linea.strip())
    cerrar_parrafo()
    cerrar_listas()
    return Markup("".join(salida))
