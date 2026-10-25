# -*- coding: utf-8 -*-
import pytest

from revit_mcp.stair_plan import plan_stair, riser_count, comfort


def test_riser_count():
    assert riser_count(2.80, 0.177) == 16
    assert riser_count(2.80, 0.175) == 16


class TestPlan:
    # the U stair of the prototype: up +y, across +x, down -y
    runs = [((0.6, 0.0), (0.6, 1.5)), ((1.2, 2.1), (2.2, 2.1)), ((2.8, 1.5), (2.8, 0.25))]

    def test_risers_shared_and_heights(self):
        p = plan_stair(self.runs, 2.80, 0.177, 0.25, 1.20)
        assert p["total"] == 16 and abs(p["riser"] - 0.175) < 1e-9
        assert [r["risers"] for r in p["runs"]] == [6, 4, 6] and p["remaining"] == 0
        assert [round(r["z0"], 3) for r in p["runs"]] == [0.0, 1.05, 1.75]

    def test_landings(self):
        p = plan_stair(self.runs, 2.80, 0.177, 0.25, 1.20)
        l1 = p["landings"][0]
        xs = sorted(set(round(x, 3) for x, _ in l1["poly"]))
        ys = sorted(set(round(y, 3) for _, y in l1["poly"]))
        assert xs == [0.0, 1.2] and ys == [1.5, 2.7] and abs(l1["z"] - 1.05) < 1e-9
        l2 = p["landings"][1]
        assert sorted(set(round(x, 3) for x, _ in l2["poly"])) == [2.2, 3.4]
        assert [r["top_tread"] for r in p["runs"]] == [True, True, False]

    def test_two_run_u_landing_spans_both(self):
        runs = [((0.6, 0.0), (0.6, 2.0)), ((2.0, 2.0), (2.0, 0.0))]
        p = plan_stair(runs, 2.80, 0.177, 0.25, 1.20)
        xs = sorted(set(round(x, 3) for x, _ in p["landings"][0]["poly"]))
        assert xs == [0.0, 2.6]


def test_comfort():
    value, ok = comfort(0.175, 0.25)
    assert abs(value - 0.60) < 1e-9 and ok
