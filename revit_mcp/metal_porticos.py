# -*- coding: utf-8 -*-
"""BOKI Metalicas - porticos de alma llena en Revit (reglas propias: solo
usa metal_spec; nada de los botones de acero de refuerzo).

Desde los ejes elegidos: los ejes transversales son los porticos (1, 2, ...)
y los dos longitudinales (A, B) las lineas de columnas. En cada cruce va una
columna W (base articulada, de la base al alero); en cada eje transversal
dos vigas W inclinadas del alero a la cumbrera; a lo largo de A y B, vigas
de amarre en el alero entre porticos; cartelas (alma + ala) bajo la viga en
aleros y cumbrera, y placa base en cada columna. Todo queda marcado en
Comentarios con metal_spec.TAG y la configuracion (JSON) para editarlo.
"""
import math
import os

import metal_spec as ms

from Autodesk.Revit import DB
from Autodesk.Revit.DB.Structure import StructuralType
from System.Collections.Generic import List

FT = 0.3048  # m por pie
LIBRARIES = [r"C:\ProgramData\Autodesk\RVT 2025\Libraries\Spanish",
             r"C:\ProgramData\Autodesk\RVT 2024\Libraries\Spanish",
             r"C:\ProgramData\Autodesk\RVT 2026\Libraries\Spanish"]
FAMILY_FILES = {
    ms.FRAME_FAMILY: os.path.join(u"Armaz\u00f3n estructural", u"Acero", u"W-Ala ancha.rfa"),
    ms.COLUMN_FAMILY: os.path.join(u"Pilares estructurales", u"Acero", u"W-Ala ancha-Pilar.rfa"),
}


def _m(ft):
    return ft * FT


def _ft(m):
    return m / FT


# --- ejes --------------------------------------------------------------------
class Layout(object):
    """Porticos leidos de los ejes: `frames` [(nombre, punto A, punto B)] en
    orden a lo largo de la nave (puntos en pies, z = 0), `span` y
    `spacings` en m, `lines` los nombres de los ejes A y B."""

    def __init__(self, grids, typed_span=None):
        lines = [g for g in grids if isinstance(g.Curve, DB.Line)]
        if len(lines) < 4:
            raise ms.SpecError(u"elige al menos 2 ejes de portico y 2 ejes de columnas (rectos)")
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
            raise ms.SpecError(u"los ejes deben ir en dos direcciones perpendiculares")
        a, b = groups
        if len(a) == 2 and len(b) == 2 and typed_span:
            # dos y dos: los longitudinales son el par separado por la luz escrita
            if abs(_m(self._gap(b[0], b[1])) - typed_span) < abs(_m(self._gap(a[0], a[1])) - typed_span):
                a, b = b, a
        elif len(a) > len(b):
            a, b = b, a
        if len(a) != 2:
            raise ms.SpecError(u"elige exactamente 2 ejes de columnas (A y B)")
        longs, frames = a, b
        ux = longs[0].Curve.Direction  # a lo largo de la nave
        o = longs[0].Curve.GetEndPoint(0)
        frames = sorted(frames, key=lambda g: (g.Curve.GetEndPoint(0) - o).DotProduct(ux))
        self.frames = []
        for g in frames:
            pa = _cross(g.Curve, longs[0].Curve)
            pb = _cross(g.Curve, longs[1].Curve)
            if pa is None or pb is None:
                raise ms.SpecError(u"el eje {} no cruza los ejes de columnas".format(g.Name))
            self.frames.append((g.Name, pa, pb))
        self.lines = (longs[0].Name, longs[1].Name)
        self.span = _m(self.frames[0][1].DistanceTo(self.frames[0][2]))
        self.spacings = [_m(self.frames[i][1].DistanceTo(self.frames[i + 1][1]))
                         for i in range(len(self.frames) - 1)]

    @staticmethod
    def _gap(g1, g2):
        p = g2.Curve.GetEndPoint(0)
        q = g1.Curve.Project(p)
        return p.DistanceTo(q.XYZPoint) if q is not None else 0.0

    def text(self):
        sp = sorted(set(round(s, 2) for s in self.spacings))
        return u"{} pórticos (ejes {} a {}), columnas en {} y {}, luz {:.2f} m, separación {}".format(
            len(self.frames), self.frames[0][0], self.frames[-1][0], self.lines[0], self.lines[1],
            self.span, u" / ".join(u"{:.2f} m".format(s) for s in sp) if sp else u"-")


def _cross(c1, c2):
    """Cruce en planta (z = 0) de dos lineas de eje, extendidas."""
    p, r = c1.GetEndPoint(0), c1.Direction
    q, s = c2.GetEndPoint(0), c2.Direction
    den = r.X * s.Y - r.Y * s.X
    if abs(den) < 1e-9:
        return None
    t = ((q.X - p.X) * s.Y - (q.Y - p.Y) * s.X) / den
    return DB.XYZ(p.X + r.X * t, p.Y + r.Y * t, 0.0)


# --- familias ------------------------------------------------------------------
def symbol(doc, family, type_name):
    """El tipo `type_name` (nombre metrico) de la familia W; cargado de la
    biblioteca de Revit si falta (dentro de una Transaction)."""
    for s in DB.FilteredElementCollector(doc).OfClass(DB.FamilySymbol):
        if s.Family.Name == family and DB.Element.Name.GetValue(s) == type_name:
            break
    else:
        s = None
    if s is None:
        path = None
        for lib in LIBRARIES:
            p = os.path.join(lib, FAMILY_FILES[family])
            if os.path.isfile(p):
                path = p
                break
        if path is None:
            raise ms.SpecError(u"no se encontro la familia {} en la biblioteca de Revit".format(family))
        ref = DB.FamilySymbol
        try:
            import clr
            ref = clr.Reference[DB.FamilySymbol]()
        except Exception:
            pass
        if not doc.LoadFamilySymbol(path, type_name, ref):
            raise ms.SpecError(u"no se pudo cargar {} {}".format(family, type_name))
        s = ref.Value
    if not s.IsActive:
        s.Activate()
        doc.Regenerate()
    return s


def steel_material(doc):
    for m in DB.FilteredElementCollector(doc).OfClass(DB.Material):
        n = (m.Name or u"").lower()
        if "a572" in n or ("acero" in n and "estructural" in n) or "steel" in n:
            return m.Id
    return DB.ElementId.InvalidElementId


# --- generacion -------------------------------------------------------------
def _tag(element, cfg, kind):
    p = element.get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
    if p is not None and not p.IsReadOnly:
        p.Set(u"{} {} {}".format(ms.TAG, kind, ms.dumps(cfg)))


def existing(doc):
    """Elementos creados por Porticos (los que llevan la marca)."""
    out = []
    for e in DB.FilteredElementCollector(doc).WhereElementIsNotElementType():
        try:
            p = e.get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
        except Exception:
            continue
        if p is not None and (p.AsString() or u"").startswith(ms.TAG + u" "):
            out.append(e)
    return out


def saved_config(doc):
    """La configuracion guardada en lo ya generado (None si no hay)."""
    for e in existing(doc):
        text = e.get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS).AsString()
        j = text.find(u"{")
        if j >= 0:
            cfg = ms.loads(text[j:])
            if cfg is not None:
                return cfg
    return None


def _frame_xyz(pa, pb, base_z, u, z):
    """Punto del portico (u a lo largo de la luz desde A, z sobre la base) en
    coordenadas del modelo (pies)."""
    d = (pb - pa).Normalize()
    return DB.XYZ(pa.X + d.X * _ft(u), pa.Y + d.Y * _ft(u), base_z + _ft(z))


def _extrude(loop_pts, normal, depth):
    """Prisma: el poligono (XYZ) extruido `depth` pies segun `normal`,
    centrado en su plano."""
    off = normal.Multiply(-depth / 2.0)
    pts = [p + off for p in loop_pts]
    loop = DB.CurveLoop()
    for i in range(len(pts)):
        loop.Append(DB.Line.CreateBound(pts[i], pts[(i + 1) % len(pts)]))
    return DB.GeometryCreationUtilities.CreateExtrusionGeometry(List[DB.CurveLoop]([loop]), normal, depth)


def _shape(doc, solids, category, cfg, kind, material):
    ds = DB.DirectShape.CreateElement(doc, DB.ElementId(category))
    geo = List[DB.GeometryObject]()
    for s in solids:
        geo.Add(s)
    ds.SetShape(geo)
    ds.Name = u"BOKI {}".format(kind)
    if material != DB.ElementId.InvalidElementId:
        p = ds.get_Parameter(DB.BuiltInParameter.STRUCTURAL_MATERIAL_PARAM)
        if p is not None and not p.IsReadOnly:
            p.Set(material)
    _tag(ds, cfg, kind)
    return ds


def _plate_category(doc):
    for bic in (DB.BuiltInCategory.OST_StructConnectionPlates, DB.BuiltInCategory.OST_StructConnections,
                DB.BuiltInCategory.OST_GenericModel):
        try:
            cid = DB.ElementId(bic)
            if DB.DirectShape.IsValidCategoryId(cid, doc):
                return int(bic)
        except Exception:
            continue
    return int(DB.BuiltInCategory.OST_GenericModel)


def delete_existing(doc):
    ids = List[DB.ElementId]([e.Id for e in existing(doc)])
    if ids.Count:
        doc.Delete(ids)
    return ids.Count


def generate(doc, layout, level, cfg):
    """Crea los porticos (dentro de una Transaction). Borra antes lo que
    Porticos genero. Devuelve {clase: [elementos]}."""
    cfg = ms.config(cfg)
    delete_existing(doc)
    col_t = symbol(doc, ms.COLUMN_FAMILY, ms.profile(cfg["column"])[1])
    raf_t = symbol(doc, ms.FRAME_FAMILY, ms.profile(cfg["rafter"])[1])
    tie_t = symbol(doc, ms.FRAME_FAMILY, ms.profile(cfg["tie"])[1])
    steel = steel_material(doc)
    base_z = level.Elevation
    made = {"columns": [], "rafters": [], "ties": [], "haunches": [], "plates": []}
    pts = ms.frame_points(cfg)
    r_tw = ms.profile(cfg["rafter"])[4] / 1000.0
    r_bf, r_tf = ms.profile(cfg["rafter"])[3] / 1000.0, ms.profile(cfg["rafter"])[5] / 1000.0
    pa_, pb_, pt_ = [x / 1000.0 for x in cfg["plate"]]
    plate_cat = _plate_category(doc)
    for name, pa, pb in layout.frames:
        span_dir = (pb - pa).Normalize()
        normal = DB.XYZ.BasisZ.CrossProduct(span_dir).Normalize()  # normal al plano del portico
        # columnas: alma en el plano del portico (eje fuerte resiste el portico)
        for key in ("col_left", "col_right"):
            (u, _), _top = pts[key]
            p = _frame_xyz(pa, pb, base_z, u, 0.0)
            col = doc.Create.NewFamilyInstance(p, col_t, level, StructuralType.Column)
            top = col.get_Parameter(DB.BuiltInParameter.FAMILY_TOP_LEVEL_PARAM)
            if top is not None:
                top.Set(level.Id)
            off = col.get_Parameter(DB.BuiltInParameter.FAMILY_TOP_LEVEL_OFFSET_PARAM)
            if off is not None:
                off.Set(_ft(cfg["eave"]))
            angle = math.atan2(span_dir.Y, span_dir.X) - math.pi / 2.0
            axis = DB.Line.CreateBound(p, p + DB.XYZ.BasisZ)
            DB.ElementTransformUtils.RotateElement(doc, col.Id, axis, angle)
            _tag(col, cfg, u"columna " + name)
            made["columns"].append(col)
            # placa base (bajo la columna, sobre el grout)
            c = DB.XYZ(p.X, p.Y, base_z)
            e1, e2 = span_dir.Multiply(_ft(pa_) / 2.0), normal.Multiply(_ft(pb_) / 2.0)
            loop = [c - e1 - e2, c + e1 - e2, c + e1 + e2, c - e1 + e2]
            solid = _extrude([q + DB.XYZ(0, 0, _ft(pt_) / 2.0) for q in loop], DB.XYZ.BasisZ, _ft(pt_))
            made["plates"].append(_shape(doc, [solid], plate_cat, cfg, u"placa base " + name, steel))
        # vigas inclinadas: cara superior en la linea alero-cumbrera
        for key in ("rafter_left", "rafter_right"):
            (u0, z0), (u1, z1) = pts[key]
            line = DB.Line.CreateBound(_frame_xyz(pa, pb, base_z, u0, z0), _frame_xyz(pa, pb, base_z, u1, z1))
            beam = doc.Create.NewFamilyInstance(line, raf_t, level, StructuralType.Beam)
            zj = beam.get_Parameter(DB.BuiltInParameter.Z_JUSTIFICATION)
            if zj is not None and not zj.IsReadOnly:
                zj.Set(0)  # superior
            _tag(beam, cfg, u"viga " + name)
            made["rafters"].append(beam)
        # cartelas: alma (triangulo, espesor tw) + ala inferior (bf x tf) en la hipotenusa
        for tri, edges in zip(ms.haunch_triangles(cfg), ms.haunch_flanges(cfg)):
            xyz = [_frame_xyz(pa, pb, base_z, u, z) for u, z in tri]
            solids = [_extrude(xyz, normal, _ft(r_tw))]
            for (ua, za), (ub, zb) in edges:
                a, b = _frame_xyz(pa, pb, base_z, ua, za), _frame_xyz(pa, pb, base_z, ub, zb)
                along = (b - a).Normalize()
                side = normal.Multiply(_ft(r_bf) / 2.0)
                down = along.CrossProduct(normal).Normalize()
                if down.Z > 0:
                    down = down.Negate()
                th = down.Multiply(_ft(r_tf))
                rect = [a - side, a + side, a + side + th, a - side + th]
                solids.append(DB.GeometryCreationUtilities.CreateExtrusionGeometry(
                    List[DB.CurveLoop]([_loop(rect)]), along, a.DistanceTo(b)))
            made["haunches"].append(_shape(doc, solids, int(DB.BuiltInCategory.OST_StructuralFraming),
                                           cfg, u"cartela " + name, steel))
    # vigas de amarre en el alero, a lo largo de A y B
    for i in range(len(layout.frames) - 1):
        for k in (1, 2):
            p = layout.frames[i][k]
            q = layout.frames[i + 1][k]
            line = DB.Line.CreateBound(DB.XYZ(p.X, p.Y, base_z + _ft(cfg["eave"])),
                                       DB.XYZ(q.X, q.Y, base_z + _ft(cfg["eave"])))
            tie = doc.Create.NewFamilyInstance(line, tie_t, level, StructuralType.Beam)
            _tag(tie, cfg, u"amarre {}-{}".format(layout.frames[i][0], layout.frames[i + 1][0]))
            made["ties"].append(tie)
    return made


def _loop(pts):
    loop = DB.CurveLoop()
    for i in range(len(pts)):
        loop.Append(DB.Line.CreateBound(pts[i], pts[(i + 1) % len(pts)]))
    return loop
