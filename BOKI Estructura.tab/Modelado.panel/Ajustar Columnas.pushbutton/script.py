# -*- coding: utf-8 -*-
"""Ajustar columnas: the top (or base) of the picked columns attached to a
beam, floor or roof - Revit cuts each column to that element's face
(sloped too), no overlap left. Columns already selected are used; else
they are picked after the element."""
__title__ = "Ajustar\nColumnas"
__author__ = "BOKI"

from pyrevit import revit, DB, forms
from Autodesk.Revit.DB import ColumnAttachment, ColumnAttachmentCutStyle, ColumnAttachmentJustification
from Autodesk.Revit.Exceptions import OperationCanceledException
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType

doc, uidoc = revit.doc, revit.uidoc
COLUMN = int(DB.BuiltInCategory.OST_StructuralColumns)
TARGETS = (int(DB.BuiltInCategory.OST_StructuralFraming), int(DB.BuiltInCategory.OST_Floors),
           int(DB.BuiltInCategory.OST_Roofs), int(DB.BuiltInCategory.OST_StructuralFoundation))


def _cat(e):
    return e.Category.Id.IntegerValue if e is not None and e.Category is not None else None


class _Filter(ISelectionFilter):
    def __init__(self, cats):
        self.cats = cats

    def AllowElement(self, e):
        return _cat(e) in self.cats

    def AllowReference(self, ref, point):
        return False


columns = [e for e in (doc.GetElement(i) for i in uidoc.Selection.GetElementIds()) if _cat(e) == COLUMN]
where = forms.CommandSwitchWindow.show([u"Parte superior (bajo la viga / losa)", u"Base (sobre la viga / zapata)"],
                                       message=u"BOKI · ¿Qué extremo de la columna ajustar?")
if not where:
    raise SystemExit
top = where.startswith(u"Parte superior")
try:
    target = doc.GetElement(uidoc.Selection.PickObject(
        ObjectType.Element, _Filter(TARGETS), u"Elige la viga, losa o techo donde se ajustan las columnas"))
    if not columns:
        columns = [doc.GetElement(r) for r in uidoc.Selection.PickObjects(
            ObjectType.Element, _Filter((COLUMN,)), u"Elige las columnas a ajustar y pulsa Finalizar")]
except OperationCanceledException:
    raise SystemExit

done, failed = 0, []
with revit.Transaction(u"BOKI Ajustar columnas"):
    for c in columns:
        try:
            old = ColumnAttachment.GetColumnAttachment(c, 1 if top else 0)
            if old is not None:
                ColumnAttachment.RemoveColumnAttachment(c, 1 if top else 0)
            ColumnAttachment.AddColumnAttachment(doc, c, target, 1 if top else 0,
                                                 ColumnAttachmentCutStyle.CutColumn,
                                                 ColumnAttachmentJustification.Minimum, 0.0)
            done += 1
        except Exception as e:
            failed.append(u"{}: {}".format(c.Id.IntegerValue, e))
if failed:
    forms.alert(u"Ajustadas {} columna(s). No se pudo:\n- ".format(done) + u"\n- ".join(failed[:10]), title=u"BOKI")
