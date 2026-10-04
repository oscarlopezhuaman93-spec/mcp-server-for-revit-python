import importlib.util
import os

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "revit_mcp")
spec = importlib.util.spec_from_file_location("ev", os.path.join(ROOT, "e060_review.py"))
ev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ev)

D58, D34, D12, D38 = 15.875, 19.05, 12.7, 9.525


def by_rule(findings):
    return dict((f["rule"], f) for f in findings)


def test_column_that_complies():
    # 0.30 x 0.60, 8 bars 5/8": rho = 8*1.98/1800 = 0.88% -> below 1%
    f = by_rule(ev.check_column("C-1", 0.30, 0.60, 0.18, [D58] * 8, False, 4.0, D38,
                                [(1, 0.05), (6, 0.10)], 0.20, 3.0))
    assert f["Cuantia longitudinal"]["status"] == ev.FAIL
    f = by_rule(ev.check_column("C-1", 0.30, 0.60, 0.18, [D34] * 8, False, 4.0, D38,
                                [(1, 0.05), (6, 0.10)], 0.20, 3.0))
    assert f["Cuantia longitudinal"]["status"] == ev.OK  # 1.27%
    assert f["Longitud de confinamiento lo"]["status"] == ev.OK  # 0.65 >= max(0.5, 0.6, 0.5)
    assert f["Espaciado en zona de confinamiento so"]["status"] == ev.OK  # 10 <= min(15.2, 15, 10)
    assert f["Espaciado fuera de lo"]["status"] == ev.OK  # 20 <= min(30.5, 45.7, 30, 30)
    assert f["Primer estribo"]["status"] == ev.OK


def test_column_failures():
    f = by_rule(ev.check_column("C-2", 0.25, 0.25, 0.0625, [D58] * 3, False, 3.0, 8.0,
                                [(1, 0.05), (3, 0.15)], 0.35, 3.0))
    assert f["Numero minimo de barras"]["status"] == ev.FAIL
    assert f["Recubrimiento"]["status"] == ev.FAIL
    assert f["Espaciado en zona de confinamiento so"]["status"] == ev.FAIL
    assert f["Longitud de confinamiento lo"]["status"] == ev.OK  # 0.05 + 3*0.15 = 0.50 >= 0.50
    assert f["Espaciado fuera de lo"]["status"] == ev.FAIL


def test_stirrup_min_diameter():
    assert ev.stirrup_min_diameter_mm(D58) == 8.0
    assert ev.stirrup_min_diameter_mm(25.4) == 9.5
    assert ev.stirrup_min_diameter_mm(35.8) == 12.7


def test_wall_quantities():
    # t 0.20, vertical 2 faces 3/8"@0.30: rho = 2*71.3/300/200 = 0.00238 -> warning band;
    # horizontal 1 face 3/8"@0.30: 0.00119 -> below 0.0020
    f = by_rule(ev.check_wall("M-1", 0.20, [(D38, 0.30), (D38, 0.30)], (D38, 0.30, 1), 2.5))
    assert f["Cuantia vertical"]["status"] == ev.WARN
    assert f["Cuantia horizontal"]["status"] == ev.FAIL
    f = by_rule(ev.check_wall("M-1", 0.20, [(D12, 0.20), (D12, 0.20)], (D12, 0.20, 2), 2.5))
    assert f["Cuantia vertical"]["status"] == ev.OK
    assert f["Espaciado vertical"]["status"] == ev.OK


def test_footing_and_stair():
    f = by_rule(ev.check_footing("Z-1", 0.60, [(u"inferior", u"X", D58, 0.15)], 7.5))
    assert f["Recubrimiento contra el terreno"]["status"] == ev.OK
    assert f["Cuantia minima (inferior X)"]["status"] == ev.OK  # 0.0022
    f = by_rule(ev.check_footing("Z-1", 0.80, [(u"inferior", u"X", D12, 0.30)], 5.0))
    assert f["Cuantia minima (inferior X)"]["status"] == ev.FAIL
    assert f["Recubrimiento contra el terreno"]["status"] == ev.FAIL
    f = by_rule(ev.check_stair("E", 0.15, 2.5, (D12, 0.20), (D38, 0.25)))
    assert f["Cuantia minima (acero principal)"]["status"] == ev.OK  # 0.0043
    assert f["Cuantia de temperatura"]["status"] == ev.OK  # 0.0019


def test_summary_sort_and_csv():
    fs = [ev.finding(ev.OK, "a", "x", "r", "1", "2", "n"), ev.finding(ev.FAIL, "a", "y", "r;s", "1", "2", "n")]
    assert ev.summary(fs)[ev.FAIL] == 1
    assert ev.sort_findings(fs)[0]["status"] == ev.FAIL
    csv = ev.to_csv(fs)
    assert csv.startswith(u"ESTADO;") and u"r,s" in csv
