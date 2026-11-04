import importlib.util
import os
import sys
import types

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "revit_mcp")


def _load():
    # rebar_foundation imports Revit modules only to read geometry: stub them
    for name in ("pyrevit", "System", "System.Collections", "System.Collections.Generic"):
        sys.modules.setdefault(name, types.ModuleType(name))
    sys.modules["pyrevit"].DB = object()
    sys.modules["System.Collections.Generic"].List = list
    spec = importlib.util.spec_from_file_location("rf", os.path.join(ROOT, "rebar_foundation.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


rf = _load()
SQUARE = [(-0.5, 0.0), (0.5, 0.0), (0.5, 0.7), (-0.5, 0.7)]


def test_inner_outline_per_edge():
    inner = rf.inner_outline(SQUARE, [0.075, 0.075, 0.05, 0.075])
    assert inner[0] == pytest.approx((-0.425, 0.075))
    assert inner[2] == pytest.approx((0.425, 0.65))


def test_bottom_mesh_with_hooks_up():
    inner = rf.inner_outline(SQUARE, [0.075, 0.075, 0.05, 0.075])
    (path,) = rf.mesh_layer_paths(inner, rf.BOTTOM, 0.0127, 0.25)
    assert path[1][1] == pytest.approx(0.075 + 0.00635)
    assert path[0][1] - path[1][1] == pytest.approx(0.25)


def test_top_mesh_hooks_down_and_two_spans():
    inner = rf.inner_outline(SQUARE, [0.075, 0.075, 0.05, 0.075])
    (path,) = rf.mesh_layer_paths(inner, rf.TOP, 0.0127, 0.25)
    assert path[0][1] < path[1][1]
    u = [(0, 0), (2, 0), (2, 1), (1.5, 1), (1.5, 0.4), (0.5, 0.4), (0.5, 1), (0, 1)]
    assert len(rf.polygon_spans(u, 0.8)) == 2


def test_positions_centered_and_snap():
    assert rf.bar_positions(0.0, 1.0, 0.3) == pytest.approx([0.05, 0.35, 0.65, 0.95])
    assert rf.snap_to_outline((0.48, 0.3), SQUARE, 0.05) == pytest.approx((0.5, 0.3))


def test_distribute_by_spacing_quantity_or_both():
    assert rf.distribute(0.0, 1.0, rf.SPACING, 0.3, 0) == pytest.approx([0.05, 0.35, 0.65, 0.95])
    assert rf.distribute(0.0, 1.0, rf.QUANTITY, 0.0, 5) == pytest.approx([0.0, 0.25, 0.5, 0.75, 1.0])
    assert rf.distribute(0.0, 1.0, rf.BOTH, 0.2, 3) == pytest.approx([0.3, 0.5, 0.7])


def test_split_path_and_segment_length():
    path = [(0.0, 0.25), (0.0, 0.0), (10.0, 0.0), (10.0, 0.25)]
    pieces = rf.split_path(path, 9.0, 0.75)
    assert len(pieces) == 2
    assert rf.path_length(pieces[0]) == pytest.approx(9.0)
    assert rf.path_length(pieces[0]) + rf.path_length(pieces[1]) - 0.75 == pytest.approx(10.5)
    # the first leg grows at its free end, the last one too
    pts = rf.set_segment_length([(0, 1), (0, 0), (2, 0)], 0, 1.5)
    assert pts == [(0.0, 1.5), (0, 0), (2, 0)]
    pts = rf.set_segment_length([(0, 0), (2, 0), (2, 1)], 1, 1.5)
    assert pts == [(0, 0), (2, 0), (2.0, 1.5)]
    assert rf.clamp_inside((2.0, 0.5), SQUARE) == pytest.approx((0.5, 0.5))


def test_merge_collinear_edges():
    edges = [((0, 0), (1, 0)), ((1, 0), (3, 0)), ((3, 0), (3, 2))]
    assert sorted(rf.merge_collinear(edges)) == sorted([((0, 0), (3, 0)), ((3, 0), (3, 2))])


# --- zones -------------------------------------------------------------------
MAIN = {"mx": rf.SPACING, "nx": 15, "sx": 0.20}


def test_positions_without_zones_is_the_single_distribution():
    assert rf.mesh_positions(0.0, 1.0, MAIN, "x") == rf.distribute(0.0, 1.0, rf.SPACING, 0.2, 15)


def test_zones_each_with_its_distribution_and_shared_limit_once():
    zones = [{"a": -1.0, "b": 1.0, "m": rf.QUANTITY, "n": 3, "s": 0.2},
             {"a": 1.0, "b": 3.0, "m": rf.QUANTITY, "n": 5, "s": 0.2}]
    pos = rf.mesh_positions(-0.9, 2.9, dict(MAIN, zx=zones), "x")
    assert pos == pytest.approx([-0.9, 0.05, 1.0, 1.475, 1.95, 2.425, 2.9])
    # the shared bar goes to the zone with a quantity: zone 2 keeps its 5
    owners = [k for _, k in rf.zone_positions(-0.9, 2.9, dict(MAIN, zx=zones), "x")]
    assert owners == [0, 0, 1, 1, 1, 1, 1]


def test_auto_zones_split_at_the_shape_changes_with_the_same_density():
    q = {"mx": rf.QUANTITY, "nx": 27, "sx": 0.2}
    zones = rf.auto_zones([-2.6, -0.9, 1.1, 2.6], -2.6, 2.6, q, "x")
    assert [(z["a"], z["b"]) for z in zones] == [(-2.6, -0.9), (-0.9, 1.1), (1.1, 2.6)]
    assert all(z["m"] == rf.SPACING and z["s"] == pytest.approx(0.2) for z in zones)


def test_split_move_and_remove_a_limit():
    zones = rf.split_zone([], 0.5, MAIN, "x", 0.0, 2.0)
    assert [(z["a"], z["b"]) for z in zones] == [(0.0, 0.5), (0.5, 2.0)]
    zones = rf.split_zone(zones, 1.2, MAIN, "x", 0.0, 2.0)
    zones = rf.move_limit(zones, 0, 0.9)
    assert [(z["a"], z["b"]) for z in zones] == [(0.0, 0.9), (0.9, 1.2), (1.2, 2.0)]
    assert rf.move_limit(zones, 0, 5.0)[0]["b"] == pytest.approx(1.15)
    zones = rf.remove_limit(zones, 1)
    assert [(z["a"], z["b"]) for z in zones] == [(0.0, 0.9), (0.9, 2.0)]
    assert rf.remove_limit(zones, 0) == []


@pytest.mark.parametrize("text, expected", [
    ("15", (rf.QUANTITY, 15, 0.2)), ("@0.20", (rf.SPACING, 1, 0.2)), ("0,25", (rf.SPACING, 1, 0.25)),
    ("15@0.20", (rf.BOTH, 15, 0.2)), ("15 barras", (rf.QUANTITY, 15, 0.2)),
])
def test_zone_text_round_trip(text, expected):
    assert rf.parse_zone_text(text) == expected
    assert rf.parse_zone_text(rf.zone_text(*expected)) == expected


def test_zone_text_rejects_garbage():
    with pytest.raises(ValueError):
        rf.parse_zone_text("abc")


def _rect(u0, z0, u1, z1):
    return [(u0, z0), (u1, z0), (u1, z1), (u0, z1)]


WALL = _rect(-0.075, 0.0, 0.075, 1.4)
INNER = _rect(-0.035, 0.04, 0.035, 1.36)


def test_two_faces_anchor_and_outward_leg():
    below = _rect(-0.21, -0.56, 0.21, -0.04)
    (p0, a0), (p1, a1) = rf.wall_mesh_paths(WALL, INNER, below, 2, 0.30, 0.15)
    assert abs(a0 - 0.30) < 1e-9 and abs(a1 - 0.30) < 1e-9
    assert p0[0] == (-0.035, 1.36)
    assert abs(p0[-2][1] + 0.30) < 1e-9
    assert abs(p0[-1][0] + 0.185) < 1e-9
    assert abs(p1[-1][0] - 0.185) < 1e-9


def test_anchor_clamped_to_bottom_cover_and_leg_inward():
    below = _rect(-0.06, -0.20, 0.06, -0.04)
    (p0, a0), (p1, a1) = rf.wall_mesh_paths(WALL, INNER, below, 2, 1.0, 0.10)
    assert abs(a0 - 0.175) < 1e-9 and abs(a1 - 0.20) < 1e-9  # crossing legs: one lies on the other
    assert p0[-1][0] > p0[-2][0]  # no room outwards: the leg turns inwards


def test_no_element_below():
    res = rf.wall_mesh_paths(WALL, INNER, None, 2)
    assert [r[1] for r in res] == [0.0, 0.0]
    assert len(res[0][0]) == 2


def test_one_layer_in_the_middle():
    below = _rect(-0.21, -0.56, 0.21, -0.04)
    (p0, a0), = rf.wall_mesh_paths(WALL, INNER, below, 1, None, 0.0)
    assert abs(p0[0][0]) < 1e-9 and abs(a0 - 0.56) < 1e-9


def test_mesh_legs_same_side_do_not_cross():
    below = _rect(-0.035, -0.36, 0.135, -0.04)  # beam flush with the wall's left face
    (p0, a0), (p1, a1) = rf.wall_mesh_paths(WALL, INNER, below, 2, 0.30, 0.15)
    assert p0[-1][0] > p0[-2][0] and p1[-1][0] > p1[-2][0]
    assert abs(p0[-1][1] - (p1[-1][1] + 0.025)) < 1e-9
    assert abs(a0 - 0.275) < 1e-9


def test_equal_positions():
    assert rf.equal_positions(0.0, 1.0, rf.SPACING, 0.30, 1) == pytest.approx([0, 0.25, 0.5, 0.75, 1.0])
    assert rf.equal_positions(0.0, 0.9, rf.SPACING, 0.30, 1) == pytest.approx([0, 0.3, 0.6, 0.9])
    assert rf.equal_positions(0.0, 1.0, rf.QUANTITY, 0.0, 3) == pytest.approx([0, 0.5, 1.0])
    assert rf.equal_positions(0.0, 1.0, rf.BOTH, 0.2, 3) == pytest.approx([0.3, 0.5, 0.7])
