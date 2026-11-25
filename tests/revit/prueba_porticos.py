# -*- coding: utf-8 -*-
"""Prueba del boton Porticos (BOKI Metalicas) dentro de Revit por el MCP,
sin guardar nada: en una zona vacia (x = ORIGIN) crea 7 ejes de portico
@ 6 m y los ejes A y B a 20 m, genera los porticos con el codigo de `root`,
revisa la geometria, exporta imagenes y deshace todo."""
import codecs
import math
import os
import sys

OURS = ("metal_", "boki_brand")
ORIGIN = (300.0, 300.0)  # m, lejos del edificio
OUT = r"C:\Users\oscar\BOKI-desarrollo\docs\propuesta_metalicas"


def load(root):
    for k in list(sys.modules.keys()):
        if k.startswith(OURS):
            del sys.modules[k]
    mcp = root + r"\revit_mcp"
    sys.path[:] = [p for p in sys.path if not p.lower().endswith(r"\revit_mcp")]
    sys.path.insert(0, mcp)
    import metal_spec as ms
    import metal_porticos as mp
    return ms, mp


def grids(doc, DB, level):
    F = 1 / 0.3048
    x0, y0 = ORIGIN
    out = []
    for i in range(7):  # porticos 1..7 (transversales, a lo largo de y)
        x = x0 + 6.0 * i
        g = DB.Grid.Create(doc, DB.Line.CreateBound(DB.XYZ(x * F, (y0 - 3) * F, 0), DB.XYZ(x * F, (y0 + 23) * F, 0)))
        g.Name = u"P{}".format(i + 1)
        out.append(g)
    for name, y in ((u"PA", y0), (u"PB", y0 + 20.0)):
        g = DB.Grid.Create(doc, DB.Line.CreateBound(DB.XYZ((x0 - 3) * F, y * F, 0), DB.XYZ((x0 + 39) * F, y * F, 0)))
        g.Name = name
        out.append(g)
    return out


def check(doc, DB, made, ms, cfg):
    """Lo que hay que mirar como se construye: largos, orientacion y alturas."""
    F = 0.3048
    rep = []
    for c in made["columns"][:2]:
        b = c.get_BoundingBox(None)
        rep.append(u"columna {}: z {:.2f} a {:.2f} m, en planta {:.0f} x {:.0f} mm".format(
            c.Id.IntegerValue, b.Min.Z * F, b.Max.Z * F, (b.Max.X - b.Min.X) * F * 1000, (b.Max.Y - b.Min.Y) * F * 1000))
    for r in made["rafters"][:2]:
        b = r.get_BoundingBox(None)
        ln = r.Location.Curve
        rep.append(u"viga {}: largo {:.2f} m, z {:.2f} a {:.2f} m".format(
            r.Id.IntegerValue, ln.Length * F, b.Min.Z * F, b.Max.Z * F))
    vols = [sum(s.Volume for s in h.get_Geometry(DB.Options()) if isinstance(s, DB.Solid)) * F ** 3
            for h in made["haunches"][:3]]
    rep.append(u"cartelas (alero, alero, cumbrera) kg: " + u", ".join(u"{:.0f}".format(v * 7850) for v in vols))
    return rep


def run(doc, uidoc, DB, root, images=True):
    ms, mp = load(root)
    import System
    level = sorted(DB.FilteredElementCollector(doc).OfClass(DB.Level), key=lambda l: abs(l.Elevation))[0]
    tg = DB.TransactionGroup(doc, "prueba porticos")
    tg.Start()
    rep = []
    try:
        t = DB.Transaction(doc, "ejes")
        t.Start()
        gs = grids(doc, DB, level)
        t.Commit()
        cfg = ms.config()
        layout = mp.Layout(gs, cfg["span"])
        rep.append(layout.text())
        cfg["spacing"] = 6.0
        t = DB.Transaction(doc, "porticos")
        t.Start()
        made = mp.generate(doc, layout, level, cfg)
        t.Commit()
        rep.append(u"creados: " + u", ".join(u"{} {}".format(len(v), k) for k, v in sorted(made.items())))
        rep += check(doc, DB, made, ms, cfg)
        s = ms.summary(cfg, 7, 6)
        rep.append(u"peso total aprox. {:.2f} t".format(s["kg_total"] / 1000.0))
        if images:
            rep += export(doc, uidoc, DB, made, level)
    finally:
        tg.RollBack()
    return rep


def export(doc, uidoc, DB, made, level):
    import System
    F = 1 / 0.3048
    x0, y0 = ORIGIN
    vt = [v for v in DB.FilteredElementCollector(doc).OfClass(DB.ViewFamilyType)
          if v.ViewFamily == DB.ViewFamily.ThreeDimensional][0]
    out = []
    shots = [
        ("02_porticos_3d", DB.XYZ(-1.0, -1.3, -0.75), (x0 - 2, y0 - 2, -1, x0 + 38, y0 + 22, 10)),
        ("03_portico_alero", DB.XYZ(-0.6, -1.0, -0.35), (x0 + 5, y0 - 1.2, 5.0, x0 + 13, y0 + 2.5, 8.4)),
        ("04_placa_base", DB.XYZ(-0.8, -1.0, -0.6), (x0 - 0.6, y0 - 0.6, -0.1, x0 + 0.6, y0 + 0.6, 0.6)),
        ("05_cumbrera", DB.XYZ(-0.7, -1.0, -0.45), (x0 + 5, y0 + 8.2, 6.4, x0 + 7, y0 + 11.8, 8.2)),
    ]
    hide = [DB.BuiltInCategory.OST_Walls, DB.BuiltInCategory.OST_Floors, DB.BuiltInCategory.OST_Roofs,
            DB.BuiltInCategory.OST_Rebar, DB.BuiltInCategory.OST_Topography, DB.BuiltInCategory.OST_GenericModel,
            DB.BuiltInCategory.OST_StructuralFoundation, DB.BuiltInCategory.OST_Levels, DB.BuiltInCategory.OST_Grids]
    for name, fwd, box in shots:
        t = DB.Transaction(doc, "vista")
        t.Start()
        v = DB.View3D.CreateIsometric(doc, vt.Id)
        v.DetailLevel = DB.ViewDetailLevel.Fine
        v.DisplayStyle = DB.DisplayStyle.ShadingWithEdges
        for bic in hide:
            try:
                v.SetCategoryHidden(DB.ElementId(bic), True)
            except Exception:
                pass
        bb = DB.BoundingBoxXYZ()
        bb.Min = DB.XYZ(box[0] * F, box[1] * F, level.Elevation + box[2] * F)
        bb.Max = DB.XYZ(box[3] * F, box[4] * F, level.Elevation + box[5] * F)
        v.SetSectionBox(bb)
        fwd = fwd.Normalize()
        right = fwd.CrossProduct(DB.XYZ.BasisZ).Normalize()
        up = right.CrossProduct(fwd).Normalize()
        c = (bb.Min + bb.Max) * 0.5
        v.SetOrientation(DB.ViewOrientation3D(c - fwd * 200, up, fwd))
        t.Commit()
        o = DB.ImageExportOptions()
        o.ExportRange = DB.ExportRange.SetOfViews
        o.SetViewsAndSheets(System.Collections.Generic.List[DB.ElementId]([v.Id]))
        o.FilePath = os.path.join(OUT, name)
        o.ZoomType = DB.ZoomFitType.FitToPage
        o.PixelSize = 1600
        o.HLRandWFViewsFileType = DB.ImageFileType.PNG
        o.ShadowViewsFileType = DB.ImageFileType.PNG
        doc.ExportImage(o)
        for f in os.listdir(OUT):
            if f.startswith(name + " ") and f.endswith(".png"):
                dst = os.path.join(OUT, name + ".png")
                if os.path.exists(dst):
                    os.remove(dst)
                os.rename(os.path.join(OUT, f), dst)
                out.append(dst)
    return out
