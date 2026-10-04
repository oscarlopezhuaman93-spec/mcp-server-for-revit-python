import importlib.util
import os

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "revit_mcp")
spec = importlib.util.spec_from_file_location("ct", os.path.join(ROOT, "column_table.py"))
ct = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ct)

MM = {'5/8"': 15.9, '3/4"': 19.1, '3/8"': 9.5}


@pytest.mark.parametrize("text, expected", [
    ("1@0.05, 5@0.10, rto @0.20", "1@5, 5@10, r@20cm"),
    ("1@0.05, 8@0.10, rto@0.25", "1@5, 8@10, r@25cm"),
    ("1@5, 8@10, r@25", "1@5, 8@10, r@25cm"),
    ("", ""),
])
def test_spacing_text(text, expected):
    assert ct.spacing_text(text) == expected


def test_bars_text_largest_first():
    bars = [(0, 0, '5/8"'), (0, 0, '3/4"'), (0, 0, '3/4"'), (0, 0, '5/8"'), (0, 0, '3/4"')]
    assert ct.bars_text(bars, MM) == u'3Ø3/4" + 2Ø5/8"'


def test_size_and_levels():
    assert ct.size_text([(-0.1, -0.3), (0.1, -0.3), (0.1, 0.3), (-0.1, 0.3)]) == "0.20 x 0.60"
    assert ct.levels_text(["A"]) == "A"
    assert ct.levels_text(["A", "B", "C"]) == "A\na\nC"


def test_row_height_and_columns():
    square = [(-0.1, -0.3), (0.1, -0.3), (0.1, 0.3), (-0.1, 0.3)]
    assert ct.row_height(square, 20) == pytest.approx(48.0)
    assert ct.row_height([], 20) == ct.MIN_ROW_H
    xs, total = ct.column_x()
    assert xs[0] == 0 and total == pytest.approx(194.0)


def test_natural_order():
    assert sorted(["C-10", "C-2", "C-1"], key=ct.natural_key) == ["C-1", "C-2", "C-10"]
