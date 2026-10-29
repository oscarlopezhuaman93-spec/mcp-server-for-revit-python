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


def _fit(e, targets, top):
    """`targets`: one or several (a column under a ridge: both sloped
    beams, each cutting it with its own face)."""
    end = 1 if top else 0
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
where = forms.CommandSwitchWindow.show([u"Parte superior (bajo el elemento)", u"Base (sobre el elemento)"],
                                       message=u"BOKI · ¿Qué extremo ajustar? (columnas y muros)")
if not where:
    raise SystemExit
top = where.startswith(u"Parte superior")
from System.Collections.Generic import List
uidoc.Selection.SetElementIds(List[DB.ElementId]())  # else the picked elements start as targets
try:
    targets =[doc.GetElement(r) for r in uidoc.Selection.PickObjects(
        ObjectType.Element, _Model(),
        u"Elige el elemento o los elementos donde se ajustan (ej. las dos vigas de una cumbrera) y pulsa Finalizar")]
    if not targets:
        raise SystemExit
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
