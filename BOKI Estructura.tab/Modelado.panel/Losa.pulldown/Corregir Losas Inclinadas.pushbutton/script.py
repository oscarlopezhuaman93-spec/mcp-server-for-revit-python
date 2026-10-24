# -*- coding: utf-8 -*-
"""Corrige las losas inclinadas hechas con edición de forma: Revit les deja
el espesor del tipo en vertical (una losa de 0.20 a 23 grados mide 0.184
perpendicular y no llega al fondo de una viga cumbrera bien modelada). Las
vuelve a crear con flecha de pendiente - el espesor del tipo perpendicular
a la cara -, con el mismo contorno, la misma cara superior, sus parámetros,
y unidas a sus vigas (las vigas las cortan)."""

__title__ = "Corregir\nLosas Inclinadas"
__author__ = "Revit MCP"

import os
import sys

SCRIPT_DIR = os.path.dirname(__file__)
REVIT_MCP_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", "..", "..", "revit_mcp"))
if REVIT_MCP_DIR not in sys.path:
    sys.path.append(REVIT_MCP_DIR)

import slab_create as sc
import slab_regions as sg
import slab_fix as sf
import rebar_slabs as rsl

reload(sc)
reload(sg)
reload(sf)
reload(rsl)

from pyrevit import revit, DB, forms
from System.Collections.Generic import List

doc = revit.doc
uidoc = revit.uidoc


def candidates():
    """The selected floors, else every floor of the project, that need it."""
    sel = [doc.GetElement(i) for i in uidoc.Selection.GetElementIds()]
    floors = [e for e in sel if isinstance(e, DB.Floor)]
    picked = bool(floors)
    if not floors:
        floors = list(DB.FilteredElementCollector(doc).OfClass(DB.Floor).WhereElementIsNotElementType())
    out = []
    for f in floors:
        try:
            if sf.needs_fix(f):
                out.append(f)
        except Exception:
            pass
    return out, picked


if __name__ == "__main__":
    floors, picked = candidates()
    if not floors:
        forms.alert(u"No hay losas inclinadas que corregir{}: todas tienen su espesor perpendicular a la cara.".format(
            u" en la selección" if picked else u""), title=u"Corregir Losas Inclinadas")
        raise SystemExit
    lines = []
    for f in floors[:15]:
        across, vertical = sf.thickness_across(f)
        t = doc.GetElement(f.GetTypeId())
        lines.append(u"- id {}: {:.3f} m perpendicular (debe ser {:.3f})".format(
            f.Id.IntegerValue, across, sg.type_thickness(t)))
    more = u"\n... y {} más".format(len(floors) - 15) if len(floors) > 15 else u""
    ok = forms.alert(
        u"{} losa(s) inclinada(s) con el espesor medido en vertical{}:\n\n{}{}\n\n"
        u"Se volverán a crear con flecha de pendiente (espesor perpendicular a la cara), con el mismo "
        u"contorno, la misma cara superior y sus parámetros, unidas a sus vigas. Tendrán otro "
        u"número de elemento; el acero o los ladrillos que tengan se borran (vuelve a generarlos con "
        u"Acero Losa).\n\n¿Corregirlas?".format(
            len(floors), u" en la selección" if picked else u" en el proyecto", u"\n".join(lines), more),
        title=u"Corregir Losas Inclinadas", yes=True, no=True)
    if not ok:
        raise SystemExit
    made, failed = [], []
    t = DB.Transaction(doc, u"Corregir losas inclinadas")
    failures = rsl.DropFailing()
    opts = t.GetFailureHandlingOptions()
    opts.SetFailuresPreprocessor(failures)
    opts.SetForcedModalHandling(False)
    t.SetFailureHandlingOptions(opts)
    t.Start()
    try:
        for f in floors:
            fid = f.Id.IntegerValue
            try:
                rsl.delete_bricks(doc, f)
                made += sf.fix_floor(doc, f)
            except Exception as e:
                failed.append(u"id {}: {}".format(fid, e))
        t.Commit()
    except Exception:
        t.RollBack()
        raise
    if made:
        uidoc.Selection.SetElementIds(List[DB.ElementId]([m.Id for m in made]))
    msg = u"{} losa(s) corregida(s) en {} losa(s) nueva(s) (quedan seleccionadas).".format(len(floors) - len(failed), len(made))
    if failed:
        msg += u"\n\nNo se pudo:\n- " + u"\n- ".join(failed)
    forms.alert(msg, title=u"Corregir Losas Inclinadas")
