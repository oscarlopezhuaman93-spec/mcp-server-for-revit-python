# -*- coding: utf-8 -*-
"""Prueba de Cerchas (BOKI Metalicas) por el MCP, sin guardar: en una zona
vacia crea ejes (cerchas @ `bay` m, apoyos A y B a `span` m), genera la
cercha con el codigo de `root`, revisa y exporta imagenes; deshace todo."""
import os
import sys

OUT = r"C:\Users\oscar\BOKI-desarrollo\docs\propuesta_metalicas"


def load(root):
    for k in list(sys.modules.keys()):
        if k.startswith("metal_"):
            del sys.modules[k]
    sys.path[:] = [p for p in sys.path if not p.lower().endswith(r"\revit_mcp")]
    sys.path.insert(0, root + r"\revit_mcp")
    import metal_cerchas as mk
    return mk


def grids(doc, DB, origin, n, bay, span, prefix=u"T"):
    F = 1 / 0.3048
    x0, y0 = origin
    out = []
    for i in range(n):
        x = x0 + bay * i
        g = DB.Grid.Create(doc, DB.Line.CreateBound(DB.XYZ(x * F, (y0 - 3) * F, 0), DB.XYZ(x * F, (y0 + span + 3) * F, 0)))
        g.Name = u"{}{}".format(prefix, i + 1)
        out.append(g)
    for name, y in ((prefix + u"A", y0), (prefix + u"B", y0 + span)):
        g = DB.Grid.Create(doc, DB.Line.CreateBound(DB.XYZ((x0 - 3) * F, y * F, 0), DB.XYZ((x0 + bay * n + 3) * F, y * F, 0)))
        g.Name = name
        out.append(g)
    return out


def shot(doc, DB, level, name, box, fwd):
    import System
    F = 1 / 0.3048
    vt = [v for v in DB.FilteredElementCollector(doc).OfClass(DB.ViewFamilyType)
          if v.ViewFamily == DB.ViewFamily.ThreeDimensional][0]
    t = DB.Transaction(doc, "vista")
    t.Start()
    v = DB.View3D.CreateIsometric(doc, vt.Id)
    v.DetailLevel = DB.ViewDetailLevel.Fine
    v.DisplayStyle = DB.DisplayStyle.ShadingWithEdges
    for bic in (DB.BuiltInCategory.OST_Levels, DB.BuiltInCategory.OST_Grids, DB.BuiltInCategory.OST_Topography,
                DB.BuiltInCategory.OST_Lines, DB.BuiltInCategory.OST_RasterImages):
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
    v.SetOrientation(DB.ViewOrientation3D((bb.Min + bb.Max) * 0.5 - fwd * 300, up, fwd))
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
            return dst
    return None


def run(doc, DB, root, cases, images=True, origin=(400.0, 400.0)):
    """cases: [(nombre, cfg, n cerchas, separacion, luz, caja de imagen o None, direccion)]."""
    mk = load(root)
    level = sorted(DB.FilteredElementCollector(doc).OfClass(DB.Level), key=lambda l: abs(l.Elevation))[0]
    rep = []
    for name, cfg, n, bay, span, box, fwd in cases:
        tg = DB.TransactionGroup(doc, "prueba cerchas")
        tg.Start()
        try:
            t = DB.Transaction(doc, "cerchas")
            t.Start()
            gs = grids(doc, DB, origin, n, bay, span)
            doc.Regenerate()
            lay = mk.Layout(DB, gs, span)
            made = mk.generate(doc, DB, lay, level, cfg)
            t.Commit()
            s = mk.summary(cfg, span, n)
            top = max(max(e.Location.Curve.Evaluate(k / 8.0, True).Z for k in range(9)) for e in made["chords"]) * 0.3048 - level.Elevation * 0.3048
            rep.append(u"{}: {} | cordones {}, montantes {}, diagonales {}, travesanos {} | cumbre {:.2f} m | peso {:.2f} t".format(
                name, lay.text(), len(made["chords"]), len(made["montantes"]), len(made["diagonales"]),
                len(made["travesanos"]), top, s["kg_total"] / 1000.0))
            if images and box:
                x0, y0 = origin
                b = (x0 + box[0], y0 + box[1], box[2], x0 + box[3], y0 + box[4], box[5])
                rep.append(shot(doc, DB, level, name, b, fwd))
        except Exception as e:
            import traceback
            rep.append(u"{}: ERROR {}".format(name, traceback.format_exc()[-600:]))
        finally:
            tg.RollBack()
    return rep
