"""Each window keeps its own behavior: a shared module's defaults are the
original window's, a new window opts in with its own parameters
(docs/INDEPENDENCIA_DE_VENTANAS.md). These read the sources, so they run
without Revit."""
import io
import os
import re

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
PANEL = os.path.join(ROOT, "BOKI Estructura.tab", "Acero.panel")


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


def test_acero_columna_neighbors_stay_beams_slabs_footings():
    src = read("revit_mcp", "rebar_columns.py")
    block = re.search(r"^NEIGHBOR_CATEGORIES = \((.*?)^\)", src, re.S | re.M).group(1)
    labels = re.findall(r'u"([A-Z]+)"', block)
    assert labels == ["VIGA", "LOSA", "ZAPATA"]


def test_only_acero_muro_asks_for_the_wider_neighbors():
    columna = read("BOKI Estructura.tab", "Acero.panel", "Acero.pushbutton", "script.py")
    muro = read("BOKI Estructura.tab", "Acero.panel", "Acero Muro.pushbutton", "script.py")
    assert "WALL_NEIGHBOR_CATEGORIES" not in columna
    assert "categories=rc.WALL_NEIGHBOR_CATEGORIES" in muro


def test_every_steel_window_has_its_own_folder_and_form():
    for button in ("Acero", "Acero Viga", "Acero Muro", "Acero Cimentacion", "Acero Escalera"):
        folder = os.path.join(PANEL, button + ".pushbutton")
        assert os.path.isfile(os.path.join(folder, "script.py")), button
        assert os.path.isfile(os.path.join(folder, "AceroForm.xaml")), button
