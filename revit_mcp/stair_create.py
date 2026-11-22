# -*- coding: utf-8 -*-
"""Build a sketched concrete stair (stair_plan) in Revit: every run is its
own straight Revit stair of the chosen stair type - alone, its waist stays
straight (joined to landings in one stair, Revit bends the waist of a short
run) and Acero Escalera reads it -, every landing a structural floor as
thick as the type's landing, its top at the landing height."""
from pyrevit import DB
from Autodesk.Revit.DB import StairsEditScope
from Autodesk.Revit.DB.Architecture import StairsType, StairsRun, StairsRunJustification, StairsRunType
from System.Collections.Generic import List

import slab_regions as sg

FT = 0.3048


def _name(e):
    return DB.Element.Name.GetValue(e)


class StairTypeInfo(object):
    """What the window reads from a Revit stair type (m)."""

    def __init__(self, doc, stype):
        self.type = stype
        self.name = _name(stype)
        self.max_riser = stype.MaxRiserHeight * FT
        self.min_tread = stype.MinTreadDepth * FT
        self.min_width = stype.MinRunWidth * FT
        run = doc.GetElement(stype.RunType)
        landing = doc.GetElement(stype.LandingType)
        self.monolithic = bool(run is not None and run.IsMonolithic)
        self.waist = run.StructuralDepth * FT if self.monolithic else None
        self.landing = (landing.Thickness * FT if landing is not None and landing.IsMonolithic else None) \
            or self.waist or 0.15


def with_thickness(info, waist=None, landing=None):
    """(2026-11-21) A copy of the type's info with the waist and the landing
    thickness typed in the window (m; None keeps the type's). A waist other
    than the type's is built with a stair type of that waist (made once)."""
    import copy
    out = copy.copy(info)
    out.type_waist = info.waist
    if waist and info.monolithic:
        out.waist = waist
    if landing:
        out.landing = landing
    return out


def stair_type_with_waist(doc, info):
    """The stair type to build with (inside an open Transaction): the
    chosen one when its waist is the one wanted; else a copy of it - and of
    its run type - with that waist, '<type> g=<cm>cm', found again when it
    exists."""
    want = getattr(info, "waist", None)
    have = getattr(info, "type_waist", None)
    if not want or have is None or abs(want - have) < 1e-4:
        return info.type
    cm = round(want * 100, 1)
    name = u"{} g={:g}cm".format(info.name.split(u" g=")[0], cm)
    for t in DB.FilteredElementCollector(doc).OfClass(StairsType):
        if _name(t) == name:
            return t
    run = doc.GetElement(info.type.RunType)
    run_name = u"{} g={:g}cm".format(_name(run).split(u" g=")[0], cm)
    new_run = next((r for r in DB.FilteredElementCollector(doc).OfClass(StairsRunType)
                    if _name(r) == run_name), None)
    if new_run is None:
        new_run = run.Duplicate(run_name)
        new_run.StructuralDepth = want / FT
    new = info.type.Duplicate(name)
    new.RunType = new_run.Id
    return new


def stair_types(doc):
    """[StairTypeInfo] of the project, monolithic (concrete) ones first."""
    infos = [StairTypeInfo(doc, t) for t in DB.FilteredElementCollector(doc).OfClass(StairsType)]
    return sorted(infos, key=lambda i: (not i.monolithic, i.name))


class _Quiet(DB.IFailuresPreprocessor):
    """Warnings dropped (kept in `messages`), so no dialog stops the run."""

    def __init__(self, messages):
        self.messages = messages

    def PreprocessFailures(self, accessor):
        for f in accessor.GetFailureMessages():
            self.messages.append(f.GetDescriptionText())
            if f.GetSeverity() == DB.FailureSeverity.Warning:
                accessor.DeleteWarning(f)
        return DB.FailureProcessingResult.Continue


def create_run(doc, base_level, top_level, info, run, riser, tread, width, messages):
    """One straight run as its own stair (no transaction may be open):
    base at its height over `base_level`, top level none, its risers and
    tread. Returns the Stairs."""
    scope = StairsEditScope(doc, u"BOKI Escalera - tramo")
    stairs_id = scope.Start(base_level.Id, top_level.Id)
    t = DB.Transaction(doc, u"BOKI Escalera - tramo")
    t.Start()
    stairs = doc.GetElement(stairs_id)
    stairs.ChangeTypeId(info.type.Id)
    stairs.get_Parameter(DB.BuiltInParameter.STAIRS_BASE_OFFSET).Set(run["z0"] / FT)
    stairs.get_Parameter(DB.BuiltInParameter.STAIRS_TOP_LEVEL_PARAM).Set(DB.ElementId.InvalidElementId)
    stairs.get_Parameter(DB.BuiltInParameter.STAIRS_STAIRS_HEIGHT).Set(run["risers"] * riser / FT)
    stairs.DesiredRisersNumber = run["risers"]
    stairs.ActualTreadDepth = tread / FT
    z = base_level.Elevation + run["z0"] / FT
    (ax, ay), (bx, by) = run["start"], run["end"]
    path = DB.Line.CreateBound(DB.XYZ(ax / FT, ay / FT, z), DB.XYZ(bx / FT, by / FT, z))
    created = StairsRun.CreateStraightRun(doc, stairs_id, path, StairsRunJustification.Center)
    created.ActualRunWidth = width / FT
    # to a landing it ends with a tread at the landing's height (its waist
    # meets the landing); to the upper floor, with its riser
    created.BeginsWithRiser = True
    created.EndsWithRiser = not run.get("top_tread")
    if run.get("from_landing"):
        # rising from a landing: its waist goes on down to the landing's
        # bottom face, so the two are one piece of concrete
        created.ExtensionBelowRiserBase = -run["from_landing"] / FT  # negative: down
    t.Commit()
    scope.Commit(_Quiet(messages))
    return doc.GetElement(stairs_id)


def landing_floor_type(doc, thickness):
    """The project's floor type of that thickness (a structural one first);
    a copy of the first floor type made that thick when none is."""
    types = sg.floor_types(doc)
    near = [t for t in types.values() if abs(sg.type_thickness(t) - thickness) < 0.002]
    if near:
        return sorted(near, key=lambda t: u"MACIZA" not in _name(t).upper())[0]
    base = sorted(types.values(), key=_name)[0]
    new = base.Duplicate(u"BOKI DESCANSO e={:g}cm".format(round(thickness * 100, 1)))
    cs = new.GetCompoundStructure()
    if cs is not None and cs.LayerCount >= 1:
        for i in range(cs.LayerCount):
            cs.SetLayerWidth(i, thickness / FT if i == cs.GetFirstCoreLayerIndex() else 0.0)
        new.SetCompoundStructure(cs)
    return new


def create_landing(doc, base_level, landing, ftype):
    """A landing as a structural floor (inside an open Transaction), its
    top at the landing's height over `base_level`."""
    z = base_level.Elevation
    pts = [DB.XYZ(x / FT, y / FT, z) for x, y in landing["poly"]]
    loop = DB.CurveLoop()
    for k in range(len(pts)):
        loop.Append(DB.Line.CreateBound(pts[k], pts[(k + 1) % len(pts)]))
    floor = DB.Floor.Create(doc, List[DB.CurveLoop]([loop]), ftype.Id, base_level.Id)
    floor.get_Parameter(DB.BuiltInParameter.FLOOR_HEIGHTABOVELEVEL_PARAM).Set(landing["z"] / FT)
    s = floor.get_Parameter(DB.BuiltInParameter.FLOOR_PARAM_IS_STRUCTURAL)
    if s is not None and not s.IsReadOnly:
        s.Set(1)
    return floor


def _end_bottom(doc, stairs, end_xy, d):
    """The lowest z (ft) of the run's solid at its end face (plan point
    `end_xy` m, direction d): where its waist ends."""
    opt = DB.Options()
    zs = []

    def walk(g):
        for x in g:
            if isinstance(x, DB.Solid) and x.Volume > 0:
                for e in x.Edges:
                    for k in (0, 1):
                        q = e.AsCurve().GetEndPoint(k)
                        s = (q.X * FT - end_xy[0]) * d[0] + (q.Y * FT - end_xy[1]) * d[1]
                        if abs(s) < 0.005:
                            zs.append(q.Z)
            elif isinstance(x, DB.GeometryInstance):
                walk(x.GetInstanceGeometry())
    walk(stairs.get_Geometry(opt))
    return min(zs) if zs else None


def clip_to_outline(solid, poly, z_lo, z_hi):
    """(2026-11-22) The solid kept within a plan outline (m): the wedge under
    a landing stays under that landing - a triangular landing piece, nothing
    sticking out past its sides. The solid itself when the cut fails."""
    try:
        ring = DB.CurveLoop()
        pp = [DB.XYZ(x / FT, y / FT, z_lo) for x, y in poly]
        for k in range(len(pp)):
            ring.Append(DB.Line.CreateBound(pp[k], pp[(k + 1) % len(pp)]))
        prism = DB.GeometryCreationUtilities.CreateExtrusionGeometry(
            List[DB.CurveLoop]([ring]), DB.XYZ.BasisZ, z_hi - z_lo)
        cut = DB.BooleanOperationsUtils.ExecuteBooleanOperation(solid, prism, DB.BooleanOperationsType.Intersect)
        return cut if cut is not None and cut.Volume > 1e-6 else None  # nothing under it
    except Exception:
        return solid


def waist_wedge(doc, base_level, run, stairs, landing, thickness, width, riser, tread, avoid=None):
    """(2026-11-21) A run reaching a landing ends square at its edge, its
    waist a few cm under the landing's soffit (Revit can't carry a run's
    waist on past its top): the wedge of concrete between them - the waist
    carried on to the soffit, as built - as a DirectShape of Floors,
    `width` across. None when the waist meets the soffit already."""
    d = run["dir"]
    n = (-d[1], d[0])
    end = run["end"]
    zb = _end_bottom(doc, stairs, end, d)
    if zb is None:
        return None
    soffit = base_level.Elevation + (landing["z"] - thickness) / FT
    drop = soffit - zb  # ft
    if drop < 0.002 / FT:
        return None
    reach = drop * (tread / riser)  # along the waist's slope up to the soffit
    h = width / 2.0 / FT
    ex, ey = end[0] / FT, end[1] / FT

    def P(t, v, z):
        return DB.XYZ(ex + d[0] * t + n[0] * v, ey + d[1] * t + n[1] * v, z)
    loop = DB.CurveLoop()
    tri = [P(0, -h, zb), P(reach, -h, soffit), P(0, -h, soffit)]
    for k in range(3):
        loop.Append(DB.Line.CreateBound(tri[k], tri[(k + 1) % 3]))
    solid = DB.GeometryCreationUtilities.CreateExtrusionGeometry(
        List[DB.CurveLoop]([loop]), DB.XYZ(n[0], n[1], 0.0), 2 * h)
    solid = clip_to_outline(solid, landing["poly"], zb - 0.1, soffit + 0.1)
    if solid is None:
        return None
    for other in (avoid or []):  # no concrete counted twice (a connecting slab there)
        try:
            rest = DB.BooleanOperationsUtils.ExecuteBooleanOperation(solid, other, DB.BooleanOperationsType.Difference)
            if rest is None or rest.Volume < 1e-6:
                return None
            solid = rest
        except Exception:
            pass
    ds =DB.DirectShape.CreateElement(doc, DB.ElementId(DB.BuiltInCategory.OST_Floors))
    ds.SetShape(List[DB.GeometryObject]([solid]))
    ds.Name = u"BOKI Escalera - garganta al descanso"
    return ds


def _shared_edge(pa, pb, tol=0.05):
    """The stretch (p, q) (m) where a side of polygon pa lies on a side of
    pb (two landing pieces touching), longest one; None when they don't."""
    import math
    best = None
    for i in range(len(pa)):
        a0, a1 = pa[i], pa[(i + 1) % len(pa)]
        L = math.hypot(a1[0] - a0[0], a1[1] - a0[1])
        if L < 1e-6:
            continue
        d = ((a1[0] - a0[0]) / L, (a1[1] - a0[1]) / L)
        for j in range(len(pb)):
            b0, b1 = pb[j], pb[(j + 1) % len(pb)]
            off = [abs((p[0] - a0[0]) * d[1] - (p[1] - a0[1]) * d[0]) for p in (b0, b1)]
            if max(off) > tol:
                continue
            ts = sorted([(p[0] - a0[0]) * d[0] + (p[1] - a0[1]) * d[1] for p in (b0, b1)])
            lo, hi = max(0.0, ts[0]), min(L, ts[1])
            if hi - lo > 0.05 and (best is None or hi - lo > best[2]):
                best = ((a0[0] + d[0] * lo, a0[1] + d[1] * lo), (a0[0] + d[0] * hi, a0[1] + d[1] * hi), hi - lo)
    return best


def step_connectors(doc, base_level, landings, thickness, slope=1.0):
    """(2026-11-22) Landing pieces at two heights side by side (a landing
    drawn with risers across it): under the step between them, the slab
    that joins them - the lower one's soffit rising at 45 degrees to the
    upper one's - so they are one piece of concrete, to be reinforced.
    DirectShapes of Floors (inside an open Transaction)."""
    import math
    made = []
    for i in range(len(landings)):
        for j in range(len(landings)):
            lo, hi = landings[i], landings[j]
            dz = hi["z"] - lo["z"]
            if dz <= 0.01 or dz > 0.5:
                continue
            edge = _shared_edge(lo["poly"], hi["poly"])
            if edge is None:
                continue
            p, q, _ = edge
            ex, ey = q[0] - p[0], q[1] - p[1]
            L = math.hypot(ex, ey)
            n = (-ey / L, ex / L)
            cx = sum(v[0] for v in hi["poly"]) / len(hi["poly"])
            cy = sum(v[1] for v in hi["poly"]) / len(hi["poly"])
            if (cx - p[0]) * n[0] + (cy - p[1]) * n[1] < 0:
                n = (-n[0], -n[1])  # towards the upper piece
            z0 = base_level.Elevation + (lo["z"] - thickness) / FT
            z1 = base_level.Elevation + (hi["z"] - thickness) / FT
            run = (z1 - z0) / max(slope, 0.05)  # as steep as the runs (1.0: 45 degrees)

            def P(u, z):
                return DB.XYZ(p[0] / FT + n[0] * u, p[1] / FT + n[1] * u, z)
            pts = [P(0.0, z0), P(run, z1), P(0.0, z1)]
            loop = DB.CurveLoop()
            for k in range(3):
                loop.Append(DB.Line.CreateBound(pts[k], pts[(k + 1) % 3]))
            solid = DB.GeometryCreationUtilities.CreateExtrusionGeometry(
                List[DB.CurveLoop]([loop]), DB.XYZ(ex / L, ey / L, 0.0), L / FT)
            # kept under the upper piece's outline (a triangular landing piece:
            # nothing sticking out past its sides)
            try:
                ring = DB.CurveLoop()
                pp = [DB.XYZ(x / FT, y / FT, z0 - 0.1) for x, y in hi["poly"]]
                for k in range(len(pp)):
                    ring.Append(DB.Line.CreateBound(pp[k], pp[(k + 1) % len(pp)]))
                prism = DB.GeometryCreationUtilities.CreateExtrusionGeometry(
                    List[DB.CurveLoop]([ring]), DB.XYZ.BasisZ, (z1 - z0) + 0.2)
                cut = DB.BooleanOperationsUtils.ExecuteBooleanOperation(
                    solid, prism, DB.BooleanOperationsType.Intersect)
                if cut is not None and cut.Volume > 1e-6:
                    solid = cut
            except Exception:
                pass
            ds = DB.DirectShape.CreateElement(doc, DB.ElementId(DB.BuiltInCategory.OST_Floors))
            ds.SetShape(List[DB.GeometryObject]([solid]))
            ds.Name = u"BOKI Escalera - losa entre descansos"
            made.append(ds)
    return made


def _hull(points):
    pts = sorted(set((round(x, 4), round(y, 4)) for x, y in points))
    if len(pts) < 3:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def snap_pieces(landings, tol=0.06):
    """(2026-11-22) The corners of landing pieces a few cm apart (Revit's
    sketch leaves slits between them) brought to one point."""
    pts = [p for l in landings for p in l["poly"]]
    groups = []
    for p in pts:
        g = next((g for g in groups if abs(g[0][0] - p[0]) < tol and abs(g[0][1] - p[1]) < tol), None)
        if g is None:
            groups.append([p])
        else:
            g.append(p)

    def snap(p):
        g = next(g for g in groups if p in g)
        return (sum(q[0] for q in g) / len(g), sum(q[1] for q in g) / len(g))
    out = []
    for l in landings:
        poly = []
        for p in l["poly"]:
            q = snap(p)
            if not poly or abs(q[0] - poly[-1][0]) > 1e-6 or abs(q[1] - poly[-1][1]) > 1e-6:
                poly.append(q)
        out.append(dict(l, poly=poly))
    return out


def stepped_landing(doc, base_level, landings, thickness, ftype):
    """(2026-11-22) A landing drawn in pieces at several heights (risers
    across it), built as in the field: ONE slab over the whole landing (its
    outline closed - no slit between the pieces), flat soffit, at the lowest
    piece's height; on it, every higher piece as a concrete step block up
    to its own height. Returns ([elements], {"poly", "z"} of the slab)."""
    z_low = min(l["z"] for l in landings)
    # the pieces' corners a few cm apart (Revit's sketch leaves slits between
    # them) brought to one point: the steps close on each other
    pts = [p for l in landings for p in l["poly"]]
    groups = []
    for p in pts:
        g = next((g for g in groups if abs(g[0][0] - p[0]) < 0.06 and abs(g[0][1] - p[1]) < 0.06), None)
        if g is None:
            groups.append([p])
        else:
            g.append(p)

    def snap(p):
        g = next(g for g in groups if p in g)
        return (sum(q[0] for q in g) / len(g), sum(q[1] for q in g) / len(g))
    landings = [dict(l, poly=[snap(p) for p in l["poly"]]) for l in landings]
    hull = _hull([p for l in landings for p in l["poly"]])
    slab_landing = {"poly": hull, "z": z_low}
    made = [create_landing(doc, base_level, slab_landing, ftype)]
    for l in landings:
        h = l["z"] - z_low
        if h < 0.005:
            continue
        ring = DB.CurveLoop()
        z0 = base_level.Elevation + z_low / FT
        pp = [DB.XYZ(x / FT, y / FT, z0) for x, y in l["poly"]]
        for k in range(len(pp)):
            ring.Append(DB.Line.CreateBound(pp[k], pp[(k + 1) % len(pp)]))
        solid = DB.GeometryCreationUtilities.CreateExtrusionGeometry(
            List[DB.CurveLoop]([ring]), DB.XYZ.BasisZ, h / FT)
        ds = DB.DirectShape.CreateElement(doc, DB.ElementId(DB.BuiltInCategory.OST_Floors))
        ds.SetShape(List[DB.GeometryObject]([solid]))
        ds.Name = u"BOKI Escalera - escalón sobre el descanso"
        made.append(ds)
    return made, slab_landing


def step_fills(doc, base_level, landings, thickness):
    """(2026-11-22) A landing drawn in pieces at several heights is built as
    ONE slab with a flat soffit (the lowest piece's) and the steps on it:
    under every higher piece, the concrete from that soffit up to its own
    slab - straight sides, inside its outline. DirectShapes of Floors."""
    if not landings:
        return []
    z_low = min(l["z"] for l in landings) - thickness
    made = []
    for l in landings:
        z_soffit = l["z"] - thickness
        if z_soffit - z_low < 0.005:
            continue
        ring = DB.CurveLoop()
        z0 = base_level.Elevation + z_low / FT
        pp = [DB.XYZ(x / FT, y / FT, z0) for x, y in l["poly"]]
        for k in range(len(pp)):
            ring.Append(DB.Line.CreateBound(pp[k], pp[(k + 1) % len(pp)]))
        solid = DB.GeometryCreationUtilities.CreateExtrusionGeometry(
            List[DB.CurveLoop]([ring]), DB.XYZ.BasisZ, (z_soffit - z_low) / FT)
        ds = DB.DirectShape.CreateElement(doc, DB.ElementId(DB.BuiltInCategory.OST_Floors))
        ds.SetShape(List[DB.GeometryObject]([solid]))
        ds.Name = u"BOKI Escalera - relleno bajo escalón del descanso"
        made.append(ds)
    return made


def create_stair(doc, base_level, top_level, info, plan, tread, width):
    """The whole stair: its runs, then its landings in one transaction.
    Returns ([Stairs], [Floor], messages)."""
    messages = []
    runs = []
    want = getattr(info, "waist", None)
    have = getattr(info, "type_waist", None)
    if want and have is not None and abs(want - have) >= 1e-4:  # a waist typed: its type
        import copy
        t = DB.Transaction(doc, u"BOKI Escalera - garganta")
        t.Start()
        stype = stair_type_with_waist(doc, info)
        t.Commit()
        info = copy.copy(info)
        info.type = stype
    for k, r in enumerate(plan["runs"]):
        r = dict(r, from_landing=info.landing if k else 0.0)  # down to its landing piece's soffit
        runs.append(create_run(doc, base_level, top_level, info, r, plan["riser"], tread, width, messages))
    floors = []
    if plan["landings"]:
        t = DB.Transaction(doc, u"BOKI Escalera - descansos")
        options = t.GetFailureHandlingOptions()
        options.SetFailuresPreprocessor(_Quiet(messages))
        t.SetFailureHandlingOptions(options)
        t.Start()
        ftype = landing_floor_type(doc, info.landing)
        # (2026-11-22, the user) landing pieces at their heights: each its own
        # slab, same thickness - the soffit stepped like the top, the pieces
        # tied by their side (thicker than a riser); or, chosen, a sloped
        # soffit between them, as steep as the runs
        floors = [create_landing(doc, base_level, l, ftype) for l in plan["landings"]]
        doc.Regenerate()
        if plan.get("steps") and plan.get("soffit") == "sloped":
            try:
                floors += step_connectors(doc, base_level, plan["landings"], info.landing,
                                          slope=plan["riser"] / tread)
            except Exception as e:
                messages.append(u"Fondo inclinado: {}".format(e))
        for k, r in enumerate(plan["runs"][:-1]):
            # the landing this run reaches: at its top
            if plan.get("steps"):
                # the piece the run reaches (at its top): its waist carried on
                # to that piece's soffit, under that piece only
                reached = [x for x in plan["landings"] if abs(x["z"] - r["z1"]) <= 0.01]
            else:
                l = min(plan["landings"], key=lambda x: abs(x["z"] - r["z1"])) if plan["landings"] else None
                reached = [l] if (l is not None and abs(l["z"] - r["z1"]) <= 0.01) else []
            for l in reached:
                try:
                    avoid = [g for f in floors if isinstance(f, DB.DirectShape)
                             for g in f.get_Geometry(DB.Options()) if isinstance(g, DB.Solid) and g.Volume > 0]
                    w = waist_wedge(doc, base_level, r, runs[k], l, info.landing, width,
                                    plan["riser"], tread, avoid)
                    if w is not None:
                        floors.append(w)
                except Exception as e:
                    messages.append(u"Cuña de garganta {}: {}".format(k + 1, e))
        t.Commit()
    return runs, floors, messages
