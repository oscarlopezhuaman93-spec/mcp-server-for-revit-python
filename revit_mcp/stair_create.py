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


def waist_wedge(doc, base_level, run, stairs, landing, thickness, width, riser, tread):
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
    ds = DB.DirectShape.CreateElement(doc, DB.ElementId(DB.BuiltInCategory.OST_Floors))
    ds.SetShape(List[DB.GeometryObject]([solid]))
    ds.Name = u"BOKI Escalera - garganta al descanso"
    return ds


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
        r = dict(r, from_landing=info.landing if k else 0.0)
        runs.append(create_run(doc, base_level, top_level, info, r, plan["riser"], tread, width, messages))
    floors = []
    if plan["landings"]:
        t = DB.Transaction(doc, u"BOKI Escalera - descansos")
        options = t.GetFailureHandlingOptions()
        options.SetFailuresPreprocessor(_Quiet(messages))
        t.SetFailureHandlingOptions(options)
        t.Start()
        ftype = landing_floor_type(doc, info.landing)
        floors = [create_landing(doc, base_level, l, ftype) for l in plan["landings"]]
        # each run's waist carried on to the soffit of the landing it reaches
        doc.Regenerate()
        for k, l in enumerate(plan["landings"]):
            try:
                w = waist_wedge(doc, base_level, plan["runs"][k], runs[k], l, info.landing, width,
                                plan["riser"], tread)
                if w is not None:
                    floors.append(w)
            except Exception as e:
                messages.append(u"Cuña de garganta {}: {}".format(k + 1, e))
        t.Commit()
    return runs, floors, messages
