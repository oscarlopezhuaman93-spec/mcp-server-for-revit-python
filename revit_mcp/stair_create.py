# -*- coding: utf-8 -*-
"""Build a sketched concrete stair (stair_plan) in Revit: every run is its
own straight Revit stair of the chosen stair type - alone, its waist stays
straight (joined to landings in one stair, Revit bends the waist of a short
run) and Acero Escalera reads it -, every landing a structural floor as
thick as the type's landing, its top at the landing height."""
from pyrevit import DB
from Autodesk.Revit.DB import StairsEditScope
from Autodesk.Revit.DB.Architecture import StairsType, StairsRun, StairsRunJustification
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


def create_stair(doc, base_level, top_level, info, plan, tread, width):
    """The whole stair: its runs, then its landings in one transaction.
    Returns ([Stairs], [Floor], messages)."""
    messages = []
    runs = [create_run(doc, base_level, top_level, info, r, plan["riser"], tread, width, messages)
            for r in plan["runs"]]
    floors = []
    if plan["landings"]:
        t = DB.Transaction(doc, u"BOKI Escalera - descansos")
        options = t.GetFailureHandlingOptions()
        options.SetFailuresPreprocessor(_Quiet(messages))
        t.SetFailureHandlingOptions(options)
        t.Start()
        ftype = landing_floor_type(doc, info.landing)
        floors = [create_landing(doc, base_level, l, ftype) for l in plan["landings"]]
        t.Commit()
    return runs, floors, messages
