# -*- coding: utf-8 -*-
"""Regression of Acero Muro (inside Revit through the MCP, nothing saved):
every wall type's walls generated with the plugin of `root` (stable or
development) in a TransactionGroup rolled back, as the button runs them with
every type checked; bars read back as a signature per wall."""
import codecs
import sys

OURS = ("rebar_", "beam_extras", "sloped_plan", "formwork_", "utils", "selection_hosts", "slab_",
        "roof_", "stair_", "boki_brand", "viga_", "columna_", "losa_", "muro_", "comun_", "brick_")


def load(root):
    mcp = root + r"\revit_mcp"
    for k in list(sys.modules.keys()):
        if k.startswith(OURS):
            del sys.modules[k]
    sys.path[:] = [p for p in sys.path if not p.lower().endswith(r"\revit_mcp")]
    sys.path.insert(0, mcp)
    d = root + r"\BOKI Estructura.tab\Acero.panel\Acero Muro.pushbutton"
    src = codecs.open(d + r"\script.py", "r", "utf-8").read().replace("\r\n", "\n")
    pre, main = src.split("# --- main ---", 1)
    gen = main[main.index("by_id = dict((t.id, t) for t in types)"):main.index("# No report window")]
    ns = {"__file__": d + r"\script.py", "__name__": "amuro"}
    exec(compile(pre, "amuro", "exec"), ns)
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


def signature(doc, DB, root, test_config=None):
    """({wall id: [bar signatures]}, warnings)."""
    ns, gen = load(root)
    types = ns["collect_types"]()
    st = ns["State"]()
    st.checked = set(t.id for t in types)
    st.scope = "model"
    ns["types"], ns["state"] = types, st
    tg = DB.TransactionGroup(doc, "regresion")
    tg.Start()
    try:
        if test_config is not None:  # walls set up for the run only (rolled back)
            import json
            rc = ns["rc"]
            t = DB.Transaction(doc, "cfg")
            t.Start()
            rc.ensure_parameters(doc)
            for bt in types:
                p = bt.element.LookupParameter(rc.WALL_PARAM)
                if p is not None and not (p.AsString() or u""):
                    p.Set(json.dumps(dict((k, json.dumps(v)) for k, v in test_config.items())))
            t.Commit()
        exec(compile(gen, "amgen", "exec"), ns)
        walls = [c for t in types for c in t.columns]
        result = dict((str(w.Id.IntegerValue), _sig(doc, DB, w)) for w in walls)
        warnings = sorted(set(ns.get("warnings") or []))
    finally:
        tg.RollBack()
    return result, warnings
