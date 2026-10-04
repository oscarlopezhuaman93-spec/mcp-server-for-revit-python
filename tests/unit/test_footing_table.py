import importlib.util
import os

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "revit_mcp")
spec = importlib.util.spec_from_file_location("ft", os.path.join(ROOT, "footing_table.py"))
ft = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ft)


@pytest.mark.parametrize("name, mark", [
    (u"SFO_ZAPATA AISLADA_CONCRETO F'C=210Kg/cm\xb2_Z-6_H=1.00m", "Z-6"),
    ("Z-4_H=0.70m", "Z-4"),
    (u"SFO_CIMIENTO CORRIDO_CONCRETO F'C=175Kg/cm\xb2_CC_10-10_H=0.70m", "CC 10-10"),
    ("SFA_CIMIENTO DE ESCALERA_CONCRETO f'c=210 kg/cm2_CE_H=0.40m", "CE"),
    ("LOSA DE CIMENTACION", "LOSA DE CIMENTACION"),
])
def test_footing_mark(name, mark):
    assert ft.footing_mark(name) == mark


def test_steel_and_spacing_texts():
    assert ft.steel_text(16, '5/8"', 0.15) == u'16Ø5/8"@ 0.15'
    assert ft.steel_text(0, '5/8"', 0.15) == "-"
    assert ft.steel_text(9, '1/2"', None) == u'9Ø1/2"@ var.'
    assert ft.mesh_spacing([0.0, 0.2, 0.4], "Espaciado", 0.2, []) == 0.2
    assert ft.mesh_spacing([0.0, 0.5, 1.0], "Cantidad", 0.2, []) == 0.5
    assert ft.mesh_spacing([0.0, 0.5], "Espaciado", 0.2, [{"a": 0}]) is None


def test_irregular_steel_and_shapes():
    assert ft.steel_text(16, '5/8"', 0.15, irregular=True) == u'Ø5/8"@ 0.15'
    rect = [((0, 0), (2, 0)), ((2, 0), (2, 3)), ((2, 3), (0, 3)), ((0, 3), (0, 0))]
    assert ft.is_rectangle(rect)
    assert ft.is_rectangle(list(reversed(rect)))
    ell = rect[:2] + [((2, 3), (1, 3)), ((1, 3), (1, 1)), ((1, 1), (0, 1)), ((0, 1), (0, 0))]
    assert not ft.is_rectangle(ell)
    assert ft.same_sizes([(2.2, 2.75, 0.8), (2.2, 2.75, 0.8)])
    assert not ft.same_sizes([(2.2, 2.75, 0.8), (5.19, 5.76, 0.8)])


def test_irregular_row_reads_ver_planta():
    class Irregular(Item):
        irregular = True
    d = Recorder()
    ft.draw_table(d, [Irregular()], "T", 0.1)
    texts = [c[1] for c in d.calls if c[0] == "text"]
    assert "ver planta" in texts and "2.50" not in texts and "21.80" not in texts


def test_df_text():
    assert ft.df_text([1.5]) == "1.50"
    assert ft.df_text([1.5, 1.501, 3.3]) == "1.50 / 3.30"
    assert ft.df_text([]) == "-"


class Recorder(object):
    def __init__(self):
        self.calls = []

    def line(self, a, b, style):
        self.calls.append(("line", style))

    def polyline(self, pts, closed, style):
        self.calls.append(("polyline", style))

    def region(self, loops, fill):
        self.calls.append(("region", fill))

    def text(self, x, y, text, kind, rotate=False, align="center"):
        self.calls.append(("text", text))


class Item(object):
    mark, b, L, h, df = "Z-1", 2.5, 21.8, 0.6, "1.50"
    steel = {"sup": {"b": "A", "L": "B"}, "inf": {"b": "C", "L": "D"}}


def test_table():
    d = Recorder()
    w, h = ft.draw_table(d, [Item(), Item()], "CUADRO DE ZAPATAS", 0.10)
    texts = [c[1] for c in d.calls if c[0] == "text"]
    for t in ("CUADRO DE ZAPATAS", "DIMENSIONES", "ACERO", "Z-1", "2.50", "21.80", "0.60", "0.10", "1.50",
              "As superior", "As inferior", "A", "B", "C", "D"):
        assert t in texts
    assert [c[1] for c in d.calls[:3]] == [ft.BAND_TITLE, ft.BAND_HEAD, ft.BAND_SIDE]
    assert w == pytest.approx(208.0)
    assert h == pytest.approx(ft.TITLE_H + ft.HEAD1_H + ft.HEAD2_H + 2 * ft.ROW_H)
