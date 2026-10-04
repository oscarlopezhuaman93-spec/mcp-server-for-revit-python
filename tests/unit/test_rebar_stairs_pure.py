import importlib.util
import os

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "revit_mcp")
spec = importlib.util.spec_from_file_location("rst", os.path.join(ROOT, "rebar_stairs.py"))
rst = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rst)

def flat(pts):
    return [v for p in pts for v in p]


MM = {'3/8"': 9.5, '1/2"': 12.7}

# a flight cut (s along the run, z up): start pad, 3 steps of 0.25 x 0.18,
# waist 0.15, landing 1.0 long at the top
FLIGHT = [
    (0.0, 0.0), (0.2, 0.0),             # bottom of the start pad
    (0.95, 0.54), (2.0, 0.54),          # soffit up to the landing, landing bottom
    (2.0, 0.69),                        # landing end face
    (0.75, 0.69), (0.75, 0.54),         # landing top, last riser down
    (0.5, 0.54), (0.5, 0.36),           # tread, riser
    (0.25, 0.36), (0.25, 0.18),
    (0.0, 0.18),
]


def test_soffit_is_the_lower_boundary():
    assert flat(rst.soffit(FLIGHT)) == pytest.approx(flat([(0.0, 0.0), (0.2, 0.0), (0.95, 0.54), (2.0, 0.54)]))


def test_offset_up_keeps_the_waist_parallel():
    low = rst.offset_up([(0.0, 0.0), (1.0, 0.0), (2.0, 1.0)], 0.1)
    assert low[0] == pytest.approx((0.0, 0.1))
    assert low[-1][1] - low[-1][0] == pytest.approx(-1.0 + 0.1 * 2 ** 0.5)


def test_noses_of_the_steps():
    # the landing edge is a nose too (the last step onto it)
    assert flat(rst.noses(FLIGHT)) == pytest.approx(flat([(0.0, 0.18), (0.25, 0.36), (0.5, 0.54), (0.75, 0.69)]))


def test_end_piece_kinds():
    assert flat(rst.end_piece((0.0, 0.1), (-1.0, 0.0), "down", 0.3, 0.15, True)) == pytest.approx(flat([(0.0, -0.2), (-0.15, -0.2)]))
    assert flat(rst.end_piece((2.0, 0.6), (1.0, 0.0), "beyond", 0.3, 0.1, True)) == pytest.approx(flat([(2.3, 0.6), (2.3, 0.7)]))
    assert flat(rst.end_piece((2.0, 0.6), (1.0, 0.0), "free", 0.0, 0.08, False)) == pytest.approx(flat([(2.0, 0.52)]))


def test_end_kind_finds_a_footing_below_and_a_wall_beyond():
    footing = ("ZAPATA", [(-0.5, -0.4), (0.5, -0.4), (0.5, 0.0), (-0.5, 0.0)])
    wall = ("MURO", [(2.0, -1.0), (2.2, -1.0), (2.2, 3.0), (2.0, 3.0)])
    kind, label, room = rst.end_kind((0.03, 0.03), (-1.0, 0.0), [footing, wall])
    assert (kind, label) == ("down", "ZAPATA") and room == pytest.approx(0.43)
    kind, label, room = rst.end_kind((1.97, 0.6), (1.0, 0.0), [footing, wall])
    assert (kind, label) == ("beyond", "MURO") and room == pytest.approx(0.23, abs=0.03)
    assert rst.end_kind((1.0, 0.6), (1.0, 0.0), [footing])[0] == "free"


def test_plan_tramo_bars():
    st = rst.default_settings()
    st[rst.STEP]["on"] = True
    footing = ("ZAPATA", [(-0.5, -0.4), (0.5, -0.4), (0.5, 0.0), (-0.5, 0.0)])
    bars = rst.plan_tramo(FLIGHT, [footing], st, 0.15, MM)
    (inf,) = bars[rst.INF]
    # into the footing at the start, a free leg up at the landing end
    assert inf["path"][0][1] < -0.2 and inf["path"][-1][1] > inf["path"][-2][1]
    assert bars["ends"]["0:inferior:start"][0] == "down"
    assert len(bars[rst.SUP]) >= 1  # short flight: the bastones join
    assert len(bars[rst.STEP]) == 4
    assert len(bars[rst.TEMP]) > 5


def test_stations_and_cross_sets():
    pts = rst.stations([(0.0, 0.0), (1.0, 0.0), (2.0, 1.0)], 0.25, 0.05)
    sets = rst.group_cross([{"at": p} for _, p, _ in pts])
    assert sum(n for _, n, _, _ in sets) == len(pts)
    assert len(sets) >= 2  # one on the level piece, one on the slope


def test_anchorage_values():
    assert rst.hook_leg(0.0127) == pytest.approx(0.16)
    assert rst.hooked_anchorage(0.0127) == pytest.approx(0.28)
