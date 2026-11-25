# -*- coding: utf-8 -*-
"""Prueba de Correas y arriostres (BOKI Metalicas) por el MCP, sin guardar:
en la zona vacia de prueba_porticos genera los 7 porticos y encima las
correas, templadores, largueros y arriostres; revisa y exporta imagenes."""
import codecs
import os
import sys

HERE = r"C:\Users\oscar\BOKI-desarrollo\tests\revit"


def run(doc, uidoc, DB, root, images=True, shots=None):
    base = {}
    exec(codecs.open(os.path.join(HERE, "prueba_porticos.py"), "r", "utf-8").read(), base)
    ms, mp = base["load"](root)
    for k in list(sys.modules.keys()):
        if k.startswith("metal_correas"):
            del sys.modules[k]
    import metal_correas as mc
    level = sorted(DB.FilteredElementCollector(doc).OfClass(DB.Level), key=lambda l: abs(l.Elevation))[0]
    rep = []
    tg = DB.TransactionGroup(doc, "prueba correas")
    tg.Start()
    try:
        t = DB.Transaction(doc, "porticos")
        t.Start()
        gs = base["grids"](doc, DB, level)
        doc.Regenerate()
        cfg = ms.config()
        mp.generate(doc, mp.Layout(gs, cfg["span"]), level, cfg)
        t.Commit()
        t = DB.Transaction(doc, "correas")
        t.Start()
        made = mc.generate(doc, DB, level, mc.config())
        t.Commit()
        rep.append(u"creados: " + u", ".join(u"{} {}".format(len(v), k) for k, v in sorted(made.items())))
        F = 0.3048
        for key in ("purlins", "girts", "sag", "brace_roof", "brace_walls"):
            if made[key]:
                e = made[key][0]
                b = e.get_BoundingBox(None)
                rep.append(u"{}: largo {:.2f} m, z {:.3f} a {:.3f} m, tipo {}".format(
                    key, e.Location.Curve.Length * F, b.Min.Z * F, b.Max.Z * F,
                    mc._name(DB, doc.GetElement(e.GetTypeId()))))
        rep += orientation(doc, DB, made, mc)
        s = mc.summary(mc.config(), 7, 20.0, 7.0, 10.0)
        rep.append(u"peso aprox. {:.2f} t (correas {:.2f}, largueros {:.2f}, templadores {:.2f}, arriostres {:.2f})".format(
            s["kg_total"] / 1000, s["kg_purlins"] / 1000, s["kg_girts"] / 1000, s["kg_sag"] / 1000, s["kg_brace"] / 1000))
        if images:
            rep += export(doc, DB, base, level, shots)
    finally:
        tg.RollBack()
    return rep


def _web_normal(DB, e):
    """Normal de la cara plana mas grande (el alma) del perfil."""
    best = None
    for g in e.get_Geometry(DB.Options()):
        solids = [g] if isinstance(g, DB.Solid) else (
            [s for s in g.GetInstanceGeometry() if isinstance(s, DB.Solid)] if isinstance(g, DB.GeometryInstance) else [])
        for s in solids:
            for f in s.Faces:
                if isinstance(f, DB.PlanarFace) and (best is None or f.Area > best.Area):
                    best = f
    return best.FaceNormal if best is not None else None


def orientation(doc, DB, made, mc):
    """Como se construye: alma de la correa perpendicular al techo (su normal
    va a lo largo de la pendiente), alma del larguero horizontal (normal
    vertical), la correa apoyada sobre la viga y el larguero por fuera de la
    columna."""
    shed = mc.Shed(doc, DB)
    rep = []
    n = len(made["purlins"]) // max(1, len(shed.frames) - 1)
    for idx, side in ((0, 0), (n // 2, 1)):
        e = made["purlins"][idx]
        a, b = shed.water(shed.frames[0], side)
        u = (b - a).Normalize()
        wn = _web_normal(DB, e)
        pts = []
        for g in e.get_Geometry(DB.Options()):
            ss = [g] if isinstance(g, DB.Solid) else (
                [s for s in g.GetInstanceGeometry() if isinstance(s, DB.Solid)] if isinstance(g, DB.GeometryInstance) else [])
            for s in ss:
                for ed in s.Edges:
                    pts += list(ed.Tessellate())
        nr = shed.along.CrossProduct(u).Normalize()
        if nr.Z < 0:
            nr = nr.Negate()
        gap = min((q - a).DotProduct(nr) for q in pts) * 304.8 if pts else 0.0
        rep.append(u"correa agua {}: |normal alma . pendiente| = {:.3f} (1 = perpendicular al techo); "
                   u"fondo a {:.1f} mm de la cara de la viga".format(side, abs(wn.DotProduct(u)) if wn else -1, gap))
    wn = _web_normal(DB, made["girts"][0])
    rep.append(u"larguero: |normal alma . vertical| = {:.3f} (1 = alma horizontal)".format(abs(wn.Z) if wn else -1))
    return rep


def export(doc, DB, base, level, shots=None):
    x0, y0 = base["ORIGIN"]
    XYZ = DB.XYZ
    allshots = [
        ("11_correas_3d", XYZ(-1.0, -1.3, -0.75), (x0 - 2, y0 - 2, -1, x0 + 38, y0 + 22, 10)),
        ("12_correas_alero", XYZ(-0.7, 1.0, -0.45), (x0 + 4.5, y0 - 0.8, 5.6, x0 + 9.5, y0 + 2.2, 7.6)),
        ("13_largueros", XYZ(-0.8, 1.0, -0.3), (x0 - 1, y0 - 0.8, 0.0, x0 + 8, y0 + 0.6, 7.3)),
        ("14_arriostre_techo", XYZ(-0.6, -0.8, -1.2), (x0 - 1, y0 - 1, 6.4, x0 + 13, y0 + 21, 8.6)),
    ]
    out = []
    for name, fwd, box in allshots:
        if shots and name not in shots:
            continue
        out += _one(doc, DB, base, level, name, fwd, box)
    return out


def _one(doc, DB, base, level, name, fwd, box):
    import System
    F = 1 / 0.3048
    OUT = base["OUT"]
    vt = [v for v in DB.FilteredElementCollector(doc).OfClass(DB.ViewFamilyType)
          if v.ViewFamily == DB.ViewFamily.ThreeDimensional][0]
    hide = [DB.BuiltInCategory.OST_Walls, DB.BuiltInCategory.OST_Floors, DB.BuiltInCategory.OST_Roofs,
            DB.BuiltInCategory.OST_Rebar, DB.BuiltInCategory.OST_GenericModel,
            DB.BuiltInCategory.OST_StructuralFoundation, DB.BuiltInCategory.OST_Levels, DB.BuiltInCategory.OST_Grids]
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
    res = []
    for f in os.listdir(OUT):
        if f.startswith(name + " ") and f.endswith(".png"):
            dst = os.path.join(OUT, name + ".png")
            if os.path.exists(dst):
                os.remove(dst)
            os.rename(os.path.join(OUT, f), dst)
            res.append(dst)
    return res
