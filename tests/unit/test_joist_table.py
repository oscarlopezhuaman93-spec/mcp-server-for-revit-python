import importlib.util
import os
import sys

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "revit_mcp")
sys.path.insert(0, os.path.abspath(ROOT))  # joist_table imports column_table
spec = importlib.util.spec_from_file_location("jt", os.path.join(ROOT, "joist_table.py"))
jt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(jt)

MM = {'1/2"': 12.7, '3/8"': 9.5, '1/4"': 6.4}


def ribbed(**kw):
    al = {"b": 0.10, "s": 0.40, "top": 0.05, "inf": {"d": '1/2"'},
          "sup": {"d": '1/2"', "frac": 1.0 / 3.0, "end_frac": 0.25},
          "temp": {"on": True, "d": '1/4"', "s": 0.25}}
    al.update(kw)
    return al


def test_nominal_thickness_from_the_type_name():
    assert jt.nominal_thickness(u"FLR_LOSA ALIGERADA_e=0.20m (23.6° perp. 20 cm)", 0.218) == 0.20
    assert jt.nominal_thickness(u"LOSA 25", 0.25) == 0.25


def test_frac_text():
    assert jt.frac_text(1.0 / 3.0) == u"L/3"
    assert jt.frac_text(0.25) == u"L/4"
    assert jt.frac_text(0) == u"-"


def test_joist_section_is_a_t_with_its_bricks_and_bars():
    j = jt.Joist(0.20, ribbed(), 2.0, MM)
    xs = [p[0] for p in j.polygon]
    ys = [p[1] for p in j.polygon]
    assert (min(xs), max(xs), min(ys), max(ys)) == pytest.approx((-0.20, 0.20, 0.0, 0.20))
    assert len(j.bricks) == 2
    assert j.bricks[1][1][0] - j.bricks[1][0][0] == pytest.approx(0.15)  # (s - b) / 2 each side
    (_, y_inf, k_inf), (_, y_sup, k_sup) = j.design["bars"]
    assert y_inf == pytest.approx(0.02 + 0.00635)
    assert y_sup == pytest.approx(0.20 - 0.02 - 0.0064 - 0.00635)
    assert j.rows()[0] == u"b=0.10 h=0.20 @0.40"
    assert u"L/3" in j.rows()[2] and u"L/4" in j.rows()[2]


def test_group_merges_equal_joists():
    a = jt.Joist(0.20, ribbed(), 2.0, MM, slabs=[1])
    b = jt.Joist(0.20, ribbed(), 2.0, MM, slabs=[2])
    c = jt.Joist(0.20, ribbed(inf={"d": '3/8"'}), 2.0, MM, slabs=[3])
    out = jt.group([a, b, c])
    assert len(out) == 2
    assert sorted(len(j.slabs) for j in out) == [1, 2]
    assert len(set(j.mark for j in out)) == 2


class Recorder(object):
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return lambda *a, **k: self.calls.append(name)


def test_draw_table_draws_every_row():
    d = Recorder()
    w, h = jt.draw_table(d, [jt.Joist(0.20, ribbed(), 2.0, MM)], u"CUADRO", 10, u"Alto", MM)
    assert w > 100 and h > 50
    assert d.calls.count("ring") == 2  # the two bars at high detail
