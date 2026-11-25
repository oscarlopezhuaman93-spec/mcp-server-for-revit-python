# -*- coding: utf-8 -*-
"""Prueba de Verificar y Metrado (BOKI Metalicas) por el MCP, sin guardar:
genera la nave (porticos + correas + conexiones) o la cubierta en arco
(cerchas + correas de tubo + conexiones), verifica, metra y deshace."""
import codecs
import os
import sys

HERE = r"C:\Users\oscar\BOKI-desarrollo\tests\revit"


def run(doc, DB, root, case, n=7, window=None):
    kc = {}
    exec(codecs.open(os.path.join(HERE, "prueba_cerchas.py"), "r", "utf-8").read(), kc)
    mk = kc["load"](root)
    import metal_spec as ms
    import metal_porticos as mp
    import metal_correas as mc
    import metal_conexiones as mx
    import metal_verificar as mv
    import metal_metrado as mm
    level = sorted(DB.FilteredElementCollector(doc).OfClass(DB.Level), key=lambda l: abs(l.Elevation))[0]
    rep = []
    tg = DB.TransactionGroup(doc, "verificar")
    tg.Start()
    t = None
    try:
        t = DB.Transaction(doc, "g")
        t.Start()
        if case == "nave":
            gs = kc["grids"](doc, DB, (400.0, 400.0), n, 6.0, 20.0)
            doc.Regenerate()
            cfg = ms.config()
            mp.generate(doc, mp.Layout(gs, cfg["span"]), level, cfg)
            ccfg = {}
        else:
            gs = kc["grids"](doc, DB, (400.0, 400.0), n, 5.2, 47.4)
            doc.Regenerate()
            mk.generate(doc, DB, mk.Layout(DB, gs, 47.4), level, {"shape": case})
            ccfg = {"purlin": u"TR 100x50x4", "spacing": 2.03}
        doc.Regenerate()
        mc.generate(doc, DB, level, ccfg)
        doc.Regenerate()
        mx.generate(doc, DB, mx.config())
        doc.Regenerate()
        t.Commit()
        for title, rows in mv.verify(doc, DB):
            rep.append(u"== " + title)
            for r in rows:
                rep.append(u"   {:<26} {:<28} {:<18} Pu {:8.1f} Mu {:7.1f} | {} | D/C {:.2f} {}".format(
                    r["elem"], r["perfil"][:28], r["combo"], r["Pu"], r["Mu"], r["cap"], r["ratio"],
                    u"OK" if r["ratio"] <= 1.0 else u"NO CUMPLE"))
        rows, total = mm.measure(doc, DB)
        rep.append(u"== Metrado")
        for r in rows:
            rep.append(u"   {:<24} {:<24} {:5d} {:9.2f} m {:10.1f} kg {:8.2f} m2".format(
                r["grupo"], r["perfil"][:24], r["cant"], r["largo"], r["kg"], r["m2"]))
        rep.append(u"   TOTAL {:.1f} kg, {:.2f} m2".format(total["kg"], total["m2"]))
        if window:
            rep.append(window(doc, DB))
    except Exception:
        import traceback
        rep.append(u"ERROR " + traceback.format_exc()[-1200:])
    finally:
        try:
            if t is not None and t.HasStarted() and not t.HasEnded():
                t.RollBack()
        except Exception:
            pass
        tg.RollBack()
    return rep
