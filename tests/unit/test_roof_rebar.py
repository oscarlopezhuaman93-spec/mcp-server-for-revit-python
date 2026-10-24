import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "revit_mcp"))
import rebar_spec as rs  # noqa: E402
import roof_rebar as rr  # noqa: E402

MM = rs.BAR_DIAMETERS_MM
M = math.tan(math.radians(23.0))


def roof():
    # eaves at s = -4 / +4 (beams 0.25), ridge beam 0.40 wide at s = 0, top 7.60 at the ridge
    top = [(-4.125, 7.6 - M * 4.125), (0.0, 7.6), (4.125, 7.6 - M * 4.125)]
    sup = [(-4.125, -3.875), (-0.2, 0.2), (3.875, 4.125)]
    return rr.Profile(top, 0.20, 0.20, sup)


def test_profile_geometry():
    p = roof()
    assert p.slope(rr.A) == pytest.approx(M) and p.slope(rr.B) == pytest.approx(M)
    assert p.top_z(0.0) == pytest.approx(7.6)
    assert p.clear(rr.A) == (-3.875, -0.2) and p.clear(rr.B) == (0.2, 3.875)
    assert p.along(rr.B, 1.0) == pytest.approx(math.cos(math.radians(23.0)))
    assert p.slope_length(-1.0, 1.0) == pytest.approx(2.0 / math.cos(math.radians(23.0)))
    # 0.20 across the face is 0.217 in the vertical
    assert p.top_z(-1.0) - p.depth_z(-1.0, 0.20) == pytest.approx(0.20 / math.cos(math.radians(23.0)))


def test_doblez_bars_cross_the_ridge_into_the_other_slab():
    bars = rr.plan_roof(roof(), {}, MM)
    names = [b["name"] for b in bars]
    assert u"inferior A" in names and u"inferior B" in names
    inf_a = [b for b in bars if b["name"] == u"inferior A"][0]
    ss = [p[0] for p in inf_a["path"]]
    # from the eave A beam's far face (hooked) past the ridge 0.60 on the slope into B
    assert min(ss) == pytest.approx(-4.125 + 0.02)
    assert max(ss) == pytest.approx(0.60 * math.cos(math.radians(23.0)))
    assert any(abs(s) < 1e-6 for s in ss)  # bent over the ridge
    assert inf_a["shift"] != 0  # side by side with the B bar
    cot = inf_a["cotas"][0]
    assert cot["tag"] == u"roof|inf|dB" and cot["value"] == pytest.approx(0.60)
    # the edited doblez is kept
    st = rr.set_value({}, u"roof|inf|dB", 0.85)
    bars = rr.plan_roof(roof(), st["roof"], MM)
    inf_a = [b for b in bars if b["name"] == u"inferior A"][0]
    assert max(p[0] for p in inf_a["path"]) == pytest.approx(0.85 * math.cos(math.radians(23.0)))


def test_continuo_and_anclado():
    bars = rr.plan_roof(roof(), {"inf": {"mode": rr.CONTINUO}, "sup": {"mode": rr.ANCLADO}}, MM)
    inf = [b for b in bars if b["face"] == u"inf"]
    assert len(inf) == 1 and inf[0]["path"][0][0] == pytest.approx(inf[0]["path"][1][0])  # hooked at the eave
    sup = [b for b in bars if b["name"].startswith(u"bastón cumbrera")]
    assert len(sup) == 2
    a = sup[0]["path"]
    assert a[-1][0] == pytest.approx(a[-2][0]) and a[-1][1] < a[-2][1]  # hooked down in the ridge beam
    ridge = [b for b in rr.plan_roof(roof(), {"sup": {"mode": rr.CONTINUO}}, MM) if b["name"] == u"bastón cumbrera"][0]
    ln = roof().slope_length(-3.875, -0.2)
    a_len = ridge["cotas"][0]["value"]
    assert a_len == pytest.approx(roof().slope_length(-0.2, 0.0) + ln / 3.0, abs=0.01)


def test_eave_bastones_and_edits():
    st = {"eave": {"A": 1.20}}
    bars = rr.plan_roof(roof(), st, MM)
    ea = [b for b in bars if b["name"] == u"bastón alero A"][0]
    assert ea["cotas"][0]["value"] == 1.20
    end = max(p[0] for p in ea["path"])
    assert roof().slope_length(-3.875, end) == pytest.approx(1.20)
    eb = [b for b in bars if b["name"] == u"bastón alero B"][0]
    assert eb["cotas"][0]["value"] == pytest.approx(round(0.25 * roof().slope_length(0.2, 3.875), 2))
