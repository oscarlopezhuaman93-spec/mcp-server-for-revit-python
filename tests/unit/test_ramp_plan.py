# -*- coding: utf-8 -*-
import math

from revit_mcp.ramp_plan import arc_through, plan_ramp, Profile, seg_length


def test_arc_through_half_circle():
    c, r, t0, sweep = arc_through((6.75, 0.0), (0.0, 6.75), (-6.75, 0.0))
    assert abs(c[0]) < 1e-9 and abs(c[1]) < 1e-9 and abs(r - 6.75) < 1e-9
    assert abs(sweep - math.pi) < 1e-9


def test_profile_rises_with_half_slope_transitions():
    p = Profile(37.2, 3.0, 3.6)
    assert abs(p.z(0.0)) < 1e-9 and abs(p.z(37.2) - 3.0) < 1e-9
    assert abs(p.slope - 3.0 / (37.2 - 3.6)) < 1e-9
    assert abs(p.z(3.6) - 3.6 * p.slope / 2) < 1e-9


U_RAMP = [{"kind": "line", "a": (6.75, -8.0), "b": (6.75, 0.0)},
          {"kind": "arc", "a": (6.75, 0.0), "m": (0.0, 6.75), "b": (-6.75, 0.0)},
          {"kind": "line", "a": (-6.75, 0.0), "b": (-6.75, -8.0)}]


def test_plan_pieces_continuous():
    p = plan_ramp(U_RAMP, 3.5, 3.0, 3.6)
    assert abs(p["length"] - (16 + math.pi * 6.75)) < 1e-9
    kinds = [q["kind"] for q in p["pieces"]]
    assert kinds == ["line", "line", "arc", "line", "line"]
    for a, b in zip(p["pieces"], p["pieces"][1:]):
        assert abs(a["z1"] - b["z0"]) < 1e-9 and abs(a["s1"] - b["s0"]) < 1e-9
    assert abs(p["pieces"][-1]["z1"] - 3.0) < 1e-9 and not p["warnings"]


def test_too_steep_warns():
    p = plan_ramp([{"kind": "line", "a": (0, 0), "b": (10, 0)}], 3.5, 3.0, 3.6)
    assert p["warnings"] and p["slope"] > 0.15


def test_arc_edges_at_width():
    p = plan_ramp(U_RAMP, 3.5, 3.0, 3.6)
    arc = [q for q in p["pieces"] if q["kind"] == "arc"][0]
    for (x, y, z) in arc["left"]:
        assert abs(math.hypot(x, y) - (6.75 - 1.75)) < 1e-6  # counterclockwise: the left edge inside
    assert abs(seg_length(U_RAMP[1]) - math.pi * 6.75) < 1e-9


def test_arc_inner_edge_steeper_than_axis():
    from revit_mcp.ramp_plan import arc_slopes
    seg = {"kind": "arc", "a": (10.0, 0.0), "m": (0.0, 10.0), "b": (-10.0, 0.0)}
    r, inner, outer = arc_slopes([seg], 4.0, 0.10)[0]
    assert abs(r - 10.0) < 1e-9
    assert abs(inner - 0.125) < 1e-9 and abs(outer - 0.10 * 10 / 12) < 1e-9
