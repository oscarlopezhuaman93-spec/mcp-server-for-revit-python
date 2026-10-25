import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "revit_mcp"))
import slab_create as sc  # noqa: E402

RECT = [(0.0, 0.0), (6.0, 0.0), (6.0, 4.0), (0.0, 4.0)]


def total_area(ps):
    return sum(abs(sc.area(p["poly"])) for p in ps)


def test_flat_and_one_way():
    flat = sc.pieces(RECT, sc.FLAT, 0.0)
    assert len(flat) == 1 and flat[0]["arrow"] is None
    one = sc.pieces(RECT, sc.ONE, 0.25)  # rises across the long side: along y
    (ax, ay), (bx, by) = one[0]["arrow"]
    assert abs(bx - ax) < 1e-6 and by - ay == pytest.approx(4.0)
    assert sc.ridge_rise(RECT, sc.ONE, 0.25) == pytest.approx(1.0)


def test_two_way_splits_at_the_ridge():
    two = sc.pieces(RECT, sc.TWO, 0.25)  # ridge along x at y = 2
    assert len(two) == 2 and total_area(two) == pytest.approx(24.0)
    for p in two:
        (ax, ay), (bx, by) = p["arrow"]
        assert by == pytest.approx(2.0)  # both rise to the ridge
    assert sc.ridge_rise(RECT, sc.TWO, 0.25) == pytest.approx(0.5)
    turned = sc.pieces(RECT, sc.TWO, 0.25, angle=90.0)  # ridge along y at x = 3
    assert all(p["arrow"][1][0] == pytest.approx(3.0) for p in turned)
    by_height = sc.pieces(RECT, sc.TWO, 0.0, ridge_height=0.6)
    assert by_height[0]["slope"] == pytest.approx(0.3)


def test_four_way_hip_and_an_l_shape():
    four = sc.pieces(RECT, sc.FOUR, 0.25)
    assert len(four) == 4 and total_area(four) == pytest.approx(24.0)
    assert all(p["slope"] == pytest.approx(0.25) for p in four)
    L = [(0, 0), (6, 0), (6, 2), (2, 2), (2, 5), (0, 5)]
    two = sc.pieces(L, sc.TWO, 0.2, angle=0.0)
    assert total_area(two) == pytest.approx(abs(sc.area(L)))


def test_clip():
    half = sc.clip(RECT, 1.0, 0.0, 2.0)  # x <= 2
    assert abs(sc.area(half)) == pytest.approx(8.0)
    assert sc.clip(RECT, 1.0, 0.0, -1.0) == []


def test_clean_drops_tiny_sides_and_straight_points():
    poly = [(0, 0), (2, 0), (2.0004, 0.0), (4, 0), (4, 3), (0, 3)]
    assert sc.clean(poly) == [(0, 0), (4, 0), (4, 3), (0, 3)]

def test_push_edge_moves_only_the_high_side():
    from revit_mcp.slab_create import push_edge
    poly = [(0.0, 18.978), (4.0, 18.978), (4.0, 22.728), (0.0, 22.728)]
    out = push_edge(poly, (0.0, 1.0), 22.728, 22.928)
    assert out == [(0.0, 18.978), (4.0, 18.978), (4.0, 22.928), (0.0, 22.928)]
