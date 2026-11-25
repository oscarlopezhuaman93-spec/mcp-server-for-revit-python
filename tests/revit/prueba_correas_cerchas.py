# -*- coding: utf-8 -*-
"""Prueba de Correas sobre Cerchas (BOKI Metalicas) por el MCP, sin guardar:
genera cerchas de prueba (arco, dos aguas, recta) y encima las correas de
tubo; revisa como se construye (alma perpendicular a la cubierta y correa
apoyada sobre el cordon) y exporta imagenes; deshace todo."""
import codecs
import os
import sys

HERE = r"C:\Users\oscar\BOKI-desarrollo\tests\revit"


def _web_normal(DB, e):
    best = None
    for g in e.get_Geometry(DB.Options()):
        ss = [g] if isinstance(g, DB.Solid) else (
            [s for s in g.GetInstanceGeometry() if isinstance(s, DB.Solid)] if isinstance(g, DB.GeometryInstance) else [])
        for s in ss:
            for f in s.Faces:
                if isinstance(f, DB.PlanarFace) and (best is None or f.Area > best.Area):
                    best = f
    return best.FaceNormal if best is not None else None


def _min_along(DB, e, origin, n):
    pts = []
    for g in e.get_Geometry(DB.Options()):
        ss = [g] if isinstance(g, DB.Solid) else (
            [s for s in g.GetInstanceGeometry() if isinstance(s, DB.Solid)] if isinstance(g, DB.GeometryInstance) else [])
        for s in ss:
            for ed in s.Edges:
                pts += list(ed.Tessellate())
    return min((q - origin).DotProduct(n) for q in pts) if pts else 0.0


def run(doc, DB, root, cases, images=True, origin=(400.0, 400.0)):
    kc = {}
    exec(codecs.open(os.path.join(HERE, "prueba_cerchas.py"), "r", "utf-8").read(), kc)
    mk = kc["load"](root)
    import metal_correas as mc
    level = sorted(DB.FilteredElementCollector(doc).OfClass(DB.Level), key=lambda l: abs(l.Elevation))[0]
    rep = []
    for name, kcfg, ccfg, n, bay, span, box, fwd in cases:
        tg = DB.TransactionGroup(doc, "prueba correas cerchas")
        tg.Start()
        t = None
        try:
            t = DB.Transaction(doc, "g")
            t.Start()
            gs = kc["grids"](doc, DB, origin, n, bay, span)
            doc.Regenerate()
            mk.generate(doc, DB, mk.Layout(DB, gs, span), level, kcfg)
            doc.Regenerate()
            made = mc.generate(doc, DB, level, ccfg)
            t.Commit()
            shed = mc.Shed(doc, DB)
            rep.append(u"{}: {} | correas {}, templadores {}, arriostres techo {}, notas {}".format(
                name, shed.text(), len(made["purlins"]), len(made["sag"]), len(made["brace_roof"]), made["notes"]))
            # 3 correas: al alero, a media agua y a la cumbrera de la primera agua del primer pano
            c0 = shed.waters[0][0]
            for frac in (0.0, 0.5, 1.0):
                p, tan = mc._at(c0, c0.Length * frac)
                nr = shed.along.CrossProduct(tan).Normalize()
                if nr.Z < 0:
                    nr = nr.Negate()
                e = min(made["purlins"], key=lambda x: min(x.Location.Curve.GetEndPoint(k).DistanceTo(p) for k in (0, 1)))
                wn = _web_normal(DB, e)
                # la seccion de la correa sobre esta cercha, contra el cordon bajo ella
                lc = e.Location.Curve
                a = min((lc.GetEndPoint(0), lc.GetEndPoint(1)), key=lambda q: q.DistanceTo(p))
                pr = c0.Project(a)
                q0 = pr.XYZPoint
                tq = c0.ComputeDerivatives(pr.Parameter, False).BasisX.Normalize()
                nq = shed.along.CrossProduct(tq).Normalize()
                if nq.Z < 0:
                    nq = nq.Negate()
                pts = []
                for g in e.get_Geometry(DB.Options()):
                    ss = [g] if isinstance(g, DB.Solid) else (
                        [s for s in g.GetInstanceGeometry() if isinstance(s, DB.Solid)] if isinstance(g, DB.GeometryInstance) else [])
                    for so in ss:
                        for ed in so.Edges:
                            pts += [q for q in ed.Tessellate() if abs((q - a).DotProduct(lc.Direction)) < 0.05]
                gap = (min((q - q0).DotProduct(nq) for q in pts) - shed.top_offset) * 304.8
                rep.append(u"   correa a {:.0%} del agua: |normal alma . tangente| = {:.3f}; fondo a {:.1f} mm de la cara del cordon".format(
                    frac, abs(wn.DotProduct(tan)) if wn else -1, gap))
            if images and box:
                x0, y0 = origin
                b = (x0 + box[0], y0 + box[1], box[2], x0 + box[3], y0 + box[4], box[5])
                rep.append(kc["shot"](doc, DB, level, name, b, fwd))
        except Exception:
            import traceback
            rep.append(u"{}: ERROR {}".format(name, traceback.format_exc()[-700:]))
        finally:
            try:
                if t is not None and t.HasStarted() and not t.HasEnded():
                    t.RollBack()
            except Exception:
                pass
            tg.RollBack()
    return rep
