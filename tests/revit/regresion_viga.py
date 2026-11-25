# -*- coding: utf-8 -*-
"""Regression of Acero Viga (run inside Revit through the MCP, nothing
saved): every beam line of the model generated with the plugin of a given
folder (stable or development), inside a TransactionGroup rolled back; the
bars read back as a signature (style, positions, centerline points to the
mm) per host. Two runs compared bar by bar: identical, or the differences.

Use: exec this file with ROOT set (the plugin folder) and call
signature(doc, config_from=None, limit=None)."""
import codecs
import json
import sys

OURS = ("rebar_", "beam_extras", "sloped_plan", "formwork_", "utils", "selection_hosts", "slab_",
        "roof_", "stair_", "boki_brand", "viga_", "columna_", "comun_")


def load(root):
    """The plugin of `root` loaded clean (its revit_mcp first on the path)."""
    mcp = root + r"\revit_mcp"
    for k in list(sys.modules.keys()):
        if k.startswith(OURS):
            del sys.modules[k]
    sys.path[:] = [p for p in sys.path if not p.lower().endswith(r"\revit_mcp")]
    sys.path.insert(0, mcp)
    d = root + r"\BOKI Estructura.tab\Acero.panel\Acero Viga.pushbutton"
    allsrc = codecs.open(d + r"\script.py", "r", "utf-8").read().replace("\r\n", "\n")
    pre, main = allsrc.split("# --- main ---", 1)
    gen = main[main.index("by_id = dict((t.id, t) for t in types)"):main.index("# No report window")]
    ns = {"__file__": d + r"\script.py", "__name__": "aviga"}
    exec(compile(pre, "aviga", "exec"), ns)
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


def signature(doc, DB, root, config_from=None, limit=None):
    """{host id: [bar signatures]} of every beam line generated (rolled back).
    `config_from`: a type id whose saved setup every beam type takes for the
    run (the model has few types set up: all its beams tested alike)."""
    ns, gen = load(root)
    types = ns["collect_types"]()
    tg = DB.TransactionGroup(doc, "regresion")
    tg.Start()
    try:
        if config_from is not None:
            src = doc.GetElement(DB.ElementId(config_from))
            names = [p.Definition.Name for p in src.Parameters if p.Definition.Name.startswith("EA_")]
            t = DB.Transaction(doc, "cfg")
            t.Start()
            for bt in types:
                el = doc.GetElement(DB.ElementId(bt.id))
                for n in names:
                    ps, pd = src.LookupParameter(n), el.LookupParameter(n)
                    if ps is not None and pd is not None and not pd.IsReadOnly and ps.AsString():
                        pd.Set(ps.AsString())
            t.Commit()
            ns, gen = load(root)
            types = ns["collect_types"]()
        ids = []
        for bt in types:
            for line in bt.lines:
                ids.append(line.elements[0].Id.IntegerValue)
        if limit:
            ids = ids[:limit]
        ns["types"] = types
        st = ns["State"]()
        st.picked_ids = ids
        st.checked = set(ns["id_of"](doc.GetElement(DB.ElementId(i)).GetTypeId()) for i in ids)
        st.scope = "pick"
        ns["state"] = st
        exec(compile(gen, "avgen", "exec"), ns)
        hosts = set()
        for bt in types:
            for line in bt.lines:
                if line.elements[0].Id.IntegerValue in ids:
                    hosts.update(e.Id.IntegerValue for e in line.elements)
        result = dict((str(h), _sig(doc, DB, doc.GetElement(DB.ElementId(h)))) for h in sorted(hosts))
        warnings = list(ns.get("warnings") or [])
    finally:
        tg.RollBack()
    return result, warnings


def compare(a, b):
    """[(host, what)] where two signatures differ."""
    out = []
    for h in sorted(set(a) | set(b)):
        if json.dumps(a.get(h)) != json.dumps(b.get(h)):
            out.append((h, len(a.get(h) or []), len(b.get(h) or [])))
    return out
