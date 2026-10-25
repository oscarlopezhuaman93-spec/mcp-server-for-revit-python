# -*- coding: utf-8 -*-
"""Build a sketched vehicle ramp (ramp_plan) in Revit with the chosen floor
type: a straight piece is a planar floor with a slope arrow; an arc piece
is a floor on the curved outline with its top shaped point by point along
both edges (warped like a real curved ramp). The pieces share their edges
at the same heights and are joined to one another and to the beams, walls
and columns they touch (the beams cut the ramp)."""
from pyrevit import DB
from System.Collections.Generic import List

import slab_regions as sg
import slab_fix as sf

FT = 0.3048


class _Quiet(DB.IFailuresPreprocessor):
    """Warnings dropped (kept in `messages`): no dialog stops the ramp."""

    def __init__(self, messages):
        self.messages = messages

    def PreprocessFailures(self, accessor):
        for f in accessor.GetFailureMessages():
            self.messages.append(f.GetDescriptionText())
            if f.GetSeverity() == DB.FailureSeverity.Warning:
                accessor.DeleteWarning(f)
        return DB.FailureProcessingResult.Continue


def _tx(doc, name, messages):
    t = DB.Transaction(doc, name)
    options = t.GetFailureHandlingOptions()
    options.SetFailuresPreprocessor(_Quiet(messages))
    options.SetForcedModalHandling(False)
    t.SetFailureHandlingOptions(options)
    t.Start()
    return t


def _xyz(p, z):
    return DB.XYZ(p[0] / FT, p[1] / FT, z)


def _line_piece(doc, level, ftype, piece, z_base):
    z = level.Elevation
    loop = DB.CurveLoop()
    pts = piece["outline"]
    for k in range(len(pts)):
        loop.Append(DB.Line.CreateBound(_xyz(pts[k], z), _xyz(pts[(k + 1) % len(pts)], z)))
    (ax, ay), (bx, by) = piece["arrow"]
    arrow = DB.Line.CreateBound(_xyz((ax, ay), z), _xyz((bx, by), z))
    floor = DB.Floor.Create(doc, List[DB.CurveLoop]([loop]), ftype.Id, level.Id, True, arrow, piece["slope"])
    floor.get_Parameter(DB.BuiltInParameter.FLOOR_HEIGHTABOVELEVEL_PARAM).Set((z_base + piece["z0"]) / FT)
    return floor


def _arc_piece(doc, level, ftype, piece):
    """The flat floor on the arc's outline (its top shaped afterwards)."""
    z = level.Elevation
    left, right = piece["left"], piece["right"]
    mid = len(left) // 2
    loop = DB.CurveLoop()
    loop.Append(DB.Line.CreateBound(_xyz(right[0], z), _xyz(left[0], z)))
    loop.Append(DB.Arc.Create(_xyz(left[0], z), _xyz(left[-1], z), _xyz(left[mid], z)))
    loop.Append(DB.Line.CreateBound(_xyz(left[-1], z), _xyz(right[-1], z)))
    loop.Append(DB.Arc.Create(_xyz(right[-1], z), _xyz(right[0], z), _xyz(right[mid], z)))
    return DB.Floor.Create(doc, List[DB.CurveLoop]([loop]), ftype.Id, level.Id)


def _shape_arc(doc, floor, piece, z_base, messages):
    """Its top raised point by point along both edges (own transactions:
    Revit only lists the shape points once enabling is committed)."""
    t = _tx(doc, u"BOKI Rampa - forma", messages)
    floor.GetSlabShapeEditor().Enable()
    t.Commit()
    t = _tx(doc, u"BOKI Rampa - forma", messages)
    editor = floor.GetSlabShapeEditor()
    z = floor.Document.GetElement(floor.LevelId).Elevation
    stations = piece["left"] + piece["right"]
    for x, y, h in stations:
        try:
            v = editor.DrawPoint(_xyz((x, y), z))
            if v is not None:
                editor.ModifySubElement(v, (z_base + h) / FT)
        except Exception:
            pass  # a corner already there: set below
    t.Commit()
    t = _tx(doc, u"BOKI Rampa - forma", messages)
    editor = floor.GetSlabShapeEditor()
    for v in editor.SlabShapeVertices:
        p = v.Position
        near = min(stations, key=lambda q: (q[0] - p.X * FT) ** 2 + (q[1] - p.Y * FT) ** 2)
        if (near[0] - p.X * FT) ** 2 + (near[1] - p.Y * FT) ** 2 < 1e-4:
            try:
                editor.ModifySubElement(v, (z_base + near[2]) / FT)
            except Exception:
                pass
    t.Commit()


def create_ramp(doc, level, ftype, plan, z_base=0.0, join=True):
    """The ramp's floors bottom up (no transaction may be open; group them
    in a TransactionGroup). `z_base`: the ramp's foot over `level` (m).
    Returns ([Floor], messages)."""
    messages = []
    floors = []
    for piece in plan["pieces"]:
        t = _tx(doc, u"BOKI Rampa - tramo", messages)
        if piece["kind"] == "line":
            floor = _line_piece(doc, level, ftype, piece, z_base)
        else:
            floor = _arc_piece(doc, level, ftype, piece)
        s = floor.get_Parameter(DB.BuiltInParameter.FLOOR_PARAM_IS_STRUCTURAL)
        if s is not None and not s.IsReadOnly:
            s.Set(1)
        t.Commit()
        if piece["kind"] == "arc":
            _shape_arc(doc, floor, piece, z_base, messages)
        floors.append(floor)
    t = _tx(doc, u"BOKI Rampa - uniones", messages)
    for a, b in zip(floors, floors[1:]):  # one continuous slab
        try:
            if not DB.JoinGeometryUtils.AreElementsJoined(doc, a, b):
                DB.JoinGeometryUtils.JoinGeometry(doc, a, b)
        except Exception:
            pass
    if join:
        for f in floors:
            try:
                sf.join_beams_cutting(doc, f)
            except Exception:
                pass
    t.Commit()
    return floors, messages
