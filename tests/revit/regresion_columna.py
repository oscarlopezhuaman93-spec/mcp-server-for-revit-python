# -*- coding: utf-8 -*-
"""Regression of Acero Columna (inside Revit through the MCP, nothing
saved): every column stack of the configured types generated with the
plugin of `root` (stable or development) in a TransactionGroup rolled back;
the bars read back as a signature per host (style, positions, centerline
points to the mm)."""
import codecs
import sys

OURS = ("rebar_", "beam_extras", "sloped_plan", "formwork_", "utils", "selection_hosts", "slab_",
        "roof_", "stair_", "boki_brand", "viga_", "columna_", "comun_")


def load(root):
    mcp = root + r"\revit_mcp"
    for k in list(sys.modules.keys()):
        if k.startswith(OURS):
            del sys.modules[k]
    sys.path[:] = [p for p in sys.path if not p.lower().endswith(r"\revit_mcp")]
    sys.path.insert(0, mcp)
    d = root + r"\BOKI Estructura.tab\Acero.panel\Acero.pushbutton"
    pre = codecs.open(d + r"\script.py", "r", "utf-8").read().replace("\r\n", "\n").split("# --- main ---")[0]
    ns = {"__file__": d + r"\script.py", "__name__": "acol"}
    exec(compile(pre, "acol", "exec"), ns)
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


def signature(doc, DB, root, only_ids=None, config_from=None):
    """({host id: [bar signatures]}, warnings) of the configured column
    types' stacks (only those holding one of `only_ids`, if given).
    `config_from`: a type id whose setup every column type takes for the run."""
    outer = DB.TransactionGroup(doc, "regresion-cfg")
    outer.Start()
    try:
        if config_from is not None:
            src = doc.GetElement(DB.ElementId(config_from))
            names = [p.Definition.Name for p in src.Parameters if p.Definition.Name.startswith("EA_")]
            tc = DB.Transaction(doc, "cfg")
            tc.Start()
            for tid in set(c.GetTypeId().IntegerValue for c in DB.FilteredElementCollector(doc)
                           .OfCategory(DB.BuiltInCategory.OST_StructuralColumns).WhereElementIsNotElementType()):
                el = doc.GetElement(DB.ElementId(tid))
                for n in names:
                    ps, pd = src.LookupParameter(n), el.LookupParameter(n)
                    if ps is not None and pd is not None and not pd.IsReadOnly and ps.AsString():
                        pd.Set(ps.AsString())
            tc.Commit()
        return _signature(doc, DB, root, only_ids)
    finally:
        outer.RollBack()


def _signature(doc, DB, root, only_ids=None):
    ns = load(root)
    rc = ns["rc"]
    rg = ns.get("rg") or sys.modules.get("rebar_group")
    types = ns["collect_types"]()
    by = dict((t.id, t) for t in types)
    specs = {}
    for t in types:
        try:
            specs[t.id] = rc.ColumnSpec(t.config())
        except Exception:
            pass
    splice = ns["splice_for_generation"](ns["load_splice_settings"]())
    cols = [c for c in DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_StructuralColumns)
            .WhereElementIsNotElementType() if ns["id_of"](c.GetTypeId()) in specs]
    stacks = rc.column_stacks(cols, same_type=splice is not None)
    if only_ids:
        stacks = [s for s in stacks if any(c.Id.IntegerValue in only_ids for c in s)]
    warnings = []
    tg = DB.TransactionGroup(doc, "regresion")
    tg.Start()
    t = DB.Transaction(doc, "regresion")
    t.Start()
    try:
        for s in stacks:
            ct = by[ns["id_of"](s[0].GetTypeId())]
            sub = DB.SubTransaction(doc)
            sub.Start()
            try:
                created, found = rc.generate_stack(doc, s, specs[ct.id], rc.BarTypes(doc), rc.StirrupHooks(doc),
                                                   rc.type_mark(ct.name), rc.RebarShapes(doc), splice,
                                                   type_specs=specs)
                if rg is not None:
                    longs = dict((item[0].Id.IntegerValue, item[0]) for items in created.values()
                                 for item in items if item[2] == rc.LONGITUDINAL)
                    rg.group_loose(doc, list(longs.values()))
                sub.Commit()
                warnings += found
            except Exception as e:
                sub.RollBack()
                warnings.append(u"{}: {}".format(s[0].Id.IntegerValue, e))
        doc.Regenerate()
        result = dict((str(c.Id.IntegerValue), _sig(doc, DB, c)) for s in stacks for c in s)
    finally:
        t.RollBack()
        tg.RollBack()
    return result, warnings
