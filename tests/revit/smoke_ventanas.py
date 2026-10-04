# -*- coding: utf-8 -*-
"""Prueba de humo de las ventanas de acero, dentro de Revit.

Abre cada ventana (sin mostrarla) con un elemento real del modelo, la
dibuja completa y reporta lo que ve: asi un cambio en un modulo compartido
(revit_mcp/rebar_*.py) que rompa otra ventana se detecta antes de entregar.
Uso (MCP execute_revit_code o la consola de pyRevit):

    exec(open(r"<extension>/tests/revit/smoke_ventanas.py").read())

No modifica el modelo.
"""
import io
import os
import sys
import traceback

EXT = r"C:\Users\oscar\AppData\Roaming\pyRevit\Extensions\mcp-server-for-revit-python.extension"
if EXT + r"\revit_mcp" not in sys.path:
    sys.path.append(EXT + r"\revit_mcp")
import rebar_spec, rebar_columns, rebar_views, rebar_foundation, rebar_stairs  # noqa: E401
for _m in (rebar_spec, rebar_columns, rebar_views, rebar_foundation, rebar_stairs):
    reload(_m)

from Autodesk.Revit.DB.Architecture import Stairs
from System.Windows.Controls import Border
from System.Windows import Size, Rect
from System.Windows.Media import PixelFormats
from System.Windows.Media.Imaging import RenderTargetBitmap

PANEL = EXT + r"\BOKI Estructura.tab\Acero.panel"


def load(button, upto="# --- main"):
    folder = os.path.join(PANEL, button + ".pushbutton")
    src = io.open(os.path.join(folder, "script.py"), encoding="utf-8").read()
    if upto and upto in src:
        src = src[:src.index(upto)]
    g = {"__name__": "smoke", "__file__": os.path.join(folder, "script.py")}
    exec(compile(src, os.path.join(folder, "script.py"), "exec"), g)
    return g, os.path.join(folder, "AceroForm.xaml")


def render(win, redraw):
    content = win.Content
    win.Content = None
    b = Border()
    b.Child = content
    b.Measure(Size(1560, 900))
    b.Arrange(Rect(0, 0, 1560, 900))
    b.UpdateLayout()
    redraw()
    b.UpdateLayout()
    RenderTargetBitmap(1560, 900, 96, 96, PixelFormats.Pbgra32).Render(b)
    win.Close()


def first(bic, test=None):
    for e in DB.FilteredElementCollector(doc).OfCategory(bic).WhereElementIsNotElementType():
        if test is None or test(e):
            return e
    return None


def column_like(button, element, group_id):
    g, xaml = load(button)
    types = g["collect_types"]()
    t = [x for x in types if x.id == group_id][0]
    st = g["State"]()
    st.picked_ids = [element.Id.IntegerValue]
    st.active = t.id
    st.checked = set([t.id])
    win = g["AceroWindow"](xaml, types, st)

    def redraw():
        win._views_sig = None
        win.redraw()
    render(win, redraw)
    labels = sorted(set(n["label"] for n in win._neighbor_cache.get(element.Id.IntegerValue, [])))
    return u"vecinos en alzado/3D: {}".format(u", ".join(labels) or u"ninguno")


def check_columna():
    col = first(DB.BuiltInCategory.OST_StructuralColumns, lambda c: rebar_columns.read_type_config(
        doc.GetElement(c.GetTypeId())).get("EA_Seccion_Armado"))
    col = col or first(DB.BuiltInCategory.OST_StructuralColumns)
    out = column_like("Acero", col, col.GetTypeId().IntegerValue)
    # Acero Columna only shows what frames into a column
    bad = [l for l in (u"MURO", u"COLUMNA") if l in out]
    assert not bad, u"Acero Columna muestra {} (solo viga, losa, zapata)".format(bad)
    return u"columna {}: {}".format(col.Id.IntegerValue, out)


def check_muro():
    wall = first(DB.BuiltInCategory.OST_Walls, lambda w: isinstance(w, DB.Wall)
                 and w.StructuralUsage != DB.Structure.StructuralWallUsage.NonBearing) \
        or first(DB.BuiltInCategory.OST_Walls)
    return u"muro {}: {}".format(wall.Id.IntegerValue, column_like("Acero Muro", wall, wall.Id.IntegerValue))


def check_viga():
    g, xaml = load("Acero Viga")
    types = g["collect_types"]()
    beam = first(DB.BuiltInCategory.OST_StructuralFraming)
    st = g["State"]()
    st.picked_ids = [beam.Id.IntegerValue]
    st.checked = set([beam.GetTypeId().IntegerValue])
    st.scope = "pick"
    st.active = beam.GetTypeId().IntegerValue
    win = g["AceroWindow"](xaml, types, st)
    render(win, lambda: win.redraw())
    return u"viga {}: dibujada".format(beam.Id.IntegerValue)


def check_cimentacion():
    g, xaml = load("Acero Cimentacion", upto="def pick(state)")
    f = first(DB.BuiltInCategory.OST_StructuralFoundation)
    st = g["State"]()
    st.picked_ids = [f.Id.IntegerValue]
    st.active = f.Id.IntegerValue
    win = g["CimentacionWindow"](xaml, st)

    def redraw():
        win.element_selected(None, None)
        win.redraw()
    render(win, redraw)
    return u"cimentacion {}: {} caras".format(f.Id.IntegerValue, len(win.foundation.faces))


def check_escalera():
    s = first(DB.BuiltInCategory.OST_Stairs, lambda e: isinstance(e, Stairs))
    if s is None:
        return u"escalera: no hay en el modelo (omitida)"
    g, xaml = load("Acero Escalera", upto=None)
    st = g["State"]()
    st.picked_ids = [s.Id.IntegerValue]
    st.active = s.Id.IntegerValue
    win = g["EscaleraWindow"](xaml, st)

    def redraw():
        win.element_selected(None, None)
        win.redraw()
    render(win, redraw)
    return u"escalera {}: {} tramos".format(s.Id.IntegerValue, len(win.model.tramos))


results = []
for name, check in ((u"Acero Columna", check_columna), (u"Acero Viga", check_viga),
                    (u"Acero Muro", check_muro), (u"Acero Cimentacion", check_cimentacion),
                    (u"Acero Escalera", check_escalera)):
    try:
        results.append((name, u"OK", check()))
    except Exception as e:
        results.append((name, u"FALLA", u"{}\n{}".format(e, traceback.format_exc()[-600:])))
for name, state, info in results:
    print(u"{:<18} {:<6} {}".format(name, state, info))
