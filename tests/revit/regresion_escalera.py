# -*- coding: utf-8 -*-
"""Regression of Acero Escalera (inside Revit through the MCP, nothing
saved): every stair generated with the plugin of `root` (stable or
development) - its own saved settings, or the defaults - in a
TransactionGroup rolled back; bars read back as a signature per stair."""
import codecs
import sys

OURS = ("rebar_", "beam_extras", "sloped_plan", "formwork_", "utils", "selection_hosts", "slab_",
        "roof_", "stair_", "boki_brand", "viga_", "columna_", "losa_", "muro_", "cimentacion_",
        "escalera_", "comun_", "brick_")


def load(root):
    mcp = root + r"\revit_mcp"
    for k in list(sys.modules.keys()):
        if k.startswith(OURS):
            del sys.modules[k]
    sys.path[:] = [p for p in sys.path if not p.lower().endswith(r"\revit_mcp")]
    sys.path.insert(0, mcp)
    d = root + r"\BOKI Estructura.tab\Acero.panel\Acero Escalera.pushbutton"
    pre = codecs.open(d + r"\script.py", "r", "utf-8").read().replace("\r\n", "\n").split("# --- main ---")[0]
    ns = {"__file__": d + r"\script.py", "__name__": "aesc"}
    exec(compile(pre, "aesc", "exec"), ns)
    return ns


def _sig(doc, DB, host):
    from Autodesk.Revit.DB.Structure import MultiplanarOption
    out = []
    data = DB.Structure.RebarHostData.GetRebarHostData(host)
    if data is None:
        return out
    for r in data.GetRebarsInHost():
        a = r.GetShapeDrivenAccessor()
        style = str(doc.GetElement(r.GetShapeId()).RebarStyle)
        pts = []
        for k in range(r.NumberOfBarPositions):
            cs = r.GetCenterlineCurves(False, False, False, MultiplanarOption.IncludeOnlyPlanarCurves, k)
            tf = a.GetBarPositionTransform(k) if a is not None else None
            for c in cs:
                c = c.CreateTransformed(tf) if tf is not None else c
                for j in (0, 1):
                    q = c.GetEndPoint(j)
                    pts.append((round(q.X * 304.8), round(q.Y * 304.8), round(q.Z * 304.8)))
        out.append((style, r.NumberOfBarPositions, tuple(pts)))
    return sorted(out)


def signature(doc, DB, root):
    """({stair id: [bar signatures]}, (count, failed))."""
    from Autodesk.Revit.DB.Architecture import Stairs
    ns = load(root)
    ids = [s.Id.IntegerValue for s in DB.FilteredElementCollector(doc).OfClass(Stairs)]
    st = ns["State"]()
    st.picked_ids = list(ids)
    tg = DB.TransactionGroup(doc, "regresion")
    tg.Start()
    try:
        count, failed = ns["generate_all"](st, {})
        result = dict((str(i), _sig(doc, DB, doc.GetElement(DB.ElementId(i)))) for i in ids)
    finally:
        tg.RollBack()
    return result, (count, sorted(failed))
