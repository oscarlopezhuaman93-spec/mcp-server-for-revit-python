# -*- coding: utf-8 -*-
"""Prueba de Conexiones (BOKI Metalicas) por el MCP, sin guardar: genera
porticos o cerchas de prueba, luego las conexiones; cuenta, pesa y exporta
imagenes de detalle; deshace todo."""
import codecs
import os
import sys

HERE = r"C:\Users\oscar\BOKI-desarrollo\tests\revit"


def run(doc, DB, root, kind, images=None):
    kc = {}
    exec(codecs.open(os.path.join(HERE, "prueba_cerchas.py"), "r", "utf-8").read(), kc)
    mk = kc["load"](root)
    import metal_spec as ms
    import metal_porticos as mp
    import metal_conexiones as mx
    level = sorted(DB.FilteredElementCollector(doc).OfClass(DB.Level), key=lambda l: abs(l.Elevation))[0]
    rep = []
    tg = DB.TransactionGroup(doc, "prueba conexiones")
    tg.Start()
    t = None
    try:
        t = DB.Transaction(doc, "g")
        t.Start()
        origin = (400.0, 400.0)
        if kind == "porticos":
            gs = kc["grids"](doc, DB, origin, 3, 6.0, 20.0)
            doc.Regenerate()
            cfg = ms.config()
            mp.generate(doc, mp.Layout(gs, cfg["span"]), level, cfg)
        else:
            gs = kc["grids"](doc, DB, origin, 3, 5.2, 47.4)
            doc.Regenerate()
            mk.generate(doc, DB, mk.Layout(DB, gs, 47.4), level, {"shape": kind})
        doc.Regenerate()
        made = mx.generate(doc, DB, mx.config())
        t.Commit()
        w = mx.weight(made)
        rep.append(u"{}: placas {}, pernos/pasadores {}, anclajes {} | kg: placas {:.0f}, pernos {:.0f}, anclajes {:.0f}".format(
            kind, len(made["plates"]), len(made["bolts"]), len(made["anchors"]), w["plates"], w["bolts"], w["anchors"]))
        cats = set(e.Category.Name for k in made for e in made[k])
        rep.append(u"   categorias: " + u", ".join(cats))
        for name, box, fwd in images or []:
            x0, y0 = origin
            b = (x0 + box[0], y0 + box[1], box[2], x0 + box[3], y0 + box[4], box[5])
            rep.append(kc["shot"](doc, DB, level, name, b, fwd))
    except Exception:
        import traceback
        rep.append(u"ERROR " + traceback.format_exc()[-900:])
    finally:
        try:
            if t is not None and t.HasStarted() and not t.HasEnded():
                t.RollBack()
        except Exception:
            pass
        tg.RollBack()
    return rep
