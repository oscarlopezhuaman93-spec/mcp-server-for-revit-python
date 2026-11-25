# -*- coding: utf-8 -*-
"""Regression of Acero Cimentacion (inside Revit through the MCP, nothing
saved): every footing whose type is set up (EA_Cim_Acero) generated with the
plugin of `root` (stable or development) in a TransactionGroup rolled back;
bars read back as a signature per footing."""
import codecs
import sys

OURS = ("rebar_", "beam_extras", "sloped_plan", "formwork_", "utils", "selection_hosts", "slab_",
        "roof_", "stair_", "boki_brand", "viga_", "columna_", "losa_", "muro_", "cimentacion_",
        "comun_", "brick_")


class _Window(object):
    action = "run"


def load(root):
    mcp = root + r"\revit_mcp"
    for k in list(sys.modules.keys()):
        if k.startswith(OURS):
            del sys.modules[k]
    sys.path[:] = [p for p in sys.path if not p.lower().endswith(r"\revit_mcp")]
    sys.path.insert(0, mcp)
    d = root + r"\BOKI Estructura.tab\Acero.panel\Acero Cimentacion.pushbutton"
    src = codecs.open(d + r"\script.py", "r", "utf-8").read().replace("\r\n", "\n")
    pre, main = src.split("# --- main ---", 1)
    gen = main[main.index("def _hook_tips_inside"):main.index("    if failed:\n        forms.alert")]
    ns = {"__file__": d + r"\script.py", "__name__": "acim"}
    exec(compile(pre, "acim", "exec"), ns)
    return ns, gen


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


def footings(doc, DB):
    out = []
    for e in DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_StructuralFoundation) \
            .WhereElementIsNotElementType():
        et = doc.GetElement(e.GetTypeId())
        p = et.LookupParameter("EA_Cim_Acero") if et is not None else None
        if p is not None and (p.AsString() or u""):
            out.append(e.Id.IntegerValue)
    return out


def signature(doc, DB, root, config_from=None):
    """({footing id: [bar signatures]}, failed)."""
    ns, gen = load(root)
    tg = DB.TransactionGroup(doc, "regresion")
    tg.Start()
    try:
        if config_from is not None:  # every footing type set up alike, for the run only
            src = doc.GetElement(DB.ElementId(config_from))
            t = DB.Transaction(doc, "cfg")
            t.Start()
            for e in DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_StructuralFoundation) \
                    .WhereElementIsElementType():
                for n in ("EA_Cim_Acero", "EA_Cim_Recubrimientos"):
                    ps, pd = src.LookupParameter(n), e.LookupParameter(n)
                    if ps is not None and pd is not None and not pd.IsReadOnly and ps.AsString():
                        pd.Set(ps.AsString())
            t.Commit()
        ids = footings(doc, DB)
        st = ns["State"]()
        st.picked_ids = list(ids)
        ns["state"], ns["window"] = st, _Window()
        exec(compile(gen, "acgen", "exec"), ns)
        result = dict((str(i), _sig(doc, DB, doc.GetElement(DB.ElementId(i)))) for i in ids)
        failed = sorted(ns.get("failed") or [])
    finally:
        tg.RollBack()
    return result, failed
