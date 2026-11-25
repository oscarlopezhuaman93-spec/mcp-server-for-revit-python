# -*- coding: utf-8 -*-
"""Regression of Acero Losa (inside Revit through the MCP, nothing saved):
every slab already set up (EA_Losa_Acero) generated with the plugin of
`root` (stable or development) in a TransactionGroup rolled back; the bars
read back as a signature per host (style, positions, points to the mm) plus
the bricks placed."""
import codecs
import sys

OURS = ("rebar_", "beam_extras", "sloped_plan", "formwork_", "utils", "selection_hosts", "slab_",
        "roof_", "stair_", "boki_brand", "viga_", "columna_", "losa_", "comun_", "brick_")


def load(root):
    mcp = root + r"\revit_mcp"
    for k in list(sys.modules.keys()):
        if k.startswith(OURS):
            del sys.modules[k]
    sys.path[:] = [p for p in sys.path if not p.lower().endswith(r"\revit_mcp")]
    sys.path.insert(0, mcp)
    d = root + r"\BOKI Estructura.tab\Acero.panel\Acero Losa.pushbutton"
    pre = codecs.open(d + r"\script.py", "r", "utf-8").read().replace("\r\n", "\n").split("# --- main ---")[0]
    ns = {"__file__": d + r"\script.py", "__name__": "alosa"}
    exec(compile(pre, "alosa", "exec"), ns)
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


def signature(doc, DB, root, only_ids=None):
    """({floor id: [bar signatures]}, (count, failed, warnings, bricks))."""
    ns = load(root)
    floors = [f for f in DB.FilteredElementCollector(doc).OfClass(DB.Floor)
              if (f.LookupParameter("EA_Losa_Acero") is not None and f.LookupParameter("EA_Losa_Acero").AsString())]
    ids = [f.Id.IntegerValue for f in floors]
    if only_ids:
        ids = [i for i in ids if i in only_ids]
    st = ns["State"]()
    st.picked_ids = list(ids)
    st.partners = dict((i, ns["partner_of"](i)) for i in ids)
    tg = DB.TransactionGroup(doc, "regresion")
    tg.Start()
    try:
        res = ns["generate_all"](st, {}, ns["load_splice"]())
        hosts = set(ids) | set(j for j in st.partners.values() if j)
        result = dict((str(h), _sig(doc, DB, doc.GetElement(DB.ElementId(h)))) for h in sorted(hosts))
    finally:
        tg.RollBack()
    return result, (res[0], sorted(res[1]), sorted(set(res[2])), res[3])
