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

    def section(self, view, at=0.0):
        """[(points [(u, z)...], [face number per edge])] outlines of the
        element cut by the plane through `at` (m, along y for FRONT, along
        x for SIDE): u is x for FRONT and -y for SIDE (seen from +x... the
        right side), z up."""
        x0, x1, y0, y1, z0, z1 = self.extent
        pad, thin = 0.5, 0.001
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
        for solid in self.solids:
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
                        f = self.face_at(mid)
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
