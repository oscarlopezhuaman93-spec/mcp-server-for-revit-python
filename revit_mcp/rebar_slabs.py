# -*- coding: utf-8 -*-
"""Revit side of "Acero Losa" (solid slabs): read a floor - its top face
and the supports under it (beams, walls, columns) in plan - and create
the bars that slab_rebar plans, as rebar sets hosted by the floor.

Local frame: rebar_foundation.Foundation (the model's x / y from the
center of the floor's box, z up from its bottom), meters."""
import math

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
        tops = [face for face in self.f.faces if face.normal[2] > 0.3]  # a sloped slab too
        self.top3d = [t for face in tops for t in face.triangles]
        self.slab_tris = _flat(self.top3d)
        self.sloped = any(face.normal[2] < 0.999 for face in tops)
        self.thickness = z1 - z0
        self.thickness_across = self.thickness
        if self.sloped:  # its real thickness, measured on its faces: vertical / across the face
            import slab_fix
            across, vertical = slab_fix.thickness_across(floor)
            if across:
                self.thickness, self.thickness_across = vertical, across
        self.supports = self._supports()
        self.support_tris = [t for s in self.supports for t in s["tris"]]
        self.angle = 0.0  # degrees: the frame of the last plan (a ribbed slab turned)

    def _supports(self, below=0.03, depth=0.02, around=0.8):
        """What holds the slab: the elements cut by a thin slice just under
        its bottom, under it or around its edges (a slab that stops at the
        face of its beams and walls)."""
        f = self.f
        x0, x1, y0, y1, z0, z1 = f.extent
        x0, x1, y0, y1 = x0 - around, x1 + around, y0 - around, y1 + around
        corners = [f.world(x, y, z0 - below - depth) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
        loop = DB.CurveLoop()
        for k in range(4):
            loop.Append(DB.Line.CreateBound(corners[k], corners[(k + 1) % 4]))
        # a sloped slab: the slice runs its whole height; what passes under its
        # bottom there is kept (a wall standing on it is not a support)
        height = (z1 - z0 + below) if self.sloped else depth
        slice_ = DB.GeometryCreationUtilities.CreateExtrusionGeometry(
            List[DB.CurveLoop]([loop]), DB.XYZ.BasisZ, height / FT)
        bb = self.element.get_BoundingBox(None)
        pad = around / FT
        near = DB.Outline(DB.XYZ(bb.Min.X - pad, bb.Min.Y - pad, bb.Min.Z - 0.3 / FT),
                          DB.XYZ(bb.Max.X + pad, bb.Max.Y + pad, bb.Max.Z))
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
                                pts = [f.local(tri.get_Vertex(k)) for k in range(3)]
                                if self.sloped:
                                    cx = sum(q[0] for q in pts) / 3.0
                                    cy = sum(q[1] for q in pts) / 3.0
                                    if sum(q[2] for q in pts) / 3.0 > self.top_z(cx, cy) - self.thickness + 0.03:
                                        continue
                                tris.append(tuple(q[:2] for q in pts))
                if tris:
                    found.append({"label": label, "id": other.Id, "tris": tris})
        return found

    def plan(self, settings, mm, splice=None, lib=None):
        """(layers {(face, axis): lines}, ribbed extras or None, warnings)."""
        if settings.get("kind") == sr.ALIGERADA:
            # the bricks' direction turned by an angle (by default along the
            # slab's longest side, its beams): planned in that frame
            angle = sr.ribbed(settings).get("angle")
            if self.sloped:
                # a sloped slab: its direction is measured from its slope
                # ("rel": 0 = down the slope, 90 = along the ridge), so both
                # waters of a roof turn alike whatever their frames; an old
                # absolute "angle" is not used (2026-11-03, with the user)
                settings = dict(settings)
                settings["alig"] = dict(settings.get("alig") or {}, dir=sr.X)
                angle = self.climb_angle() + float(slope_turn(settings))
            self.angle = float(sr.dominant_angle(self.slab_tris) if angle is None else angle)
            plan, warnings = sr.plan_ribbed(sr.rotate_tris(self.slab_tris, self.angle),
                                            sr.rotate_tris(self.support_tris, self.angle),
                                            self.thickness, settings, mm, splice, lib)
            return plan["lines"], plan, warnings
        self.angle = 0.0
        lines, warnings = sr.plan_slab(self.slab_tris, self.support_tris, self.thickness, settings, mm, splice)
        return lines, None, warnings

    def point(self, axis, s, pos, z):
        """World point of a bar point: s along its axis, pos across."""
        x, y = (s, pos) if axis == sr.X else (pos, s)
        x, y = sr.unrotate(x, y, self.angle)
        return self.f.world(x, y, self.local_z(x, y, z))

    def climb_angle(self):
        """Direction (deg, model axes) the slab climbs: its largest top face's."""
        tops = [f for f in self.f.faces if f.normal[2] > 0.3]
        f = max(tops, key=lambda q: q.area)
        import math as _m
        return _m.degrees(_m.atan2(-f.normal[1], -f.normal[0])) % 360.0

    def top_z(self, x, y):
        """Height (local) of the slab's top over (x, y): on its top
        triangles; the nearest one's plane when outside."""
        best = None
        for t in self.top3d:
            (ax, ay, az), (bx, by, bz), (cx, cy, cz) = t
            det = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
            if abs(det) < 1e-12:
                continue
            l1 = ((by - cy) * (x - cx) + (cx - bx) * (y - cy)) / det
            l2 = ((cy - ay) * (x - cx) + (ax - cx) * (y - cy)) / det
            l3 = 1.0 - l1 - l2
            z = l1 * az + l2 * bz + l3 * cz
            out = -min(l1, l2, l3)
            if out <= 1e-6:
                return z
            if best is None or out < best[0]:
                best = (out, z)
        return best[1] if best else self.f.extent[5]

    def local_z(self, x, y, z):
        """A height measured up from the slab's bottom (the bars, the bricks)
        to the local frame: on a sloped slab, from its bottom right there."""
        if not self.sloped:
            return z
        return self.top_z(x, y) - self.thickness + z

    def point_xy(self, axis, s, pos):
        x, y = (s, pos) if axis == sr.X else (pos, s)
        return sr.unrotate(x, y, self.angle)

    def frame_slab(self):
        """The slab's triangles in the frame of the last plan."""
        return sr.rotate_tris(self.slab_tris, self.angle)

    def frame_supports(self):
        return sr.rotate_tris(self.support_tris, self.angle)

    def direction(self, axis):
        """World unit vector of a plan axis (in the turned frame)."""
        x, y = sr.unrotate(1.0, 0.0, self.angle) if axis == sr.X else sr.unrotate(0.0, 1.0, self.angle)
        return (self.f.ux * x + self.f.uy * y).Normalize()


def slope_turn(settings):
    """A sloped slab's vigueta direction from its slope (deg, 0 = down the
    slope): alig "rel", 0 when not set."""
    rel = (settings.get("alig") or {}).get("rel")
    return (float(rel) % 180.0) if rel is not None else 0.0


def aligned_start(model_a, rib_a, model_b, settings_b, mm, lib=None, mirror=None):
    """The "start" (m) that lines model_b's viguetas up with model_a's (the
    two waters of a roof: each vigueta goes on across the ridge, as built).
    `mirror` (world XYZ -> world XYZ, the reflection across the ridge): the
    viguetas along the ridge instead, model_b's the mirror image of
    model_a's - symmetric about the ridge. model_a planned (rib_a, its
    angle); model_b planned here with start 0. None when either has no
    vigueta."""
    if not rib_a or not rib_a.get("viguetas"):
        return None
    st0 = dict(settings_b)
    st0["alig"] = dict(settings_b.get("alig") or {}, start=0.0)
    rib_b = model_b.plan(st0, mm, None, lib)[1]
    if not rib_b or not rib_b.get("viguetas"):
        return None
    s = sr.ribbed(st0)["s"]
    # A's first vigueta (a point of its line) in B's frame: its cross position there
    c = rib_a["viguetas"][0]
    x, y = (0.0, c) if rib_a["axis"] == sr.X else (c, 0.0)
    w = model_a.f.world(*(sr.unrotate(x, y, model_a.angle) + (0.0,)))
    if mirror is not None:
        w = mirror(w)
    bx, by, _ = model_b.f.local(w)
    rx, ry = sr.rotate_tris([((bx, by),)], model_b.angle)[0][0]
    target = ry if rib_b["axis"] == sr.X else rx
    return round((target - rib_b["viguetas"][0]) % s, 4)


def aligned_phase(model_a, rib_a, model_b, settings_b, mirror=None):
    """The grid (alig "phase", sr.rib_layout) that puts model_b's viguetas
    on model_a's: across the ridge, or - `mirror`, the reflection across the
    ridge - symmetric about it. model_a planned (rib_a, its angle); model_b
    a sloped slab (its frame: down its slope, turned by its own turn). None
    when model_a has no vigueta."""
    if not rib_a or not rib_a.get("viguetas"):
        return None
    c = rib_a["viguetas"][0]
    x, y = (0.0, c) if rib_a["axis"] == sr.X else (c, 0.0)
    w = model_a.f.world(*(sr.unrotate(x, y, model_a.angle) + (0.0,)))
    if mirror is not None:
        w = mirror(w)
    bx, by, _ = model_b.f.local(w)
    angle_b = model_b.climb_angle() + slope_turn(settings_b)
    rx, ry = sr.rotate_tris([((bx, by),)], angle_b)[0][0]
    return round(ry, 4)  # the viguetas run along the frame's x: their positions are its y


def read_settings(floor):
    p = floor.LookupParameter(PARAM)
    return sr.read(p.AsString() if p is not None else u"")


def write_settings(floor, settings):
    p = floor.LookupParameter(PARAM)
    if p is not None and not p.IsReadOnly:
        p.Set(sr.write(settings))


def generate(model, settings, mm, bar_type_for, splice=None, tag=None, skip=None):
    """Create the slab's bars (inside an open Transaction), one rebar set per
    group of equal bars side by side. bar_type_for(key) -> RebarBarType.
    Returns ([(rebar, face, axis)], warnings)."""
    from Autodesk.Revit.DB.Structure import Rebar, RebarStyle, RebarHookOrientation
    doc = model.element.Document
    lines, _, warnings = model.plan(settings, mm, splice, brick_library(doc, settings))
    made = []
    for (face, axis), layer in sorted(lines.items()):
        if skip and (face, axis) in skip:
            continue  # (a roof's vigueta bars: made by roof_model across the ridge)
        normal = model.direction(sr.Y if axis == sr.X else sr.X)  # bars laid across
        if model.sloped:  # each bar on its own: it follows the slope (bends at a ridge)
            sets = [(bar["path"], bar["key"], line["pos"], 1, 0.0) for line in layer or [] for bar in line["bars"]]
        else:
            sets = sr.bar_sets(layer or [])
        seen = set()
        for path, key, pos, count, spacing in sets:
            sig = (key, round(pos, 4), tuple((round(a_, 3), round(b_, 3)) for a_, b_ in path))
            if sig in seen:  # the same bar twice (two chords of one line): once
                continue
            seen.add(sig)
            if model.sloped:
                local = []
                for (s0, z0), (s1, z1) in zip(path, path[1:]):
                    (x0, y0), (x1, y1) = model.point_xy(axis, s0, pos), model.point_xy(axis, s1, pos)
                    if abs(z1 - z0) < 1e-9 and abs(s1 - s0) > 1e-6:  # a run: on the surface
                        run = sr.follow_surface([(x0, y0, 0.0), (x1, y1, 0.0)],
                                                lambda x, y, z=z0: model.local_z(x, y, z))
                        local += run if not local else run[1:]
                    else:  # a hook leg: straight down / up
                        if not local:
                            local.append((x0, y0, model.local_z(x0, y0, z0)))
                        local.append((x1, y1, local[-1][2] + (z1 - z0)))
                pts = [model.f.world(x, y, z) for x, y, z in local]
            else:
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
BRICK_WORDS = (u"LADRILLO", u"BLOQUE", u"CASETON", u"CAJETIN", u"CAJET\u00cdN", u"BOVEDILLA", u"TECNOPOR", u"EPS")
BRICK_TAG = u"BOKI LOSA "  # Comentarios of the bricks placed for a slab: BOKI LOSA <id>
FT_ = FT


# the size parameters of a brick / cajetin family, by the names they may have
SIZE_NAMES = {"largo": (u"Largo", u"Longitud", u"Length", u"L"), "ancho": (u"Ancho", u"Width", u"A", u"B"),
              "altura": (u"Altura", u"Alto", u"Height", u"H")}
CUT_MARK = u" CORTE "  # the types made for cut pieces: <type> CORTE L=0.180 A=0.300


def _size_param(element, what):
    for name in SIZE_NAMES[what]:
        p = element.LookupParameter(name)
        if p is not None and p.StorageType == DB.StorageType.Double:
            return p
    return None


def _double(element, what):
    p = _size_param(element, what)
    return p.AsDouble() * FT if p is not None else None


def brick_types(doc, with_cuts=True):
    """{name: (symbol, largo, ancho, altura)} of the brick / cajetin family
    types of the project (as the Properties palette lists them): models named
    LADRILLO / BLOQUE / CAJETIN... with a length, a width and a height.
    with_cuts=False leaves out the types made for cut pieces."""
    out = {}
    for sym in DB.FilteredElementCollector(doc).OfClass(DB.FamilySymbol):
        fam = sym.FamilyName.upper()
        if not any(w in fam for w in BRICK_WORDS) or u"BASADO EN CARA" in fam:
            continue  # (the face-based copy is the plugin's own, for sloped slabs)
        if not with_cuts and CUT_MARK in (sym.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM).AsString() or u""):
            continue
        largo, ancho, altura = _double(sym, "largo"), _double(sym, "ancho"), _double(sym, "altura")
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
                and l2 <= largo + 1e-6 and a2 <= ancho + 1e-6 and CUT_MARK not in name:
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


def piece_symbol(doc, types, name, largo, ancho):
    """The family type of a brick piece: the type itself when its size
    matches, another type of the family with that size, else a new type
    duplicated from it with that length and width (the brick cut to fit)."""
    sym, l0, a0, h0 = types[name]
    if abs(l0 - largo) < 1e-3 and abs(a0 - ancho) < 1e-3:
        return sym
    for other, (s2, l2, a2, h2) in types.items():
        if s2.FamilyName == sym.FamilyName and abs(h2 - h0) < 1e-3 and abs(l2 - largo) < 1e-3 \
                and abs(a2 - ancho) < 1e-3:
            return s2
    base = name.split(u" : ", 1)[1].split(CUT_MARK)[0]
    new_name = u"{}{}L={:.3f} A={:.3f}".format(base, CUT_MARK, largo, ancho)
    new = sym.Duplicate(new_name)
    for what, value in (("largo", largo), ("ancho", ancho)):
        p = _size_param(new, what)
        if p is not None and not p.IsReadOnly:
            p.Set(value / FT)
    types[u"{} : {}".format(sym.FamilyName, new_name)] = (new, largo, ancho, h0)
    return new


def _brick_sizes(types, bricks, max_h=None):
    """{(name, largo, ancho): (type name, largo, ancho, altura)} of the pieces,
    named as the original family's types (a cut one: <type> CORTE L= A=)."""
    out = {}
    for b in bricks:
        sym, l0, a0, h0 = types[b["type"]]
        largo, ancho = b.get("largo") or l0, b.get("ancho") or a0
        base = b["type"].split(u" : ", 1)[1].split(CUT_MARK)[0]
        name = base if (abs(largo - l0) < 1e-3 and abs(ancho - a0) < 1e-3) else \
            u"{}{}L={:.3f} A={:.3f}".format(base, CUT_MARK, largo, ancho)
        h = h0
        if max_h is not None and max_h < h0 - 1e-3:  # a sloped slab is thinner across its face
            h = max(0.05, math.floor(max_h / 0.005 + 1e-6) * 0.005)  # 0.1499999.. is 0.150
            name += u" H={:.3f}".format(h)
        out[(b["type"], round(largo, 3), round(ancho, 3))] = (name, largo, ancho, h)
    return out


def place_bricks_on_slope(model, ribbed_plan, types, folder):
    """The bricks of a sloped slab: the face-based copy of the family
    (brick_family), each placed on the slab's top face so it takes its
    slope, flipped into the slab. Returns the number placed."""
    import brick_family as bf
    doc = model.element.Document
    # the room under the 5 cm on top, across the face (the type's thickness is vertical)
    room = model.thickness_across - 0.05  # what fits under the 5 cm on top, across the face
    bricks = trim_bricks(model, ribbed_plan, avoid=beam_tris(model))  # none out of the slab, none into a beam
    sizes = _brick_sizes(types, bricks, room)
    symbols = bf.ensure_loaded(doc, sorted(set(sizes.values())), folder)
    if not symbols:
        return 0
    faces = []
    for r in DB.HostObjectUtils.GetTopFaces(model.element):
        f = model.element.GetGeometryObjectFromReference(r)
        if isinstance(f, DB.PlanarFace):
            faces.append((r, f))
    axis = ribbed_plan["axis"]
    along = model.direction(axis)
    tag = BRICK_TAG + str(model.element.Id.IntegerValue)
    placed = 0
    made = []
    for b in bricks:
        name, largo, ancho, altura = sizes[(b["type"], round(b.get("largo") or types[b["type"]][1], 3),
                                            round(b.get("ancho") or types[b["type"]][2], 3))]
        sym = symbols.get(name)
        if sym is None:
            continue
        if not sym.IsActive:
            sym.Activate()
        tc = (b["t0"] + b["t1"]) / 2.0
        # flipped into the slab, the family's length runs forward: from the start
        xe, ye = model.point_xy(axis, b["s0"], tc)
        w = model.f.world(xe, ye, 0.0)
        best = None
        for r, f in faces:  # the face over the point (the nearest plane in plan)
            n, o = f.FaceNormal, f.Origin
            if abs(n.Z) < 0.2:
                continue
            z = o.Z - (n.X * (w.X - o.X) + n.Y * (w.Y - o.Y)) / n.Z
            pt = DB.XYZ(w.X, w.Y, z)
            res = f.Project(pt)
            d = res.Distance if res is not None else 1e9
            if best is None or d < best[0]:
                best = (d, r, f, pt)
        if best is None:
            continue
        _, r, f, pt = best
        n = f.FaceNormal
        on_face = (along - n * along.DotProduct(n)).Normalize()
        ref_dir = on_face.CrossProduct(n).Normalize()  # family x: across; its length runs back along -y = -on_face
        inst = doc.Create.NewFamilyInstance(r, pt, ref_dir, sym)
        if inst.CanFlipWorkPlane:
            inst.IsWorkPlaneFlipped = True  # into the slab
        c = inst.get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
        if c is not None and not c.IsReadOnly:
            c.Set(tag)
        placed += 1
        made.append(inst)
    cut_bricks(model.element, made)
    return placed


def cut_bricks(slab, bricks):
    """The bricks take their room out of the slab (the slab cut by each
    one) and a beam a brick runs into cuts that brick: no brick out of the
    slab into a beam. Returns (slab cuts, beam cuts)."""
    if not bricks:
        return 0, 0
    doc = slab.Document
    S = DB.SolidSolidCutUtils
    doc.Regenerate()
    n_slab = n_beam = 0
    for b in bricks:
        try:
            if S.CanElementCutElement(b, slab)[0]:
                S.AddCutBetweenSolids(doc, slab, b)
                n_slab += 1
        except Exception:
            pass
    bb = slab.get_BoundingBox(None)
    if bb is None:
        return n_slab, 0
    pad = DB.XYZ(1.0, 1.0, 1.0)
    beams = list(DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_StructuralFraming)
                 .WhereElementIsNotElementType()
                 .WherePasses(DB.BoundingBoxIntersectsFilter(DB.Outline(bb.Min - pad, bb.Max + pad))))
    for b in bricks:
        hit = DB.ElementIntersectsElementFilter(b)
        for beam in beams:
            try:
                if hit.PassesFilter(beam) and S.CanElementCutElement(beam, b)[0]:
                    S.AddCutBetweenSolids(doc, b, beam)
                    n_beam += 1
            except Exception:
                pass
    # the cuts redrawn now: otherwise a view may keep showing the slab
    # open over its bricks (holes, loose strips) until something regenerates it
    doc.Regenerate()
    return n_slab, n_beam


def place_bricks(model, ribbed_plan, types, folder=None, tilt=None):
    """Bricks of the plan: on a sloped slab (tilt None: when it is sloped)
    the face-based copy that takes the slope, else the project's family."""
    if (model.sloped if tilt is None else tilt):
        import tempfile
        return place_bricks_on_slope(model, ribbed_plan, types, folder or tempfile.gettempdir())
    return place_bricks_level(model, ribbed_plan, types)


def _in_tris(p, tris, eps=1e-7):
    """Point (x, y) on the slab's top in plan (its triangles, m); a point
    on an edge counts in."""
    x, y = p
    for t in tris:
        (ax, ay), (bx, by), (cx, cy) = t[0][:2], t[1][:2], t[2][:2]
        d1 = (x - bx) * (ay - by) - (ax - bx) * (y - by)
        d2 = (x - cx) * (by - cy) - (bx - cx) * (y - cy)
        d3 = (x - ax) * (cy - ay) - (cx - ax) * (y - ay)
        if (d1 >= -eps and d2 >= -eps and d3 >= -eps) or (d1 <= eps and d2 <= eps and d3 <= eps):
            return True
    return False


def beam_tris(model):
    """Plan triangles (the slab's local frame, m) of the beams that run
    into the slab: their top / bottom faces."""
    slab = model.element
    doc = slab.Document
    hit = DB.ElementIntersectsElementFilter(slab)
    beams = [b for b in DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_StructuralFraming)
             .WhereElementIsNotElementType().WherePasses(hit)]
    seen = set(b.Id for b in beams)
    for i in DB.JoinGeometryUtils.GetJoinedElements(doc, slab):  # a joined beam does not "intersect"
        e = doc.GetElement(i)
        if (i not in seen and e is not None and e.Category is not None
                and e.Category.Id.IntegerValue == int(DB.BuiltInCategory.OST_StructuralFraming)):
            beams.append(e)
    out = []
    for beam in beams:
        for g in beam.get_Geometry(DB.Options()):
            solids = [g] if isinstance(g, DB.Solid) else (
                list(g.GetInstanceGeometry()) if isinstance(g, DB.GeometryInstance) else [])
            for s in solids:
                if not isinstance(s, DB.Solid) or s.Volume <= 0:
                    continue
                for face in s.Faces:
                    if abs(face.ComputeNormal(DB.UV(0.5, 0.5)).Z) < 0.3:
                        continue
                    mesh = face.Triangulate()
                    for i in range(mesh.NumTriangles):
                        tr = mesh.get_Triangle(i)
                        out.append([model.f.local(tr.get_Vertex(k))[:2] for k in range(3)])
    return out


def _end_rows(bricks):
    """One more row past each end row of the plan (a vigueta further on),
    marked 'extra': the room left before the beam / edge gets its bricks,
    narrowed to fit by trim_bricks (dropped when none fits)."""
    rows = sorted(set(round(b["t0"], 4) for b in bricks))
    if len(rows) < 2:
        return []
    gaps = sorted(round(b - a, 3) for a, b in zip(rows, rows[1:]))
    pitch = gaps[len(gaps) // 2]  # the usual one (an end row may come narrowed)
    out = []
    for edge, shift in ((rows[0], -pitch), (rows[-1], pitch)):
        for b in bricks:
            if abs(round(b["t0"], 4) - edge) < 1e-6:
                nb = dict(b)
                nb["t0"], nb["t1"], nb["extra"] = b["t0"] + shift, b["t1"] + shift, True
                out.append(nb)
    return out


def trim_bricks(model, ribbed_plan, step=0.02, shortest=0.10, avoid=None):
    """A brick at a sloped or curved edge pulled in: its end shortened until
    its four corners are on the slab (no corner out of the concrete, the
    rest filled with the slab's concrete, as built); one too short dropped.
    avoid: plan triangles (beam_tris) a brick ends flush against, its end
    square, never inside them. Returns the bricks kept."""
    axis = ribbed_plan["axis"]
    tris = model.slab_tris

    def ok(s, t):
        p = model.point_xy(axis, s, t)
        return _in_tris(p, tris) and not (avoid and _in_tris(p, avoid, eps=-1e-4))

    def ts(t0, t1):  # the end's corners (and its middle, against a beam)
        return (t0, t1, (t0 + t1) / 2.0) if avoid else (t0, t1)

    def fits(s0, s1, t0, t1):
        return all(ok(s, t) for s in (s0, s1) for t in ts(t0, t1))
    if avoid:
        step = min(step, 0.005)  # flush with the beam's face
    kept = []
    bricks = list(ribbed_plan["bricks"])
    if avoid:
        bricks += _end_rows(bricks)
    for b in bricks:
        s0, s1, t0, t1 = b["s0"], b["s1"], b["t0"], b["t1"]
        if fits(s0, s1, t0, t1):
            kept.append(b)
            continue
        if avoid:  # a row along a beam: narrowed, its side flush with the beam's face
            def in_beam(t):
                return any(not ok(s, t) if b.get("extra") else  # an added row: off the slab too
                           _in_tris(model.point_xy(axis, s, t), avoid, eps=-1e-4)
                           for s in (s0, s1, (s0 + s1) / 2.0))
            n0, n1 = t0, t1
            while n1 - n0 >= shortest and (in_beam(n0) or in_beam(n1)):
                if in_beam(n1):
                    n1 -= step
                if in_beam(n0):
                    n0 += step
            if n1 - n0 >= shortest and (n0, n1) != (t0, t1):
                nb = dict(b)
                nb["t0"], nb["t1"], nb["ancho"] = n0, n1, round(n1 - n0, 3)
                if fits(s0, s1, n0, n1):
                    kept.append(nb)
                    continue
                b, t0, t1 = nb, n0, n1  # still out at an end: shortened below
        while s1 - s0 >= shortest:  # the end that is out moves in
            out_end = not all(ok(s1, t) for t in ts(t0, t1))
            if out_end:
                s1 -= step
            else:
                s0 += step
            if fits(s0, s1, t0, t1):
                nb = dict(b)
                nb["s0"], nb["s1"], nb["largo"] = s0, s1, round(s1 - s0, 3)
                kept.append(nb)
                break
    return kept


def place_bricks_level(model, ribbed_plan, types):
    """Place the bricks of the plan, hosted by the floor, hanging from its top
    face (the family's own drop): the brick's width across the viguetas,
    its length along them; a piece cut to fit takes a type of its own size.
    Returns the number placed."""
    from Autodesk.Revit.Creation import FamilyInstanceCreationData
    doc = model.element.Document
    axis = ribbed_plan["axis"]
    across = model.direction(sr.Y if axis == sr.X else sr.X)
    # the length runs back from the point along the viguetas: hand = the
    # direction of the viguetas turned -90 degrees
    along = model.direction(axis)
    hand = DB.XYZ(along.Y, -along.X, 0.0)
    data = List[FamilyInstanceCreationData]()
    for b in trim_bricks(model, ribbed_plan):
        sym = piece_symbol(doc, types, b["type"], b.get("largo") or types[b["type"]][1],
                           b.get("ancho") or types[b["type"]][2])
        if not sym.IsActive:
            sym.Activate()
        tc = (b["t0"] + b["t1"]) / 2.0
        xe, ye = model.point_xy(axis, b["s1"], tc)
        if model.sloped:  # a brick stays level: hung at the height of the top over its middle
            xc, yc = model.point_xy(axis, (b["s0"] + b["s1"]) / 2.0, tc)
            p = model.f.world(xe, ye, model.top_z(xc, yc))
        else:
            p = model.f.world(xe, ye, model.thickness)
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

class DropFailing(DB.IFailuresPreprocessor):
    """Revit's warnings noted and dismissed; an element Revit refuses (a
    brick cut outside its host, a bar fully out of the slab) is deleted
    on its own instead of undoing everything."""

    def __init__(self):
        self.messages = []

    def PreprocessFailures(self, accessor):
        resolved = False
        for f in accessor.GetFailureMessages():
            self.messages.append(f.GetDescriptionText())
            if f.GetSeverity() == DB.FailureSeverity.Warning:
                accessor.DeleteWarning(f)
            elif f.HasResolutions():
                accessor.ResolveFailure(f)
                resolved = True
        return DB.FailureProcessingResult.ProceedWithCommit if resolved else DB.FailureProcessingResult.Continue