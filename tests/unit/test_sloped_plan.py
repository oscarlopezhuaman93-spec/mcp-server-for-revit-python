# -*- coding: utf-8 -*-
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "revit_mcp"))
import sloped_plan as sp  # noqa: E402

S = math.hypot(4.0, 2.0)
UP_A = (4.0 / S, 2.0 / S)      # beam a: eave at u=0, rising to the right
UP_B = (-4.0 / S, 2.0 / S)     # beam b: eave at u=8, rising to the left


class SlopedPlanTest(unittest.TestCase):
    def test_top_bar_meets_at_ridge_with_plumb_hooks(self):
        path = sp.top_path(((0.0, 0.0), UP_A), ((8.0, 0.0), UP_B), 0.15, 0.15)
        self.assertEqual(path[0], (0.0, -0.15))
        self.assertAlmostEqual(path[2][0], 4.0)
        self.assertAlmostEqual(path[2][1], 2.0)
        self.assertEqual(path[-1], (8.0, -0.15))

    def test_bottom_bar_crosses_and_anchors_along_other_beam(self):
        row = ((0.0, -0.1), UP_A)
        other_top = ((8.0, 0.0), UP_B)
        path = sp.anchored_path(row, other_top, 0.1, 0.5, 0.02)
        bend, end = path[-2], path[-1]
        self.assertGreater(bend[0], 4.0)  # past the ridge
        self.assertAlmostEqual(math.hypot(end[0] - bend[0], end[1] - bend[1]), 0.5)
        self.assertGreater(end[0], bend[0])  # down the other beam

    def test_split_top_laps_past_ridge(self):
        path = sp.top_path(((0.0, 0.0), UP_A), ((8.0, 0.0), UP_B), 0.0, 0.0)
        first, second = sp.split_top(path, 1, 0.6)
        self.assertAlmostEqual(sp.length(first), S + 0.6)
        self.assertAlmostEqual(sp.length(second), S + 0.6)

    def test_resize_path_keeps_directions(self):
        pts = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)]
        out = sp.resize_path(pts, [2.0, 0.5])
        self.assertEqual(out, [(0.0, 0.0), (2.0, 0.0), (2.0, 0.5)])
        self.assertEqual(sp.segment_lengths(out), [2.0, 0.5])


if __name__ == "__main__":
    unittest.main()
