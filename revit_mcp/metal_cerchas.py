# -*- coding: utf-8 -*-
"""BOKI Metalicas - cerchas de tubo rectangular sobre ejes (reglas PROPIAS:
no importa nada de Porticos, Correas ni acero de refuerzo).

Formas: "arco" (cordones concentricos), "dos_aguas" (cordones paralelos con
cumbrera) y "recta" (cordones paralelos horizontales). Seccion: "plana" (un
plano: 1 cordon superior + 1 inferior) o "espacial" (rectangular: 2 planos
separados `width`, unidos por travesanos en cada nudo arriba y abajo).
Celosia: montantes en cada nudo (radiales en el arco) y diagonales en zigzag
(nudo inferior -> superior siguiente, y al reves).

Estandar por defecto = cobertura de la cancha de Chumbivilcas (2026-11-25):
cordones HSS 50x100x3, montantes y diagonales 50x50x3, travesanos 25x50x3,
peralte 0.70 m, ancho 0.40 m, panel 0.85 m, flecha 7.5 m en 47.4 m de luz.
Unidades: m y kg (mm donde se dice).
"""
import json
import math
import os

FT = 0.3048
TAG = u"BOKI Cercha"
STEEL = 7850.0
HSS_FAMILY = u"M_HSS-Hollow Structural Section"
FAMILY_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), u"familias", u"metalicas")

# tubos rectangulares (mm): alto Ht, ancho b, espesor t
TUBES = {
    u"TR 25x50x2": (25, 50, 2.0), u"TR 25x50x3": (25, 50, 3.0),
    u"TR 40x40x2": (40, 40, 2.0), u"TR 50x50x2": (50, 50, 2.0), u"TR 50x50x3": (50, 50, 3.0),
    u"TR 60x40x2": (60, 40, 2.0), u"TR 75x50x3": (75, 50, 3.0),
    u"TR 100x50x2": (100, 50, 2.0), u"TR 100x50x3": (100, 50, 3.0), u"TR 100x50x4": (100, 50, 4.0),
    u"TR 100x100x3": (100, 100, 3.0), u"TR 150x50x3": (150, 50, 3.0), u"TR 150x100x4": (150, 100, 4.0),
}

SHAPES = (u"arco", u"dos_aguas", u"recta")
SECTIONS = (u"plana", u"espacial")

DEFAULTS = {
    "shape": u"arco", "section": u"espacial",
    "rise": 7.5,  # m, flecha del arco (cordon inferior)
    "slope": 15.0,  # %, pendiente de la cercha a dos aguas
    "depth": 0.70,  # m, peralte eje a eje de cordones
    "width": 0.40,  # m, ancho de la seccion espacial
    "panel": 0.85,  # m, separacion maxima de montantes
    "support": 5.0,  # m sobre el nivel, si no hay columna en el apoyo
    "chord": u"TR 100x50x3", "web": u"TR 50x50x3", "cross": u"TR 25x50x3",
}


class SpecError(Exception):
    pass


def config(data=None):
    cfg = dict(DEFAULTS)
    cfg.update(dict((k, v) for k, v in (data or {}).items() if v is not None))
    for k in ("rise", "slope", "depth", "width", "panel", "support"):
        cfg[k] = float(cfg[k])
    if cfg["shape"] not in SHAPES:
        raise SpecError(u"forma no válida")
    if cfg["section"] not in SECTIONS:
        raise SpecError(u"sección no válida")
    if not 0.20 <= cfg["depth"] <= 3.0:
        raise SpecError(u"peralte fuera de rango (0.20 a 3.00 m)")
    if not 0.30 <= cfg["panel"] <= 3.0:
        raise SpecError(u"panel fuera de rango (0.30 a 3.00 m)")
    if cfg["section"] == u"espacial" and not 0.15 <= cfg["width"] <= 2.0:
        raise SpecError(u"ancho fuera de rango (0.15 a 2.00 m)")
    for k in ("chord", "web", "cross"):
        if cfg[k] not in TUBES:
            raise SpecError(u"tubo no disponible: {}".format(cfg[k]))
    return cfg


def tube_kg_per_m(name):
    h, b, t = TUBES[name]
    return 2 * (h + b - 2 * t) * t * 1e-6 * STEEL


# --- geometria pura en el plano de la cercha (u a lo largo de la luz, z arriba)
def arc_center(span, rise):
    c = span / 2.0
    R = (c * c + rise * rise) / (2.0 * rise)
    return (c, rise - R), R


def nodes(cfg, span):
    """[(inferior (u, z), superior (u, z))] en cada nudo, de A a B."""
    d = cfg["depth"]
    if cfg["shape"] == u"arco":
        (cu, cz), R = arc_center(span, cfg["rise"])
        a0 = math.atan2(0 - cz, 0 - cu)
        a1 = math.atan2(0 - cz, span - cu)
        length = R * abs(a0 - a1)
        n = max(2, int(math.ceil(length / cfg["panel"] - 1e-9)))
        out = []
        for i in range(n + 1):
            a = a0 + (a1 - a0) * i / float(n)
            out.append(((cu + R * math.cos(a), cz + R * math.sin(a)),
                        (cu + (R + d) * math.cos(a), cz + (R + d) * math.sin(a))))
        return out
    n = max(2, int(math.ceil(span / cfg["panel"] - 1e-9)))
    if cfg["shape"] == u"dos_aguas" and n % 2:
        n += 1  # un nudo en la cumbrera
    out = []
    t = cfg["slope"] / 100.0 if cfg["shape"] == u"dos_aguas" else 0.0
    for i in range(n + 1):
        u = span * i / float(n)
        z = min(u, span - u) * t
        out.append(((u, z), (u, z + d)))
    return out


def chords(cfg, span):
    """Cordones como tramos: {"inf": [...], "sup": [...]} con ("arc", p0, pm,
    p1) o ("line", p0, p1) en (u, z)."""
    ns = nodes(cfg, span)
    out = {}
    for k, key in ((0, "inf"), (1, "sup")):
        if cfg["shape"] == u"arco":
            out[key] = [("arc", ns[0][k], ns[len(ns) // 2][k] if len(ns) % 2 else
                         _mid_arc(cfg, span, k), ns[-1][k])]
        elif cfg["shape"] == u"dos_aguas":
            m = len(ns) // 2
            out[key] = [("line", ns[0][k], ns[m][k]), ("line", ns[m][k], ns[-1][k])]
        else:
            out[key] = [("line", ns[0][k], ns[-1][k])]
    return out


def _mid_arc(cfg, span, k):
    (cu, cz), R = arc_center(span, cfg["rise"])
    r = R + (cfg["depth"] if k else 0.0)
    return (cu, cz + r)


def web(cfg, span):
    """Montantes y diagonales: [("montante"|"diagonal", (u, z), (u, z))]."""
    ns = nodes(cfg, span)
    out = []
    for i, (b, t) in enumerate(ns):
        out.append(("montante", b, t))
        if i + 1 < len(ns):
            b2, t2 = ns[i + 1]
            out.append(("diagonal", b, t2) if i % 2 == 0 else ("diagonal", t, b2))
    return out


def _length(seg):
    if seg[0] == "line":
        return math.hypot(seg[2][0] - seg[1][0], seg[2][1] - seg[1][1])
    (u0, z0), (um, zm), (u1, z1) = seg[1], seg[2], seg[3]
    # arco por tres puntos
    ax, ay, bx, by, cx, cy = u0, z0, um, zm, u1, z1
    dd = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) + (cx * cx + cy * cy) * (ay - by)) / dd
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) + (cx * cx + cy * cy) * (bx - ax)) / dd
    r = math.hypot(ax - ux, ay - uy)
    a0, a1 = math.atan2(ay - uy, ax - ux), math.atan2(cy - uy, cx - ux)
    return r * abs(a1 - a0)


def summary(cfg, span, trusses):
    cfg = config(cfg)
    ns = nodes(cfg, span)
    planes = 2 if cfg["section"] == u"espacial" else 1
    ch = chords(cfg, span)
    chord_len = sum(_length(s) for s in ch["inf"] + ch["sup"])
    wb = web(cfg, span)
    web_len = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for _, a, b in wb)
    cross_n = 2 * len(ns) if planes == 2 else 0
    out = {
        "nodes": len(ns), "panels": len(ns) - 1, "planes": planes,
        "montantes": planes * len(ns) * trusses, "diagonales": planes * (len(ns) - 1) * trusses,
        "travesanos": cross_n * trusses,
        "chord_m": planes * chord_len * trusses, "web_m": planes * web_len * trusses,
        "cross_m": cross_n * cfg["width"] * trusses,
        "top": max(t[1] for _, t in ns),
    }
    out["kg_chords"] = out["chord_m"] * tube_kg_per_m(cfg["chord"])
    out["kg_web"] = out["web_m"] * tube_kg_per_m(cfg["web"])
    out["kg_cross"] = out["cross_m"] * tube_kg_per_m(cfg["cross"])
    out["kg_total"] = out["kg_chords"] + out["kg_web"] + out["kg_cross"]
    return out


# --- Revit ------------------------------------------------------------------
def _name(e):
    from Autodesk.Revit.DB import Element
    return Element.Name.GetValue(e)


def _ft(m):
    return m / FT


def tube_type(doc, DB, name):
    """El tipo de tubo `name` en la familia HSS de BOKI (cargada de
    familias/metalicas si falta)."""
    fam = None
    for f in DB.FilteredElementCollector(doc).OfClass(DB.Family):
        if f.Name == HSS_FAMILY:
            fam = f
            break
    if fam is None:
        import clr
        ref = clr.Reference[DB.Family]()
        path = os.path.join(FAMILY_DIR, HSS_FAMILY + u".rfa")
        if not os.path.isfile(path) or not doc.LoadFamily(path, ref):
            raise SpecError(u"no se encontró la familia de tubo de BOKI ({})".format(path))
        fam = ref.Value
    syms = [doc.GetElement(i) for i in fam.GetFamilySymbolIds()]
    s = next((x for x in syms if _name(x) == name), None)
    if s is None:
        s = syms[0].Duplicate(name)
    h, b, t = TUBES[name]
    for k, v in ((u"Ht", h), (u"b", b), (u"t", t)):
        p = s.LookupParameter(k)
        if p is not None and not p.IsReadOnly:
            p.Set(v / 304.8)
    if not s.IsActive:
        s.Activate()
    return s


def _cross(c1, c2):
    p, r = c1.GetEndPoint(0), c1.Direction
    q, s = c2.GetEndPoint(0), c2.Direction
    den = r.X * s.Y - r.Y * s.X
    if abs(den) < 1e-9:
        return None
    t = ((q.X - p.X) * s.Y - (q.Y - p.Y) * s.X) / den
    return (p.X + r.X * t, p.Y + r.Y * t)


class Layout(object):
    """Cerchas leidas de los ejes: los transversales son las cerchas y los
    dos longitudinales los apoyos (A y B). `frames`: [(nombre, A, B)] en
    pies, z = 0, en orden a lo largo de la cubierta."""

    def __init__(self, DB, grids, typed_span=None):
        lines = [g for g in grids if isinstance(g.Curve, DB.Line)]
        groups = []
        for g in lines:
            d = g.Curve.Direction
            for grp in groups:
                if abs(abs(grp[0].Curve.Direction.DotProduct(d)) - 1.0) < 1e-6:
                    grp.append(g)
                    break
            else:
                groups.append([g])
        if len(groups) != 2:
            raise SpecError(u"elige los ejes de las cerchas y los dos ejes de apoyo (perpendiculares)")
        a, b = groups

        def gap(p):
            q = p[1].Curve.Project(p[0].Curve.GetEndPoint(0))
            return p[0].Curve.GetEndPoint(0).DistanceTo(q.XYZPoint) * FT if q is not None else 0.0
        if len(a) == 2 and len(b) == 2 and typed_span:
            # dos y dos: los apoyos son el par separado por la luz escrita
            if abs(gap(b) - typed_span) < abs(gap(a) - typed_span):
                a, b = b, a
        elif len(a) > len(b):
            a, b = b, a
        if len(a) != 2 or len(b) < 1:
            raise SpecError(u"elige exactamente 2 ejes de apoyo (A y B) y al menos 1 eje de cercha")
        ux = a[0].Curve.Direction
        if ux.X < -1e-9 or (abs(ux.X) < 1e-9 and ux.Y < 0):
            ux = ux.Negate()
        o = a[0].Curve.GetEndPoint(0)
        b = sorted(b, key=lambda g: (g.Curve.GetEndPoint(0) - o).DotProduct(ux))
        self.frames = []
        for g in b:
            pa, pb = _cross(g.Curve, a[0].Curve), _cross(g.Curve, a[1].Curve)
            if pa is None or pb is None:
                raise SpecError(u"el eje {} no cruza los ejes de apoyo".format(g.Name))
            self.frames.append((g.Name, DB.XYZ(pa[0], pa[1], 0), DB.XYZ(pb[0], pb[1], 0)))
        self.lines = (a[0].Name, a[1].Name)
        self.span = self.frames[0][1].DistanceTo(self.frames[0][2]) * FT
        self.spacings = [self.frames[i][1].DistanceTo(self.frames[i + 1][1]) * FT for i in range(len(self.frames) - 1)]

    def text(self):
        sp = sorted(set(round(s, 2) for s in self.spacings))
        return u"{} cercha(s) (ejes {} a {}), apoyos en {} y {}, luz {:.2f} m{}".format(
            len(self.frames), self.frames[0][0], self.frames[-1][0], self.lines[0], self.lines[1], self.span,
            (u", separación " + u" / ".join(u"{:.2f} m".format(s) for s in sp)) if sp else u"")


def support_top(doc, DB, point, level_z, default_m):
    """Cota de apoyo en un cruce: la cara superior de la columna que esta
    ahi (concreto o acero), o el nivel + `default_m`."""
    best = None
    for c in DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_StructuralColumns) \
            .WhereElementIsNotElementType():
        bb = c.get_BoundingBox(None)
        if bb is None:
            continue
        if bb.Min.X - 0.01 <= point.X <= bb.Max.X + 0.01 and bb.Min.Y - 0.01 <= point.Y <= bb.Max.Y + 0.01:
            best = bb.Max.Z if best is None else max(best, bb.Max.Z)
    return best if best is not None else level_z + _ft(default_m)


def _tag(DB, e, cfg, kind):
    p = e.get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
    if p is not None and not p.IsReadOnly:
        p.Set(u"{} {} {}".format(TAG, kind, json.dumps(cfg, sort_keys=True)))


def existing(doc, DB):
    out = []
    for e in DB.FilteredElementCollector(doc).WhereElementIsNotElementType():
        try:
            p = e.get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
        except Exception:
            continue
        if p is not None and (p.AsString() or u"").startswith(TAG + u" "):
            out.append(e)
    return out


def saved_config(doc, DB):
    for e in existing(doc, DB):
        text = e.get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS).AsString()
        j = text.find(u"{")
        if j >= 0:
            try:
                return config(json.loads(text[j:]))
            except (ValueError, SpecError):
                pass
    return None


def _member(doc, DB, curve, sym, level, cfg, kind):
    from Autodesk.Revit.DB.Structure import StructuralType, StructuralFramingUtils
    e = doc.Create.NewFamilyInstance(curve, sym, level, StructuralType.Beam)
    for bip, val in ((DB.BuiltInParameter.Z_JUSTIFICATION, 1), (DB.BuiltInParameter.Y_JUSTIFICATION, 1)):
        p = e.get_Parameter(bip)
        if p is not None and not p.IsReadOnly:
            p.Set(val)  # centrado en su eje
    for end in (0, 1):
        try:
            StructuralFramingUtils.DisallowJoinAtEnd(e, end)
        except Exception:
            pass
    _tag(DB, e, cfg, kind)
    return e


def generate(doc, DB, layout, level, cfg):
    """Crea las cerchas (en una Transaction). Borra antes lo que este boton
    genero. {clase: [elementos]}."""
    from System.Collections.Generic import List
    cfg = config(cfg)
    old = List[DB.ElementId]([e.Id for e in existing(doc, DB)])
    if old.Count:
        doc.Delete(old)
    t_chord = tube_type(doc, DB, cfg["chord"])
    t_web = tube_type(doc, DB, cfg["web"])
    t_cross = tube_type(doc, DB, cfg["cross"]) if cfg["section"] == u"espacial" else None
    made = {"chords": [], "montantes": [], "diagonales": [], "travesanos": []}
    for name, pa, pb in layout.frames:
        z0 = min(support_top(doc, DB, pa, level.Elevation, cfg["support"]),
                 support_top(doc, DB, pb, level.Elevation, cfg["support"]))
        span = pa.DistanceTo(pb) * FT
        d = (pb - pa).Normalize()
        along = DB.XYZ.BasisZ.CrossProduct(d).Normalize()
        offs = [0.0] if cfg["section"] == u"plana" else [-cfg["width"] / 2.0, cfg["width"] / 2.0]

        def P(u, z, y):
            return DB.XYZ(pa.X + d.X * _ft(u) + along.X * _ft(y), pa.Y + d.Y * _ft(u) + along.Y * _ft(y), z0 + _ft(z))

        ch = chords(cfg, span)
        for y in offs:
            for key in ("inf", "sup"):
                for seg in ch[key]:
                    if seg[0] == "arc":
                        crv = DB.Arc.Create(P(seg[1][0], seg[1][1], y), P(seg[3][0], seg[3][1], y),
                                            P(seg[2][0], seg[2][1], y))
                    else:
                        crv = DB.Line.CreateBound(P(seg[1][0], seg[1][1], y), P(seg[2][0], seg[2][1], y))
                    made["chords"].append(_member(doc, DB, crv, t_chord, level, cfg,
                                                  u"cordon_{} {}".format(key, name)))
            for kind, a, b in web(cfg, span):
                crv = DB.Line.CreateBound(P(a[0], a[1], y), P(b[0], b[1], y))
                made[kind + "s" if kind == "montante" else "diagonales"].append(
                    _member(doc, DB, crv, t_web, level, cfg, u"{} {}".format(kind, name)))
        if t_cross is not None:
            for bot, top in nodes(cfg, span):
                for q in (bot, top):
                    crv = DB.Line.CreateBound(P(q[0], q[1], offs[0]), P(q[0], q[1], offs[1]))
                    made["travesanos"].append(_member(doc, DB, crv, t_cross, level, cfg, u"travesano " + name))
    return made
