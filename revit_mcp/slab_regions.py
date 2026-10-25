# -*- coding: utf-8 -*-
"""Revit side of "Crear Losas": the free regions of a level (the spaces
closed by its beams, walls and columns at the slab's height) and the
floors made from them (slab_create.pieces). Plan coordinates in meters,
the model's x / y."""
import math

from pyrevit import DB
from System.Collections.Generic import List

import rebar_foundation as rf
import slab_create as sc

FT = 0.3048
SUPPORTS = (DB.BuiltInCategory.OST_StructuralFraming, DB.BuiltInCategory.OST_Walls,
            DB.BuiltInCategory.OST_StructuralColumns)


def floor_types(doc):
    """{name: FloorType} of the project's floor types (as the type selector
    lists them), foundations left out."""
    out = {}
    for t in DB.FilteredElementCollector(doc).OfClass(DB.FloorType):
        if getattr(t, "IsFoundationSlab", False):
            continue
        out[t.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM).AsString()] = t
    return out


def type_thickness(t):
    p = t.get_Parameter(DB.BuiltInParameter.FLOOR_ATTR_DEFAULT_THICKNESS_PARAM)
    return p.AsDouble() * FT if p is not None else 0.20


def _loop_points(loop):
    pts = []
    for c in loop:
        for p in list(c.Tessellate())[:-1]:
            pts.append((p.X * FT, p.Y * FT))
    return pts


class LevelRegions(object):
    """The free spaces of a level at the slab's height: a box over the level's
    supports minus every beam, wall and column, split in its pieces."""

    def __init__(self, doc, level, top_offset, thickness, pad=2.0, gap=0.06, column_reach=1.5):
        self.doc = doc
        z_top = level.Elevation * FT + top_offset
        z0, z1 = z_top - thickness + 0.01, z_top - 0.01
        self.z = (z0, z1)
        band = DB.Outline(DB.XYZ(-1e5, -1e5, z0 / FT), DB.XYZ(1e5, 1e5, z1 / FT))
        # columns reaching up to just under the slab cut it too (where they
        # stand out of the beams the slab breaks round them)
        low = DB.Outline(DB.XYZ(-1e5, -1e5, (z0 - column_reach) / FT), DB.XYZ(1e5, 1e5, z1 / FT))
        solids, xs, ys = [], [], []
        for bic in SUPPORTS:
            column = bic == DB.BuiltInCategory.OST_StructuralColumns
            for e in (DB.FilteredElementCollector(doc).OfCategory(bic).WhereElementIsNotElementType()
                      .WherePasses(DB.BoundingBoxIntersectsFilter(low if column else band))):
                bb = e.get_BoundingBox(None)
                if bb is None:
                    continue
                for s in rf._solids(e):
                    if column and bb.Max.Z * FT < z1:
                        s = _raised_to(s, z0, z1)  # its plan shape at the slab
                        if s is None:
                            continue
                    solids.append(s)
                xs += [bb.Min.X * FT, bb.Max.X * FT]
                ys += [bb.Min.Y * FT, bb.Max.Y * FT]
        self.count = len(solids)
        self.pieces = []
        self.free = []  # every free space, the one around the building too (drawing)
        if not solids:
            return
        x0, x1, y0, y1 = min(xs) - pad, max(xs) + pad, min(ys) - pad, max(ys) + pad
        self.box = (x0, x1, y0, y1)
        corners = [DB.XYZ(x / FT, y / FT, z0 / FT) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
        loop = DB.CurveLoop()
        for k in range(4):
            loop.Append(DB.Line.CreateBound(corners[k], corners[(k + 1) % 4]))
        slab = DB.GeometryCreationUtilities.CreateExtrusionGeometry(
            List[DB.CurveLoop]([loop]), DB.XYZ.BasisZ, (z1 - z0) / FT)
        band_solid = slab
        self.footprints = []  # the supports cut at the slab's height (drawing): triangles in plan
        for s in solids:
            try:
                cut = DB.BooleanOperationsUtils.ExecuteBooleanOperation(s, band_solid, DB.BooleanOperationsType.Intersect)
                for f in (cut.Faces if cut is not None else []):
                    if isinstance(f, DB.PlanarFace) and f.FaceNormal.Z > 0.99:
                        mesh = f.Triangulate()
                        for i in range(mesh.NumTriangles if mesh else 0):
                            t = mesh.get_Triangle(i)
                            self.footprints.append(tuple((t.get_Vertex(k).X * FT, t.get_Vertex(k).Y * FT) for k in range(3)))
            except Exception:
                pass
            # each support also moved `gap` four ways: the small openings where
            # beams meet without a column close (the space is put back after)
            moved = [s] + [DB.SolidUtils.CreateTransformed(s, DB.Transform.CreateTranslation(DB.XYZ(dx / FT, dy / FT, 0)))
                           for dx, dy in ((gap, 0), (-gap, 0), (0, gap), (0, -gap))] if gap else [s]
            for m in moved:
                try:
                    slab = DB.BooleanOperationsUtils.ExecuteBooleanOperation(slab, m, DB.BooleanOperationsType.Difference)
                except Exception:
                    continue  # a solid Revit can't subtract (rare): skipped
        for part in DB.SolidUtils.SplitVolumes(slab):
            top = None
            for f in part.Faces:
                if isinstance(f, DB.PlanarFace) and f.FaceNormal.Z > 0.99:
                    top = f
                    break
            if top is None:
                continue
            loops = [_loop_points(l) for l in top.GetEdgesAsCurveLoops()]
            loops = [l for l in loops if len(l) >= 3]
            if not loops:
                continue
            loops.sort(key=lambda l: -abs(sc.area(l)))
            outer, holes = loops[0], loops[1:]
            self.free.append({"outer": outer, "holes": holes})
            bx = [p[0] for p in outer]
            by = [p[1] for p in outer]
            outside = (min(bx) <= x0 + 1e-3 or max(bx) >= x1 - 1e-3 or min(by) <= y0 + 1e-3 or max(by) >= y1 - 1e-3)
            if outside:
                continue  # around the building: not a slab
            # back to the supports' faces
            self.pieces.append({"outer": offset_poly(outer, gap), "holes": [offset_poly(h, -gap) for h in holes]})

    def at(self, x, y):
        """The free region holding the plan point, or None."""
        best = None
        for p in self.pieces:
            if _inside(p["outer"], x, y) and not any(_inside(h, x, y) for h in p["holes"]):
                a = abs(sc.area(p["outer"]))
                if best is None or a < best[0]:
                    best = (a, p)
        return best[1] if best else None


def _raised_to(solid, z0, z1):
    """A column ending under the slab: its top `z1 - z0` m (the slab's
    height of it) moved up into the slab band, so its plan shape cuts it."""
    bb = solid.GetBoundingBox()
    top = bb.Transform.OfPoint(bb.Max).Z
    height = (z1 - z0) / FT
    try:
        box = DB.GeometryCreationUtilities.CreateExtrusionGeometry(
            List[DB.CurveLoop]([_box_loop(bb, top - height - 0.01)]), DB.XYZ.BasisZ, height + 0.01)
        piece = DB.BooleanOperationsUtils.ExecuteBooleanOperation(solid, box, DB.BooleanOperationsType.Intersect)
    except Exception:
        return None
    if piece is None or piece.Volume < 1e-9:
        return None
    return DB.SolidUtils.CreateTransformed(piece, DB.Transform.CreateTranslation(DB.XYZ(0, 0, z1 / FT - top)))


def _box_loop(bb, z):
    lo, hi = bb.Transform.OfPoint(bb.Min), bb.Transform.OfPoint(bb.Max)
    x0, x1 = min(lo.X, hi.X) - 1, max(lo.X, hi.X) + 1
    y0, y1 = min(lo.Y, hi.Y) - 1, max(lo.Y, hi.Y) + 1
    pts = [DB.XYZ(x0, y0, z), DB.XYZ(x1, y0, z), DB.XYZ(x1, y1, z), DB.XYZ(x0, y1, z)]
    loop = DB.CurveLoop()
    for k in range(4):
        loop.Append(DB.Line.CreateBound(pts[k], pts[(k + 1) % 4]))
    return loop


def _inside(poly, x, y):
    inside = False
    n = len(poly)
    for i in range(n):
        (x0, y0), (x1, y1) = poly[i], poly[(i + 1) % n]
        if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
            inside = not inside
    return inside


def _curve_loop(poly, z):
    loop = DB.CurveLoop()
    pts = [DB.XYZ(x / FT, y / FT, z) for x, y in poly]
    for k in range(len(pts)):
        a, b = pts[k], pts[(k + 1) % len(pts)]
        if a.DistanceTo(b) > 1e-4:
            loop.Append(DB.Line.CreateBound(a, b))
    return loop


def offset_poly(poly, d):
    """The outline moved out by d (m; inward when negative): each side
    shifted, the corners met again."""
    if abs(d) < 1e-6:
        return poly
    poly = sc.ccw(poly)
    n = len(poly)
    lines = []
    for i in range(n):
        (x0, y0), (x1, y1) = poly[i], poly[(i + 1) % n]
        L = math.hypot(x1 - x0, y1 - y0) or 1.0
        nx, ny = (y1 - y0) / L, -(x1 - x0) / L  # outward of a ccw outline
        lines.append(((x0 + nx * d, y0 + ny * d), (x1 + nx * d, y1 + ny * d)))
    out = []
    for i in range(n):
        (a, b), (c, e) = lines[i - 1], lines[i]
        d1 = (b[0] - a[0], b[1] - a[1])
        d2 = (e[0] - c[0], e[1] - c[1])
        den = d1[0] * d2[1] - d1[1] * d2[0]
        if abs(den) < 1e-9:
            out.append(c)
        else:
            t = ((c[0] - a[0]) * d2[1] - (c[1] - a[1]) * d2[0]) / den
            out.append((a[0] + d1[0] * t, a[1] + d1[1] * t))
    return out


def create_region(doc, level, ftype, region, top_offset):
    """The floors of one region (inside an open Transaction): [Floor]."""
    z = level.Elevation
    made = []
    holes = region.get("holes") or []
    for piece in sc.pieces(region["poly"], region.get("shape", sc.FLAT), region.get("slope", 0.0),
                           region.get("angle"), region.get("ridge")):
        loops = [_curve_loop(piece["poly"], z)]
        if piece["arrow"] is None:
            loops += [_curve_loop(h, z) for h in holes]
            floor = DB.Floor.Create(doc, List[DB.CurveLoop](loops), ftype.Id, level.Id)
        else:
            (ax, ay), (bx, by) = piece["arrow"]
            arrow = DB.Line.CreateBound(DB.XYZ(ax / FT, ay / FT, z), DB.XYZ(bx / FT, by / FT, z))
            floor = DB.Floor.Create(doc, List[DB.CurveLoop](loops), ftype.Id, level.Id, True, arrow, piece["slope"])
        p = floor.get_Parameter(DB.BuiltInParameter.FLOOR_HEIGHTABOVELEVEL_PARAM)
        if p is not None and not p.IsReadOnly:
            p.Set(top_offset / FT)
        s = floor.get_Parameter(DB.BuiltInParameter.FLOOR_PARAM_IS_STRUCTURAL)
        if s is not None and not s.IsReadOnly:
            s.Set(1)
        made.append(floor)
    return made


def join_supports(doc, floor):
    """Join the floor with the beams, walls and columns it touches (they
    keep cutting it). Returns how many."""
    bb = floor.get_BoundingBox(None)
    pad = 0.1 / FT
    near = DB.Outline(DB.XYZ(bb.Min.X - pad, bb.Min.Y - pad, bb.Min.Z - pad),
                      DB.XYZ(bb.Max.X + pad, bb.Max.Y + pad, bb.Max.Z + pad))
    n = 0
    for bic in SUPPORTS:
        for e in (DB.FilteredElementCollector(doc).OfCategory(bic).WhereElementIsNotElementType()
                  .WherePasses(DB.BoundingBoxIntersectsFilter(near))):
            try:
                if not DB.JoinGeometryUtils.AreElementsJoined(doc, floor, e):
                    DB.JoinGeometryUtils.JoinGeometry(doc, e, floor)
                    n += 1
            except Exception:
                pass
    return n
