# -*- coding: utf-8 -*-
"""Revit side of "Acero Losa" (solid slabs): read a floor - its top face
and the supports under it (beams, walls, columns) in plan - and create
the bars that slab_rebar plans, as rebar sets hosted by the floor.

Local frame: rebar_foundation.Foundation (the model's x / y from the
center of the floor's box, z up from its bottom), meters."""
from pyrevit import DB
from System.Collections.Generic import List

import rebar_foundation as rf
import slab_rebar as sr

FT = 0.3048
SUPPORT_CATEGORIES = (
    (DB.BuiltInCategory.OST_StructuralFraming, u"VIGA"),
    (DB.BuiltInCategory.OST_Walls, u"MURO"),
    (DB.BuiltInCategory.OST_StructuralColumns, u"COLUMNA"),
)
PARAM = "EA_Losa_Acero"  # JSON settings on the floor (sr.default_settings)


def _flat(triangles):
    return [tuple((p[0], p[1]) for p in t) for t in triangles]


class SlabModel(object):
    """A floor read for its steel: slab_tris (top face in plan), supports
    [{"label", "tris"}] (their footprint just under the slab), thickness."""

    def __init__(self, floor):
        self.element = floor
        self.f = rf.Foundation(floor)
        x0, x1, y0, y1, z0, z1 = self.f.extent
        self.thickness = z1 - z0
        tops = [face for face in self.f.faces if face.normal[2] > 0.9]
        self.slab_tris = _flat([t for face in tops for t in face.triangles])
        self.supports = self._supports()
        self.support_tris = [t for s in self.supports for t in s["tris"]]

    def _supports(self, below=0.03, depth=0.02):
        """What holds the slab: the elements cut by a thin slice just under
        its bottom, inside its outline."""
        f = self.f
        x0, x1, y0, y1, z0, z1 = f.extent
        corners = [f.world(x, y, z0 - below - depth) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
        loop = DB.CurveLoop()
        for k in range(4):
            loop.Append(DB.Line.CreateBound(corners[k], corners[(k + 1) % 4]))
        slice_ = DB.GeometryCreationUtilities.CreateExtrusionGeometry(
            List[DB.CurveLoop]([loop]), DB.XYZ.BasisZ, depth / FT)
        bb = self.element.get_BoundingBox(None)
        near = DB.Outline(DB.XYZ(bb.Min.X, bb.Min.Y, bb.Min.Z - 0.3 / FT), DB.XYZ(bb.Max.X, bb.Max.Y, bb.Max.Z))
        doc = self.element.Document
        found = []
        for bic, label in SUPPORT_CATEGORIES:
            for other in (DB.FilteredElementCollector(doc).OfCategory(bic).WhereElementIsNotElementType()
                          .WherePasses(DB.BoundingBoxIntersectsFilter(near))):
                tris = []
                for solid in rf._solids(other):
                    try:
                        part = DB.BooleanOperationsUtils.ExecuteBooleanOperation(
                            solid, slice_, DB.BooleanOperationsType.Intersect)
                    except Exception:
                        continue
                    if part is None or part.Volume < 1e-9:
                        continue
                    for face in part.Faces:
                        if isinstance(face, DB.PlanarFace) and face.FaceNormal.Z < -0.9:
                            mesh = face.Triangulate()
                            for i in range(mesh.NumTriangles):
                                tri = mesh.get_Triangle(i)
                                tris.append(tuple(f.local(tri.get_Vertex(k))[:2] for k in range(3)))
                if tris:
                    found.append({"label": label, "id": other.Id, "tris": tris})
        return found

    def plan(self, settings, mm, splice=None, lib=None):
        """(layers {(face, axis): lines}, ribbed extras or None, warnings)."""
        if settings.get("kind") == sr.ALIGERADA:
            plan, warnings = sr.plan_ribbed(self.slab_tris, self.support_tris, self.thickness, settings, mm, splice, lib)
            return plan["lines"], plan, warnings
        lines, warnings = sr.plan_slab(self.slab_tris, self.support_tris, self.thickness, settings, mm, splice)
        return lines, None, warnings

    def point(self, axis, s, pos, z):
        """World point of a bar point: s along its axis, pos across."""
        x, y = (s, pos) if axis == sr.X else (pos, s)
        return self.f.world(x, y, z)


def read_settings(floor):
    p = floor.LookupParameter(PARAM)
    return sr.read(p.AsString() if p is not None else u"")


def write_settings(floor, settings):
    p = floor.LookupParameter(PARAM)
    if p is not None and not p.IsReadOnly:
        p.Set(sr.write(settings))


def generate(model, settings, mm, bar_type_for, splice=None, tag=None):
    """Create the slab's bars (inside an open Transaction), one rebar set per
    group of equal bars side by side. bar_type_for(key) -> RebarBarType.
    Returns ([(rebar, face, axis)], warnings)."""
    from Autodesk.Revit.DB.Structure import Rebar, RebarStyle, RebarHookOrientation
    doc = model.element.Document
    lines, _, warnings = model.plan(settings, mm, splice, brick_library(doc, settings))
    made = []
    for (face, axis), layer in sorted(lines.items()):
        normal = model.f.uy if axis == sr.X else model.f.ux  # bars laid across
        for path, key, pos, count, spacing in sr.bar_sets(layer or []):
            pts = [model.point(axis, s, pos, z) for s, z in path]
            curves = List[DB.Curve]([DB.Line.CreateBound(pts[k], pts[k + 1]) for k in range(len(pts) - 1)
                                     if pts[k].DistanceTo(pts[k + 1]) > 1e-4])
            rebar = Rebar.CreateFromCurves(doc, RebarStyle.Standard, bar_type_for(key), None, None, model.element,
                                           normal, curves, RebarHookOrientation.Right, RebarHookOrientation.Right,
                                           True, True)
            if count > 1:
                rebar.GetShapeDrivenAccessor().SetLayoutAsNumberWithSpacing(count, spacing / FT, True, True, True)
            if tag:
                tag(rebar)
            made.append((rebar, face, axis))
    return made, warnings


# --- bricks of a ribbed slab ("ladrillo de techo", a floor-hosted family) ---------
BRICK_WORDS = (u"LADRILLO", u"BLOQUE", u"CASETON", u"BOVEDILLA")
BRICK_TAG = u"BOKI LOSA "  # Comentarios of the bricks placed for a slab: BOKI LOSA <id>
FT_ = FT


def _double(element, name):
    p = element.LookupParameter(name)
    return p.AsDouble() * FT if p is not None and p.StorageType == DB.StorageType.Double else None


def brick_types(doc):
    """{name: (symbol, largo, ancho, altura)} of the brick family types of the
    project (as the Properties palette lists them): generic models named
    LADRILLO / BLOQUE... with Largo, Ancho and Altura."""
    out = {}
    for sym in DB.FilteredElementCollector(doc).OfClass(DB.FamilySymbol):
        fam = sym.FamilyName.upper()
        if not any(w in fam for w in BRICK_WORDS):
            continue
        largo, ancho, altura = _double(sym, "Largo"), _double(sym, "Ancho"), _double(sym, "Altura")
        if not (largo and ancho and altura):
            continue
        name = u"{} : {}".format(sym.FamilyName, sym.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM).AsString())
        out[name] = (sym, largo, ancho, altura)
    return out


def default_brick(types, h, top=0.05):
    """The brick that fills the ribbed slab: its height h - top, 0.30 wide
    and long if there is one."""
    want = h - top
    best = None
    for name, (sym, largo, ancho, altura) in types.items():
        score = abs(altura - want) * 10 + abs(ancho - 0.30) + abs(largo - 0.30)
        if best is None or score < best[0]:
            best = (score, name)
    return best[1] if best else None


def brick_library(doc, settings, types=None):
    """[(name, largo, ancho)] of the chosen brick's family with its height:
    the whole brick and its shorter / narrower pieces."""
    if settings.get("kind") != sr.ALIGERADA:
        return []
    types = types if types is not None else brick_types(doc)
    chosen = sr.ribbed(settings).get("brick")
    if chosen not in types:
        return []
    sym, largo, ancho, altura = types[chosen]
    lib = [(chosen, largo, ancho)]
    for name, (s2, l2, a2, h2) in types.items():
        if name != chosen and s2.FamilyName == sym.FamilyName and abs(h2 - altura) < 1e-3 \
                and l2 <= largo + 1e-6 and a2 <= ancho + 1e-6:
            lib.append((name, l2, a2))
    return lib


def delete_bricks(doc, floor):
    tag = BRICK_TAG + str(floor.Id.IntegerValue)
    old = [e.Id for e in DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_GenericModel)
           .WhereElementIsNotElementType()
           if (e.get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS).AsString() or u"") == tag]
    if old:
        doc.Delete(List[DB.ElementId](old))
    return len(old)


def place_bricks(model, ribbed_plan, types):
    """Place the bricks of the plan, hosted by the floor, hanging from its top
    face (the family's own drop): the brick's width across the viguetas,
    its length along them. Returns the number placed."""
    from Autodesk.Revit.Creation import FamilyInstanceCreationData
    doc = model.element.Document
    axis = ribbed_plan["axis"]
    f = model.f
    hand = (f.uy.Negate() if axis == sr.X else f.ux)  # the length runs back from the point along the viguetas
    data = List[FamilyInstanceCreationData]()
    for b in ribbed_plan["bricks"]:
        sym = types[b["type"]][0]
        if not sym.IsActive:
            sym.Activate()
        p = model.point(axis, b["s1"], (b["t0"] + b["t1"]) / 2.0, model.thickness)
        data.Add(FamilyInstanceCreationData(p, sym, hand, model.element, DB.Structure.StructuralType.NonStructural))
    if data.Count == 0:
        return 0
    ids = doc.Create.NewFamilyInstances2(data)
    tag = BRICK_TAG + str(model.element.Id.IntegerValue)
    for i in ids:
        c = doc.GetElement(i).get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
        if c is not None and not c.IsReadOnly:
            c.Set(tag)
    return ids.Count