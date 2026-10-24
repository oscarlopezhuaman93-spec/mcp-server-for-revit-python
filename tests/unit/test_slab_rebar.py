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


LIB = [(u"H15", 0.30, 0.30), (u"H15 L.275", 0.275, 0.30), (u"H15 L.1889", 0.1889, 0.30),
       (u"H15 A.20", 0.30, 0.20), (u"H15 A.20 L.275", 0.275, 0.20), (u"H15 A.15", 0.30, 0.15)]


def test_ribbed_layout_bricks_and_bars():
    assert sr.auto_direction(SLAB, BEAMS) == sr.X  # spans along x (4.25) shorter than along y (5.25)
    centers, strips = sr.rib_layout((0.0, 1.25), 0.10, 0.40, 0.30)
    assert strips[0] == (0.0, 0.30) and centers[0] == pytest.approx(0.35)
    assert strips[-1][1] == pytest.approx(1.25) or strips[-1][1] - strips[-1][0] <= 0.30 + 1e-9
    # whole bricks, a shorter type of the family, else the rest cut to size
    pieces = sr.fill_bricks(0.0, 1.10, LIB, 0.30)  # 3 + a 0.1889 type: 0.0111 left (< 5 cm) stays concrete
    assert [p["type"] for p in pieces] == [u"H15"] * 3 + [u"H15 L.1889"]
    pieces = sr.fill_bricks(0.0, 1.08, LIB, 0.30)  # 3 + 0.18: no type fits, cut to 0.18
    assert len(pieces) == 4 and pieces[-1]["largo"] == pytest.approx(0.18) and pieces[-1]["type"] == u"H15"
    assert sr.fill_bricks(0.0, 1.08, LIB, 0.30, cut=False)[-1]["s1"] <= 0.90 + 1e-6
    assert sr.brick_width(LIB, 0.20) == 0.20
    assert sr.brick_width(LIB, 0.22) == 0.22  # cut to the strip
    assert sr.brick_width(LIB, 0.22, cut=False) == 0.20
    pieces = sr.fill_bricks(0.0, 0.65, LIB, 0.22)  # a cut width from the widest type
    # 2 bricks + 0.05 left: under the 0.10 minimum piece, it stays concrete
    assert all(p["ancho"] == 0.22 for p in pieces) and pieces[-1]["s1"] == pytest.approx(0.60)
    # the start: the first strip 0.10 from the face, the vigueta spacing kept
    centers, strips = sr.rib_layout((0.0, 2.0), 0.10, 0.40, 0.30, start=0.10)
    assert strips[0][0] == pytest.approx(0.10)
    assert all(abs((b - a) - 0.40) < 1e-6 for a, b in zip(centers, centers[1:]))
    # a slab with a slanted side: no brick sticks out of it
    slab = [((0.0, 0.0), (4.0, 0.0), (4.0, 3.0)), ((0.0, 0.0), (4.0, 3.0), (0.0, 1.0))]
    runs = sr.strip_runs(slab, [], sr.X, 0.5, 0.8)
    assert runs and runs[0][0] >= (0.8 - 1.0) / 0.5 - 1e-6  # the upper edge decides: x >= (t - 1) * 2
    tr = sr.rotate_tris(rect(0, 0, 2, 1), 90)
    x, y = sr.unrotate(tr[0][1][0], tr[0][1][1], 90)
    assert (x, y) == (pytest.approx(2.0), pytest.approx(0.0, abs=1e-9))
    st = sr.default_settings()
    st["alig"] = {"dir": sr.Y}
    # beams along x at y = 0 and 5 (the viguetas span y)
    beams = rect(0.0, 0.0, 9.25, 0.25) + rect(0.0, 5.0, 9.25, 5.25)
    plan, warns = sr.plan_ribbed(SLAB, beams, H, st, MM, None, LIB)
    assert plan["axis"] == sr.Y
    gaps = [b - a for a, b in zip(plan["viguetas"], plan["viguetas"][1:])]
    assert all(abs(g - 0.40) < 1e-6 for g in gaps)
    inf = plan["lines"][(sr.INF, sr.Y)]
    assert len(inf) == len(plan["viguetas"]) and len(inf[0]["bars"]) == 1
    sup = plan["lines"][(sr.SUP, sr.Y)]
    edge = sup[0]["bars"][0]  # an end support: Ln/4 from the face
    assert edge["s1"] == pytest.approx(0.25 + 4.75 / 4.0)
    assert plan["bricks"] and all(b["s0"] >= 0.25 - 1e-6 and b["s1"] <= 5.0 + 1e-6 for b in plan["bricks"])
    assert plan["lines"][(u"temp", sr.X)]
    st["alig"]["ens"] = {"mode": sr.ENS_FULL, "len": 0.25}
    plan2, _ = sr.plan_ribbed(SLAB, beams, H, st, MM, None, LIB)
    assert min(b["s0"] for b in plan2["bricks"]) >= 0.50 - 1e-6

def test_slab_stopping_at_the_faces_of_its_supports():
    # the slab between walls 0.15 thick that stand beside it (as in the user's model)
    slab = rect(0.0, 0.0, 3.0, 2.0)
    walls = rect(-0.15, -0.5, 0.0, 2.5) + rect(3.0, -0.5, 3.15, 2.5)
    st = sr.default_settings()
    sup = sr.line_intervals(walls, sr.X, 1.0)
    bars, spans, gaps, _ = sr.plan_line((0.0, 3.0), sup, sr.INF, sr.X, st, H, MM)
    assert spans == [(0.0, 3.0)]
    assert bars[0]["s0"] == pytest.approx(-0.15 + 0.02) and bars[0]["s1"] == pytest.approx(3.15 - 0.02)
    tops, _, _, _ = sr.plan_line((0.0, 3.0), sup, sr.SUP, sr.X, st, H, MM)
    assert len(tops) == 2 and tops[0]["s0"] == pytest.approx(-0.13) and tops[0]["s1"] == pytest.approx(1.0)