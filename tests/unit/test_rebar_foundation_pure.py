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
