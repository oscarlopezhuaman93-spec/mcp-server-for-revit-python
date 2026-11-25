# -*- coding: utf-8 -*-
"""BOKI Metalicas - metrado del acero estructural generado (reglas PROPIAS:
no importa los otros modulos metal_*; lee las piezas marcadas "BOKI ..." en
Comentarios).

Por grupo (columnas, vigas, cordones, correas, planchas, pernos...) y
perfil: cantidad, longitud (m), peso (kg = volumen real del solido x 7850) y
area de pintura (m2 = superficie del solido, sin las caras de los extremos
en barras). Los pernos no se pintan (galvanizados)."""
import codecs
import math

FT = 0.3048
STEEL = 7850.0
PREFIXES = (u"BOKI Portico", u"BOKI Cercha", u"BOKI Correas", u"BOKI Conexion")

GROUPS = [
    (u"BOKI Portico", u"columna", u"Columnas"), (u"BOKI Portico", u"viga", u"Vigas"),
    (u"BOKI Portico", u"amarre", u"Vigas de amarre"), (u"BOKI Portico", u"cartela", u"Cartelas"),
    (u"BOKI Portico", u"placa", u"Placas base"),
    (u"BOKI Cercha", u"cordon_sup", u"Cordones superiores"), (u"BOKI Cercha", u"cordon_inf", u"Cordones inferiores"),
    (u"BOKI Cercha", u"montante", u"Montantes"), (u"BOKI Cercha", u"diagonal", u"Diagonales"),
    (u"BOKI Cercha", u"travesano", u"Travesaños"),
    (u"BOKI Correas", u"correa", u"Correas"), (u"BOKI Correas", u"larguero", u"Largueros"),
    (u"BOKI Correas", u"templador", u"Templadores"), (u"BOKI Correas", u"arriostre", u"Arriostres"),
    (u"BOKI Conexion", u"plancha", u"Planchas de conexión"), (u"BOKI Conexion", u"placa", u"Placas de apoyo"),
    (u"BOKI Conexion", u"cartelas", u"Cartelas de apoyo"), (u"BOKI Conexion", u"pernos", u"Pernos A325"),
    (u"BOKI Conexion", u"anclajes", u"Pernos de anclaje"), (u"BOKI Conexion", u"pasador", u"Pasadores"),
]
NO_PAINT = (u"Pernos A325", u"Pernos de anclaje", u"Pasadores")


def _comment(DB, e):
    try:
        p = e.get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
    except Exception:
        return u""
    return (p.AsString() or u"") if p is not None else u""


def _solids(DB, e):
    out = []
    geo = e.get_Geometry(DB.Options())
    for g in geo or []:
        if isinstance(g, DB.Solid) and g.Volume > 1e-9:
            out.append(g)
        elif isinstance(g, DB.GeometryInstance):
            out += [s for s in g.GetInstanceGeometry() if isinstance(s, DB.Solid) and s.Volume > 1e-9]
    return out


def _group(prefix, kind):
    for p, k, label in GROUPS:
        if p == prefix and kind.startswith(k):
            return label
    return u"Otros"


def measure(doc, DB):
    """[{grupo, perfil, cant, largo, kg, m2}] ordenado, y totales."""
    from Autodesk.Revit.DB import Element
    rows = {}
    for e in DB.FilteredElementCollector(doc).WhereElementIsNotElementType():
        text = _comment(DB, e)
        prefix = next((p for p in PREFIXES if text.startswith(p + u" ")), None)
        if prefix is None:
            continue
        kind = text[len(prefix) + 1:].split(u" ")[0]
        group = _group(prefix, kind)
        if isinstance(e, DB.DirectShape):
            profile = u"-"
        else:
            try:
                profile = Element.Name.GetValue(doc.GetElement(e.GetTypeId()))
            except Exception:
                profile = u"-"
        length = 0.0
        loc = getattr(e, "Location", None)
        if isinstance(loc, DB.LocationCurve):
            length = loc.Curve.Length * FT
        elif isinstance(loc, DB.LocationPoint) and not isinstance(e, DB.DirectShape):
            bb = e.get_BoundingBox(None)  # columna: su altura
            length = (bb.Max.Z - bb.Min.Z) * FT if bb is not None else 0.0
        solids = _solids(DB, e)
        vol = sum(s.Volume for s in solids) * FT ** 3
        area = 0.0
        if group not in NO_PAINT:
            for s in solids:
                for f in s.Faces:
                    area += f.Area
            area *= FT ** 2
            if length > 0 and solids:  # sin las dos caras de los extremos de la barra
                ends = sorted((f.Area for s in solids for f in s.Faces), reverse=False)[:2]
                area -= sum(ends) * FT ** 2
        key = (group, profile)
        r = rows.setdefault(key, {"grupo": group, "perfil": profile, "cant": 0, "largo": 0.0, "kg": 0.0, "m2": 0.0})
        r["cant"] += 1
        r["largo"] += length
        r["kg"] += vol * STEEL
        r["m2"] += max(area, 0.0)
    order = [g[2] for g in GROUPS] + [u"Otros"]
    out = sorted(rows.values(), key=lambda r: (order.index(r["grupo"]), r["perfil"]))
    total = {"cant": sum(r["cant"] for r in out), "largo": sum(r["largo"] for r in out),
             "kg": sum(r["kg"] for r in out), "m2": sum(r["m2"] for r in out)}
    return out, total


def to_csv(path, rows, total):
    with codecs.open(path, "w", "utf-8-sig") as f:
        f.write(u"Grupo;Perfil;Cantidad;Longitud (m);Peso (kg);Pintura (m2)\n")
        for r in rows:
            f.write(u"{};{};{};{:.2f};{:.1f};{:.2f}\n".format(r["grupo"], r["perfil"], r["cant"], r["largo"], r["kg"], r["m2"]))
        f.write(u"TOTAL;;{};{:.2f};{:.1f};{:.2f}\n".format(total["cant"], total["largo"], total["kg"], total["m2"]))
    return path
