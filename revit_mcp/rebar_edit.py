# -*- coding: UTF-8 -*-
"""Editar Acero: one bar or stirrup of the plugin edited by hand - its
segment lengths typed in, moved up / down / across / along - always inside
the concrete less the cover (the move stops at the last place that keeps
it there). Runs inside Revit's IronPython engine - no f-strings."""
import math

from pyrevit import DB
from Autodesk.Revit.DB.Structure import Rebar, RebarHookOrientation, MultiplanarOption

import rebar_spec as spec

FT = 0.3048
CONCRETE = (DB.BuiltInCategory.OST_StructuralColumns, DB.BuiltInCategory.OST_StructuralFraming,
            DB.BuiltInCategory.OST_Floors, DB.BuiltInCategory.OST_StructuralFoundation,
            DB.BuiltInCategory.OST_Walls, DB.BuiltInCategory.OST_Stairs)


def _solids(element):
    for g in element.get_Geometry(DB.Options()) or []:
        if isinstance(g, DB.Solid) and g.Volume > 1e-9:
            yield g
        elif isinstance(g, DB.GeometryInstance):
            for s in g.GetInstanceGeometry():
                if isinstance(s, DB.Solid) and s.Volume > 1e-9:
                    yield s


class BarEdit(object):
    """The bar as a polyline (its bends sharp, world ft), its frame and the
    concrete around it."""

    def __init__(self, rebar):
        self.rebar = rebar
        self.doc = rebar.Document
        self.host = self.doc.GetElement(rebar.GetHostId())
        curves = rebar.GetCenterlineCurves(False, True, True, MultiplanarOption.IncludeOnlyPlanarCurves, 0)
        pts = [curves[0].GetEndPoint(0)]
        for c in curves:
            p = c.GetEndPoint(1)
            if p.DistanceTo(pts[-1]) > 1e-6:
                pts.append(p)
        self.closed = pts[0].DistanceTo(pts[-1]) < 1e-4 and len(pts) > 3
        self.points = pts
        bt = self.doc.GetElement(rebar.GetTypeId())
        self.diameter_m = bt.BarNominalDiameter * FT if hasattr(bt, "BarNominalDiameter") else bt.BarDiameter * FT
        self.key = spec.nearest_diameter(self.diameter_m * 1000.0)
        # the frame: along its longest segment, up, across
        segs = [(b - a) for a, b in zip(pts, pts[1:])]
        longest = max(segs, key=lambda v: v.GetLength()).Normalize()
        self.along = longest
        flat = DB.XYZ(longest.X, longest.Y, 0)
        self.across = (DB.XYZ.BasisZ.CrossProduct(flat).Normalize() if flat.GetLength() > 1e-6 else DB.XYZ.BasisX)
        self.up = DB.XYZ.BasisZ if flat.GetLength() > 1e-6 else DB.XYZ.BasisY
        # its drawing plane: the bar's own (two of its segments)
        n = None
        for a, b in zip(segs, segs[1:]):
            c = a.CrossProduct(b)
            if c.GetLength() > 1e-9:
                n = c.Normalize()
                break
        self.normal = n
        if self.closed and n is not None:
            # a stirrup: "along" is the member's axis (its plane's normal)
            self.along = n
            if abs(n.Z) > 0.9:  # a column's: moved up / down its axis, across in plan
                self.up, self.across = DB.XYZ.BasisZ, DB.XYZ.BasisX
                self.plane = (DB.XYZ.BasisX, DB.XYZ.BasisY)
            else:
                self.up = DB.XYZ.BasisZ
                self.across = DB.XYZ.BasisZ.CrossProduct(DB.XYZ(n.X, n.Y, 0).Normalize()).Normalize()
                self.plane = (self.across, DB.XYZ.BasisZ)
        else:
            self.plane = (self.along, (n.CrossProduct(self.along).Normalize() if n is not None else self.up))
        self._concrete = None

    # -- lengths -------------------------------------------------------------
    def lengths(self):
        return [a.DistanceTo(b) * FT for a, b in zip(self.points, self.points[1:])]

    def with_lengths(self, lengths_m):
        """The points with each segment set to its length (m), its direction
        kept, from the first point (a closed stirrup: unchanged)."""
        if self.closed:
            return list(self.points)
        out = [self.points[0]]
        for k, (a, b) in enumerate(zip(self.points, self.points[1:])):
            d = (b - a).Normalize()
            want = lengths_m[k] if k < len(lengths_m) and lengths_m[k] > 0 else a.DistanceTo(b) * FT
            out.append(out[-1] + d * (want / FT))
        return out

    # -- cover ---------------------------------------------------------------
    def concrete(self):
        if self._concrete is None:
            pts = self.points
            mn = DB.XYZ(min(p.X for p in pts), min(p.Y for p in pts), min(p.Z for p in pts))
            mx = DB.XYZ(max(p.X for p in pts), max(p.Y for p in pts), max(p.Z for p in pts))
            pad = 1.0 / FT
            out = DB.Outline(mn - DB.XYZ(pad, pad, pad), mx + DB.XYZ(pad, pad, pad))
            solids = []
            for bic in CONCRETE:
                for e in (DB.FilteredElementCollector(self.doc).OfCategory(bic).WhereElementIsNotElementType()
                          .WherePasses(DB.BoundingBoxIntersectsFilter(out))):
                    solids += list(_solids(e))
            self._concrete = solids
        return self._concrete

    def _inside(self, p):
        ray = DB.Line.CreateBound(p, p + DB.XYZ(0.0123, 0.0071, 300.0))
        for s in self.concrete():
            try:
                hits = s.IntersectWithCurve(ray, DB.SolidCurveIntersectionOptions())
            except Exception:
                continue
            for k in range(hits.SegmentCount):
                if hits.GetCurveSegment(k).GetEndPoint(0).DistanceTo(p) < 1e-6:
                    return True
        return False

    def fits(self, points, cover_m):
        """Every point (and points along each segment) at least cover + half
        the bar inside the concrete, every way across it."""
        r = (cover_m + self.diameter_m / 2.0) / FT * 0.98
        probes = []
        for a, b in zip(points, points[1:]):
            n = max(1, int(a.DistanceTo(b) * FT / 0.25))
            probes += [a + (b - a) * (k / float(n)) for k in range(n + 1)]
        dirs = [self.up, -self.up, self.across, -self.across]
        for p in probes:
            if not self._inside(p):
                return False
            if not all(self._inside(p + v * r) for v in dirs):
                return False
        return True

    def clamp_move(self, points, move, cover_m, steps=12):
        """The share of `move` (XYZ, ft) that keeps the bar in: all of it,
        else the most found by halving. Returns (points, share)."""
        moved = [p + move for p in points]
        if self.fits(moved, cover_m):
            return moved, 1.0
        lo, hi = 0.0, 1.0
        for _ in range(steps):
            mid = (lo + hi) / 2.0
            if self.fits([p + move * mid for p in points], cover_m):
                lo = mid
            else:
                hi = mid
        return [p + move * lo for p in points], lo

    # -- apply ---------------------------------------------------------------
    def apply(self, points):
        """The bar remade on `points` (inside an open Transaction): same type,
        host, plane, layout and comments; the old one deleted. Returns the new."""
        old = self.rebar
        acc = old.GetShapeDrivenAccessor() if old.IsRebarShapeDriven() else None
        normal = acc.Normal if acc is not None else self.across
        curves = [DB.Line.CreateBound(a, b) for a, b in zip(points, points[1:]) if a.DistanceTo(b) > 1e-4]
        from System.Collections.Generic import List
        style = old.GetShapeId()
        shape = self.doc.GetElement(style)
        new = Rebar.CreateFromCurves(self.doc, shape.RebarStyle if shape is not None else DB.Structure.RebarStyle.Standard,
                                     self.doc.GetElement(old.GetTypeId()),
                                     self.doc.GetElement(old.GetHookTypeId(0)), self.doc.GetElement(old.GetHookTypeId(1)),
                                     self.host, normal, List[DB.Curve](curves),
                                     RebarHookOrientation.Right, RebarHookOrientation.Right, True, True)
        if old.Quantity > 1 and acc is not None:
            try:
                new.GetShapeDrivenAccessor().SetLayoutAsNumberWithSpacing(
                    old.Quantity, old.MaxSpacing, True, True, True)
            except Exception:
                pass
        for bip in (DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS, DB.BuiltInParameter.ALL_MODEL_MARK):
            a, b = old.get_Parameter(bip), new.get_Parameter(bip)
            if a is not None and b is not None and not b.IsReadOnly and a.AsString():
                b.Set(a.AsString())
        for p in old.Parameters:  # the plugin's own tags (origin, element)
            if p.IsShared and p.StorageType == DB.StorageType.String and p.AsString():
                q = new.LookupParameter(p.Definition.Name)
                if q is not None and not q.IsReadOnly:
                    q.Set(p.AsString())
        self.doc.Delete(old.Id)
        return new


def host_weight(doc, host, weight_param):
    """The steel weight on the host again (its bars' lengths x kg/m)."""
    doc.Regenerate()
    kg = 0.0
    for r in DB.FilteredElementCollector(doc).OfClass(Rebar):
        if r.GetHostId() != host.Id:
            continue
        bt = doc.GetElement(r.GetTypeId())
        d = (bt.BarNominalDiameter if hasattr(bt, "BarNominalDiameter") else bt.BarDiameter) * FT * 1000.0
        key = spec.nearest_diameter(d)
        if key:
            kg += r.TotalLength * FT * spec.bar_weight_kg_per_m(key)
    p = host.LookupParameter(weight_param)
    if p is not None and not p.IsReadOnly:
        p.Set(round(kg, 2))
    return kg
