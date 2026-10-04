# -*- coding: utf-8 -*-
"""Geometry of structural foundations (zapatas, cimientos corridos, losas
de cimentacion) for the "Acero Cimentacion" window.

Everything is in the element's local frame, in meters: x / y along the
model's axes (or the family's, for a family instance) from the center of
its bounding box, z up from its bottom.

- Foundation(element).faces: its planar faces merged by plane and
  numbered (1 = bottom, 2 = top, then the sides around), each with its
  normal, triangles (3D view) and description ("Fondo", "Cara superior",
  "Lateral 3"...). Every face can take its own cover.
- Foundation.section(view): the outline cut through the element center
  for "Alzado Frontal" (looking along +y: x across, z up) or "Alzado
  Lateral" (looking along -x: y across, z up), each edge tagged with the
  face it lies on.
"""
import math

from pyrevit import DB
from System.Collections.Generic import List

FT = 0.3048
FRONT = u"frontal"
SIDE = u"lateral"
DEFAULT_COVER_CM = 7.5  # E.060: concrete cast against the ground


def _solids(element):
    options = DB.Options()
    options.ComputeReferences = False
    found = []

    def walk(geometry):
        for g in geometry:
            if isinstance(g, DB.Solid) and g.Volume > 1e-6:
                found.append(g)
            elif isinstance(g, DB.GeometryInstance):
                walk(g.GetInstanceGeometry())
    walk(element.get_Geometry(options))
    return found


NEIGHBOR_CATEGORIES = (
    (DB.BuiltInCategory.OST_StructuralColumns, u"COLUMNA"),
    (DB.BuiltInCategory.OST_Walls, u"MURO"),
    (DB.BuiltInCategory.OST_StructuralFraming, u"VIGA"),
    (DB.BuiltInCategory.OST_StructuralFoundation, u"ZAPATA"),
    (DB.BuiltInCategory.OST_Floors, u"LOSA"),
) if hasattr(DB, "BuiltInCategory") else ()


class Face(object):
    def __init__(self, number, normal, offset, label):
        self.number = number
        self.normal = normal  # local unit (x, y, z)
        self.offset = offset  # plane: normal . p = offset (m)
        self.label = label
        self.triangles = []
        self.area = 0.0


class Foundation(object):
    def __init__(self, element):
        self.element = element
        bb = element.get_BoundingBox(None)
        transform = getattr(element, "GetTransform", None)
        curve = getattr(getattr(element, "Location", None), "Curve", None)
        if isinstance(element, DB.Wall) and isinstance(curve, DB.Line):
            basis = curve.Direction  # a wall: x along it, its cut across (SIDE)
        else:
            basis = transform().BasisX if transform else DB.XYZ.BasisX
        basis = DB.XYZ(basis.X, basis.Y, 0.0).Normalize()
        self.ux = basis
        self.uy = DB.XYZ.BasisZ.CrossProduct(basis)
        center = (bb.Min + bb.Max) * 0.5
        self.origin = DB.XYZ(center.X, center.Y, bb.Min.Z)
        self.solids = _solids(element)
        self._read_faces()
        xs = [p[0] for f in self.faces for t in f.triangles for p in t]
        ys = [p[1] for f in self.faces for t in f.triangles for p in t]
        zs = [p[2] for f in self.faces for t in f.triangles for p in t]
        self.extent = (min(xs), max(xs), min(ys), max(ys), min(zs), max(zs))

    def local(self, p):
        d = p - self.origin
        return (d.DotProduct(self.ux) * FT, d.DotProduct(self.uy) * FT, d.Z * FT)

    def world(self, x, y, z):
        return self.origin + self.ux * (x / FT) + self.uy * (y / FT) + DB.XYZ.BasisZ * (z / FT)

    def _vector(self, v):
        return (v.DotProduct(self.ux), v.DotProduct(self.uy), v.Z)

    def _read_faces(self):
        groups = []  # [Face], merged by plane
        for solid in self.solids:
            for face in solid.Faces:
                if not isinstance(face, DB.PlanarFace):
                    continue
                n = self._vector(face.FaceNormal)
                p = self.local(face.Origin)
                offset = n[0] * p[0] + n[1] * p[1] + n[2] * p[2]
                group = None
                for g in groups:
                    if (sum(a * b for a, b in zip(g.normal, n)) > 0.999
                            and abs(g.offset - offset) < 0.005):
                        group = g
                        break
                if group is None:
                    group = Face(0, n, offset, u"")
                    groups.append(group)
                mesh = face.Triangulate()
                for i in range(mesh.NumTriangles):
                    tri = mesh.get_Triangle(i)
                    group.triangles.append(tuple(self.local(tri.get_Vertex(k)) for k in range(3)))
                group.area += face.Area * FT * FT
        groups = [g for g in groups if g.area > 0.005]  # slivers left by joins
        bottoms = sorted([g for g in groups if g.normal[2] < -0.9], key=lambda g: -g.area)
        tops = sorted([g for g in groups if g.normal[2] > 0.9], key=lambda g: -g.area)
        sides = [g for g in groups if abs(g.normal[2]) <= 0.9]
        # sides around, counterclockwise from the -y face (the front)
        sides.sort(key=lambda g: (math.atan2(g.normal[1], g.normal[0]) + math.pi / 2.0) % (2 * math.pi))
        self.faces = []
        for g in bottoms:
            g.label = u"Fondo" if len(bottoms) == 1 else u"Fondo {}".format(bottoms.index(g) + 1)
            self.faces.append(g)
        for g in tops:
            g.label = u"Cara superior" if len(tops) == 1 else u"Cara superior {}".format(tops.index(g) + 1)
            self.faces.append(g)
        for g in sides:
            g.label = u"Lateral"
            self.faces.append(g)
        for i, g in enumerate(self.faces):
            g.number = i + 1
            if g.label == u"Lateral":
                g.label = u"Lateral ({})".format(_direction(g.normal))

    def face_at(self, p, tol=0.01):
        """The face whose plane holds the local point p, or None."""
        best = None
        for f in self.faces:
            d = abs(sum(a * b for a, b in zip(f.normal, p)) - f.offset)
            if d < tol and (best is None or d < best[0]):
                best = (d, f)
        return best[1] if best else None

    def touching(self, contact_m=0.05):
        """[(label, element)] of the elements touching this one (columns,
        walls, beams, slabs, foundations)."""
        bb = self.element.get_BoundingBox(None)
        touch = contact_m / FT
        near = DB.Outline(DB.XYZ(bb.Min.X - touch, bb.Min.Y - touch, bb.Min.Z - touch),
                          DB.XYZ(bb.Max.X + touch, bb.Max.Y + touch, bb.Max.Z + touch))
        found = []
        for bic, label in NEIGHBOR_CATEGORIES:
            for other in (DB.FilteredElementCollector(self.element.Document).OfCategory(bic)
                          .WhereElementIsNotElementType().WherePasses(DB.BoundingBoxIntersectsFilter(near))):
                if other.Id != self.element.Id:
                    found.append((label, other))
        return found

    def neighbor_sections(self, view, at=0.0, reach_m=0.8):
        """[(label, points [(u, z)])] of the touching elements cut by the
        same plane, kept to `reach_m` around this element."""
        out = []
        for label, other in self.touching():
            for pts, _ in self.section(view, at, _solids(other), pad=reach_m):
                out.append((label, pts))
        return out

    def section(self, view, at=0.0, solids=None, pad=0.5):
        """[(points [(u, z)...], [face number per edge])] outlines of the
        element (or of `solids`, another element's, then untagged) cut by
        the plane through `at` (m, along y for FRONT, along x for SIDE): u
        is x for FRONT and -y for SIDE, z up; `pad` m around the element."""
        x0, x1, y0, y1, z0, z1 = self.extent
        thin = 0.001
        if view == FRONT:
            a, b = (x0 - pad, at - thin), (x1 + pad, at + thin)
        else:
            a, b = (at - thin, y0 - pad), (at + thin, y1 + pad)
        corners = [self.world(a[0], a[1], z0 - pad), self.world(b[0], a[1], z0 - pad),
                   self.world(b[0], b[1], z0 - pad), self.world(a[0], b[1], z0 - pad)]
        loop = DB.CurveLoop()
        for k in range(4):
            loop.Append(DB.Line.CreateBound(corners[k], corners[(k + 1) % 4]))
        slab = DB.GeometryCreationUtilities.CreateExtrusionGeometry(
            List[DB.CurveLoop]([loop]), DB.XYZ.BasisZ, (z1 - z0 + 2 * pad) / FT)
        axis = (0.0, 1.0, 0.0) if view == FRONT else (1.0, 0.0, 0.0)
        outlines = []
        own = solids is None
        for solid in (self.solids if own else solids):
            try:
                cut = DB.BooleanOperationsUtils.ExecuteBooleanOperation(
                    solid, slab, DB.BooleanOperationsType.Intersect)
            except Exception:
                continue
            if cut is None or cut.Volume <= 0:
                continue
            for face in cut.Faces:
                if not isinstance(face, DB.PlanarFace):
                    continue
                n = self._vector(face.FaceNormal)
                if sum(c * d for c, d in zip(n, axis)) < 0.99:
                    continue
                for edges in face.EdgeLoops:
                    pts, tags = [], []
                    for edge in edges:
                        c = edge.AsCurveFollowingFace(face)  # oriented around the loop
                        p, q = self.local(c.GetEndPoint(0)), self.local(c.GetEndPoint(1))
                        u = p[0] if view == FRONT else -p[1]
                        pts.append((u, p[2]))
                        mid = tuple((s + t) / 2.0 for s, t in zip(p, q))
                        # the cut face itself is thin: test against the element's faces
                        f = self.face_at(mid) if own else None
                        tags.append(f.number if f else None)
                    outlines.append((pts, tags))
        return outlines


def _direction(n):
    ang = math.degrees(math.atan2(n[1], n[0])) % 360
    names = [(0, u"+X"), (90, u"+Y"), (180, u"-X"), (270, u"-Y"), (360, u"+X")]
    best = min(names, key=lambda a: abs(a[0] - ang))
    return best[1] if abs(best[0] - ang) < 10 else u"{:.0f}°".format(ang)


def inner_outline(points, covers):
    """The outline `points` [(u, z)] moved inside by the cover of each edge
    (covers[i] for the edge points[i] -> points[i+1], m): each edge
    offset towards the inside, consecutive ones intersected. For a
    counterclockwise or clockwise loop alike."""
    n = len(points)
    area = sum(points[i][0] * points[(i + 1) % n][1] - points[(i + 1) % n][0] * points[i][1] for i in range(n))
    sign = 1.0 if area > 0 else -1.0  # inside on the left of a counterclockwise loop
    lines = []
    for i in range(n):
        (ax, az), (bx, bz) = points[i], points[(i + 1) % n]
        dx, dz = bx - ax, bz - az
        length = math.hypot(dx, dz) or 1.0
        nx, nz = -dz / length * sign, dx / length * sign  # inward normal
        c = covers[i]
        lines.append(((ax + nx * c, az + nz * c), (dx, dz)))
    result = []
    for i in range(n):
        (p, d), (q, e) = lines[i - 1], lines[i]
        det = d[0] * e[1] - d[1] * e[0]
        if abs(det) < 1e-12:
            result.append(q)
            continue
        t = ((q[0] - p[0]) * e[1] - (q[1] - p[1]) * e[0]) / det
        result.append((p[0] + d[0] * t, p[1] + d[1] * t))
    return result


# --- Bars (pure: local meters) ------------------------------------------------
BOTTOM = u"inf"
TOP = u"sup"


def polygon_spans(points, z):
    """[(u0, u1)] where the horizontal line at height z runs inside the
    polygon `points` [(u, z)] (even-odd)."""
    xs = []
    n = len(points)
    for i in range(n):
        (a, b), (c, d) = points[i], points[(i + 1) % n]
        if (b <= z < d) or (d <= z < b):
            xs.append(a + (z - b) * (c - a) / (d - b))
    xs.sort()
    return [(xs[k], xs[k + 1]) for k in range(0, len(xs) - 1, 2)]


def mesh_layer_paths(inner, layer, diameter, hook, level=0, hook_b=None):
    """Bar paths [(u, z)] of one mesh layer in a section, inside the
    `inner` outline (covers already applied): along its bottom (BOTTOM,
    hooks up) or top (TOP, hooks down), `level` bar diameters further in
    (the second direction of the mesh lies on the first), ends a half
    diameter inside, legs of `hook` (start, T1) and `hook_b` (end, T3;
    `hook` when None) m kept inside the outline."""
    zs = [p[1] for p in inner]
    r = diameter / 2.0
    if layer == BOTTOM:
        z, sign = min(zs) + r + level * diameter, 1.0
    else:
        z, sign = max(zs) - r - level * diameter, -1.0
    paths = []
    for u0, u1 in polygon_spans(inner, z + sign * 1e-4):
        a, b = u0 + r, u1 - r
        if b - a < 0.05:
            continue
        room = max(zs) - min(zs) - diameter
        ha = min(hook, room) if hook > 0 else 0.0
        hb = hook if hook_b is None else hook_b
        hb = min(hb, room) if hb > 0 else 0.0
        path = [(a, z), (b, z)]
        if ha > 0.05:
            path.insert(0, (a, z + sign * ha))
        if hb > 0.05:
            path.append((b, z + sign * hb))
        paths.append(path)
    return paths


def bar_positions(lo, hi, spacing):
    """Positions from lo to hi at `spacing`, the leftover split at both
    ends (bars centered in the element)."""
    if hi - lo < 1e-6 or spacing <= 0:
        return [(lo + hi) / 2.0]
    n = int((hi - lo) / spacing + 1e-6) + 1
    start = lo + (hi - lo - (n - 1) * spacing) / 2.0
    return [start + k * spacing for k in range(n)]


SPACING = u"Espaciado"
QUANTITY = u"Cantidad"
BOTH = u"Ambos"
DIST_MODES = (SPACING, QUANTITY, BOTH)


def distribute(lo, hi, mode, spacing, count):
    """Bar positions between lo and hi: SPACING - every `spacing` m, as
    many as fit (centered); QUANTITY - `count` bars spread from lo to hi;
    BOTH - `count` bars every `spacing` m, centered (clipped to lo..hi)."""
    count = max(1, int(count or 1))
    if mode == QUANTITY:
        if count == 1 or hi - lo < 1e-6:
            return [(lo + hi) / 2.0]
        step = (hi - lo) / (count - 1)
        return [lo + k * step for k in range(count)]
    if mode == BOTH:
        mid = (lo + hi) / 2.0
        start = mid - (count - 1) * spacing / 2.0
        return [z for z in (start + k * spacing for k in range(count)) if lo - 1e-6 <= z <= hi + 1e-6]
    return bar_positions(lo, hi, spacing)


# --- Distribution zones (pure) ------------------------------------------------
# A mesh direction may be split into zones along its distribution axis,
# each with its own mode, quantity and spacing: {"a", "b", "m", "n", "s"}
# (a..b in the element's local m). Kept in the mesh settings as "zx" (the
# X bars, spread along y) and "zy" (the Y bars, spread along x).
def mesh_positions(lo, hi, settings, axis):
    """Bar positions of one mesh direction ("x" or "y") between lo and hi
    (the cover range): its zones, or the single distribution."""
    return [p for p, _ in zone_positions(lo, hi, settings, axis)]


def zone_positions(lo, hi, settings, axis):
    """[(position, zone index)] of one mesh direction (index 0 without
    zones). A bar on a shared limit is laid once: it goes to the zone
    with a quantity (its count is kept), else to the first one."""
    zones = settings.get("z" + axis) or []
    if not zones:
        return [(p, 0) for p in distribute(lo, hi, settings.get("m" + axis, SPACING),
                                           float(settings.get("s" + axis) or 0.2), settings.get("n" + axis, 1))]
    out = []
    for k, z in enumerate(sorted(zones, key=lambda z: z["a"])):
        a, b = max(lo, z["a"]), min(hi, z["b"])
        if b < a - 1e-6:
            continue
        counted = z.get("m", SPACING) != SPACING
        for p in distribute(a, b, z.get("m", SPACING), float(z.get("s") or 0.2), z.get("n", 1)):
            if out and p - out[-1][0] <= 0.03:
                if counted:
                    out[-1] = (p, k)
                continue
            out.append((p, k))
    return out


def zone_from(settings, axis, a, b):
    """A zone a..b with the direction's single distribution."""
    return {"a": round(a, 3), "b": round(b, 3), "m": settings.get("m" + axis, SPACING),
            "n": int(settings.get("n" + axis) or 1), "s": float(settings.get("s" + axis) or 0.2)}


def auto_zones(breaks, lo, hi, settings, axis):
    """Zones split where the shape changes (`breaks`, e.g. the plan
    corners along the axis), each with the single distribution; a
    quantity becomes its spacing, so every zone keeps the same density."""
    cuts = sorted(set(round(b, 3) for b in breaks if lo + 0.05 < b < hi - 0.05))
    edges = [round(lo, 3)] + cuts + [round(hi, 3)]
    template = zone_from(settings, axis, lo, hi)
    if template["m"] != SPACING:
        n = max(2, template["n"])
        template["s"] = round((hi - lo) / (n - 1), 2) if template["m"] == QUANTITY else template["s"]
        template["m"] = SPACING
    return [dict(template, a=a, b=b) for a, b in zip(edges, edges[1:])]


def split_zone(zones, at, settings, axis, lo, hi):
    """Zones with the one holding `at` split there (both halves keep its
    distribution); no zones yet: the whole lo..hi is the first one."""
    zones = [dict(z) for z in (zones or [zone_from(settings, axis, lo, hi)])]
    for k, z in enumerate(zones):
        if z["a"] + 0.05 < at < z["b"] - 0.05:
            zones[k:k + 1] = [dict(z, b=at), dict(z, a=at)]
            break
    return zones


def move_limit(zones, k, to):
    """The limit between zone k and k+1 moved to `to` (each keeps 5 cm)."""
    zones = [dict(z) for z in zones]
    to = max(zones[k]["a"] + 0.05, min(zones[k + 1]["b"] - 0.05, to))
    zones[k]["b"] = zones[k + 1]["a"] = to
    return zones


def remove_limit(zones, k):
    """The limit between zone k and k+1 taken away: zone k takes both."""
    zones = [dict(z) for z in zones]
    zones[k]["b"] = zones[k + 1]["b"]
    del zones[k + 1]
    return zones if len(zones) > 1 else []


def parse_zone_text(text):
    """(mode, count, spacing) from "15" (quantity), "@0.20" / "0.20"
    (spacing) or "15@0.20" (both); ValueError otherwise."""
    t = (text or u"").replace(u",", u".").replace(u" ", u"")
    for word in (u"barras", u"barra", u"m"):
        t = t.replace(word, u"")
    if u"@" in t:
        n, sp = t.split(u"@", 1)
        spacing = float(sp)
        if spacing <= 0.01:
            raise ValueError(text)
        if n:
            return BOTH, max(1, int(n)), spacing
        return SPACING, 1, spacing
    if u"." in t:
        spacing = float(t)
        if spacing <= 0.01:
            raise ValueError(text)
        return SPACING, 1, spacing
    return QUANTITY, max(1, int(t)), 0.2


def zone_text(mode, count, spacing):
    """The text parse_zone_text reads back."""
    if mode == QUANTITY:
        return u"{}".format(int(count))
    if mode == BOTH:
        return u"{}@{:g}".format(int(count), spacing)
    return u"@{:g}".format(spacing)


def snap_to_outline(p, outline, tol):
    """The point of the outline nearest to p when within tol (m), else p."""
    best = None
    n = len(outline)
    for i in range(n):
        (ax, az), (bx, bz) = outline[i], outline[(i + 1) % n]
        dx, dz = bx - ax, bz - az
        length2 = dx * dx + dz * dz or 1e-12
        t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - az) * dz) / length2))
        q = (ax + dx * t, az + dz * t)
        d = math.hypot(q[0] - p[0], q[1] - p[1])
        if d < tol and (best is None or d < best[0]):
            best = (d, q)
    return best[1] if best else p


def point_inside(points, p):
    return any(u0 <= p[0] <= u1 for u0, u1 in polygon_spans(points, p[1]))


# --- Bars of a foundation (Revit geometry) -------------------------------------
def default_steel():
    """The steel settings of a foundation type ("EA_Cim_Acero")."""
    mesh = {"on": True, "dx": u'1/2"', "sx": 0.20, "dy": u'1/2"', "sy": 0.20, "hook": 0.25,
            "hook_a": 0.25, "hook_b": 0.25, "tx": u"", "ty": u"",
            "mx": SPACING, "nx": 10, "my": SPACING, "ny": 10}
    top = dict(mesh, on=False)
    return {BOTTOM: mesh, TOP: top, "sketch": []}


class BarPlanner(object):
    """Bars of one foundation from its covers ({face: cm}) and steel
    settings: [(view, position, path [(u, z)], diameter key)]; a FRONT
    bar lies in the plane y = position (u = x), a SIDE one in x = position
    (u = -y)."""

    def __init__(self, foundation, covers, diameters_mm):
        self.f = foundation
        self.covers = covers
        self.mm = diameters_mm
        self._sections = {}

    def outlines(self, view, pos):
        key = (view, round(pos, 4))
        if key not in self._sections:
            found = []
            for pts, tags in self.f.section(view, pos):
                c = [self.covers.get(t, DEFAULT_COVER_CM) / 100.0 if t else 0.0 for t in tags]
                found.append((pts, inner_outline(pts, c)))
            self._sections[key] = found
        return self._sections[key]

    def _range(self, view, d):
        x0, x1, y0, y1, _, _ = self.f.extent
        side = max([v for k, v in self.covers.items()] + [DEFAULT_COVER_CM]) / 100.0
        lo, hi = (y0, y1) if view == FRONT else (x0, x1)
        return lo + side + d / 2.0, hi - side - d / 2.0

    def mesh(self, layer, settings):
        bars = []
        if not settings.get("on"):
            return bars
        for view, dkey, skey, level in ((FRONT, "dx", "sx", 0), (SIDE, "dy", "sy", 1)):
            axis = skey[1]
            key = settings[dkey]
            d = self.mm[key] / 1000.0
            lo, hi = self._range(view, d)
            # the second direction lies on the first: one first-direction bar further in
            level_d = self.mm[settings["dx"]] / 1000.0 if level else 0.0
            for pos in mesh_positions(lo, hi, settings, axis):
                for _, inner in self.outlines(view, pos):
                    zs = [p[1] for p in inner]
                    ha = float(settings.get("hook_a", settings.get("hook")) or 0.0)
                    hb = float(settings.get("hook_b", settings.get("hook")) or 0.0)
                    for path in mesh_layer_paths(inner, layer, d, ha, level=0, hook_b=hb):
                        if level:
                            shift = level_d if layer == BOTTOM else -level_d
                            path = [(u, z + shift) for u, z in path]
                        bars.append((view, pos, path, key, settings.get("t" + axis) or u""))
        return bars

    def sketch(self, item):
        view, key = item["view"], item["d"]
        d = self.mm[key] / 1000.0
        pts = [tuple(p) for p in item["pts"]]
        if item.get("closed"):
            pts = pts + [pts[0]]  # a closed stirrup: back to its start
        lo, hi = self._range(view, d)
        bars = []
        for pos in distribute(lo, hi, item.get("m", SPACING), float(item["s"]), item.get("n", 1)):
            for outer, inner in self.outlines(view, pos):
                if all(point_inside(outer, p) for p in pts):
                    bars.append((view, pos, pts, key, item.get("t") or u""))
                    break
        return bars

    def all_bars(self, steel, splice=None):
        """Every bar; with `splice` ({"max", "laps": {key: m}}) the open
        ones longer than the maximum cut into lapped pieces."""
        bars = self.mesh(BOTTOM, steel.get(BOTTOM, {})) + self.mesh(TOP, steel.get(TOP, {}))
        if steel.get("sketch_on", True):
            for item in steel.get("sketch", []):
                bars += self.sketch(item)
        if not splice:
            return bars
        out = []
        for view, pos, path, key, tname in bars:
            lap = splice["laps"].get(key)
            closed = len(path) > 2 and path[0] == path[-1]
            if lap is None or closed:
                out.append((view, pos, path, key, tname))
            else:
                out += [(view, pos, piece, key, tname) for piece in split_path(path, splice["max"], lap)]
        return out


def bar_sets(bars):
    """Bars grouped into Revit sets: same view, diameter, bar type and
    path, at consecutive evenly spaced positions ->
    [(view, key, path, first, count, spacing, type name)]."""
    groups = {}
    for view, pos, path, key, tname in bars:
        sig = (view, key, tname, tuple((round(u, 3), round(z, 3)) for u, z in path))
        groups.setdefault(sig, []).append(pos)
    sets = []
    for (view, key, tname, path), positions in groups.items():
        positions.sort()
        run = [positions[0]]
        for p in positions[1:]:
            if len(run) > 1 and abs((p - run[-1]) - (run[1] - run[0])) > 1e-3:
                sets.append((view, key, list(path), run[0], len(run), run[1] - run[0], tname))
                run = [p]
            else:
                run.append(p)
        sets.append((view, key, list(path), run[0], len(run), (run[1] - run[0]) if len(run) > 1 else 0.0, tname))
    return sets


# --- Splices and segment edits (pure) ----------------------------------------
def path_length(path):
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(path, path[1:]))


def sub_path(path, s0, s1):
    """The part of the polyline between arc lengths s0 and s1."""
    out, walked = [], 0.0
    for a, b in zip(path, path[1:]):
        seg = math.hypot(b[0] - a[0], b[1] - a[1])
        lo, hi = walked, walked + seg
        if hi >= s0 - 1e-9 and lo <= s1 + 1e-9 and seg > 1e-12:
            t0 = max(0.0, (s0 - lo) / seg)
            t1 = min(1.0, (s1 - lo) / seg)
            p = (a[0] + (b[0] - a[0]) * t0, a[1] + (b[1] - a[1]) * t0)
            q = (a[0] + (b[0] - a[0]) * t1, a[1] + (b[1] - a[1]) * t1)
            if not out:
                out.append(p)
            if math.hypot(q[0] - out[-1][0], q[1] - out[-1][1]) > 1e-6:
                out.append(q)
        walked = hi
    return out


def split_path(path, max_length, lap):
    """A bar longer than `max_length` cut into pieces of at most that
    length, each lapping the next by `lap` (m): [path...]."""
    total = path_length(path)
    if max_length <= 0 or total <= max_length + 1e-6 or lap >= max_length:
        return [path]
    pieces, start = [], 0.0
    while total - start > max_length + 1e-6:
        pieces.append(sub_path(path, start, start + max_length))
        start += max_length - lap
    pieces.append(sub_path(path, start, total))
    return pieces


def set_segment_length(points, index, length):
    """Points with segment `index` (points[index] -> points[index+1]) made
    `length` m long. The first and the last segment grow at their free
    end (a leg grows away from the bar: down from a top bar, up from a
    bottom one); a middle one moves the points after it."""
    a, b = points[index], points[index + 1]
    old = math.hypot(b[0] - a[0], b[1] - a[1])
    if old < 1e-9 or length <= 0:
        return list(points)
    ux, uz = (b[0] - a[0]) / old, (b[1] - a[1]) / old
    dx, dz = ux * (length - old), uz * (length - old)
    pts = list(points)
    if index == 0 and len(points) > 2:
        pts[0] = (a[0] - dx, a[1] - dz)
        return pts
    return [p if k <= index else (p[0] + dx, p[1] + dz) for k, p in enumerate(points)]


def clamp_inside(p, inner):
    """p, or the nearest point of the cover outline when p is outside it."""
    if point_inside(inner, p):
        return p
    return snap_to_outline(p, inner, 1e9)


# --- Elements touching the foundation (Revit geometry) -------------------------
# NEIGHBOR_CATEGORIES: see the top of the module


def neighbors(foundation, reach_m=0.6, contact_m=0.05):
    """Triangles (local) of the elements touching the foundation, cut to
    `reach_m` around it: [{"label", "triangles"}]."""
    element = foundation.element
    bb = element.get_BoundingBox(None)
    touch = contact_m / FT
    near = DB.Outline(DB.XYZ(bb.Min.X - touch, bb.Min.Y - touch, bb.Min.Z - touch),
                      DB.XYZ(bb.Max.X + touch, bb.Max.Y + touch, bb.Max.Z + touch))
    x0, x1, y0, y1, z0, z1 = foundation.extent
    corners = [foundation.world(x, y, z0 - reach_m) for x, y in
               ((x0 - reach_m, y0 - reach_m), (x1 + reach_m, y0 - reach_m),
                (x1 + reach_m, y1 + reach_m), (x0 - reach_m, y1 + reach_m))]
    loop = DB.CurveLoop()
    for k in range(4):
        loop.Append(DB.Line.CreateBound(corners[k], corners[(k + 1) % 4]))
    crop = DB.GeometryCreationUtilities.CreateExtrusionGeometry(
        List[DB.CurveLoop]([loop]), DB.XYZ.BasisZ, (z1 - z0 + 2 * reach_m) / FT)
    doc = element.Document
    result = []
    for bic, label in NEIGHBOR_CATEGORIES:
        found = (DB.FilteredElementCollector(doc).OfCategory(bic).WhereElementIsNotElementType()
                 .WherePasses(DB.BoundingBoxIntersectsFilter(near)))
        for other in found:
            if other.Id == element.Id:
                continue
            triangles = []
            for solid in _solids(other):
                try:
                    part = DB.BooleanOperationsUtils.ExecuteBooleanOperation(
                        solid, crop, DB.BooleanOperationsType.Intersect)
                except Exception:
                    continue
                if part is None or part.Volume < 1e-6:
                    continue
                for face in part.Faces:
                    mesh = face.Triangulate()
                    for i in range(mesh.NumTriangles):
                        tri = mesh.get_Triangle(i)
                        triangles.append(tuple(foundation.local(tri.get_Vertex(k)) for k in range(3)))
            if triangles:
                result.append({"label": label, "triangles": triangles})
    return result


def bar_points_3d(view, pos, path):
    """A bar's local 3D points from its section path."""
    if view == FRONT:
        return [(u, pos, z) for u, z in path]
    return [(pos, -u, z) for u, z in path]


def plan_outline(foundation):
    """The foundation's outline in plan: the edges [((x, y), (x, y))] of
    its bottom faces that belong to one triangle only."""
    count = {}
    for face in foundation.faces:
        if face.normal[2] > -0.9:
            continue
        for tri in face.triangles:
            for k in range(3):
                a, b = tri[k], tri[(k + 1) % 3]
                ka, kb = (round(a[0], 3), round(a[1], 3)), (round(b[0], 3), round(b[1], 3))
                edge = (ka, kb) if ka <= kb else (kb, ka)
                count[edge] = count.get(edge, 0) + 1
    edges = [e for e, n in count.items() if n == 1]
    return merge_collinear(edges)


def merge_collinear(edges, tol=1e-3):
    """Edges [((x, y), (x, y))] with the collinear ones sharing an end
    joined: a triangulated face splits a straight side in pieces."""
    edges = [tuple(e) for e in edges]
    merged = True
    while merged:
        merged = False
        for i in range(len(edges)):
            for j in range(i + 1, len(edges)):
                a, b = edges[i]
                c, d = edges[j]
                shared = set([a, b]) & set([c, d])
                if len(shared) != 1:
                    continue
                m = shared.pop()
                p = a if b == m else b
                q = c if d == m else d
                cross = (m[0] - p[0]) * (q[1] - p[1]) - (m[1] - p[1]) * (q[0] - p[0])
                if abs(cross) < tol:
                    edges[i] = (p, q)
                    del edges[j]
                    merged = True
                    break
            if merged:
                break
    return edges
