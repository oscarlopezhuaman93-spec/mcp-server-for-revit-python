import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "revit_mcp"))
import rebar_spec as rs  # noqa: E402
import slab_rebar as sr  # noqa: E402

MM = rs.BAR_DIAMETERS_MM


def rect(x0, y0, x1, y1):
    return [((x0, y0), (x1, y0), (x1, y1)), ((x0, y0), (x1, y1), (x0, y1))]


# a 9.25 x 5.25 slab on beams 0.25 wide along x = 0, 4.5, 9 (centers 0.125, 4.625, 9.125)
SLAB = rect(0.0, 0.0, 9.25, 5.25)
BEAMS = rect(0.0, 0.0, 0.25, 5.25) + rect(4.5, 0.0, 4.75, 5.25) + rect(9.0, 0.0, 9.25, 5.25)
H = 0.20


def test_line_intervals_and_spans():
    assert sr.line_intervals(SLAB, sr.X, 1.0) == [(0.0, 9.25)]
    sup = sr.line_intervals(BEAMS, sr.X, 1.0)
    assert sup == [(0.0, 0.25), (4.5, 4.75), (9.0, 9.25)]
    spans, gaps = sr.clear_and_gaps((0.0, 9.25), sup)
    assert spans == [(0.25, 4.5), (4.75, 9.0)]
    assert sr.line_intervals(SLAB, sr.Y, 3.0) == [(0.0, 5.25)]


def test_bastones_from_the_faces_and_edits():
    st = sr.default_settings()
    sup = sr.line_intervals(BEAMS, sr.X, 1.0)
    bars, spans, gaps, _ = sr.plan_line((0.0, 9.25), sup, sr.SUP, sr.X, st, H, MM)
    assert len(bars) == 3
    mid = bars[1]
    assert mid["s0"] == pytest.approx(4.5 - 4.25 / 3) and mid["s1"] == pytest.approx(4.75 + 4.25 / 3)
    edge = bars[0]
    assert edge["s0"] == pytest.approx(0.02) and edge["s1"] == pytest.approx(0.25 + 4.25 / 3)
    assert len(edge["path"]) > 2 and len(mid["path"]) == 2  # hooked at the edge only
    st["bast"][mid["support"]] = [1.0, 1.2]
    bars, _, _, _ = sr.plan_line((0.0, 9.25), sup, sr.SUP, sr.X, st, H, MM)
    assert (bars[1]["s0"], bars[1]["s1"]) == (pytest.approx(3.5), pytest.approx(5.95))


def test_bottom_bars_spliced_from_the_face_in_the_end_quarter():
    st = sr.default_settings()
    slab = rect(0.0, 0.0, 13.75, 3.0)
    beams = rect(0.0, 0.0, 0.25, 3.0) + rect(4.5, 0.0, 4.75, 3.0) + rect(9.0, 0.0, 9.25, 3.0) + rect(13.5, 0.0, 13.75, 3.0)
    splice = {"max": 9.0, "laps": {u'3/8"': 0.55}}
    sup = sr.line_intervals(beams, sr.X, 1.0)
    bars, spans, gaps, warns = sr.plan_line((0.0, 13.75), sup, sr.INF, sr.X, st, H, MM, splice)
    assert len(bars) == 2 and not warns
    lo, hi = bars[0]["lap_at"]
    assert hi - lo == pytest.approx(0.55)
    assert any(a - 1e-6 <= lo and hi <= a + (b - a) / 4.0 + 1e-6 or b - (b - a) / 4.0 - 1e-6 <= lo and hi <= b + 1e-6
               for a, b in spans)


def test_plan_and_sets():
    st = sr.default_settings()
    plan, warns = sr.plan_slab(SLAB, BEAMS, H, st, MM)
    assert not warns
    inf_x = plan[(sr.INF, sr.X)]
    assert len(inf_x) == 28  # 5.25 - 2 x 2 cm cover - d, every 0.20 at most
    sets = sr.bar_sets(inf_x)
    assert len(sets) == 1 and sets[0][3] == 28 and sets[0][4] == pytest.approx((5.25 - 0.04 - 0.009525) / 27)
    z = sr.layer_z(st, H, MM)
    assert z[(sr.INF, sr.X)] < z[(sr.INF, sr.Y)] < z[(sr.SUP, sr.Y)] < z[(sr.SUP, sr.X)]
    assert len(sr.bar_sets(plan[(sr.SUP, sr.X)])) == 3
    cuts = sr.default_cuts(SLAB)
    assert sr.crossing(plan[(sr.SUP, sr.Y)], cuts[sr.X]) == []  # no y bastones (no beams along x)
    assert len(sr.crossing(plan[(sr.INF, sr.Y)], cuts[sr.X])) == len(plan[(sr.INF, sr.Y)])


def test_settings_round_trip():
    st = sr.default_settings()
    st["bast"]["x@4.62"] = [1.0, 1.1]
    st[sr.INF][sr.X]["s"] = 0.15
    back = sr.read(sr.write(st))
    assert back["bast"] == {"x@4.62": [1.0, 1.1]} and back[sr.INF][sr.X]["s"] == 0.15
    assert back[sr.SUP]["mode"] == sr.TOP_BASTONES
