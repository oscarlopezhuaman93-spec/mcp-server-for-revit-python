# -*- coding: utf-8 -*-
"""BOKI Metalicas - conexiones de los porticos y cerchas BOKI ya generados.

Reglas PROPIAS (2026-11-25): no importa metal_spec, metal_porticos,
metal_cerchas ni metal_correas; lee las piezas del modelo por su marca en
Comentarios ("BOKI Portico ...", "BOKI Cercha ...") y las medidas de los
perfiles de sus tipos de Revit.

Porticos (alma llena, base articulada):
- Alero: plancha de extremo empernada a la cara de la columna (8 pernos A325
  3/4" en 2 filas x 4) que cubre viga + cartela.
- Cumbrera: par de planchas de extremo empernadas (6 pernos).
- Base: 4 pernos de anclaje F1554 3/4" (L = 0.60 m) en la placa base.
Cerchas (apoyo articulado con pasador, como la cancha de Chumbivilcas):
- Placa de apoyo anclada sobre la columna (4 pernos 3/4"), cartelas
  verticales en cada plano de la cercha y pasador horizontal.
Todo como piezas de acero (DirectShape) con su peso, marcadas
"BOKI Conexion ..." para editar o borrar.
"""
import json
import math

FT = 0.3048
TAG = u"BOKI Conexion"
PORTICO_TAG = u"BOKI Portico"
CERCHA_TAG = u"BOKI Cercha"
STEEL = 7850.0

DEFAULTS = {
    "eave": True, "ridge": True, "anchors": True, "truss_supports": True,
    "end_plate": 25.0,  # mm, alero
    "ridge_plate": 20.0,  # mm
    "bolt": 19.05,  # mm (3/4") A325
    "anchor": 19.05,  # mm (3/4") F1554
    "anchor_len": 0.60,  # m
    "support_plate": [400.0, 600.0, 20.0],  # mm (a lo largo de la luz, ancho, espesor) - cerchas
    "gusset": 12.0,  # mm, cartelas del apoyo de cercha
    "pin": 50.0,  # mm, pasador
}


class SpecError(Exception):
    pass


def config(data=None):
    cfg = dict(DEFAULTS)
    cfg.update(dict((k, v) for k, v in (data or {}).items() if v is not None))
    for k in ("end_plate", "ridge_plate", "bolt", "anchor", "anchor_len", "gusset", "pin"):
        cfg[k] = float(cfg[k])
    cfg["support_plate"] = [float(x) for x in cfg["support_plate"]]
    if not 8.0 <= cfg["end_plate"] <= 50.0:
        raise SpecError(u"espesor de plancha de extremo fuera de rango (8 a 50 mm)")
    return cfg


def _ft(m):
    return m / FT


def _comment(DB, e):
    try:
        p = e.get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
    except Exception:
        return u""
    return (p.AsString() or u"") if p is not None else u""


def _type_mm(doc, e, name, default):
    """Medida (mm) del tipo del elemento (familias W: d, bf, tf, tw)."""
    t = doc.GetElement(e.GetTypeId())
    p = t.LookupParameter(name) if t is not None else None
    return p.AsDouble() * 304.8 if p is not None else default


def read_model(doc, DB):
    """{"porticos": {eje: {"cols": [...], "rafters": [...]}},
        "cerchas": {eje: {"inf": [curvas], "sup": [curvas]}}}"""
    por, cer = {}, {}
    for e in DB.FilteredElementCollector(doc).WhereElementIsNotElementType():
        text = _comment(DB, e)
        if text.startswith(PORTICO_TAG + u" "):
            w = text[len(PORTICO_TAG) + 1:].split(u" ")
            if len(w) >= 2 and w[0] in (u"columna", u"viga"):
                por.setdefault(w[1], {"cols": [], "rafters": []})["cols" if w[0] == u"columna" else "rafters"].append(e)
        elif text.startswith(CERCHA_TAG + u" cordon_"):
            w = text[len(CERCHA_TAG) + 1:].split(u" ")
            key = w[0][len(u"cordon_"):]
            if isinstance(getattr(e, "Location", None), DB.LocationCurve):
                cer.setdefault(w[1], {"inf": [], "sup": []})[key].append(e.Location.Curve)
    por = dict((k, v) for k, v in por.items() if len(v["cols"]) == 2 and len(v["rafters"]) == 2)
    return {"porticos": por, "cerchas": cer}


# --- solidos -----------------------------------------------------------------
def _loop(DB, pts):
    loop = DB.CurveLoop()
    for i in range(len(pts)):
        loop.Append(DB.Line.CreateBound(pts[i], pts[(i + 1) % len(pts)]))
    return loop


def plate(DB, center, u, v, w_u, w_v, thick, n):
    """Plancha: rectangulo w_u x w_v (pies) en el plano (u, v) centrado en
    `center`, espesor `thick` (pies) hacia `n`."""
    from System.Collections.Generic import List
    a, b = u * (w_u / 2.0), v * (w_v / 2.0)
    pts = [center - a - b, center + a - b, center + a + b, center - a + b]
    return DB.GeometryCreationUtilities.CreateExtrusionGeometry(List[DB.CurveLoop]([_loop(DB, pts)]), n, thick)


def polygon_plate(DB, pts, n, thick):
    """Plancha poligonal (puntos en un plano) de espesor `thick` centrada en
    ese plano, normal `n`."""
    from System.Collections.Generic import List
    off = n * (-thick / 2.0)
    return DB.GeometryCreationUtilities.CreateExtrusionGeometry(
        List[DB.CurveLoop]([_loop(DB, [p + off for p in pts])]), n, thick)


def cylinder(DB, base, axis, radius, length):
    """Cilindro (perno, pasador) desde `base` a lo largo de `axis`."""
    from System.Collections.Generic import List
    axis = axis.Normalize()
    x = axis.CrossProduct(DB.XYZ.BasisZ)
    if x.GetLength() < 1e-6:
        x = DB.XYZ.BasisX
    x = x.Normalize()
    y = axis.CrossProduct(x).Normalize()
    frame = DB.Frame(base, x, y, axis)
    loop = DB.CurveLoop()
    loop.Append(DB.Arc.Create(DB.Plane.CreateByOriginAndBasis(base, x, y), radius, 0.0, math.pi))
    loop.Append(DB.Arc.Create(DB.Plane.CreateByOriginAndBasis(base, x, y), radius, math.pi, 2 * math.pi))
    return DB.GeometryCreationUtilities.CreateExtrusionGeometry(List[DB.CurveLoop]([loop]), axis, length)


def hex_prism(DB, base, axis, across_flats, length):
    """Prisma hexagonal (cabeza o tuerca) desde `base` a lo largo de `axis`."""
    from System.Collections.Generic import List
    axis = axis.Normalize()
    x = axis.CrossProduct(DB.XYZ.BasisZ)
    if x.GetLength() < 1e-6:
        x = DB.XYZ.BasisX
    x = x.Normalize()
    y = axis.CrossProduct(x).Normalize()
    r = across_flats / math.sqrt(3.0)  # radio a las esquinas
    pts = [base + x * (r * math.cos(math.pi / 3 * k)) + y * (r * math.sin(math.pi / 3 * k)) for k in range(6)]
    return DB.GeometryCreationUtilities.CreateExtrusionGeometry(List[DB.CurveLoop]([_loop(DB, pts)]), axis, length)


def structural_bolt(DB, head_face, axis, d, grip):
    """Perno estructural A325 como se instala: cabeza hexagonal apoyada en
    `head_face` (lado opuesto a `axis`), vastago a traves del paquete de
    planchas (`grip`), arandela, tuerca hexagonal y 2 hilos fuera.
    Medidas (ASTM A325 / A563): cabeza 1.5d entre caras x 0.625d, tuerca
    1.5d x 0.875d, arandela 2d x 4 mm. Todo en pies."""
    axis = axis.Normalize()
    w = _ft(0.004)
    head = hex_prism(DB, head_face - axis * (0.625 * d), axis, 1.5 * d, 0.625 * d)
    shank = cylinder(DB, head_face, axis, d / 2.0, grip + w + 0.875 * d + 0.3 * d)
    washer = cylinder(DB, head_face + axis * grip, axis, d, w)
    nut = hex_prism(DB, head_face + axis * (grip + w), axis, 1.5 * d, 0.875 * d)
    return [head, shank, washer, nut]


def anchor_bolt(DB, plate_top, d, length, hook_dir):
    """Perno de anclaje F1554 en L: vastago vertical embebido `length`
    bajo la placa con gancho horizontal de 4d al fondo, y arriba arandela
    plana (2.5d x 6 mm) y tuerca hexagonal sobre la placa, con el
    vastago saliendo 3 hilos (0.5d) sobre la tuerca. Pies."""
    up = DB.XYZ.BasisZ
    w = _ft(0.006)
    bottom = plate_top - up * length
    parts = [cylinder(DB, bottom, up, d / 2.0, length + w + 0.875 * d + 0.5 * d),
             cylinder(DB, bottom + up * (d / 2.0), hook_dir, d / 2.0, 4 * d),  # gancho
             cylinder(DB, plate_top, up, 1.25 * d, w),
             hex_prism(DB, plate_top + up * w, up, 1.5 * d, 0.875 * d)]
    return parts


def _category(doc, DB, bics):
    for bic in bics:
        try:
            cid = DB.ElementId(bic)
            if DB.DirectShape.IsValidCategoryId(cid, doc):
                return cid
        except Exception:
            continue
    return DB.ElementId(DB.BuiltInCategory.OST_GenericModel)


def _shape(doc, DB, solids, cat, cfg, kind):
    from System.Collections.Generic import List
    ds = DB.DirectShape.CreateElement(doc, cat)
    geo = List[DB.GeometryObject]()
    for s in solids:
        geo.Add(s)
    ds.SetShape(geo)
    ds.Name = u"BOKI {}".format(kind.split(u" ")[0])
    p = ds.get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
    if p is not None and not p.IsReadOnly:
        p.Set(u"{} {} {}".format(TAG, kind, json.dumps(cfg, sort_keys=True)))
    return ds


def existing(doc, DB):
    return [e for e in DB.FilteredElementCollector(doc).WhereElementIsNotElementType()
            if _comment(DB, e).startswith(TAG + u" ")]


def saved_config(doc, DB):
    for e in existing(doc, DB):
        text = _comment(DB, e)
        j = text.find(u"{")
        if j >= 0:
            try:
                return config(json.loads(text[j:]))
            except (ValueError, SpecError):
                pass
    return None


def kg(solids):
    return sum(s.Volume for s in solids) * FT ** 3 * STEEL


# --- porticos ---------------------------------------------------------------
def _portico_plate_mm(DB, column):
    """Espesor de la placa base del portico (de la configuracion guardada
    en la marca de la columna; 25 mm si no se lee)."""
    text = _comment(DB, column)
    j = text.find(u"{")
    try:
        return float(json.loads(text[j:])["plate"][2]) if j >= 0 else 25.0
    except (ValueError, KeyError, IndexError, TypeError):
        return 25.0


def _portico_connections(doc, DB, name, data, cfg, cat_plate, cat_bolt, made):
    cols = data["cols"]
    rafters = data["rafters"]
    pa, pb = [c.Location.Point for c in cols]
    span = (pb - pa)
    d = DB.XYZ(span.X, span.Y, 0).Normalize()
    n_frame = DB.XYZ.BasisZ.CrossProduct(d).Normalize()  # normal al plano del portico
    col_d = _type_mm(doc, cols[0], u"d", 351.0) / 304.8
    raf = rafters[0]
    raf_d = _type_mm(doc, raf, u"d", 404.0) / 304.8
    raf_bf = _type_mm(doc, raf, u"bf", 140.0) / 304.8
    bolt_r = cfg["bolt"] / 2.0 / 304.8
    for foot in (pa, pb):
        inward = d if foot is pa else d.Negate()
        c = min(rafters, key=lambda r: min(r.Location.Curve.GetEndPoint(k).DistanceTo(foot) for k in (0, 1)))
        crv = c.Location.Curve
        start = min((crv.GetEndPoint(0), crv.GetEndPoint(1)), key=lambda q: DB.XYZ(q.X - foot.X, q.Y - foot.Y, 0).GetLength())
        tan = (crv.GetEndPoint(1) - crv.GetEndPoint(0)).Normalize()
        if tan.DotProduct(inward) < 0:
            tan = tan.Negate()
        cos = abs(DB.XYZ(tan.X, tan.Y, 0).GetLength())
        if cfg["eave"]:
            # plancha de extremo: plano vertical en la cara de la columna; alto = viga + cartela
            h_top = _ft(0.05)
            h_bot = raf_d / cos + raf_d + _ft(0.05)
            hgt = h_top + h_bot
            cz = start + DB.XYZ.BasisZ * ((h_top - h_bot) / 2.0)
            face = DB.XYZ(cz.X, cz.Y, cz.Z)  # en la cara de la columna
            t = cfg["end_plate"] / 304.8
            pl = plate(DB, face, n_frame, DB.XYZ.BasisZ, raf_bf + _ft(0.04), hgt, t, inward)
            made["plates"].append(_shape(doc, DB, [pl], cat_plate, cfg, u"plancha_alero " + name))
            # 8 pernos A325: 2 columnas (gramil 100 mm) x 4 filas; cabeza contra
            # la cara interior del ala de la columna, tuerca sobre la plancha
            bolts = []
            g = _ft(0.05)
            tf_col = _type_mm(doc, cols[0], u"tf", 15.1) / 304.8
            for k in range(4):
                z = h_top - _ft(0.10) - (hgt - _ft(0.20)) * k / 3.0
                for s in (-1, 1):
                    face = start + DB.XYZ.BasisZ * z + n_frame * (s * g) - inward * tf_col
                    bolts += structural_bolt(DB, face, inward, 2 * bolt_r, tf_col + t)
            made["bolts"].append(_shape(doc, DB, bolts, cat_bolt, cfg, u"pernos_alero " + name))
        if cfg["anchors"]:
            # pernos de anclaje en L en la placa base (4, a 60 mm de los bordes de 400 x 400)
            anchors = []
            e = _ft(0.14)
            pt = _ft(_portico_plate_mm(DB, cols[0]) / 1000.0)
            for s1 in (-1, 1):
                for s2 in (-1, 1):
                    top = DB.XYZ(foot.X, foot.Y, foot.Z) + d * (s1 * e) + n_frame * (s2 * e) + DB.XYZ.BasisZ * pt
                    hook = (d * (-s1) + n_frame * (-s2)).Normalize()  # gancho hacia el centro del pedestal
                    anchors += anchor_bolt(DB, top, cfg["anchor"] / 304.8, _ft(cfg["anchor_len"]), hook)
            made["anchors"].append(_shape(doc, DB, anchors, cat_bolt, cfg, u"anclajes " + name))
    if cfg["ridge"]:
        ends = [r.Location.Curve.GetEndPoint(k) for r in rafters for k in (0, 1)]
        apex = max(ends, key=lambda q: q.Z)
        t = cfg["ridge_plate"] / 304.8
        hgt = raf_d * 1.6 + _ft(0.10)
        cz = apex + DB.XYZ.BasisZ * (_ft(0.05) - hgt / 2.0)
        plates = [plate(DB, cz - d * t, n_frame, DB.XYZ.BasisZ, raf_bf + _ft(0.04), hgt, t, d),
                  plate(DB, cz, n_frame, DB.XYZ.BasisZ, raf_bf + _ft(0.04), hgt, t, d)]
        made["plates"].append(_shape(doc, DB, plates, cat_plate, cfg, u"plancha_cumbrera " + name))
        bolts = []
        for k in range(3):
            z = _ft(0.05) - _ft(0.10) - (hgt - _ft(0.20)) * k / 2.0
            for s in (-1, 1):
                # cabeza contra una plancha, tuerca contra la otra (paquete 2 t)
                face = apex + DB.XYZ.BasisZ * z + n_frame * (s * _ft(0.05)) - d * t
                bolts += structural_bolt(DB, face, d, 2 * bolt_r, 2 * t)
        made["bolts"].append(_shape(doc, DB, bolts, cat_bolt, cfg, u"pernos_cumbrera " + name))


# --- cerchas ----------------------------------------------------------------
def _truss_supports(doc, DB, name, data, cfg, cat_plate, cat_bolt, made):
    infs, sups = data["inf"], data["sup"]
    if not infs or not sups:
        return
    ends = [c.GetEndPoint(k) for c in infs for k in (0, 1)]
    zmin = min(q.Z for q in ends)
    lows = [q for q in ends if abs(q.Z - zmin) < _ft(0.02)]
    # los dos apoyos: extremos bajos agrupados por lado
    a = lows[0]
    far = max(lows, key=lambda q: DB.XYZ(q.X - a.X, q.Y - a.Y, 0).GetLength())
    d = DB.XYZ(far.X - a.X, far.Y - a.Y, 0).Normalize()
    along = DB.XYZ.BasisZ.CrossProduct(d).Normalize()
    for side, foot in ((0, a), (1, far)):
        inward = d if side == 0 else d.Negate()
        pts = [q for q in lows if DB.XYZ(q.X - foot.X, q.Y - foot.Y, 0).GetLength() < _ft(1.0)]
        center = DB.XYZ(sum(q.X for q in pts) / len(pts), sum(q.Y for q in pts) / len(pts), zmin)
        offs = sorted(set(round((q - center).DotProduct(along) * FT, 3) for q in pts))
        top_end = min([c.GetEndPoint(k) for c in sups for k in (0, 1)],
                      key=lambda q: DB.XYZ(q.X - center.X, q.Y - center.Y, 0).GetLength())
        rise = top_end.Z - zmin
        back = (top_end - center).DotProduct(inward)  # el cordon superior puede salir por fuera (arco)
        lu, wv, th = [x / 304.8 for x in cfg["support_plate"]]
        width = max(wv, (offs[-1] - offs[0]) / FT + _ft(0.20))
        sp = plate(DB, center + DB.XYZ.BasisZ * 0 - DB.XYZ.BasisZ * th, inward, along, lu, width, th, DB.XYZ.BasisZ)
        made["plates"].append(_shape(doc, DB, [sp], cat_plate, cfg, u"placa_apoyo " + name))
        gussets = []
        u0 = min(back, 0.0) - _ft(0.10)
        u1 = _ft(0.30)
        for o in offs:
            base = center + along * _ft(o)
            quad = [base + inward * u0, base + inward * u1,
                    base + inward * u1 + DB.XYZ.BasisZ * _ft(0.20),
                    base + inward * u0 + DB.XYZ.BasisZ * (rise + _ft(0.08))]
            gussets.append(polygon_plate(DB, quad, along, cfg["gusset"] / 304.8))
        made["plates"].append(_shape(doc, DB, gussets, cat_plate, cfg, u"cartelas_apoyo " + name))
        pin_len = (offs[-1] - offs[0]) / FT + _ft(0.12)
        pin_c = center + inward * _ft(0.10) + DB.XYZ.BasisZ * _ft(0.10) + along * (_ft(offs[0]) - _ft(0.06))
        pr = cfg["pin"] / 2.0 / 304.8
        pin = [cylinder(DB, pin_c, along, pr, pin_len),
               # cabeza y arandela con tuerca en los extremos (pasador retenido)
               cylinder(DB, pin_c - along * _ft(0.010), along, pr * 1.6, _ft(0.010)),
               cylinder(DB, pin_c + along * pin_len, along, pr * 1.6, _ft(0.008)),
               hex_prism(DB, pin_c + along * (pin_len + _ft(0.008)), along, pr * 3.0, pr * 1.2)]
        made["bolts"].append(_shape(doc, DB, pin, cat_bolt, cfg, u"pasador " + name))
        if cfg["anchors"]:
            anchors = []
            for s1 in (-1, 1):
                for s2 in (-1, 1):
                    top = center + inward * (s1 * (lu / 2.0 - _ft(0.06))) + along * (s2 * (width / 2.0 - _ft(0.06)))
                    hook = (inward * (-s1) + along * (-s2)).Normalize()
                    anchors += anchor_bolt(DB, top, cfg["anchor"] / 304.8, _ft(cfg["anchor_len"]), hook)
            made["anchors"].append(_shape(doc, DB, anchors, cat_bolt, cfg, u"anclajes " + name))


def generate(doc, DB, cfg):
    """Crea las conexiones (en una Transaction). Borra antes lo que este
    boton genero. {clase: [elementos]}."""
    from System.Collections.Generic import List
    cfg = config(cfg)
    model = read_model(doc, DB)
    if not model["porticos"] and not model["cerchas"]:
        raise SpecError(u"no hay pórticos ni cerchas BOKI en el modelo")
    old = List[DB.ElementId]([e.Id for e in existing(doc, DB)])
    if old.Count:
        doc.Delete(old)
    cat_plate = _category(doc, DB, (DB.BuiltInCategory.OST_StructConnectionPlates, DB.BuiltInCategory.OST_StructConnections))
    cat_bolt = _category(doc, DB, (DB.BuiltInCategory.OST_StructConnectionBolts, DB.BuiltInCategory.OST_StructConnections))
    made = {"plates": [], "bolts": [], "anchors": []}
    for name, data in sorted(model["porticos"].items()):
        _portico_connections(doc, DB, name, data, cfg, cat_plate, cat_bolt, made)
    if cfg["truss_supports"]:
        for name, data in sorted(model["cerchas"].items()):
            _truss_supports(doc, DB, name, data, cfg, cat_plate, cat_bolt, made)
    return made


def weight(made):
    """kg por clase de lo creado (regenera antes si hace falta)."""
    from Autodesk.Revit.DB import Options
    out = {}
    for k, items in made.items():
        total = 0.0
        for e in items:
            geo = e.get_Geometry(Options())
            if geo is None:
                try:
                    e.Document.Regenerate()
                except Exception:
                    pass
                geo = e.get_Geometry(Options())
            for g in geo or []:
                if hasattr(g, "Volume"):
                    total += g.Volume * FT ** 3 * STEEL
        out[k] = total
    return out
