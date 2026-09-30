# -*- coding: utf-8 -*-
"""Tests for the pure rebar ("Acero") helpers."""
import pytest

from revit_mcp.rebar_spec import (
    SpecError,
    bar_weight_kg_per_m,
    group_runs,
    layout_rectangular_bars,
    parse_diameter,
    parse_distribution,
    parse_longitudinal,
    stirrup_positions,
)


class TestParsing:
    @pytest.mark.parametrize(
        "text, key",
        [('5/8"', '5/8"'), ("Ø3/8", '3/8"'), ("1 3/8 pulg", '1 3/8"'), ("12mm", "12mm"), ("8 mm", "8mm")],
    )
    def test_diameter(self, text, key):
        assert parse_diameter(text) == key

    def test_longitudinal_keeps_count_next_to_symbol(self):
        # "8Ø5/8" must not become "85/8".
        assert parse_longitudinal('8Ø5/8"') == [(8, '5/8"')]
        assert parse_longitudinal("8 5/8") == [(8, '5/8"')]

    def test_longitudinal_mixed_largest_first(self):
        assert parse_longitudinal('4Ø5/8" + 4Ø3/4"') == [(4, '3/4"'), (4, '5/8"')]

    @pytest.mark.parametrize("text", ["", '5Ø5/8"', "3 1/2", "8x7/8"])
    def test_longitudinal_errors(self, text):
        with pytest.raises(SpecError):
            parse_longitudinal(text)

    def test_distribution_meters_and_cm(self):
        assert parse_distribution("1@.05, 10@.10, rto@.20") == ([(1, 0.05), (10, 0.10)], 0.20)
        zones, rest = parse_distribution("1@5, 10@10, R@20")
        assert zones == [(1, pytest.approx(0.05)), (10, pytest.approx(0.10))]
        assert rest == pytest.approx(0.20)

    def test_distribution_needs_rest(self):
        with pytest.raises(SpecError):
            parse_distribution("1@.05, 10@.10")


class TestStirrups:
    """The stirrup distribution rules agreed with the user
    (OL-STR.tab/Structural.panel/Acero.pushbutton/REGLAS_ACERO.md): if one
    of these fails, the rule was broken - fix the code, not the test."""

    def test_both_ends_mirror_each_other(self):
        pos = stirrup_positions(2.65, [(1, 0.05), (10, 0.10)], 0.20)
        assert len(pos) == 24
        assert pos[0] == pytest.approx(0.05)
        assert pos[-1] == pytest.approx(2.60)
        for a, b in zip(pos, reversed(pos)):
            assert a + b == pytest.approx(2.65)

    def test_one_set_per_zone_and_end_as_written(self):
        from revit_mcp.rebar_spec import stirrup_sets

        sets = stirrup_sets(4.0, [(1, 0.05), (5, 0.10)], 0.20)
        expected = [
            (0.05, 1, 0.0, 0, 1),     # 1@0.05: a single stirrup
            (0.15, 5, 0.10, 1, 1),    # 5@0.10 counted from it
            (0.75, 7, 0.20, 2, 1),    # rto@0.20 up from the last of 5@0.10 (0.75 ... 1.95)
            (2.05, 7, 0.20, 2, -1),   # rto@0.20 down from the top 5@0.10 (3.25 ... 2.05)
            (3.45, 5, 0.10, 1, -1),   # 5@0.10 at the top (3.45 ... 3.85)
            (3.95, 1, 0.0, 0, -1),    # 1@0.05 under the beam
        ]
        assert len(sets) == len(expected)
        for got, want in zip(sets, expected):
            assert got[:3] == pytest.approx(want[:3]) and got[3:] == want[3:]

    @pytest.mark.parametrize("length", [4.0, 2.5, 3.25, 2.4, 4.65, 3.4, 2.1, 1.95, 2.05])
    def test_each_end_as_written_and_no_gap_over_the_rest(self, length):
        from revit_mcp.rebar_spec import STIRRUP_MERGE_GAP

        zones, rest = [(1, 0.05), (5, 0.10)], 0.20
        pos = stirrup_positions(length, zones, rest)
        ends = [0.05, 0.15, 0.25, 0.35, 0.45, 0.55]
        assert pos[:6] == pytest.approx(ends)  # from the bottom
        assert [length - p for p in reversed(pos)][:6] == pytest.approx(ends)  # from the top
        gaps = [b - a for a, b in zip(pos, pos[1:])]
        assert max(gaps) <= rest + STIRRUP_MERGE_GAP + 1e-9
        assert min(gaps) >= STIRRUP_MERGE_GAP - 1e-9  # two stirrups never on the same spot
        # the rest runs every 0.20 from the last stirrup of 5@0.10 of each
        # end (a stirrup right at the middle only fills the leftover)
        for p in pos:
            d = min(p, length - p)  # from the nearer end
            if d > 0.55 + 1e-6 and abs(p - length / 2.0) > 1e-6:
                k = (d - 0.55) / 0.20
                assert k == pytest.approx(round(k))

    def test_short_column_zones_stop_at_the_middle(self):
        pos = stirrup_positions(0.9, [(1, 0.05), (5, 0.10)], 0.20)
        assert pos == pytest.approx([0.05, 0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85])

    def test_runs_of_constant_spacing(self):
        pos = stirrup_positions(2.65, [(1, 0.05), (10, 0.10)], 0.20)
        runs = group_runs(pos)
        assert sum(n for _, n, _ in runs) == len(pos)
        assert runs[0] == (pytest.approx(0.05), 11, pytest.approx(0.10))


class TestLayout:
    def test_corners_get_the_largest_bars(self):
        bars = layout_rectangular_bars(0.30, 0.90, 0.04, 0.0095, parse_longitudinal('4Ø3/4" + 6Ø5/8"'))
        assert len(bars) == 10
        assert [k for _, _, k in bars[:4]] == ['3/4"'] * 4
        # the 6 extra bars go to the long faces (x = +/-), 3 per face
        assert sum(1 for x, _, k in bars[4:] if x < 0) == 3

    def test_section_too_small(self):
        with pytest.raises(SpecError):
            layout_rectangular_bars(0.08, 0.08, 0.04, 0.0095, parse_longitudinal("4 5/8"))


def test_weight_matches_peruvian_tables():
    assert bar_weight_kg_per_m('3/8"') == pytest.approx(0.56, abs=0.01)
    assert bar_weight_kg_per_m('5/8"') == pytest.approx(1.55, abs=0.01)
    assert bar_weight_kg_per_m('1"') == pytest.approx(3.97, abs=0.01)


def test_weight_table_matches_the_supplier_table():
    from revit_mcp.rebar_spec import read_weight_table

    table = read_weight_table()
    expected = {  # key: (area mm2, nominal kg/m, minimum kg/m)
        "6mm": (28, 0.222, 0.207), "8mm": (50, 0.395, 0.371), '3/8"': (71, 0.56, 0.526),
        "12mm": (113, 0.888, 0.835), '1/2"': (129, 0.994, 0.934), '5/8"': (199, 1.552, 1.459),
        '3/4"': (284, 2.235, 2.101), '1"': (510, 3.973, 3.735), '1 3/8"': (1006, 7.907, 7.433),
    }
    assert set(table) == set(expected)
    for key, (area, nominal, minimum) in expected.items():
        assert (table[key]["area_mm2"], table[key]["nominal"], table[key]["minimum"]) == (area, nominal, minimum)
        assert bar_weight_kg_per_m(key) == nominal
    # not in the table: steel density times the area
    assert bar_weight_kg_per_m('1/4"') == pytest.approx(0.249, abs=0.001)


class TestSplices:
    # the C-8 stack: CIST 0..3.45 (clear 0..3.25), S02 3.45..6.15 (clear
    # to 5.55), S01 6.15..10.95 (clear to 10.15)
    stories = [(0.0, 3.25), (3.45, 5.55), (6.15, 10.15)]

    def test_short_bar_is_not_cut(self):
        from revit_mcp.rebar_spec import splice_pieces

        assert splice_pieces(0.0, 8.5, self.stories, 0.75) == ([(0.0, 8.5)], [])

    def test_lap_in_the_central_half_of_a_story(self):
        from revit_mcp.rebar_spec import splice_pieces

        pieces, warnings = splice_pieces(0.0, 10.95, self.stories, 0.75)
        assert not warnings
        assert len(pieces) == 2
        (s0, e0), (s1, e1) = pieces
        assert s0 == 0.0 and e1 == 10.95
        assert e0 - s0 <= 9.0 and e1 - s1 <= 9.0
        assert e0 - s1 == pytest.approx(0.75)  # the lap
        # inside the central half of S01's clear height (7.15 .. 9.15)
        assert 7.15 - 1e-9 <= s1 and e0 <= 9.15 + 1e-9
        assert e0 == pytest.approx(9.0)  # as high as the 9 m bar allows

    def test_every_piece_within_the_maximum(self):
        from revit_mcp.rebar_spec import splice_pieces

        stories = [(k * 3.0, k * 3.0 + 2.4) for k in range(8)]  # 8 floors of 3 m
        pieces, warnings = splice_pieces(0.0, 24.0, stories, 0.60)
        assert not warnings
        assert all(e - s <= 9.0 + 1e-9 for s, e in pieces)
        for (_, e), (s, _) in zip(pieces, pieces[1:]):
            assert e - s == pytest.approx(0.60)
            k = int(s // 3.0)
            assert k * 3.0 + 0.6 - 1e-9 <= s and e <= k * 3.0 + 1.8 + 1e-9  # central half

    def test_lower_bar_cranked_one_diameter_inwards(self):
        from revit_mcp.rebar_spec import bar_piece_points

        d = 0.015875
        pts = bar_piece_points(0.1, 0.0, d, 0.0, 9.0, 0.75, True)
        assert pts[0] == pytest.approx((0.1, 0.0, 0.0))
        assert pts[1] == pytest.approx((0.1, 0.0, 9.0 - 0.75 - 6 * d))  # crank starts 1:6 before the lap
        assert pts[2] == pytest.approx((0.1 - d, 0.0, 8.25))  # one diameter towards the center
        assert pts[3] == pytest.approx((0.1 - d, 0.0, 9.0))
        assert bar_piece_points(0.1, 0.0, d, 8.25, 10.95, 0.75, False) == [(0.1, 0.0, 8.25), (0.1, 0.0, 10.95)]

    def test_no_room_for_the_lap_warns(self):
        from revit_mcp.rebar_spec import splice_pieces

        pieces, warnings = splice_pieces(0.0, 10.0, [(0.0, 1.0), (1.2, 2.2)], 0.75)
        assert warnings and pieces[0] == (0.0, 9.0)


class TestBeams:
    def test_clear_spans_between_supports(self):
        from revit_mcp.rebar_spec import clear_spans

        # a beam 0 .. 5.0 resting on a beam before 0, a column at 4.0 .. 4.8
        assert clear_spans((0.0, 5.0), [(-0.3, 0.0), (4.0, 4.8), (5.0, 6.0)]) == pytest.approx([(0.0, 4.0), (4.8, 5.0)])
        assert clear_spans((0.0, 8.0), [(3.9, 4.1)]) == pytest.approx([(0.0, 3.9), (4.1, 8.0)])
        assert clear_spans((0.0, 2.0), [(-1.0, 0.03)]) == pytest.approx([(0.03, 2.0)])

    def test_lap_zones_top_centre_bottom_ends(self):
        from revit_mcp.rebar_spec import beam_lap_zones, confinement_length

        conf = confinement_length([(1, 0.05), (10, 0.10)])
        assert conf == pytest.approx(1.05)
        assert beam_lap_zones([(0.0, 6.0)], True, conf) == pytest.approx([(2.0, 4.0)])
        assert beam_lap_zones([(0.0, 6.0)], False, conf) == pytest.approx([(1.05, 2.0), (4.0, 4.95)])
        assert beam_lap_zones([(0.0, 3.0)], False, conf) == []  # the confinement takes the end thirds

    def test_spliced_top_bar_laps_in_a_central_third(self):
        from revit_mcp.rebar_spec import beam_lap_zones, lap_pieces

        spans = [(0.0, 6.0), (6.5, 12.5)]
        zones = beam_lap_zones(spans, True, 1.05)  # (2.0, 4.0) and (8.5, 10.5)
        pieces, warnings = lap_pieces(-0.2, 12.7, zones, 0.75)
        assert not warnings and len(pieces) == 3
        assert all(e - s <= 9.0 + 1e-9 for s, e in pieces)
        for (_, e), (s, _) in zip(pieces, pieces[1:]):
            assert e - s == pytest.approx(0.75)
            assert any(lo - 1e-9 <= s and e <= hi + 1e-9 for lo, hi in zones)  # each lap in a central third

    def test_hooks_turn_towards_the_other_face(self):
        from revit_mcp.rebar_spec import beam_bar_points

        top = beam_bar_points(0.1, 0.24, 0.0159, -0.26, 5.3, 0.0, False, 0.20, 0.20)
        expected = [(0.1, 0.04, -0.26), (0.1, 0.24, -0.26), (0.1, 0.24, 5.3), (0.1, 0.04, 5.3)]
        assert len(top) == 4
        for got, want in zip(top, expected):
            assert got == pytest.approx(want)
        bottom = beam_bar_points(0.1, -0.24, 0.0159, 0.0, 3.0, 0.0, False, 0.0, 0.15)
        assert bottom[-1] == pytest.approx((0.1, -0.09, 3.0))


def test_tie_hook_marks_c_and_s():
    from revit_mcp.rebar_spec import TIE_C, TIE_S, tie_hook_marks

    a, b = (0.0, 0.0), (0.2, 0.0)
    c = tie_hook_marks(a, b, TIE_C, 0.01)
    s = tie_hook_marks(a, b, TIE_S, 0.01)
    assert len(c) == 2 and len(s) == 2
    # C: both hooks bend to the same side (+y); S: to opposite sides
    assert max(p[1] for p in c[0]) > 0.015 and max(p[1] for p in c[1]) > 0.015
    assert max(p[1] for p in s[0]) > 0.015 and min(p[1] for p in s[1]) < -0.015
    # each hook turns back towards the inside of the tie
    assert c[0][-1][0] > 0 and c[1][-1][0] < 0.2
    assert tie_hook_marks(a, b, None, 0.01) == []


def test_hooked_tie_lines_wrap_both_bars():
    import math
    from revit_mcp.rebar_spec import TIE_C, TIE_S, hooked_tie_line, tie_leg_cm

    a, b, r = (0.0, 0.0), (0.2, 0.0), 0.019
    pa, pb = hooked_tie_line(a, b, r, TIE_C)
    assert pa == pytest.approx((0.0, -r)) and pb == pytest.approx((0.2, -r))  # both on one side
    pa, pb = hooked_tie_line(a, b, r, TIE_S)
    assert pa[1] < 0 < pb[1]  # crosses diagonally
    # each end sits one bend radius from its bar, square to the line
    dx, dy = pb[0] - pa[0], pb[1] - pa[1]
    length = math.hypot(dx, dy)
    for end, bar in ((pa, a), (pb, b)):
        assert math.hypot(end[0] - bar[0], end[1] - bar[1]) == pytest.approx(r, abs=1e-6)
        assert abs((bar[0] - end[0]) * dx + (bar[1] - end[1]) * dy) / length < 1e-6
    assert tie_leg_cm('3/8"', 180) == 6.5 and tie_leg_cm('5/8"', 180) == 6.5
    assert tie_leg_cm('3/4"', 180) == 8.0 and tie_leg_cm('3/8"', 135) == 7.5


def test_e060_class_b_laps():
    from revit_mcp.rebar_spec import e060_lap_cm

    # 1.3 x ld rounded up to 5 cm (5/8": 90.1 -> 95)
    expected = {'3/8"': 55, '1/2"': 75, '5/8"': 95, '3/4"': 110, '1"': 180, "6mm": 35}
    for key, cm in expected.items():
        assert e060_lap_cm(key) == cm, key


class TestHaunch:
    # a 0.25 wide haunch: top at +0.425, bottom from -0.425 (0.85 deep,
    # the reference) at s = 2.5 up to -0.075 (0.50 deep) at s = 0
    bottom = [(0.0, -0.075), (2.5, -0.425)]

    def test_interpolate(self):
        from revit_mcp.rebar_spec import interpolate

        assert interpolate(self.bottom, 1.25) == pytest.approx(-0.25)
        assert interpolate(self.bottom, -1.0) == pytest.approx(-0.075)
        assert interpolate(self.bottom, 9.0) == pytest.approx(-0.425)

    def test_stirrup_keeps_its_cover_on_both_faces(self):
        from revit_mcp.rebar_spec import haunch_polyline

        stirrup = [(-0.07, -0.37), (0.07, -0.37), (0.07, 0.37), (-0.07, 0.37)]  # drawn on the 0.85 section
        fitted = haunch_polyline(stirrup, -0.425, 0.425, -0.075, 0.425)  # at the 0.50 end
        assert fitted[0] == pytest.approx((-0.07, -0.02))  # 5.5 cm over the bottom, as drawn
        assert fitted[2] == pytest.approx((0.07, 0.37))  # the top corners stay

    def test_bottom_bar_follows_the_sloped_face(self):
        from revit_mcp.rebar_spec import follow_profile, haunch_shift, interpolate

        def shift(s):
            return haunch_shift(-0.36, -0.425, 0.425, interpolate(self.bottom, s), 0.425)

        path = [(0.07, -0.36, -0.2), (0.07, -0.36, 3.0)]
        out = follow_profile(path, shift, [0.0, 2.5])
        assert [p[2] for p in out] == pytest.approx([-0.2, 0.0, 2.5, 3.0])
        assert out[1][1] == pytest.approx(-0.36 + 0.35)  # 0.50 end
        assert out[2][1] == pytest.approx(-0.36)  # 0.85 end
        # a top bar keeps its height: no point left in line with its neighbours
        flat = follow_profile([(0.07, 0.36, -0.2), (0.07, 0.36, 3.0)], lambda s: 0.0, [0.0, 2.5])
        assert flat == [(0.07, 0.36, -0.2), (0.07, 0.36, 3.0)]


def test_a_diameter_added_to_the_table_becomes_known(tmp_path, monkeypatch):
    import revit_mcp.rebar_spec as rs

    monkeypatch.setattr(rs, "BAR_DIAMETERS_MM", dict(rs.BAR_DIAMETERS_MM))
    table_file = tmp_path / "pesos.csv"
    table_file.write_text(u'DIAMETRO;AREA;NOMINAL;MINIMO\n7/8";387;3.04;2.86\n', encoding="utf-8")
    table = rs.read_weight_table(str(table_file))
    assert table['7/8"']["nominal"] == 3.04
    assert rs.parse_diameter(u"Ø7/8") == '7/8"'
    assert rs.BAR_DIAMETERS_MM['7/8"'] == pytest.approx(22.2, abs=0.05)
    assert '7/8"' in rs.bar_diameter_keys()


@pytest.mark.parametrize(
    "name, key",
    [
        (u'SRB_ACERO DE REFUERZO FY=4200 KG/CM2_Ø5/8"_ZAPATA_Z-1', '5/8"'),
        (u"Ø12mm_COLUMNA C-8", "12mm"),
        (u'SRB_..._Ø1/4"_LOSA ALIGERADA_B5', '1/4"'),
        (u'Ø1 3/8"_COLUMNA', '1 3/8"'),
        (u"13M", None),
        (u"Armadura estructural 1", None),
    ],
)
def test_diameter_from_bar_type_name(name, key):
    from revit_mcp.rebar_spec import diameter_from_name

    assert diameter_from_name(name) == key


def test_stack_lifts_puts_stirrups_side_by_side():
    from revit_mcp.rebar_spec import stack_lifts

    d = 0.009525
    # drawn: a confinement tie, two edge stirrups, a confinement stirrup
    items = [("confinamiento", d, True), ("borde", d, False), ("borde", d, False),
             ("confinamiento", 0.008, False)]
    assert stack_lifts(items) == pytest.approx([-d, 0.0, d, 2 * d])  # the tie just below
    assert stack_lifts([("borde", d, False)]) == [0.0]
    # three ties: one layer just below the stirrup, not stacked
    ties = [("borde", d, False)] + [("confinamiento", d, True)] * 3
    assert stack_lifts(ties) == pytest.approx([0.0, -d, -d, -d])
    assert stack_lifts([]) == []


class TestPlaceBar:
    # a 35 x 80 column, edge stirrup 3/8" on a 4 cm cover: outer face at
    # x = +-0.135, y = +-0.36; a 3/4" bar sits at 0.135 - 0.0095 - 0.0095
    outline = [(-0.135, -0.36), (0.135, -0.36), (0.135, 0.36), (-0.135, 0.36)]
    stirrups = [(outline, '3/8"')]
    edge = 0.135 - 0.009525 - 0.009525

    def test_sits_against_the_face_and_in_the_corner(self):
        from revit_mcp.rebar_spec import place_bar

        x, y = place_bar((0.07, 0.10), '3/4"', [], self.stirrups)  # 4.6 cm off the face
        assert x == pytest.approx(self.edge) and y == pytest.approx(0.10)
        x, y = place_bar((0.10, 0.31), '3/4"', [], self.stirrups)  # near the corner
        assert (x, y) == pytest.approx((self.edge, 0.36 - 0.009525 - 0.009525))

    def test_lines_up_with_the_facing_bar(self):
        from revit_mcp.rebar_spec import place_bar

        bars = [(-self.edge, -0.12, '3/4"')]
        x, y = place_bar((0.11, -0.135), '3/4"', bars, self.stirrups)
        assert x == pytest.approx(self.edge) and y == pytest.approx(-0.12)

    def test_far_from_stirrups_only_lines_up(self):
        from revit_mcp.rebar_spec import place_bar

        bars = [(0.0, 0.2, '5/8"')]
        assert place_bar((0.01, -0.05), '5/8"', bars, self.stirrups) == pytest.approx((0.0, -0.05))


def test_nearest_diameter():
    from revit_mcp.rebar_spec import nearest_diameter

    assert nearest_diameter(9.5) == '3/8"'
    assert nearest_diameter(6.4) == '1/4"'
    assert nearest_diameter(35.8) == '1 3/8"'
    assert nearest_diameter(22.2) is None


class TestSpaceSeparatedDistribution:
    def test_like_the_reference_tool(self):
        zones, rest = parse_distribution("1@5 6@10 Rto@25")
        assert zones == [(1, pytest.approx(0.05)), (6, pytest.approx(0.10))]
        assert rest == pytest.approx(0.25)

    def test_garbage_between_tokens(self):
        with pytest.raises(SpecError):
            parse_distribution("1@5 y 6@10 rto@25")


class TestDrawing:
    def test_auto_design_stirrup_matches_cover(self):
        from revit_mcp.rebar_spec import auto_design, stirrup_centerline

        design = auto_design(0.30, 0.60, 0.04, '3/8"', parse_longitudinal("8 5/8"))
        kind, poly, wrap, is_open = design["stirrups"][0]
        assert kind == "borde" and not is_open
        line = stirrup_centerline(poly, design["bars"], '3/8"', wrap)
        xs = [x for x, _ in line]
        # stirrup centerline sits cover + half the stirrup inside the face
        assert max(xs) == pytest.approx(0.15 - 0.04 - 0.009525 / 2)

    def test_offset_trapezoid(self):
        from revit_mcp.rebar_spec import offset_polygon_outward, polygon_signed_area

        trap = [(-0.3, -0.1), (0.3, -0.1), (0.25, 0.1), (-0.25, 0.1)]
        out = offset_polygon_outward(trap, 0.01)
        assert polygon_signed_area(out) > polygon_signed_area(trap)
        assert len(out) == 4

    def test_tie_wraps_both_bars(self):
        from revit_mcp.rebar_spec import tie_centerline

        bars = [(-0.1, 0.0, '5/8"'), (0.1, 0.0, '5/8"')]
        a, b = tie_centerline((-0.1, 0.0), (0.1, 0.0), bars, '3/8"')
        assert b[0] - a[0] == pytest.approx(0.2 + 0.015875 + 0.009525)

    def test_roundtrip_and_blank(self):
        from revit_mcp.rebar_spec import design_from_text, design_to_text, empty_design

        design = empty_design()
        design["bars"] = [(0.1, -0.2, '3/4"')]
        design["stirrups"] = [("confinamiento", [(0, 0), (0.1, 0), (0.1, 0.1)], 0.008, True)]
        design["ties"] = [("borde", (0, 0), (0.1, 0.1))]
        again = design_from_text(design_to_text(design))
        assert again["bars"] == [(0.1, -0.2, '3/4"')]
        assert again["stirrups"][0][0] == "confinamiento"
        assert again["stirrups"][0][2] == pytest.approx(0.008)
        assert again["stirrups"][0][3] is True
        assert again["ties"][0][0] == "borde"
        assert design_to_text(empty_design()) == ""
        assert design_from_text("") is None
        with pytest.raises(SpecError):
            design_from_text("{no es json")

    def test_shapes_follow_their_stirrups_and_ties(self):
        from revit_mcp.rebar_spec import (
            add_item, design_from_text, design_shapes, design_to_text, empty_design, remove_item)

        design = empty_design()
        square = [(0, 0), (0.1, 0), (0.1, 0.1), (0, 0.1)]
        add_item(design, "stirrups", ("borde", square, 0.008, False), "M_T1")
        add_item(design, "stirrups", ("confinamiento", square[:3], 0.0, True))
        add_item(design, "stirrups", ("confinamiento", square, 0.0, False), "M_T1")
        add_item(design, "ties", ("confinamiento", (0, 0), (0.1, 0)), "M_02")
        remove_item(design, "stirrups", 0)
        assert design_shapes(design, "stirrups") == [None, "M_T1"]
        again = design_from_text(design_to_text(design))
        assert design_shapes(again, "stirrups") == [None, "M_T1"]
        assert design_shapes(again, "ties") == ["M_02"]
        # a design without shapes (older drawings, auto_design) reads as None
        old = empty_design()
        old["stirrups"] = [("borde", square, 0.008, False)]
        assert design_shapes(old, "stirrups") == [None]

    def test_config_file_roundtrip(self):
        from revit_mcp.rebar_spec import (
            add_item, config_file_text, design_shapes, empty_design, read_config_file)

        design = empty_design()
        design["bars"] = [(-0.09, -0.34, '5/8"'), (0.09, 0.34, '5/8"')]
        add_item(design, "stirrups", ("borde", [(-0.09, -0.34), (0.09, -0.34), (0.09, 0.34)], 0.008, False), "M_T1")
        form = {"conf": '3/8"', "conf_dist": "", "edge": '3/8"', "edge_dist": "1@0.05, 5@0.10, rto@0.20",
                "cover": "4.0", "nucleo": "10"}
        text = config_file_text(u"C-3_0.25x0.80m", (25.0, 80.0), form, design)
        name, size, form2, design2 = read_config_file(text)
        assert name == u"C-3_0.25x0.80m"
        assert size == (25.0, 80.0)
        assert form2 == dict(form, anchor="", conf_type="", edge_type="", bars_type="", izaje="", ends="")  # blanks: automatic
        assert design2["bars"] == design["bars"]
        assert design_shapes(design2, "stirrups") == ["M_T1"]
        # a form without drawing reads with design None
        assert read_config_file(config_file_text(u"X", (20, 60), form, empty_design()))[3] is None
        for bad in ("{no es json", '{"otra": 1}', "[1, 2]"):
            with pytest.raises(SpecError):
                read_config_file(bad)

    def test_joint_positions(self):
        from revit_mcp.rebar_spec import joint_positions

        assert joint_positions(0.60, 0.10) == pytest.approx([0.1, 0.2, 0.3, 0.4, 0.5])
        assert joint_positions(0.12, 0.15) == pytest.approx([0.06])


def test_edge_vs_confinement_stirrups():
    from revit_mcp.rebar_spec import is_edge_stirrup

    bars = [(-0.1, -0.3, '5/8"'), (0.1, -0.3, '5/8"'), (0.1, 0.3, '5/8"'), (-0.1, 0.3, '5/8"'),
            (-0.1, 0.0, '5/8"'), (0.1, 0.0, '5/8"')]
    outer = [(-0.1, -0.3), (0.1, -0.3), (0.1, 0.3), (-0.1, 0.3)]
    inner = [(-0.1, -0.3), (0.1, -0.3), (0.1, 0.0), (-0.1, 0.0)]
    assert is_edge_stirrup(outer, bars)
    assert not is_edge_stirrup(inner, bars)


def test_first_format_drawings_still_load():
    from revit_mcp.rebar_spec import design_from_text

    old = ('{"v":1,"bars":[[-0.1,-0.3,"5/8\\""],[0.1,-0.3,"5/8\\""],[0.1,0.3,"5/8\\""],[-0.1,0.3,"5/8\\""]],'
           '"stirrups":[[[-0.1,-0.3],[0.1,-0.3],[0.1,0.3],[-0.1,0.3]]],"ties":[[[-0.1,0],[0.1,0]]]}')
    design = design_from_text(old)
    assert design["stirrups"][0][0] == "borde"  # perimeter one
    assert design["ties"][0][0] == "confinamiento"


class TestAutoTie:
    bars = [(-0.1, -0.35, '5/8"'), (0.1, -0.35, '5/8"'), (0.1, 0.35, '5/8"'), (-0.1, 0.35, '5/8"'),
            (-0.1, -0.117, '5/8"'), (0.1, -0.117, '5/8"'), (-0.1, 0.117, '5/8"'), (0.1, 0.117, '5/8"')]

    def test_click_near_middle_pair(self):
        from revit_mcp.rebar_spec import auto_tie

        a, b = auto_tie((0.02, 0.10), self.bars)
        assert {a, b} == {(-0.1, 0.117), (0.1, 0.117)}

    def test_never_along_a_face(self):
        from revit_mcp.rebar_spec import auto_tie

        # click right on the long face: the 4 bars at x=-0.1 are a face,
        # so the tie still crosses the section
        a, b = auto_tie((-0.1, 0.0), self.bars)
        assert abs(a[1] - b[1]) < 1e-9

    def test_no_facing_bars(self):
        from revit_mcp.rebar_spec import SpecError, auto_tie

        with pytest.raises(SpecError):
            auto_tie((0, 0), [(0, 0, '5/8"'), (0.1, 0.2, '5/8"')])


class TestMeasures:
    section = [(-0.15, -0.40), (0.15, -0.40), (0.15, 0.40), (-0.15, 0.40)]

    def test_perimeter_stirrup_measures_the_cover(self):
        from revit_mcp.rebar_spec import auto_design, rect_measures, stirrup_outline

        design = auto_design(0.30, 0.80, 0.04, '3/8"', parse_longitudinal("8 5/8"))
        kind, poly, wrap, _ = design["stirrups"][0]
        width, height, left, bottom = rect_measures(
            stirrup_outline(poly, design["bars"], '3/8"', wrap), self.section)
        assert (width, height) == (pytest.approx(0.22), pytest.approx(0.72))
        assert (left, bottom) == (pytest.approx(0.04), pytest.approx(0.04))

    def test_edit_roundtrip(self):
        from revit_mcp.rebar_spec import rect_from_measures, rect_measures, stirrup_outline

        pts = rect_from_measures(0.20, 0.30, 0.05, 0.25, self.section, '3/8"', 0.008)
        got = rect_measures(stirrup_outline(pts, [], '3/8"', 0.008), self.section)
        assert got == (pytest.approx(0.20), pytest.approx(0.30), pytest.approx(0.05), pytest.approx(0.25))

    def test_not_a_rectangle(self):
        from revit_mcp.rebar_spec import rect_measures

        assert rect_measures([(0, 0), (1, 0), (0.9, 1), (0.1, 1)], self.section) is None

    def test_too_small(self):
        from revit_mcp.rebar_spec import rect_from_measures

        with pytest.raises(SpecError):
            rect_from_measures(0.02, 0.30, 0.05, 0.05, self.section, '3/8"', 0.008)


class TestCoverFit:
    section = [(-0.15, -0.40), (0.15, -0.40), (0.15, 0.40), (-0.15, 0.40)]

    def test_drawn_sides_land_on_the_cover(self):
        from revit_mcp.rebar_spec import cover_bounds, snap_rect_to_cover

        bounds = cover_bounds(self.section, 0.04)  # (-0.11, -0.36, 0.11, 0.36)
        # left side 1 cm inside the cover line, top 1 cm past it: both snap;
        # the bottom is 40 cm away and stays
        got = snap_rect_to_cover((-0.10, 0.0, 0.05, 0.37), bounds)
        assert got == (pytest.approx(-0.11), 0.0, 0.05, pytest.approx(0.36))

    def test_resize_stays_inside(self):
        from revit_mcp.rebar_spec import cover_bounds, resize_rect_in_cover

        bounds = cover_bounds(self.section, 0.04)
        got = resize_rect_in_cover((-0.11, 0.20, 0.11, 0.36), 0.22, 0.30, bounds)
        assert got == (pytest.approx(-0.11), pytest.approx(0.06), pytest.approx(0.11), pytest.approx(0.36))
        # wider than fits -> clamped to the space inside the cover
        got = resize_rect_in_cover((-0.05, 0.0, 0.05, 0.1), 0.50, 0.10, bounds)
        assert got[2] - got[0] == pytest.approx(0.22)

    def test_vertices_roundtrip(self):
        from revit_mcp.rebar_spec import outer_rect, rect_vertices

        rect = (-0.11, -0.36, 0.11, 0.36)
        pts = rect_vertices(rect, '3/8"', 0.0079375)
        assert outer_rect(pts, [], '3/8"', 0.0079375) == tuple(pytest.approx(v) for v in rect)


class TestCoverLimit:
    section = [(-0.15, -0.40), (0.15, -0.40), (0.15, 0.40), (-0.15, 0.40)]

    def test_corner_past_the_cover_is_refused(self):
        from revit_mcp.rebar_spec import fit_vertex

        # cover 4 cm + 3/8" stirrup: free corners must stay within |x| <= 0.1005
        assert fit_vertex((0.12, 0.0), self.section, 0.04, '3/8"') is None
        assert fit_vertex((0.0, -0.39), self.section, 0.04, '3/8"') is None

    def test_corner_near_the_limit_snaps_onto_it(self):
        from revit_mcp.rebar_spec import fit_vertex

        x, y = fit_vertex((0.09, 0.0), self.section, 0.04, '3/8"')
        assert x == pytest.approx(0.15 - 0.04 - 0.009525) and y == 0.0

    def test_trapezoid_uses_the_real_outline(self):
        from revit_mcp.rebar_spec import fit_vertex

        trap = [(-0.355, -0.125), (0.355, -0.125), (0.385, 0.125), (-0.385, 0.125)]
        assert fit_vertex((0.30, 0.0), trap, 0.04, '3/8"', rectangular=False) == (0.30, 0.0)
        assert fit_vertex((0.34, -0.07), trap, 0.04, '3/8"', rectangular=False) is None

    def test_whole_stirrup_check(self):
        from revit_mcp.rebar_spec import stirrup_inside_cover

        inside = [(-0.10, -0.35), (0.10, -0.35), (0.10, 0.35), (-0.10, 0.35)]
        outside = [(-0.13, -0.35), (0.10, -0.35), (0.10, 0.35), (-0.13, 0.35)]
        assert stirrup_inside_cover(inside, [], '3/8"', 0.0, self.section, 0.04)
        assert not stirrup_inside_cover(outside, [], '3/8"', 0.0, self.section, 0.04)


class TestOpenStirrup:
    # U drawn through three bars' centers, open to the left (like a bracket)
    u = [(-0.10, 0.30), (0.10, 0.30), (0.10, -0.30), (-0.10, -0.30)]

    def test_offset_goes_away_from_the_inside(self):
        from revit_mcp.rebar_spec import offset_polyline_outward

        out = offset_polyline_outward(self.u, 0.01)
        assert out[0] == pytest.approx((-0.10, 0.31))      # top leg moved up
        assert out[1] == pytest.approx((0.11, 0.31))       # corner mitered out
        assert out[2] == pytest.approx((0.11, -0.31))
        assert out[3] == pytest.approx((-0.10, -0.31))     # bottom leg moved down

    def test_open_stirrup_roundtrip_and_measures(self):
        from revit_mcp.rebar_spec import design_from_text, design_to_text, empty_design, side_lengths, stirrup_outline

        design = empty_design()
        design["stirrups"] = [("confinamiento", self.u, 0.0, True)]
        again = design_from_text(design_to_text(design))
        kind, poly, wrap, is_open = again["stirrups"][0]
        assert is_open
        sides = side_lengths(stirrup_outline(poly, [], '3/8"', wrap, is_open), closed=False)
        assert len(sides) == 3

    def test_needs_two_points(self):
        from revit_mcp.rebar_spec import offset_polyline_outward

        with pytest.raises(SpecError):
            offset_polyline_outward([(0, 0)], 0.01)


def test_clean_polyline():
    from revit_mcp.rebar_spec import clean_polyline

    straight = [(0, 0), (0, 0.1), (0, 0.1), (0, 0.2), (0.1, 0.2)]
    assert clean_polyline(straight, closed=False) == [(0, 0), (0, 0.2), (0.1, 0.2)]
    square = [(0, 0), (0.05, 0), (0.1, 0), (0.1, 0.1), (0, 0.1), (0, 0)]
    assert clean_polyline(square, closed=True) == [(0, 0), (0.1, 0), (0.1, 0.1), (0, 0.1)]


class TestResizeSegment:
    # bracket: leg, chamfer, back, chamfer, leg (like the user's drawing)
    pts = [(0.03, 0.30), (-0.01, 0.30), (-0.05, 0.26), (-0.05, -0.26), (-0.01, -0.30), (0.03, -0.30)]

    def test_outer_measure_changes_by_the_same_amount(self):
        from revit_mcp.rebar_spec import resize_segment, side_lengths, stirrup_outline

        before = side_lengths(stirrup_outline(self.pts, [], '3/8"', 0.0, True), closed=False)
        new = resize_segment(self.pts, 2, 0.10)  # back 10 cm longer
        after = side_lengths(stirrup_outline(new, [], '3/8"', 0.0, True), closed=False)
        assert after[2] == pytest.approx(before[2] + 0.10)
        for k in (0, 1, 3, 4):  # the others keep their size (angles kept)
            assert after[k] == pytest.approx(before[k])

    def test_too_short(self):
        from revit_mcp.rebar_spec import resize_segment

        with pytest.raises(SpecError):
            resize_segment(self.pts, 0, -0.05)


def test_legs_grow_from_their_free_end():
    from revit_mcp.rebar_spec import resize_segment

    pts = TestResizeSegment.pts
    first = resize_segment(pts, 0, 0.05)
    assert first[0] == pytest.approx((0.08, 0.30)) and first[1:] == pts[1:]
    last = resize_segment(pts, len(pts) - 2, 0.05)
    assert last[:-1] == pts[:-1] and last[-1] == pytest.approx((0.08, -0.30))


class TestShapes:
    def test_closed_stirrup_with_hooks(self):
        from revit_mcp.rebar_spec import shape_outline

        # hook, 4 sides (last overlapping the start corner), hook
        lines = [((0.3, 0.9), (0.1, 1.0)), ((0.0, 1.0), (0.0, 0.0)), ((0.0, 0.0), (1.0, 0.0)),
                 ((1.0, 0.0), (1.0, 1.0)), ((1.0, 1.0), (0.05, 1.0)), ((0.05, 1.0), (0.2, 0.85))]
        vertices, closed = shape_outline(lines, True, True)
        assert closed and len(vertices) == 4

    def test_open_bracket(self):
        from revit_mcp.rebar_spec import shape_outline

        lines = [((1.0, 1.0), (0.2, 1.0)), ((0.0, 0.8), (0.0, 0.2)), ((0.2, 0.0), (1.0, 0.0))]
        vertices, closed = shape_outline(lines)
        assert not closed
        assert vertices == [(1.0, 1.0), (0.0, 1.0), (0.0, 0.0), (1.0, 0.0)]

    def test_fit_to_box(self):
        from revit_mcp.rebar_spec import fit_polyline_to_box

        pts = fit_polyline_to_box([(0, 0), (2, 0), (2, 1)], (-0.1, -0.3, 0.1, 0.3))
        assert pts == [(-0.1, -0.3), (0.1, -0.3), (0.1, 0.3)]
        straight = fit_polyline_to_box([(0, 0), (0, 5)], (-0.1, -0.3, 0.1, 0.3))
        assert straight[0][0] == 0.0 and straight[1][1] == 0.3


def test_open_shape_keeps_its_legs():
    from revit_mcp.rebar_spec import shape_outline

    # a U with short legs up, even if Revit reports hooks at its ends
    lines = [((0.0, 0.3), (0.0, 0.0)), ((0.0, 0.0), (1.0, 0.0)), ((1.0, 0.0), (1.0, 0.3))]
    vertices, closed = shape_outline(lines, True, True)
    assert not closed and len(vertices) == 4


def test_closed_stirrup_whose_hooks_are_not_straight_segments():
    from revit_mcp.rebar_spec import shape_outline

    # M_T1 in Revit's browser: just its 4 sides (hooks drawn as arcs)
    lines = [((0.0, 1.0), (0.0, 0.0)), ((0.0, 0.0), (1.0, 0.0)),
             ((1.0, 0.0), (1.0, 1.0)), ((1.0, 1.0), (0.0, 1.0))]
    vertices, closed = shape_outline(lines, True, True)
    assert closed and len(vertices) == 4


def test_m_t1_corner_is_rebuilt():
    from revit_mcp.rebar_spec import shape_outline

    # M_T1 exactly as Revit's browser returns it (gap at the hook corner)
    lines = [((0.0, -0.025), (-3.912, -0.025)), ((-3.912, -0.025), (-3.912, -3.912)),
             ((-3.912, -3.912), (-0.025, -3.912)), ((-0.025, -3.912), (-0.025, 0.0))]
    vertices, closed = shape_outline(lines, True, True)
    assert closed
    assert sorted(set(round(x, 4) for x, _ in vertices)) == [-3.912, -0.025]
    assert sorted(set(round(y, 4) for _, y in vertices)) == [-3.912, -0.025]


def test_zone_positions_match_plain_positions():
    from revit_mcp.rebar_spec import stirrup_positions, stirrup_zone_positions

    for length in (2.65, 3.25, 0.9, 0.3):
        zones, rest = [(1, 0.05), (6, 0.10)], 0.20
        tagged = stirrup_zone_positions(length, zones, rest)
        assert [p for p, _ in tagged] == pytest.approx(stirrup_positions(length, zones, rest))
    tagged = stirrup_zone_positions(2.65, [(1, 0.05), (6, 0.10)], 0.20)
    assert tagged[0][1] == 0 and tagged[1][1] == 1 and tagged[-1][1] == 0
    assert any(zone == 2 for _, zone in tagged)


class TestCustomBars:
    inner = (-0.1005, -0.3505, 0.1005, 0.3505)  # 30x80, cover 4 cm, 3/8" stirrup

    def test_mixed_diameters_all_touch_the_stirrup(self):
        from revit_mcp.rebar_spec import custom_bar_layout

        bars = custom_bar_layout(self.inner, '3/4"', (1, '5/8"'), (2, '5/8"'))
        assert len(bars) == 4 + 2 + 4
        assert bars[0] == (pytest.approx(-0.1005 + 0.009525), pytest.approx(-0.3505 + 0.009525), '3/4"')
        face_y = [b for b in bars[4:] if b[0] < 0]
        for x, y, key in face_y:  # left face bars: their own radius off the stirrup
            assert x == pytest.approx(-0.1005 + 0.0079375)

    def test_seat_on_a_side_and_in_a_corner(self):
        from revit_mcp.rebar_spec import bar_seat

        outline = [(-0.11, -0.36), (0.11, -0.36), (0.11, 0.36), (-0.11, 0.36)]
        side = bar_seat((-0.085, 0.0), outline, '3/8"', '5/8"')
        assert side == (pytest.approx(-0.11 + 0.009525 + 0.0079375), pytest.approx(0.0))
        corner = bar_seat((-0.09, -0.34), outline, '3/8"', '5/8"')
        assert corner == (pytest.approx(-0.0925375), pytest.approx(-0.3425375))
        assert bar_seat((0.0, 0.0), outline, '3/8"', '5/8"') is None  # far from it


class TestIzajeAndBarEnds:
    def test_izaje_from_the_cota_down(self):
        from revit_mcp.rebar_spec import izaje_positions

        # 9@0.15 under a 1.35 m cota: 1.20, 1.05 ... every 0.15 down to the footing
        pos = izaje_positions(1.35, "9@0.15")
        assert pos[-1] == pytest.approx(1.20)
        assert len(pos) == 9 and pos[0] == pytest.approx(0.0)  # the lowest on the footing
        assert all(abs((b - a) - 0.15) < 1e-6 for a, b in zip(pos, pos[1:]))
        # a rest fills down to the footing
        assert izaje_positions(1.0, "1@0.05, rto@0.30") == pytest.approx([0.05, 0.35, 0.65, 0.95])

    def test_leg_vector_square_to_the_nearest_face(self):
        from revit_mcp.rebar_spec import leg_vector, LEG_IN, LEG_OUT

        assert leg_vector(0.09, 0.0, 0.125, 0.40, LEG_OUT) == (1.0, 0.0)
        assert leg_vector(0.0, -0.35, 0.125, 0.40, LEG_OUT) == (0.0, -1.0)
        assert leg_vector(0.09, 0.0, 0.125, 0.40, LEG_IN) == (-1.0, 0.0)

    def test_bar_with_ends(self):
        from revit_mcp.rebar_spec import bar_with_ends

        pts = bar_with_ends([(0.1, 0.0, 0.0), (0.1, 0.0, 3.0)], anchor=0.5, leg_bottom=0.25,
                            dir_bottom=(1.0, 0.0), top_drop=0.05, leg_top=0.2, dir_top=(-1.0, 0.0))
        assert pts == pytest.approx([(0.35, 0.0, -0.5), (0.1, 0.0, -0.5), (0.1, 0.0, 2.95), (-0.1, 0.0, 2.95)])
        assert bar_with_ends([(0, 0, 0), (0, 0, 3)]) == [(0, 0, 0), (0, 0, 3)]
