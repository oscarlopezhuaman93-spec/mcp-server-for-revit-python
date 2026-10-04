import importlib.util
import math
import os

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "revit_mcp")
spec = importlib.util.spec_from_file_location("ct", os.path.join(ROOT, "column_table.py"))
ct = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ct)

MM = {'5/8"': 15.9, '3/4"': 19.1, '3/8"': 9.5, '1/2"': 12.7}
RECT = [(-0.15, -0.2), (0.15, -0.2), (0.15, 0.2), (-0.15, 0.2)]


@pytest.mark.parametrize("text, expected", [
    ("1@0.05, 8@0.10, rto @0.20", "1@0.05, 8@0.10, Resto @ 0.20 C/Extr."),
    ("1@5, 8@10, r@25", "1@0.05, 8@0.10, Resto @ 0.25 C/Extr."),
    ("", ""),
])
def test_spacing_text(text, expected):
    assert ct.spacing_text(text) == expected


def test_bars_and_shape_texts():
    bars = [(0, 0, '1/2"'), (0, 0, '5/8"'), (0, 0, '5/8"')]
    assert ct.bars_text(bars, MM) == u'2 Ø5/8" + 1 Ø1/2"'
    assert ct.shape_text(RECT) == "0.30m x 0.40m"
    assert ct.shape_text([(0, 0)] * 6) == 'TIPO "L"'
    assert ct.shape_text([(0, 0)] * 8) == 'TIPO "T"'


def test_levels_between():
    levels = [("S01", -3.6), ("NTZ", -9.75), ("P01", 1.2), ("NFP", -0.05)]
    assert ct.levels_between(levels, -9.75, 1.2) == ["NTZ", "S01", "NFP"]


def test_offset_and_rounded_band():
    inner = ct.offset_polygon(RECT, 0.05)
    assert min(p[0] for p in inner) == pytest.approx(-0.10)
    outer, hole = ct.stirrup_band(RECT, 0.0095)
    assert max(p[0] for p in outer) == pytest.approx(0.15 + 0.00475, abs=1e-6)
    assert max(p[0] for p in hole) == pytest.approx(0.15 - 0.00475, abs=1e-6)
    # the corners are bent: no outer point at the sharp corner
    assert all(math.hypot(p[0] - 0.15475, p[1] - 0.20475) > 0.004 for p in outer)


def test_hooks_at_the_top_left_corner_going_inwards():
    i = ct.hook_corner(RECT)
    assert RECT[i] == (-0.15, 0.2)
    for start, end in ct.hook_tails(RECT, 0.0095):
        assert end[0] > start[0] and end[1] < start[1]  # towards the core


def test_bar_groups_by_rows_or_columns():
    bars = [(-0.1, -0.15, '5/8"'), (0.0, -0.15, '5/8"'), (0.1, -0.15, '5/8"'),
            (-0.1, 0.15, '5/8"'), (0.0, 0.15, '5/8"'), (0.1, 0.15, '5/8"'),
            (-0.1, 0.0, '1/2"'), (0.1, 0.0, '1/2"')]
    groups = ct.bar_groups(bars)
    five = [g for g in groups if g["key"] == '5/8"']
    assert len(five) == 2 and all(g["axis"] == "row" and len(g["bars"]) == 3 for g in five)
    half = [g for g in groups if g["key"] == '1/2"']
    assert len(half) == 1 and len(half[0]["bars"]) == 2


def test_chain_and_sizes():
    assert ct.chain([0.3, 0.0, 0.3, 0.6001]) == [0.0, 0.3, 0.6001]
    assert ct.section_scale(RECT) == 10
    assert ct.section_size_paper(RECT) == pytest.approx((30.0, 40.0))
    assert ct.section_scale([(0, 0), (2.2, 0), (2.2, 0.3), (0, 0.3)]) == 25
    assert ct.type_width(RECT) == pytest.approx(106.0)


def test_natural_order():
    assert sorted(["C-10", "C-2", "C-1"], key=ct.natural_key) == ["C-1", "C-2", "C-10"]
