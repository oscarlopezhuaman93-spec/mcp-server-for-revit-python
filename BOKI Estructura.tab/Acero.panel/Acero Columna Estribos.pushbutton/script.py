# -*- coding: utf-8 -*-
"""Estribos de columnas: estribos de borde y confinamiento, grapas, izaje y
nucleo. Es la misma ventana de Acero Columna (vistas, vecinos y nudo
compartidos) en modo estribos: solo genera estribos y deja las barras
longitudinales ya generadas."""

__title__ = "Acero Columna\nEstribos"
__author__ = "Revit MCP"

import io
import os

_SOURCE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "Acero.pushbutton", "script.py"))
_scope = dict(globals())
_scope.update({"__file__": _SOURCE, "ACERO_COLUMNA_MODO": "estribos"})
exec(compile(io.open(_SOURCE, encoding="utf-8").read(), _SOURCE, "exec"), _scope)
