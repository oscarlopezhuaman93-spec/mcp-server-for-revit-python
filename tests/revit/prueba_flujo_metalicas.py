# -*- coding: utf-8 -*-
"""Prueba del flujo completo de BOKI Metalicas con el plugin de `root`
(estable o desarrollo), sin guardar: (A) nave con porticos + correas C +
conexiones; (B) cubierta en arco + correas de tubo + conexiones. Cuenta,
pesa y exporta imagenes; deshace todo."""
import codecs
import os

HERE = r"C:\Users\oscar\BOKI-desarrollo\tests\revit"


def run(doc, DB, root, case, shots):
    kc = {}
    exec(codecs.open(os.path.join(HERE, "prueba_cerchas.py"), "r", "utf-8").read(), kc)
    mk = kc["load"](root)
    import metal_spec as ms
    import metal_porticos as mp
    import metal_correas as mc
    import metal_conexiones as mx
    level = sorted(DB.FilteredElementCollector(doc).OfClass(DB.Level), key=lambda l: abs(l.Elevation))[0]
    origin = (400.0, 400.0)
    rep = [u"plugin: " + os.path.dirname(mk.__file__)]
    tg = DB.TransactionGroup(doc, "flujo")
    tg.Start()
    t = None
    try:
        t = DB.Transaction(doc, "g")
        t.Start()
        if case == "nave":
            gs = kc["grids"](doc, DB, origin, 7, 6.0, 20.0)
            doc.Regenerate()
            cfg = ms.config()
            a = mp.generate(doc, mp.Layout(gs, cfg["span"]), level, cfg)
            rep.append(u"porticos: " + u", ".join(u"{} {}".format(len(v), k) for k, v in sorted(a.items())))
            ccfg = {}
        else:
            gs = kc["grids"](doc, DB, origin, 5, 5.2, 47.4)
            doc.Regenerate()
            a = mk.generate(doc, DB, mk.Layout(DB, gs, 47.4), level, {"shape": u"arco"})
            rep.append(u"cerchas: " + u", ".join(u"{} {}".format(len(v), k) for k, v in sorted(a.items())))
            ccfg = {"purlin": u"TR 100x50x4", "spacing": 2.03}
        doc.Regenerate()
        b = mc.generate(doc, DB, level, ccfg)
        rep.append(u"correas: " + u", ".join(u"{} {}".format(len(v), k) for k, v in sorted(b.items()) if k != "notes"))
        doc.Regenerate()
        c = mx.generate(doc, DB, mx.config())
        w = mx.weight(c)
        rep.append(u"conexiones: {} placas ({:.0f} kg), {} grupos de pernos ({:.0f} kg), {} grupos de anclajes ({:.0f} kg)".format(
            len(c["plates"]), w["plates"], len(c["bolts"]), w["bolts"], len(c["anchors"]), w["anchors"]))
        t.Commit()
        for name, box, fwd in shots:
            x0, y0 = origin
            bx = (x0 + box[0], y0 + box[1], box[2], x0 + box[3], y0 + box[4], box[5])
            rep.append(kc["shot"](doc, DB, level, name, bx, fwd))
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
