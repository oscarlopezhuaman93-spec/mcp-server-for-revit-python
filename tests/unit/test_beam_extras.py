import importlib.util
import os

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "revit_mcp")
spec = importlib.util.spec_from_file_location("be", os.path.join(ROOT, "beam_extras.py"))
be = importlib.util.module_from_spec(spec)
spec.loader.exec_module(be)

MM = {'5/8"': 15.875, '1/2"': 12.7, '3/8"': 9.525, '3/4"': 19.05}
SPANS = [(0.25, 3.87), (4.12, 7.92), (8.17, 9.39)]
START, END = 0.0, 9.64
# a 0.25 x 0.40 beam: 2 top and 2 bottom bars 5/8"
MAIN = [(-0.0796, 0.1396, '5/8"'), (0.0796, 0.1396, '5/8"'), (-0.0796, -0.1396, '5/8"'), (0.0796, -0.1396, '5/8"')]


def test_settings_round_trip_and_span_texts():
    st = be.default_settings()
    be.set_span_text(st, 1, "edge", u"1@0.05, 6@0.10, rto@0.15")
    st2 = be.read(be.write(st))
    assert be.span_text(st2, 1, "edge") == u"1@0.05, 6@0.10, rto@0.15"
    assert be.span_text(st2, 0, "edge") == u""
    be.set_span_text(st2, 1, "edge", u"")
    assert st2["spans"] == {}


def test_gaps_and_defaults():
    g = be.gaps(SPANS, START, END)
    assert g[0] == (0.0, 0.25) and g[1] == (3.87, 4.12) and g[-1] == (9.39, 9.64)
    b = be.default_baston(1, SPANS, START, END, 2, '5/8"')
    assert b["s0"] == pytest.approx(3.87 - 3.62 / 3, abs=1e-3) and b["s1"] == pytest.approx(4.12 + 3.8 / 3, abs=1e-3)
    end = be.default_baston(0, SPANS, START, END, 2, '5/8"')
    assert end["s0"] == 0.0
    bal = be.default_balancin(1, SPANS, 2, '5/8"')
    assert bal["face"] == be.BOTTOM and bal["s0"] == pytest.approx(4.12 + 0.57, abs=1e-3)
    assert len(be.default_cuts(SPANS)) == 9
    assert be.nearest_gap(4.0, SPANS, START, END) == 1


def test_labels_and_parsing():
    b = be.default_baston(1, SPANS, START, END, 2, '5/8"')
    assert be.extra_label(b, SPANS, START, END) == u'2Ø5/8" 1.21|1.27'
    bal = be.default_balancin(1, SPANS, 2, '5/8"')
    assert be.extra_label(bal, SPANS, START, END) == u'2Ø5/8" L=2.66'
    assert be.parse_count_diameter(u"2 5/8", MM) == (2, '5/8"')
    assert be.parse_count_diameter(u'3Ø1/2"', MM) == (3, '1/2"')
    with pytest.raises(ValueError):
        be.parse_count_diameter(u"dos", MM)
    assert be.parse_lengths(u"1.20|1.30") == (1.2, 1.3)
    assert be.parse_lengths(u"0,9") == (0.9, 0.9)


def test_extra_positions_same_layer_then_second():
    pts = be.extra_positions(MAIN, be.TOP, 2, '5/8"', MM)
    assert len(pts) == 2 and all(abs(y - 0.1396) < 1e-6 for _, y in pts)
    assert -0.0796 < pts[0][0] < pts[1][0] < 0.0796
    pts = be.extra_positions(MAIN, be.BOTTOM, 4, '5/8"', MM)  # no room for 4: second layer
    assert len(pts) == 4 and all(y > -0.1396 + 0.02 for _, y in pts)


def test_steel_at_cuts_and_ends():
    extras = [be.default_baston(1, SPANS, START, END, 2, '5/8"')]
    text, area = be.steel_at(4.0, MAIN, extras, MM, be.TOP)
    assert text == u'2Ø5/8" + 2Ø5/8"' and area == pytest.approx(7.92, abs=0.01)
    text, area = be.steel_at(6.0, MAIN, extras, MM, be.TOP)
    assert text == u'2Ø5/8"' and area == pytest.approx(3.96, abs=0.01)
    s0, s1, h0, h1 = be.extra_ends({"s0": 0.0, "s1": 1.5}, 0.04, 9.60)
    assert (s0, s1, h0, h1) == (0.04, 1.5, True, False)
