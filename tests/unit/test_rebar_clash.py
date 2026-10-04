import importlib.util
import os

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "revit_mcp")
spec = importlib.util.spec_from_file_location("cl", os.path.join(ROOT, "rebar_clash.py"))
cl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cl)

R = 0.008  # 5/8" radius


def test_segment_distance_crossing_and_parallel():
    d, _, _ = cl.segment_distance((0, 0, 0), (1, 0, 0), (0.5, -1, 0.01), (0.5, 1, 0.01))
    assert d == pytest.approx(0.01)
    d, _, _ = cl.segment_distance((0, 0, 0), (1, 0, 0), (0, 0.05, 0), (1, 0.05, 0))
    assert d == pytest.approx(0.05)
    d, _, _ = cl.segment_distance((0, 0, 0), (1, 0, 0), (2, 0, 0), (3, 0, 0))
    assert d == pytest.approx(1.0)


def test_clash_between_two_rebar_elements_only():
    pieces = [
        (1, 0, (0, 0, 0), (2, 0, 0), R),          # beam bar
        (2, 0, (1, -0.5, 0.005), (1, 0.5, 0.005), R),  # crossing it 5 mm apart -> overlaps 11 mm
        (1, 1, (0, 0.002, 0), (2, 0.002, 0), R),  # same element: ignored
        (3, 0, (1.5, -0.5, 0.016), (1.5, 0.5, 0.016), R),  # laid on top: just touches
    ]
    clashes = cl.find_clashes(pieces)
    assert len(clashes) == 1
    c = clashes[0]
    assert (c["a"], c["b"]) == (1, 2)
    assert c["overlap"] == pytest.approx(0.011, abs=1e-6)
    assert c["point"][0] == pytest.approx(1.0)


def test_one_result_per_pair_and_far_bars_skip():
    pieces = [(1, 0, (0, 0, 0), (3, 0, 0), R)] + [(2, k, (k, -1, 0), (k, 1, 0), R) for k in range(3)] + \
             [(4, 0, (10, 10, 10), (11, 10, 10), R)]
    clashes = cl.find_clashes(pieces)
    assert len(clashes) == 1


def test_csv():
    assert cl.to_csv([("Choque", "A", "B", "x", 1, 2, 3)]).startswith(u"TIPO;")
