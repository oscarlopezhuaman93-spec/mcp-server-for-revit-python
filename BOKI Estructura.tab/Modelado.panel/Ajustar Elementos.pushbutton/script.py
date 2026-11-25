# -*- coding: utf-8 -*-
"""Ajustar elementos: the picked elements fitted to a target element.
- columns (structural or architectural): top or base attached to it,
  cut along its face (sloped too), flush;
- walls: top or base attached to it (Revit's own attach);
- anything else: joined, the target cutting it (no overlap).
Elements already selected are used; else they are picked after the target."""
__title__ = "Ajustar\nElementos"
__author__ = "BOKI"

from pyrevit import revit, DB, forms
from Autodesk.Revit.DB import ColumnAttachment, ColumnAttachmentCutStyle, ColumnAttachmentJustification
from Autodesk.Revit.Exceptions import OperationCanceledException
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType

doc, uidoc = revit.doc, revit.uidoc
COLUMNS = (int(DB.BuiltInCategory.OST_StructuralColumns), int(DB.BuiltInCategory.OST_Columns))


def _cat(e):
    return e.Category.Id.IntegerValue if e is not None and e.Category is not None else None


class _Model(ISelectionFilter):
    """Model elements only (no views, annotations, levels...)."""

    def __init__(self, exclude=None):
        self.exclude = exclude

    def AllowElement(self, e):
        return e.Category is not None and e.Category.CategoryType == DB.CategoryType.Model and \
            (self.exclude is None or e.Id != self.exclude)

    def AllowReference(self, ref, point):
        return False


def _solids(e):
    out = []
    for g in e.get_Geometry(DB.Options()):
        items = [g] if isinstance(g, DB.Solid) else (
            list(g.GetInstanceGeometry()) if isinstance(g, DB.GeometryInstance) else [])
        out += [s for s in items if isinstance(s, DB.Solid) and s.Volume > 1e-6]
    return out


def _sloped(b):
    curve = getattr(getattr(b, "Location", None), "Curve", None)
    if not isinstance(curve, DB.Line):
        return False
    p0, p1 = curve.GetEndPoint(0), curve.GetEndPoint(1)
    plan = ((p1.X - p0.X) ** 2 + (p1.Y - p0.Y) ** 2) ** 0.5
    return plan > 1e-6 and abs(p1.Z - p0.Z) / plan > 0.02


def _fit_under_roof(e, targets):
    """A column under sloped beams (a gable: one or both waters): its top up
    to the lowest top of the concrete over it, and every sloped beam over it
    joined and cutting it - the column takes both slopes, peak under the
    ridge (2026-11-13, the user: one sloped beam picked cut it along that
    water only, or not at all). False when the beams don't cover its whole
    head (then the usual attachment)."""
    if not any(_sloped(t) for t in targets if _cat(t) == int(DB.BuiltInCategory.OST_StructuralFraming)):
        return False
    b = e.get_BoundingBox(None)
    out = DB.Outline(DB.XYZ(b.Min.X, b.Min.Y, b.Max.Z - 2.0 / 0.3048), DB.XYZ(b.Max.X, b.Max.Y, b.Max.Z + 4.0 / 0.3048))
    beams = [x for x in DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_StructuralFraming)
             .WhereElementIsNotElementType().WherePasses(DB.BoundingBoxIntersectsFilter(out)) if _sloped(x)]
    ids = set(x.Id for x in beams)
    beams += [t for t in targets if t.Id not in ids and _sloped(t)]
    solids = [s for x in beams for s in _solids(x)]
    tops = []
    # at the very edges of its head (1 mm in): a sloped top is lowest there -
    # measured 2 cm in, a sliver of the column stood out of the roof
    edge = 0.001 / 0.3048
    for i in range(9):
        for j in range(5):
            x = b.Min.X + edge + (b.Max.X - b.Min.X - 2 * edge) * i / 8.0
            y = b.Min.Y + edge + (b.Max.Y - b.Min.Y - 2 * edge) * j / 4.0
            ray = DB.Line.CreateBound(DB.XYZ(x, y, b.Max.Z + 6.0 / 0.3048), DB.XYZ(x, y, b.Min.Z))
            zt = None
            for s in solids:
                r = s.IntersectWithCurve(ray, DB.SolidCurveIntersectionOptions())
                for k in range(r.SegmentCount):
                    seg = r.GetCurveSegment(k)
                    z = max(seg.GetEndPoint(0).Z, seg.GetEndPoint(1).Z)
                    zt = z if zt is None else max(zt, z)
            if zt is None:
                return False  # a part of its head under no beam
            tops.append(zt)
    while ColumnAttachment.GetColumnAttachment(e, 1) is not None:
        ColumnAttachment.RemoveColumnAttachment(e, 1)
    level = doc.GetElement(e.get_Parameter(DB.BuiltInParameter.FAMILY_TOP_LEVEL_PARAM).AsElementId())
    # 2 mm under the lowest top: never a hair of it out of the concrete
    e.get_Parameter(DB.BuiltInParameter.FAMILY_TOP_LEVEL_OFFSET_PARAM).Set(min(tops) - 0.002 / 0.3048 - level.Elevation)
    _join_cut(e, beams)
    return True


def _fit(e, targets, top):
    """`targets`: one or several (a column under a ridge: both sloped
    beams, each cutting it with its own face)."""
    end = 1 if top else 0
    if top and _cat(e) in COLUMNS and isinstance(e, DB.FamilyInstance) and _fit_under_roof(e, targets):
        return
    if _cat(e) in COLUMNS and isinstance(e, DB.FamilyInstance):
        while ColumnAttachment.GetColumnAttachment(e, end) is not None:
            ColumnAttachment.RemoveColumnAttachment(e, end)
        # one attachment per end (Revit): up to the first target's highest
        # point, cut along its face; the others join it and cut what is left
        # on their side (a column under a ridge: both sloped faces)
        ColumnAttachment.AddColumnAttachment(doc, e, targets[0], end, ColumnAttachmentCutStyle.CutColumn,
                                             ColumnAttachmentJustification.Maximum, 0.0)
        _join_cut(e, targets[1:])
        return
    if isinstance(e, DB.Wall):
        for target in targets:
            e.AddAttachment(target.Id, DB.AttachmentLocation.Top if top else DB.AttachmentLocation.Base)
        return
    _join_cut(e, targets)


def _join_cut(e, targets):
    J = DB.JoinGeometryUtils
    for target in targets:
        if not J.AreElementsJoined(doc, e, target):
            J.JoinGeometry(doc, e, target)
        if not J.IsCuttingElementInJoin(doc, target, e):
            J.SwitchJoinOrder(doc, target, e)


picked = [doc.GetElement(i) for i in uidoc.Selection.GetElementIds()]
picked = [e for e in picked if e is not None and _Model().AllowElement(e)]
NODE = u"Nudo de cumbrera (columna hasta la cumbrera, vigas contra ella)"
where = forms.CommandSwitchWindow.show([u"Parte superior (bajo el elemento)", u"Base (sobre el elemento)", NODE],
                                       message=u"BOKI · ¿Qué ajustar?")
if not where:
    raise SystemExit
from System.Collections.Generic import List


def ridge_node(c):
    """The column up to the top of the beams around its head (a ridge):
    its own attachment removed, every one of those beams joined and cut by
    it - the node cast with the column, the beams ending against it."""
    if ColumnAttachment.GetColumnAttachment(c, 1) is not None:
        ColumnAttachment.RemoveColumnAttachment(c, 1)
    cb = c.get_BoundingBox(None)
    pad = 0.4 / 0.3048
    out = DB.Outline(DB.XYZ(cb.Min.X - pad, cb.Min.Y - pad, cb.Max.Z - 1.0 / 0.3048),
                     DB.XYZ(cb.Max.X + pad, cb.Max.Y + pad, cb.Max.Z + 3.0 / 0.3048))
    beams = [b for b in DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_StructuralFraming)
             .WhereElementIsNotElementType().WherePasses(DB.BoundingBoxIntersectsFilter(out))]
    beams = [b for b in beams if b.get_BoundingBox(None).Min.X < cb.Max.X + 0.05 and b.get_BoundingBox(None).Max.X > cb.Min.X - 0.05
             and b.get_BoundingBox(None).Min.Y < cb.Max.Y + 0.05 and b.get_BoundingBox(None).Max.Y > cb.Min.Y - 0.05]
    if not beams:
        raise Exception(u"no hay vigas en su cabeza")
    ztop = max(b.get_BoundingBox(None).Max.Z for b in beams)
    level = doc.GetElement(c.get_Parameter(DB.BuiltInParameter.FAMILY_TOP_LEVEL_PARAM).AsElementId())
    c.get_Parameter(DB.BuiltInParameter.FAMILY_TOP_LEVEL_OFFSET_PARAM).Set(ztop - level.Elevation)
    J = DB.JoinGeometryUtils
    for b in beams:
        if not J.AreElementsJoined(doc, c, b):
            try:
                J.JoinGeometry(doc, c, b)
            except Exception:
                continue
        if not J.IsCuttingElementInJoin(doc, c, b):
            J.SwitchJoinOrder(doc, c, b)


if where == NODE:
    cols = [e for e in picked if _cat(e) in COLUMNS]
    if not cols:
        try:
            cols = [doc.GetElement(r) for r in uidoc.Selection.PickObjects(
                ObjectType.Element, _Model(), u"Elige las columnas de la cumbrera y pulsa Finalizar")]
        except OperationCanceledException:
            raise SystemExit
        cols = [e for e in cols if _cat(e) in COLUMNS]
    done, failed = 0, []
    with revit.Transaction(u"BOKI Nudo de cumbrera"):
        for c in cols:
            try:
                ridge_node(c)
                done += 1
            except Exception as ex:
                failed.append(u"{}: {}".format(c.Id.IntegerValue, ex))
    if failed:
        forms.alert(u"Nudos hechos: {}. No se pudo:\n- ".format(done) + u"\n- ".join(failed[:10]), title=u"BOKI")
    raise SystemExit
top = where.startswith(u"Parte superior")
uidoc.Selection.SetElementIds(List[DB.ElementId]())  # else the picked elements start as targets
try:
    # one click and done (a ridge column: the "Nudo de cumbrera" option)
    targets = [doc.GetElement(uidoc.Selection.PickObject(
        ObjectType.Element, _Model(), u"Clic en el elemento donde se ajustan (viga, losa, techo...)"))]
    ids = set(x.Id for x in targets)
    elements = [e for e in picked if e.Id not in ids]
    if not elements:
        elements = [doc.GetElement(r) for r in uidoc.Selection.PickObjects(
            ObjectType.Element, _Model(), u"Elige los elementos a ajustar y pulsa Finalizar")]
        elements = [e for e in elements if e.Id not in ids]
except OperationCanceledException:
    raise SystemExit

done, failed = 0, []
with revit.Transaction(u"BOKI Ajustar elementos"):
    for e in elements:
        try:
            _fit(e, targets, top)
            done += 1
        except Exception as ex:
            failed.append(u"{} {}: {}".format(e.Category.Name, e.Id.IntegerValue, ex))
if failed:
    forms.alert(u"Ajustados {} elemento(s). No se pudo:\n- ".format(done) + u"\n- ".join(failed[:10]), title=u"BOKI")
