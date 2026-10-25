# -*- coding: utf-8 -*-
"""Sloped slabs made by shape editing keep their type's thickness in the
vertical: across a 23 degree slope a 0.20 slab is only 0.184 thick and
stops short of a ridge beam modeled right. This remakes each sloped plane
of such a floor as a floor with a slope arrow (Revit then keeps the
thickness across the face): same outline, same top face, same type, its
parameters copied, joined to its beams (the beams cutting it).

Plan m, the model's x / y; heights m."""
import math

from pyrevit import DB
from System.Collections.Generic import List

import slab_create as sc
import slab_regions as sg

FT = 0.3048
SKIP_PARAMS = set([
    DB.BuiltInParameter.FLOOR_HEIGHTABOVELEVEL_PARAM, DB.BuiltInParameter.LEVEL_PARAM,
    DB.BuiltInParameter.ROOF_SLOPE, DB.BuiltInParameter.FLOOR_PARAM_IS_STRUCTURAL,
    DB.BuiltInParameter.ELEM_TYPE_PARAM, DB.BuiltInParameter.ELEM_FAMILY_AND_TYPE_PARAM,
    DB.BuiltInParameter.ELEM_FAMILY_PARAM, DB.BuiltInParameter.SYMBOL_ID_PARAM,
])


def _top_faces(floor):
    out = []
    for r in DB.HostObjectUtils.GetTopFaces(floor):
        f = floor.GetGeometryObjectFromReference(r)
        if isinstance(f, DB.PlanarFace):
            out.append(f)
    return out


def thickness_across(floor):
    """(across the face, vertical) thickness (m) of a sloped floor, from its
    top and bottom faces."""
    tops = _top_faces(floor)
    bots = [floor.GetGeometryObjectFromReference(r) for r in DB.HostObjectUtils.GetBottomFaces(floor)]
    bots = [b for b in bots if isinstance(b, DB.PlanarFace)]
    if not tops or not bots:
        return None, None
    t = max(tops, key=lambda f: f.Area)
    n = t.FaceNormal
    b = min(bots, key=lambda q: abs(q.FaceNormal.DotProduct(n) + 1.0))
    d = abs((t.Origin - b.Origin).DotProduct(n)) * FT
    return d, d / max(abs(n.Z), 1e-6)


def needs_fix(floor, tol=0.003):
    """A floor with a sloped top whose thickness across it is short of its
    type's (made by shape editing)."""
    tops = _top_faces(floor)
    if not tops or all(abs(f.FaceNormal.Z) > 0.999 for f in tops):
        return False
    t = floor.Document.GetElement(floor.GetTypeId())
    want = sg.type_thickness(t)
    across, _ = thickness_across(floor)
    return across is not None and across < want - tol


def _face_outline(face):
    """[(outer [(x, y)], holes [[(x, y)]])] of a top face in plan."""
    loops = []
    for loop in face.GetEdgesAsCurveLoops():
        pts = []
        for c in loop:
            for p in list(c.Tessellate())[:-1]:
                pts.append((p.X * FT, p.Y * FT))
        if len(pts) >= 3:
            loops.append(sc.clean(pts))
    loops.sort(key=lambda l: -abs(sc.area(l)))
    return loops[0], loops[1:]


def plane_slab(face):
    """(outline, holes, arrow ((x, y), (x, y)), slope, tail height m) of a
    sloped planar top face: the arrow along its steepest climb, from the
    outline's lowest edge."""
    outer, holes = _face_outline(face)
    n = face.FaceNormal
    o = face.Origin
    # the climb in plan: against the normal's horizontal part
    hx, hy = -n.X, -n.Y
    L = math.hypot(hx, hy)
    if L < 1e-9:
        return outer, holes, None, 0.0, o.Z * FT
    hx, hy = hx / L, hy / L
    slope = L / abs(n.Z)

    def height(x, y):  # the face's plane over (x, y), m
        return (o.Z - (n.X * (x / FT - o.X) + n.Y * (y / FT - o.Y)) / n.Z) * FT
    proj = [p[0] * hx + p[1] * hy for p in outer]
    lo, hi = min(proj), max(proj)
    cx = sum(p[0] for p in outer) / len(outer)
    cy = sum(p[1] for p in outer) / len(outer)
    c = cx * hx + cy * hy
    # the arrow through the middle, from the low side to the high one
    tail = (cx + (lo - c) * hx, cy + (lo - c) * hy)
    head = (cx + (hi - c) * hx, cy + (hi - c) * hy)
    return outer, holes, (tail, head), slope, height(*tail)


def copy_parameters(src, dst):
    n = 0
    for p in src.Parameters:
        if p.IsReadOnly or not p.HasValue:
            continue
        d = getattr(p.Definition, "BuiltInParameter", DB.BuiltInParameter.INVALID)
        if d in SKIP_PARAMS:
            continue
        q = dst.get_Parameter(p.Definition) if hasattr(dst, "get_Parameter") else None
        q = q or dst.LookupParameter(p.Definition.Name)
        if q is None or q.IsReadOnly or q.StorageType != p.StorageType:
            continue
        try:
            if p.StorageType == DB.StorageType.String:
                q.Set(p.AsString() or u"")
            elif p.StorageType == DB.StorageType.Double:
                q.Set(p.AsDouble())
            elif p.StorageType == DB.StorageType.Integer:
                q.Set(p.AsInteger())
            elif p.StorageType == DB.StorageType.ElementId:
                q.Set(p.AsElementId())
            n += 1
        except Exception:
            pass
    return n


def join_beams_cutting(doc, floor):
    """Join the floor with what it touches; beams cut it (it stops at their
    faces, a ridge beam's sloped side included)."""
    n = sg.join_supports(doc, floor)
    bb = floor.get_BoundingBox(None)
    pad = 0.1 / FT
    near = DB.Outline(DB.XYZ(bb.Min.X - pad, bb.Min.Y - pad, bb.Min.Z - pad),
                      DB.XYZ(bb.Max.X + pad, bb.Max.Y + pad, bb.Max.Z + pad))
    for e in (DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_StructuralFraming)
              .WhereElementIsNotElementType().WherePasses(DB.BoundingBoxIntersectsFilter(near))):
        try:
            if DB.JoinGeometryUtils.AreElementsJoined(doc, floor, e) and \
                    not DB.JoinGeometryUtils.IsCuttingElementInJoin(doc, e, floor):
                DB.JoinGeometryUtils.SwitchJoinOrder(doc, e, floor)
        except Exception:
            pass
    return n


def fix_floor(doc, floor, join=True):
    """Remake a shape-edited sloped floor (inside an open Transaction): one
    floor per sloped plane of its top. Returns the new floors (the old one
    deleted)."""
    level = doc.GetElement(floor.LevelId)
    ftype = doc.GetElement(floor.GetTypeId())
    z_level = level.Elevation * FT
    made = []
    for face in _top_faces(floor):
        outer, holes, arrow, slope, z_tail = plane_slab(face)
        loops = [sg._curve_loop(outer, level.Elevation)] + [sg._curve_loop(h, level.Elevation) for h in holes]
        if arrow is None:
            new = DB.Floor.Create(doc, List[DB.CurveLoop](loops), ftype.Id, level.Id)
        else:
            (ax, ay), (bx, by) = arrow
            line = DB.Line.CreateBound(DB.XYZ(ax / FT, ay / FT, level.Elevation), DB.XYZ(bx / FT, by / FT, level.Elevation))
            new = DB.Floor.Create(doc, List[DB.CurveLoop](loops), ftype.Id, level.Id, True, line, slope)
        new.get_Parameter(DB.BuiltInParameter.FLOOR_HEIGHTABOVELEVEL_PARAM).Set((z_tail - z_level) / FT)
        s = new.get_Parameter(DB.BuiltInParameter.FLOOR_PARAM_IS_STRUCTURAL)
        if s is not None and not s.IsReadOnly:
            s.Set(1)
        copy_parameters(floor, new)
        made.append(new)
    doc.Delete(floor.Id)
    doc.Regenerate()
    for new in made if join else []:
        join_beams_cutting(doc, new)
    return made


def _single_slope(floor):
    """(face, outline, holes, arrow, slope, tail height, climb (x, y), high
    side m) of a floor with one sloped planar top, else None."""
    tops = _top_faces(floor)
    if len(tops) != 1 or abs(tops[0].FaceNormal.Z) > 0.999:
        return None
    face = tops[0]
    outer, holes, arrow, slope, z_tail = plane_slab(face)
    if arrow is None:
        return None
    (ax, ay), (bx, by) = arrow
    L = math.hypot(bx - ax, by - ay)
    h = ((bx - ax) / L, (by - ay) / L)
    hi = max(p[0] * h[0] + p[1] * h[1] for p in outer)
    return face, outer, holes, arrow, slope, z_tail, h, hi


def ridge_beam(doc, floor, reach=0.8):
    """The beam a sloped floor's high edge meets (a ridge beam): running
    along that edge, its axis just past it (within `reach` m) and its top
    up at the slab's. Returns (beam, how far the edge must go to its axis
    m) or None - none there, or the edge already on the axis."""
    info = _single_slope(floor)
    if info is None:
        return None
    face, outer, holes, arrow, slope, z_tail, h, hi = info
    bb = floor.get_BoundingBox(None)
    pad = reach / FT
    near = DB.Outline(DB.XYZ(bb.Min.X - pad, bb.Min.Y - pad, bb.Min.Z - pad),
                      DB.XYZ(bb.Max.X + pad, bb.Max.Y + pad, bb.Max.Z + pad))
    best = None
    for e in (DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_StructuralFraming)
              .WhereElementIsNotElementType().WherePasses(DB.BoundingBoxIntersectsFilter(near))):
        c = getattr(e.Location, "Curve", None)
        if not isinstance(c, DB.Line):
            continue
        d = c.Direction
        if abs(d.X * h[0] + d.Y * h[1]) > 0.05:  # not along the edge
            continue
        # the stretch of the edge it runs along (another span of a ridge on
        # the same axis does not count)
        along = (-h[1], h[0])
        ends = sorted((q.X * FT * along[0] + q.Y * FT * along[1]) for q in (c.GetEndPoint(0), c.GetEndPoint(1)))
        span = [x * along[0] + y * along[1] for x, y in outer]
        if min(ends[1], max(span)) - max(ends[0], min(span)) < 0.10:
            continue
        p = c.GetEndPoint(0)
        axis = p.X * FT * h[0] + p.Y * FT * h[1]
        ebb = e.get_BoundingBox(None)
        if not (0.0 <= axis - hi <= reach) or ebb.Max.Z < bb.Max.Z - 0.05 / FT:
            continue
        if best is None or axis - hi < best[1]:
            best = (e, axis - hi)
    if best is None or best[1] < 0.005:
        return None
    return best


def fit_to_ridge(doc, floor, join=True):
    """Remake a sloped floor (inside an open Transaction) with its high edge
    carried to its ridge beam's axis - the beam then cuts it, so it ends on
    the beam's sloped side with no wedge left under it. Same plane, type
    and parameters. Returns the new floor, or None when there is nothing to
    fit."""
    found = ridge_beam(doc, floor)
    info = _single_slope(floor)
    if found is None or info is None:
        return None
    beam, extend = found
    face, outer, holes, arrow, slope, z_tail, h, hi = info
    outer = sc.push_edge(outer, h, hi, hi + extend)
    level = doc.GetElement(floor.LevelId)
    ftype = doc.GetElement(floor.GetTypeId())
    loops = [sg._curve_loop(outer, level.Elevation)] + [sg._curve_loop(q, level.Elevation) for q in holes]
    (ax, ay), (bx, by) = arrow
    bx, by = bx + h[0] * extend, by + h[1] * extend
    line = DB.Line.CreateBound(DB.XYZ(ax / FT, ay / FT, level.Elevation), DB.XYZ(bx / FT, by / FT, level.Elevation))
    new = DB.Floor.Create(doc, List[DB.CurveLoop](loops), ftype.Id, level.Id, True, line, slope)
    new.get_Parameter(DB.BuiltInParameter.FLOOR_HEIGHTABOVELEVEL_PARAM).Set((z_tail - level.Elevation * FT) / FT)
    s = new.get_Parameter(DB.BuiltInParameter.FLOOR_PARAM_IS_STRUCTURAL)
    if s is not None and not s.IsReadOnly:
        s.Set(1)
    copy_parameters(floor, new)
    doc.Delete(floor.Id)
    doc.Regenerate()
    if join:  # with the beams it touches; the ridge beam always cuts it
        join_beams_cutting(doc, new)
    if not DB.JoinGeometryUtils.AreElementsJoined(doc, new, beam):
        DB.JoinGeometryUtils.JoinGeometry(doc, new, beam)
    if not DB.JoinGeometryUtils.IsCuttingElementInJoin(doc, beam, new):
        DB.JoinGeometryUtils.SwitchJoinOrder(doc, beam, new)
    return new


def fit_to_ridge_in_place(doc, floor, join=True):
    """The same floor (no transaction open: it opens its own) with its high
    edge carried to its ridge beam's axis by editing its sketch - its id,
    parameters, steel and shape points kept; the shape points moved with
    the edge are raised to the slab's plane there - and the beam cutting it.
    Returns the ridge beam, or None when there is nothing to fit."""
    found = ridge_beam(doc, floor)
    info = _single_slope(floor)
    if found is None or info is None:
        return None
    beam, extend = found
    face, outer, holes, arrow, slope, z_tail, h, hi = info
    n, o = face.FaceNormal, face.Origin

    def plane_z(x, y):  # the slab's top plane over (x, y) (ft)
        return o.Z - (n.X * (x - o.X) + n.Y * (y - o.Y)) / n.Z

    def on_edge(p):
        return abs((p.X * h[0] + p.Y * h[1]) * FT - hi) < 0.01

    push = DB.XYZ(h[0] * extend / FT, h[1] * extend / FT, 0)
    quiet = _Quiet()
    scope = DB.SketchEditScope(doc, u"Ajustar losa a la cumbrera")
    scope.Start(floor.SketchId)
    t = DB.Transaction(doc, u"Ajustar losa a la cumbrera")
    t.Start()
    sketch = doc.GetElement(floor.SketchId)
    for eid in sketch.GetAllElements():
        mc = doc.GetElement(eid)
        c = getattr(mc, "GeometryCurve", None)
        if c is None:
            continue
        ends = [c.GetEndPoint(0), c.GetEndPoint(1)]
        moved = [p + push if on_edge(p) else p for p in ends]
        if all(a.IsAlmostEqualTo(b) for a, b in zip(ends, moved)):
            continue
        if not isinstance(c, DB.Line):
            t.RollBack()
            scope.Cancel()
            raise Exception(u"el borde alto no es recto")
        mc.SetGeometryCurve(DB.Line.CreateBound(moved[0], moved[1]), True)
    t.Commit()
    scope.Commit(quiet)
    t = DB.Transaction(doc, u"Ajustar losa a la cumbrera")
    options = t.GetFailureHandlingOptions()
    options.SetFailuresPreprocessor(quiet)
    t.SetFailureHandlingOptions(options)
    t.Start()
    editor = floor.GetSlabShapeEditor()
    if editor is not None and editor.IsEnabled:
        level = doc.GetElement(floor.LevelId)
        base = level.Elevation + floor.get_Parameter(DB.BuiltInParameter.FLOOR_HEIGHTABOVELEVEL_PARAM).AsDouble()
        for v in editor.SlabShapeVertices:
            p = v.Position
            if abs((p.X * h[0] + p.Y * h[1]) * FT - (hi + extend)) < 0.01:
                editor.ModifySubElement(v, plane_z(p.X, p.Y) - base)
    if join:
        join_beams_cutting(doc, floor)
    if not DB.JoinGeometryUtils.AreElementsJoined(doc, floor, beam):
        DB.JoinGeometryUtils.JoinGeometry(doc, floor, beam)
    if not DB.JoinGeometryUtils.IsCuttingElementInJoin(doc, beam, floor):
        DB.JoinGeometryUtils.SwitchJoinOrder(doc, beam, floor)
    t.Commit()
    return beam


class _Quiet(DB.IFailuresPreprocessor):
    """Warnings dropped: no dialog stops the correction."""

    def PreprocessFailures(self, accessor):
        for f in accessor.GetFailureMessages():
            if f.GetSeverity() == DB.FailureSeverity.Warning:
                accessor.DeleteWarning(f)
        return DB.FailureProcessingResult.Continue
