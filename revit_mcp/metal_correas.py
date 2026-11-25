# -*- coding: utf-8 -*-
"""BOKI Metalicas - correas, templadores, largueros y arriostres sobre los
porticos de alma llena ya generados.

Reglas PROPIAS (2026-11-25): no importa metal_spec ni metal_porticos ni nada
de acero de refuerzo; lee la nave de lo que hay en el modelo (las piezas
marcadas "BOKI Portico ..." en Comentarios), asi un cambio en Porticos no
cambia estas reglas y al reves.

Criterio (E.090 / AISI S100, cobertura TR-4, paredes cerradas):
- Correas de techo C 150x50x15x2.5 mm, separacion <= 1.20 m, la primera y la
  ultima a 0.15 m del alero y de la cumbrera, simplemente apoyadas de portico
  a portico, alma perpendicular al techo, sobre el ala superior de la viga.
- Templadores de barra lisa 1/2" a los tercios de cada pano, de la primera a
  la ultima correa de cada agua, a media altura de la correa.
- Largueros laterales C 150x50x15x2.5 en los ejes de columnas, por fuera de
  las columnas, cada 1.50 m en altura hasta 0.30 m bajo el alero (el amarre
  del alero lo pone Porticos); alma horizontal (el viento flexiona su eje
  fuerte).
- Arriostres en X de barra lisa 3/4" en los panos extremos: techo (2 paneles
  por agua, sobre las vigas) y paredes (de la base al alero, en el eje).
"""
import json
import math
import os

FT = 0.3048
TAG = u"BOKI Correas"
PORTICO_TAG = u"BOKI Portico"
STEEL = 7850.0

DEFAULTS = {
    "roof": True, "purlin": u"C150x50x15x2.5", "spacing": 1.20, "first": 0.15,
    "sag": True, "sag_rod": 12.7,
    "girts": True, "girt": u"C150x50x15x2.5", "girt_step": 1.50, "girt_top_gap": 0.30,
    "brace_roof": True, "brace_walls": True, "brace_rod": 19.05,
}

# Perfiles C con labios (mm): peralte, ala, labio, espesor
C_PROFILES = {
    u"C100x50x15x2": (100, 50, 15, 2.0),
    u"C150x50x15x2": (150, 50, 15, 2.0),
    u"C150x50x15x2.5": (150, 50, 15, 2.5),
    u"C150x50x15x3": (150, 50, 15, 3.0),
    u"C200x50x15x2.5": (200, 50, 15, 2.5),
    u"C200x50x15x3": (200, 50, 15, 3.0),
    u"C250x75x20x3": (250, 75, 20, 3.0),
}
# Tubos rectangulares (mm): alto Ht, ancho b, espesor t (como la cancha de
# Chumbivilcas: correa 100x50x4 @ 2.03 m)
TUBE_PROFILES = {
    u"TR 75x50x3": (75, 50, 3.0), u"TR 100x50x2": (100, 50, 2.0), u"TR 100x50x3": (100, 50, 3.0),
    u"TR 100x50x4": (100, 50, 4.0), u"TR 150x50x3": (150, 50, 3.0), u"TR 150x100x4": (150, 100, 4.0),
}
RODS = {12.7: u"\u00d8 1/2\"", 15.875: u"\u00d8 5/8\"", 19.05: u"\u00d8 3/4\"", 25.4: u"\u00d8 1\""}

CERCHA_TAG = u"BOKI Cercha"
HSS_FAMILY = u"M_HSS-Hollow Structural Section"  # de familias/metalicas (cancha)
FAMILY_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), u"familias", u"metalicas")
PURLIN_FAMILY = u"Calibre delgado-Vigueta"  # C con labios (D peralte, B ala, d labio, t)
ROD_FAMILY = u"Barra redonda"
LIBRARIES = [u"C:\\ProgramData\\Autodesk\\RVT 2025\\Libraries\\Spanish",
             u"C:\\ProgramData\\Autodesk\\RVT 2024\\Libraries\\Spanish",
             u"C:\\ProgramData\\Autodesk\\RVT 2026\\Libraries\\Spanish"]
FAMILY_FILES = {
    PURLIN_FAMILY: (u"Armaz\u00f3n estructural", u"Acero de calibre delgado", u"Calibre delgado-Vigueta.rfa"),
    ROD_FAMILY: (u"Armaz\u00f3n estructural", u"Acero", u"Barra redonda.rfa"),
}


class SpecError(Exception):
    pass


# --- reglas puras -----------------------------------------------------------
def config(data=None):
    cfg = dict(DEFAULTS)
    cfg.update(dict((k, v) for k, v in (data or {}).items() if v is not None))
    for k in ("spacing", "first", "girt_step", "girt_top_gap", "sag_rod", "brace_rod"):
        cfg[k] = float(cfg[k])
    if not 0.60 <= cfg["spacing"] <= 2.50:  # TR5 de la cancha: 2.03 m
        raise SpecError(u"separaci\u00f3n de correas fuera de rango (0.60 a 2.50 m)")
    if not 0.80 <= cfg["girt_step"] <= 2.50:
        raise SpecError(u"separaci\u00f3n de largueros fuera de rango (0.80 a 2.50 m)")
    for k in ("purlin", "girt"):
        if cfg[k] not in C_PROFILES and cfg[k] not in TUBE_PROFILES:
            raise SpecError(u"perfil no disponible: {}".format(cfg[k]))
    return cfg


def profiles():
    return sorted(C_PROFILES) + sorted(TUBE_PROFILES)


def depth_mm(name):
    return (C_PROFILES.get(name) or TUBE_PROFILES[name])[0]


def c_kg_per_m(name):
    """kg/m de una C con labios o de un tubo rectangular."""
    if name in TUBE_PROFILES:
        h, b, t = TUBE_PROFILES[name]
        return 2 * (h + b - 2 * t) * t * 1e-6 * STEEL
    h, b, lip, t = C_PROFILES[name]
    area = (h + 2 * b + 2 * lip - 4 * t) * t  # mm2, linea media
    return area * 1e-6 * STEEL


def rod_kg_per_m(d_mm):
    return math.pi * (d_mm / 2000.0) ** 2 * STEEL


def purlin_stations(length, spacing, first):
    """Distancias a lo largo de una agua (desde el alero) de las correas:
    la primera y la ultima a `first` de los extremos, iguales, <= spacing."""
    run = length - 2 * first
    if run <= 0:
        return [length / 2.0]
    n = int(math.ceil(run / spacing - 1e-9))
    return [first + run * i / float(n) for i in range(n + 1)]


def girt_heights(eave, step, top_gap):
    out, z = [], step
    while z <= eave - top_gap + 1e-9:
        out.append(round(z, 4))
        z += step
    return out


# --- lectura de la nave -----------------------------------------------------
class Shed(object):
    """La cubierta leida del modelo: de los Porticos BOKI (vigas a dos aguas
    sobre columnas) o de las Cerchas BOKI (arco, dos aguas o recta). Por
    portico/cercha, en orden a lo largo de la cubierta: `waters[i]` = sus
    aguas, curvas (recta o arco) del alero a la cumbrera sobre las que van
    las correas; `top_offset` (pies) de esas curvas a la cara superior del
    acero; `walls` si hay columnas BOKI para largueros y arriostres de
    pared."""

    def __init__(self, doc, DB):
        try:
            self._porticos(doc, DB)
        except SpecError:
            try:
                self._cerchas(doc, DB)
            except SpecError:
                raise SpecError(u"no hay pórticos ni cerchas BOKI en el modelo "
                                u"(genera primero Pórticos o Cerchas)")

    def _cerchas(self, doc, DB):
        tops, cfg_text = {}, u"{}"
        for e in DB.FilteredElementCollector(doc).WhereElementIsNotElementType():
            try:
                p = e.get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
            except Exception:
                continue
            text = (p.AsString() or u"") if p is not None else u""
            if not text.startswith(CERCHA_TAG + u" cordon_sup "):
                continue
            name = text[len(CERCHA_TAG + u" cordon_sup "):].split(u" ")[0]
            if isinstance(getattr(e, "Location", None), DB.LocationCurve):
                tops.setdefault(name, []).append(e.Location.Curve)
                cfg_text = text[text.find(u"{"):] if u"{" in text else cfg_text
        if len(tops) < 2:
            raise SpecError(u"no hay cerchas BOKI")
        try:
            ccfg = json.loads(cfg_text)
        except ValueError:
            ccfg = {}
        chord = ccfg.get("chord", u"TR 100x50x3")
        try:
            ht = float(chord.split(u" ")[1].split(u"x")[0])
        except (IndexError, ValueError):
            ht = 100.0
        self.source = u"cerchas"
        self.top_offset = _ft(ht / 2000.0)  # eje del cordon -> cara superior
        frames = []
        for name, curves in tops.items():
            ends = [c.GetEndPoint(k) for c in curves for k in (0, 1)]
            low = sorted(ends, key=lambda q: q.Z)[:2] if len(curves) == 1 else None
            frames.append([name, curves])
        # direccion de la luz: de un extremo bajo al otro del primer cordon
        c0 = frames[0][1]
        pts = [c.GetEndPoint(k) for c in c0 for k in (0, 1)]
        pa = min(pts, key=lambda q: (q.Z, q.X, q.Y))
        pb = max(pts, key=lambda q: DB.XYZ(q.X - pa.X, q.Y - pa.Y, 0).GetLength())
        span_dir = DB.XYZ(pb.X - pa.X, pb.Y - pa.Y, 0).Normalize()
        along = DB.XYZ.BasisZ.CrossProduct(span_dir).Normalize()
        if along.X < -1e-9 or (abs(along.X) < 1e-9 and along.Y < 0):
            along = along.Negate()
        self.span_dir, self.along = span_dir, along
        out = []
        for name, curves in frames:
            # espacial: dos planos -> la linea media entre ellos
            pos = lambda c: c.Evaluate(0.5, True).DotProduct(along)
            curves = sorted(curves, key=pos)
            if len(curves) >= 2 and abs(pos(curves[-1]) - pos(curves[0])) > _ft(0.05):
                half = len(curves) // 2
                shift = along * ((pos(curves[-1]) - pos(curves[0])) / 2.0)
                curves = [c.CreateTransformed(DB.Transform.CreateTranslation(shift)) for c in curves[:half]]
            waters = []
            for c in curves:
                waters += _split_at_apex(DB, c)
            o = waters[0].GetEndPoint(0)
            waters.sort(key=lambda c: (_low_end(c) - o).DotProduct(span_dir))
            ends = [_low_end(c) for c in waters]
            fa = min(ends, key=lambda q: q.DotProduct(span_dir))
            fb = max([w.GetEndPoint(k) for w in waters for k in (0, 1)], key=lambda q: q.DotProduct(span_dir))
            ridge = max([w.GetEndPoint(k) for w in waters for k in (0, 1)], key=lambda q: q.Z)
            out.append([name, fa, fb, waters, ridge])
        o = out[0][1]
        out.sort(key=lambda f: (f[1] - o).DotProduct(along))
        self.frames = out
        self.waters = [f[3] for f in out]
        self.walls = False
        self.base_z = self.eave_z = min(_low_end(w).Z for f in out for w in f[3])
        self.span = DB.XYZ(out[0][2].X - out[0][1].X, out[0][2].Y - out[0][1].Y, 0).GetLength() * FT
        self.column_depth = 0.0
        self.shape = ccfg.get("shape", u"")

    def _porticos(self, doc, DB):
        cols, rafters = {}, {}
        self.column_depth = 0.0
        for e in DB.FilteredElementCollector(doc).WhereElementIsNotElementType():
            try:
                p = e.get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
            except Exception:
                continue
            text = (p.AsString() or u"") if p is not None else u""
            if not text.startswith(PORTICO_TAG + u" "):
                continue
            words = text[len(PORTICO_TAG) + 1:].split(u" ")
            if len(words) < 2:
                continue
            kind, name = words[0], words[1]
            if kind == u"columna" and e.Location is not None:
                cols.setdefault(name, []).append(e)
            elif kind == u"viga" and isinstance(getattr(e, "Location", None), DB.LocationCurve):
                rafters.setdefault(name, []).append(e.Location.Curve)
            if kind == u"columna" and not self.column_depth:
                self.cfg_text = text[text.find(u"{"):] if u"{" in text else u"{}"
        names = [n for n in cols if len(cols[n]) == 2 and len(rafters.get(n, [])) == 2]
        if len(names) < 2:
            raise SpecError(u"no hay p\u00f3rticos BOKI en el modelo (genera primero los P\u00f3rticos)")
        try:
            pcfg = json.loads(self.cfg_text)
        except ValueError:
            pcfg = {}
        self.portico_cfg = pcfg
        frames = []
        for n in names:
            pa, pb = [c.Location.Point for c in cols[n]]
            curves = rafters[n]
            ridge = max([c.GetEndPoint(k) for c in curves for k in (0, 1)], key=lambda q: q.Z)
            frames.append([n, pa, pb, curves, ridge])
        # orden a lo largo de la nave; A = el lado del primer pie de columna
        span_dir = (frames[0][2] - frames[0][1]).Normalize()
        along = DB.XYZ.BasisZ.CrossProduct(span_dir).Normalize()
        if along.X < -1e-9 or (abs(along.X) < 1e-9 and along.Y < 0):
            along = along.Negate()  # P1, P2, ... en el orden de los ejes
        o = frames[0][1]
        frames.sort(key=lambda f: (f[1] - o).DotProduct(along))
        for f in frames:  # mismo lado A para todos
            if (f[2] - f[1]).DotProduct(span_dir) < 0:
                f[1], f[2] = f[2], f[1]
        self.frames = frames
        self.span_dir = span_dir
        self.along = along if (frames[-1][1] - frames[0][1]).DotProduct(along) >= 0 else along.Negate()
        self.base_z = min(f[1].Z for f in frames)
        self.eave_z = min(min(c.GetEndPoint(0).Z, c.GetEndPoint(1).Z) for f in frames for c in f[3])
        self.span = frames[0][1].DistanceTo(frames[0][2]) * FT
        bb = cols[names[0]][0].get_BoundingBox(None)
        dx = abs((bb.Max - bb.Min).DotProduct(span_dir))
        self.column_depth = dx if dx > 0 else 0.35 / FT  # pies, en el plano del portico
        self.source = u"porticos"
        self.top_offset = 0.0  # las vigas ya estan en su cara superior
        self.walls = True
        self.shape = u"dos_aguas"
        self.waters = []
        for f in frames:
            ws = []
            for side in (0, 1):
                a, b = self.water(f, side)
                ws.append(DB.Line.CreateBound(a, b))
            self.waters.append(ws)

    def water_lengths(self):
        return [w.Length * FT for w in self.waters[0]]

    def text(self):
        what = u"pórticos" if self.source == u"porticos" else u"cerchas ({})".format(self.shape)
        return u"Cubierta leída de los/las {} BOKI: {} ({} a {}), luz {:.2f} m{}.".format(
            what, len(self.frames), self.frames[0][0], self.frames[-1][0], self.span,
            u"" if self.walls else u" - sin columnas BOKI: no hay largueros ni arriostres de pared")

    def water(self, f, side):
        """(inicio, fin) de la cara superior de la viga de un agua: side 0 =
        lado A, 1 = lado B; del alero (cara de columna) a la cumbrera."""
        foot = f[1] if side == 0 else f[2]
        c = min(f[3], key=lambda cv: min(cv.GetEndPoint(0).DistanceTo(foot), cv.GetEndPoint(1).DistanceTo(foot)))
        a, b = c.GetEndPoint(0), c.GetEndPoint(1)
        return (a, b) if a.Z <= b.Z else (b, a)


def _low_end(c):
    a, b = c.GetEndPoint(0), c.GetEndPoint(1)
    return a if a.Z <= b.Z + 1e-9 else b


def _split_at_apex(DB, c):
    """Un arco con cumbrera en medio -> dos arcos (cada agua); una recta
    queda como esta."""
    if not isinstance(c, DB.Arc):
        return [c]
    ts = [k / 200.0 for k in range(201)]
    tz = max(ts, key=lambda t: c.Evaluate(t, True).Z)
    if tz < 0.05 or tz > 0.95:
        return [c]
    P = lambda t: c.Evaluate(t, True)
    return [DB.Arc.Create(P(0.0), P(tz), P(tz / 2.0)), DB.Arc.Create(P(1.0), P(tz), P((1.0 + tz) / 2.0))]


def _at(c, s):
    """(punto, tangente subiendo) a `s` pies del extremo bajo de la curva."""
    lowfirst = c.GetEndPoint(0).Z <= c.GetEndPoint(1).Z + 1e-9
    t = max(0.0, min(1.0, s / c.Length))
    d = c.ComputeDerivatives(t if lowfirst else 1.0 - t, True)
    tan = d.BasisX.Normalize()
    return d.Origin, (tan if lowfirst else tan.Negate())


# --- Revit ------------------------------------------------------------------
def _name(DB, e):
    try:
        from Autodesk.Revit.DB import Element
        return Element.Name.GetValue(e)
    except Exception:
        return e.Name


def _ft(m):
    return m / FT


def _family_path(family):
    for lib in LIBRARIES:
        p = os.path.join(lib, *FAMILY_FILES[family])
        if os.path.isfile(p):
            return p
    raise SpecError(u"no se encontr\u00f3 la familia {} en la biblioteca de Revit".format(family))


def _family(doc, DB, family):
    for f in DB.FilteredElementCollector(doc).OfClass(DB.Family):
        if f.Name == family:
            return f
    import clr
    ref = clr.Reference[DB.Family]()
    if not doc.LoadFamily(_family_path(family), ref):
        raise SpecError(u"no se pudo cargar {}".format(family))
    return ref.Value


def _set_mm(DB, sym, name, mm):
    p = sym.LookupParameter(name)
    if p is not None and not p.IsReadOnly:
        p.Set(mm / 304.8)


def _tube_type(doc, DB, name):
    """Tubo rectangular `name` en la familia HSS de BOKI (familias/metalicas)."""
    fam = next((f for f in DB.FilteredElementCollector(doc).OfClass(DB.Family) if f.Name == HSS_FAMILY), None)
    if fam is None:
        import clr
        ref = clr.Reference[DB.Family]()
        path = os.path.join(FAMILY_DIR, HSS_FAMILY + u".rfa")
        if not os.path.isfile(path) or not doc.LoadFamily(path, ref):
            raise SpecError(u"no se encontró la familia de tubo de BOKI ({})".format(path))
        fam = ref.Value
    syms = [doc.GetElement(i) for i in fam.GetFamilySymbolIds()]
    s = next((x for x in syms if _name(DB, x) == name), None) or syms[0].Duplicate(name)
    h, b, t = TUBE_PROFILES[name]
    for k, v in ((u"Ht", h), (u"b", b), (u"t", t)):
        _set_mm(DB, s, k, v)
    if not s.IsActive:
        s.Activate()
    return s


def purlin_type(doc, DB, name):
    """El tipo `name`: C con labios (familia de calibre delgado) o tubo
    rectangular (familia HSS de BOKI)."""
    if name in TUBE_PROFILES:
        return _tube_type(doc, DB, name)
    fam = _family(doc, DB, PURLIN_FAMILY)
    syms = [doc.GetElement(i) for i in fam.GetFamilySymbolIds()]
    for s in syms:
        if _name(DB, s) == name:
            break
    else:
        s = syms[0].Duplicate(name)
    h, b, lip, t = C_PROFILES[name]
    for k, v in ((u"D", h), (u"B", b), (u"d", lip), (u"t", t)):
        _set_mm(DB, s, k, v)
    if not s.IsActive:
        s.Activate()
    return s


def rod_type(doc, DB, d_mm):
    name = RODS.get(d_mm, u"\u00d8 {:g} mm".format(d_mm))
    fam = _family(doc, DB, ROD_FAMILY)
    syms = [doc.GetElement(i) for i in fam.GetFamilySymbolIds()]
    for s in syms:
        if _name(DB, s) == name:
            break
    else:
        s = syms[0].Duplicate(name)
    _set_mm(DB, s, u"d", d_mm)
    if not s.IsActive:
        s.Activate()
    return s


def _tag(DB, element, cfg, kind):
    p = element.get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
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


def _beam(doc, DB, line, sym, level, cfg, kind, z_just=None, rotation=None):
    from Autodesk.Revit.DB.Structure import StructuralType
    e = doc.Create.NewFamilyInstance(line, sym, level, StructuralType.Beam)
    # centrada tambien en el ancho: Revit gira la seccion alrededor de su eje
    # de justificacion (con "origen" el tubo se corria en la cubierta curva)
    y_just = 1 if z_just == 1 else None
    for bip, val in ((DB.BuiltInParameter.Z_JUSTIFICATION, z_just),
                     (DB.BuiltInParameter.Y_JUSTIFICATION, y_just),
                     (DB.BuiltInParameter.STRUCTURAL_BEND_DIR_ANGLE, rotation)):
        if val is None:
            continue
        p = e.get_Parameter(bip)
        if p is not None and not p.IsReadOnly:
            p.Set(val)
    # ni se une ni se corta con las vigas de los porticos
    try:
        DB.Structure.StructuralFramingUtils.DisallowJoinAtEnd(e, 0)
        DB.Structure.StructuralFramingUtils.DisallowJoinAtEnd(e, 1)
    except Exception:
        pass
    _tag(DB, e, cfg, kind)
    return e


def generate(doc, DB, level, cfg):
    """Crea correas, templadores, largueros y arriostres (en una
    Transaction). Borra antes lo que este boton genero."""
    from System.Collections.Generic import List
    cfg = config(cfg)
    shed = Shed(doc, DB)
    old = List[DB.ElementId]([e.Id for e in existing(doc, DB)])
    if old.Count:
        doc.Delete(old)
    made = {"purlins": [], "sag": [], "girts": [], "brace_roof": [], "brace_walls": [], "notes": []}
    F = shed.frames
    Wt = shed.waters
    Z = DB.XYZ.BasisZ

    def roof_normal(tan):
        n = shed.along.CrossProduct(tan).Normalize()
        return n.Negate() if n.Z < 0 else n

    if cfg["roof"]:
        pt = purlin_type(doc, DB, cfg["purlin"])
        h = depth_mm(cfg["purlin"]) / 1000.0
        rt = rod_type(doc, DB, cfg["sag_rod"]) if cfg["sag"] else None
        for w in range(len(Wt[0])):
            stations = purlin_stations(Wt[0][w].Length * FT, cfg["spacing"], cfg["first"])
            for i in range(len(F) - 1):
                c0, c1 = Wt[i][w], Wt[i + 1][w]

                def place(s, frac=None):
                    """Punto de la correa (su eje, a medio peralte sobre la
                    cara superior del acero) a `s` m del alero; con `frac`,
                    el punto a esa fraccion del pano entre las dos cerchas."""
                    p0, t0 = _at(c0, _ft(s))
                    p1, t1 = _at(c1, _ft(s))
                    n = roof_normal(t0)
                    off = n * (shed.top_offset + _ft(h / 2.0))
                    if frac is None:
                        return p0 + off, p1 + off, n, t0
                    return p0 + (p1 - p0) * frac + off, None, n, t0
                for s in stations:
                    q0, q1, n, t0 = place(s)
                    # la familia trae el alma vertical: girarla lo que se inclina el techo
                    # (el signo segun el sentido de la correa respecto a donde sube el agua)
                    ang = math.acos(max(-1.0, min(1.0, n.Z)))
                    axis = (q1 - q0).Normalize()
                    rot = ang if axis.CrossProduct(Z).DotProduct(t0) > 0 else -ang
                    made["purlins"].append(_beam(doc, DB, DB.Line.CreateBound(q0, q1), pt, level, cfg,
                                                 u"correa {}-{}".format(F[i][0], F[i + 1][0]), 1, rot))
                if rt is not None and len(stations) > 1:
                    for frac in (1 / 3.0, 2 / 3.0):
                        if isinstance(c0, DB.Line):
                            pairs = [(stations[0], stations[-1])]
                        else:  # curva: un tramo recto entre correas consecutivas
                            pairs = list(zip(stations[:-1], stations[1:]))
                        for sa, sb in pairs:
                            line = DB.Line.CreateBound(place(sa, frac)[0], place(sb, frac)[0])
                            made["sag"].append(_beam(doc, DB, line, rt, level, cfg,
                                                     u"templador {}-{}".format(F[i][0], F[i + 1][0]), 1))
    if cfg["girts"] and not shed.walls:
        made["notes"].append(u"sin columnas BOKI: no se colocan largueros ni arriostres de pared")
    if cfg["girts"] and shed.walls:
        gt = purlin_type(doc, DB, cfg["girt"])
        gd = depth_mm(cfg["girt"]) / 1000.0
        heights = girt_heights((shed.eave_z - shed.base_z) * FT, cfg["girt_step"], cfg["girt_top_gap"])
        for i in range(len(F) - 1):
            for k, out in ((1, shed.span_dir.Negate()), (2, shed.span_dir)):
                off = out * (shed.column_depth / 2.0 + _ft(gd / 2.0))
                for z in heights:
                    p0 = DB.XYZ(F[i][k].X, F[i][k].Y, shed.base_z + _ft(z)) + off
                    p1 = DB.XYZ(F[i + 1][k].X, F[i + 1][k].Y, shed.base_z + _ft(z)) + off
                    made["girts"].append(_beam(doc, DB, DB.Line.CreateBound(p0, p1), gt, level, cfg,
                                               u"larguero {}-{}".format(F[i][0], F[i + 1][0]), 1, math.pi / 2.0))
    bays = [0, len(F) - 2] if len(F) > 2 else [0]
    if cfg["brace_roof"] or cfg["brace_walls"]:
        bt = rod_type(doc, DB, cfg["brace_rod"])
    for i in bays:
        if cfg["brace_roof"]:
            for w in range(len(Wt[i])):
                c0, c1 = Wt[i][w], Wt[i + 1][w]

                def top(c, frac):
                    # bajo las correas: el eje medio diametro debajo de la cara superior del acero
                    p, tan = _at(c, c.Length * frac)
                    n = roof_normal(tan)
                    return p + n * (shed.top_offset - _ft(cfg["brace_rod"] / 2000.0))
                for f0, f1 in ((0.0, 0.5), (0.5, 1.0)):
                    for p, q in ((top(c0, f0), top(c1, f1)), (top(c1, f0), top(c0, f1))):
                        made["brace_roof"].append(_beam(doc, DB, DB.Line.CreateBound(p, q), bt, level, cfg,
                                                        u"arriostre techo {}-{}".format(F[i][0], F[i + 1][0])))
        if cfg["brace_walls"] and shed.walls:
            for k in (1, 2):
                lo0 = DB.XYZ(F[i][k].X, F[i][k].Y, shed.base_z)
                lo1 = DB.XYZ(F[i + 1][k].X, F[i + 1][k].Y, shed.base_z)
                hi0 = DB.XYZ(lo0.X, lo0.Y, shed.eave_z - _ft(0.20))
                hi1 = DB.XYZ(lo1.X, lo1.Y, shed.eave_z - _ft(0.20))
                for p, q in ((lo0, hi1), (lo1, hi0)):
                    made["brace_walls"].append(_beam(doc, DB, DB.Line.CreateBound(p, q), bt, level, cfg,
                                                     u"arriostre pared {}-{}".format(F[i][0], F[i + 1][0])))
    return made


def summary(cfg, frames, span, eave, slope_pct, water=None, waters=None, walls=True):
    """Cantidades y pesos (kg) para la ventana, con la geometria de la
    cubierta (`waters`: largo de cada agua en m, si se conoce)."""
    cfg = config(cfg)
    bays = max(frames - 1, 0)
    bay = 6.0 if "bay" not in cfg else cfg["bay"]
    rise = span / 2.0 * slope_pct / 100.0
    water = water or math.hypot(span / 2.0, rise)  # largo real de la viga si se conoce
    waters = waters or [water, water]
    sts = [purlin_stations(w, cfg["spacing"], cfg["first"]) if cfg["roof"] else [] for w in waters]
    st = sts[0]
    hs = girt_heights(eave, cfg["girt_step"], cfg["girt_top_gap"]) if cfg["girts"] and walls else []
    run = (st[-1] - st[0]) if len(st) > 1 else 0.0
    water = waters[0]
    end_bays = min(2, bays)
    lines = sum(len(s) for s in sts)
    out = {
        "purlin_lines": lines, "purlins": lines * bays,
        "sag": (2 * len(waters) * bays if cfg["sag"] and cfg["roof"] else 0),
        "girt_lines": 2 * len(hs), "girts": 2 * len(hs) * bays,
        "brace_roof": (2 * len(waters) * end_bays if cfg["brace_roof"] else 0),
        "brace_walls": (2 * end_bays if cfg["brace_walls"] and walls else 0),
        "spacing_real": (run / (len(st) - 1)) if len(st) > 1 else 0.0,
    }
    out["kg_purlins"] = out["purlins"] * bay * c_kg_per_m(cfg["purlin"])
    out["kg_girts"] = out["girts"] * bay * c_kg_per_m(cfg["girt"])
    out["kg_sag"] = out["sag"] * run * rod_kg_per_m(cfg["sag_rod"])
    panel = math.hypot(bay, water / 2.0)
    wall = math.hypot(bay, eave - 0.20)
    out["kg_brace"] = (out["brace_roof"] * 2 * panel + out["brace_walls"] * 2 * wall) * rod_kg_per_m(cfg["brace_rod"])
    out["kg_total"] = out["kg_purlins"] + out["kg_girts"] + out["kg_sag"] + out["kg_brace"]
    return out
