# -*- coding: UTF-8 -*-
"""Sloped beams in Acero Viga: a gable frame (two sloped beam lines meeting
at a ridge) has its stirrups made by rebar_beams.generate_line (square to
the slope) and its longitudinal bars here, bent across the ridge
(sloped_plan): top bars continuous over it, bottom bars crossing it and
anchored along the other beam, plumb hooks into the eave supports.
Also the Boceto bars: a bar drawn point by point in a view.

Runs inside Revit's IronPython engine - no f-strings."""
import math

from pyrevit import DB
from Autodesk.Revit.DB.Structure import Rebar, RebarHookOrientation, RebarStyle
from System.Collections.Generic import List

import rebar_columns as rc
import rebar_spec as spec
import rebar_beams as rb
import beam_extras as bx
import sloped_plan as sp
from utils import element_id_value

FT = rc.FT
RIDGE_REACH_M = 0.8  # two sloped beams whose ends are this close meet at a ridge


def _ends(line):
    return line.point(0.0, 0.0, 0.0), line.point(0.0, 0.0, line.length)


def _horizontal(v):
    h = DB.XYZ(v.X, v.Y, 0.0)
    return h.Normalize() if h.GetLength() > 1e-9 else None


def ridge_pairs(lines, reach_m=RIDGE_REACH_M):
    """[(line a, line b, a's ridge end 0/1, b's ridge end 0/1)]: sloped
    lines in one vertical plane, rising to a common high end."""
    sloped = [l for l in lines if getattr(l, "sloped", False)]
    pairs, used = [], set()
    for i, a in enumerate(sloped):
        for b in sloped[i + 1:]:
            if id(a) in used or id(b) in used:
                continue
            ha, hb = _horizontal(a.direction), _horizontal(b.direction)
            if ha is None or hb is None or abs(abs(ha.DotProduct(hb)) - 1.0) > 1e-3:
                continue
            ea, eb = _ends(a), _ends(b)
            best = None
            for ka in (0, 1):
                for kb in (0, 1):
                    dist = ea[ka].DistanceTo(eb[kb]) * FT
                    if dist <= reach_m and (best is None or dist < best[0]):
                        best = (dist, ka, kb)
            if best is None:
                continue
            _, ka, kb = best
            # the meeting ends are the high ones (a ridge, not a valley)
            if ea[ka].Z < ea[1 - ka].Z or eb[kb].Z < eb[1 - kb].Z:
                continue
            pairs.append((a, b, ka, kb))
            used.update([id(a), id(b)])
    return pairs


class _Frame(object):
    """The vertical plane of a pair: u along e (horizontal), z up."""

    def __init__(self, line):
        self.origin = line.origin
        self.e = _horizontal(line.direction)
        self.across = DB.XYZ.BasisZ.CrossProduct(self.e).Normalize()

    def uzw(self, p):
        q = p - self.origin
        return q.DotProduct(self.e) * FT, p.Z * FT, q.DotProduct(self.across) * FT

    def world(self, u, z, w):
        p = self.origin + self.e * (u / FT) + self.across * (w / FT)
        return DB.XYZ(p.X, p.Y, z / FT)


def _rows(frame, line, design, cover, ridge_end, eave_face):
    """Top and bottom bar rows of a line: [((eave (u, z), dir (du, dz)), key, w)]."""
    s_eave = eave_face + cover if ridge_end == 1 else eave_face - cover
    s_ridge = line.length if ridge_end == 1 else 0.0
    top, bottom = [], []
    for x, y, key in design["bars"]:
        u0, z0, w = frame.uzw(line.point(x, y, s_eave))
        u1, z1, _ = frame.uzw(line.point(x, y, s_ridge))
        L = math.hypot(u1 - u0, z1 - z0) or 1.0
        row = (((u0, z0), ((u1 - u0) / L, (z1 - z0) / L)), key, w)
        (top if y > 0 else bottom).append(row)
    top.sort(key=lambda r: r[2])
    bottom.sort(key=lambda r: r[2])
    return top, bottom


def _bar(doc, frame, host, bar_type, path, w):
    world = [frame.world(u, z, w) for u, z in path]
    curves = [DB.Line.CreateBound(world[k], world[k + 1]) for k in range(len(world) - 1)
              if world[k].DistanceTo(world[k + 1]) > 1e-4]
    rebar = Rebar.CreateFromCurves(
        doc, RebarStyle.Standard, bar_type, None, None, host, frame.across, List[DB.Curve](curves),
        RebarHookOrientation.Right, RebarHookOrientation.Right, True, True)
    rc._tag(rebar, host)
    return rebar


def generate_pair(doc, pair, spec_a, spec_b, anchor_a, anchor_b, bar_types, splice, legs,
                  settings_a=None, settings_b=None):
    """The longitudinal bars of a ridge pair (inside an active Transaction;
    its stirrups made apart with longitudinal=False).
    Returns ({element id: [(rebar, key, kind, share)]}, warnings)."""
    a, b, ka, kb = pair
    frame = _Frame(a)
    sides = []
    for line, beam_spec, anchor, settings, ridge_end in (
            (a, spec_a, anchor_a, settings_a, ka), (b, spec_b, anchor_b, settings_b, kb)):
        supports = rb.line_supports(doc, line)
        _, start_face, end_face = rb.spans_and_ends(line, supports)
        eave_end = 1 - ridge_end
        face = start_face if eave_end == 0 else end_face
        top, bottom = _rows(frame, line, beam_spec.design, beam_spec.cover_m, ridge_end, face)
        hooked = anchor == rb.ANCHOR_HOOK
        # the plumb hook stays inside the eave support (a shallow beam too)
        floor_z = None
        for f in supports:
            if (f["s0"] < 0.05) if eave_end == 0 else (f["s1"] > line.length - 0.05):
                box = doc.GetElement(DB.ElementId(f["id"])).get_BoundingBox(None)
                if box is not None:
                    z = box.Min.Z * FT + beam_spec.cover_m
                    floor_z = z if floor_z is None else min(floor_z, z)
        leg = {}
        for (eave, _), key, _ in top + bottom:
            want = bx.hook_leg(settings, eave_end, key, legs, spec.BAR_DIAMETERS_MM, hooked)
            if want > 0 and floor_z is not None:
                room = eave[1] - floor_z - spec.BAR_DIAMETERS_MM[key] / 2000.0
                want = max(spec.MIN_LEG_M, min(want, room))
            leg[eave] = want  # per row: a bottom bar has less room than a top one
        host = line.element_at(0.0 if eave_end == 0 else line.length)
        sides.append({"line": line, "spec": beam_spec, "top": top, "bottom": bottom, "leg": leg,
                      "host": host, "ridge_host": line.element_at(line.length if ridge_end == 1 else 0.0)})
    sa, sb = sides

    keys = set(k for s in sides for _, k, _ in s["top"] + s["bottom"])
    ld = dict((k, spec.e060_lap_cm(k, factor=1.0) / 100.0) for k in keys)

    def top_d(side):
        return max([spec.BAR_DIAMETERS_MM[k] for _, k, _ in side["top"]] or [0.0]) / 1000.0
    max_length = splice["max"] if splice else 1e9
    laps = splice["laps"] if splice else None
    created = dict((element_id_value(e.Id), []) for s in sides for e in s["line"].elements)
    warnings = []

    n = min(len(sa["top"]), len(sb["top"]))
    paths = []
    for i in range(n):
        (row_a, key_a, w), (row_b, key_b, _) = sa["top"][i], sb["top"][i]
        leg_a, leg_b = sa["leg"].get(row_a[0], 0.0), sb["leg"].get(row_b[0], 0.0)
        path = sp.top_path(row_a, row_b, leg_a, leg_b)
        if path is None:
            continue
        pieces = [path]
        if sp.length(path) > max_length + 1e-6 and laps and laps.get(key_a):
            pieces = sp.split_top(path, 2 if leg_a > 0 else 1, laps.get(key_a))
        for piece in pieces:
            if sp.length(piece) > max_length + 1e-6:
                warnings.append(u"Barra superior {}: {:.2f} m".format(key_a, sp.length(piece)))
            paths.append((piece, key_a, w, [(sa, 0.5), (sb, 0.5)]))
    for own, other in ((sa, sb), (sb, sa)):
        other_top = [r for r, _, _ in other["top"]] or [r for r, _, _ in other["bottom"]]
        if not other_top:
            continue
        for row, key, w in own["top"][n:] + own["bottom"]:
            gap = (top_d(other) + spec.BAR_DIAMETERS_MM[key] / 1000.0) / 2.0
            path = sp.anchored_path(row, other_top[0], own["leg"].get(row[0], 0.0), ld[key], gap)
            if path is None:
                continue
            if sp.length(path) > max_length + 1e-6:
                warnings.append(u"Barra {}: {:.2f} m".format(key, sp.length(path)))
            paths.append((path, key, w, [(own, 1.0)]))
    for path, key, w, shares in paths:
        side = shares[0][0]
        bar_type = side["spec"].bar_type(doc, bar_types, key, rb.BEAM_MARK)
        rebar = _bar(doc, frame, side["host"], bar_type, path, w)
        for s, share in shares:
            created[element_id_value(s["host"].Id)].append((rebar, key, rc.LONGITUDINAL, share))
    return created, warnings


# --- Boceto: a bar drawn point by point -------------------------------------

def beam_plane(beam):
    """(origin, horizontal axis e, across) of the vertical plane through a
    beam's axis (Boceto bars are drawn there)."""
    c = beam.Location.Curve
    p0, p1 = c.GetEndPoint(0), c.GetEndPoint(1)
    e = _horizontal(p1 - p0)
    return p0, e, DB.XYZ.BasisZ.CrossProduct(e).Normalize()


def onto_plane(points, beam):
    """The clicked points moved square onto the beam's vertical plane."""
    o, e, across = beam_plane(beam)
    return [p - across * (p - o).DotProduct(across) for p in points]


def width_offsets(beam, count, cover_m, bar_key):
    """Across offsets (ft) of `count` bars spread over the beam's width
    inside the cover (one bar: the middle)."""
    try:
        section = rb.BeamSection(beam)
        xs = [x for x, _ in section.polygon_m]
        half = (max(xs) - min(xs)) / 2.0 - cover_m - spec.BAR_DIAMETERS_MM[bar_key] / 2000.0 - 0.0095
        center = section.center[0]
    except spec.SpecError:
        half, center = 0.0, 0.0
    if count <= 1 or half <= 0:
        return [center]
    return [center + (-half + 2.0 * half * k / (count - 1)) / FT for k in range(count)]


def sketch_bar(doc, beam, bar_type, points, count, cover_m, bar_key):
    """The drawn bar(s) on the beam (inside an active Transaction): the
    path through `points` (world, already on the beam plane), `count`
    copies spread over the width. Returns the rebars."""
    _, _, across = beam_plane(beam)
    out = []
    for off in width_offsets(beam, count, cover_m, bar_key):
        pts = [p + across * off for p in points]
        curves = [DB.Line.CreateBound(pts[k], pts[k + 1]) for k in range(len(pts) - 1)
                  if pts[k].DistanceTo(pts[k + 1]) > 1e-4]
        rebar = Rebar.CreateFromCurves(
            doc, RebarStyle.Standard, bar_type, None, None, beam, across, List[DB.Curve](curves),
            RebarHookOrientation.Right, RebarHookOrientation.Right, True, True)
        rc._tag(rebar, beam)
        out.append(rebar)
    return out
