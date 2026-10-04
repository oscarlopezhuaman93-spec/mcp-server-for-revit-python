import importlib.util
import os

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "revit_mcp")
spec = importlib.util.spec_from_file_location("sr", os.path.join(ROOT, "steel_report.py"))
sr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sr)

W = {'5/8"': 1.552, '3/8"': 0.56}
MM = {'5/8"': 15.9, '3/8"': 9.5}


def test_aggregate_and_ratios():
    bars = [("Columna", '5/8"', 4, 12.0), ("Columna", '5/8"', 2, 6.0), ("Columna", '3/8"', 10, 15.0),
            ("Escalera", '3/8"', 5, 5.0)]
    rows, totals = sr.aggregate(bars, W.get, MM)
    assert rows[0] == ("Columna", '3/8"', 10, 15.0, pytest.approx(8.4))
    assert rows[1][2:4] == (6, 18.0) and rows[1][4] == pytest.approx(27.936)
    assert totals["Columna"][2] == pytest.approx(36.336)
    r = sr.ratios(totals, {"Columna": 0.5})
    assert r == {"Columna": pytest.approx(72.672)}
    assert sr.grand_total(totals)[0] == 21


def test_csv_and_html():
    rows, totals = sr.aggregate([("Columna", '5/8"', 4, 12.0)], W.get, MM)
    csv = sr.to_csv(rows, totals, {"Columna": 0.4})
    assert u"TOTAL;4;12.00;18.62" in csv
    html = sr.to_html("Reporte", "Proyecto <A>", "2026-10-04", rows, totals, {"Columna": 0.4},
                      {"OK": 3, "AVISO": 1, "NO CUMPLE": 2}, (0, 1))
    assert "Proyecto &lt;A&gt;" in html and "NO CUMPLE: 2" in html and "18.62" in html
