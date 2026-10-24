# -*- coding: utf-8 -*-
"""Revit side of the two-way roof steel (roof_rebar): finds the slab on the
other side of a ridge, builds the roof's frame - s down / up the slope from
the ridge axis (side A negative), t along the ridge - and the profile of
each vigueta, and creates its bars (rebar sets along the ridge).

Meters; world plan coordinates."""
import math

from pyrevit import DB
from System.Collections.Generic import List

import roof_rebar as rr
import slab_rebar as sr
import slab_fix as sf

FT = 0.3048


def _plane(floor):
    tops = sf._top_faces(floor)
    if not tops:
        return None
    f = max(tops, key=lambda q: q.Area)
    return f.FaceNormal, f.Origin


def _height(plane, x, y):
    """Height (m) of a plane over the plan point (x, y) m."""
    n, o = plane
    return (o.Z - (n.X * (x / FT - o.X) + n.Y * (y / FT - o.Y)) / n.Z) * FT


def _top_tris(floor):
    out = []
    for f in sf._top_faces(floor):
        mesh = f.Triangulate()
        for i in range(mesh.NumTriangles if mesh else 0):
            t = mesh.get_Triangle(i)
            out.append(tuple((t.get_Vertex(k).X * FT, t.get_Vertex(k).Y * FT) for k in range(3)))
    return out


def high_edge(floor, tol=0.03):
    """(the top's highest points in plan, their height) - the ridge side."""
    pts = []
    for f in sf._top_faces(floor):
        for loop in f.EdgeLoops:
            for e in loop:
                for p in e.Tessellate():
                    pts.append((p.X * FT, p.Y * FT, p.Z * FT))
    if not pts:
        return [], 0.0
    zmax = max(p[2] for p in pts)
    return [(p[0], p[1]) for p in pts if p[2] > zmax - tol], zmax


def _overlap(ea, eb, r):
    """Shared length along the ridge direction r of two high edges (m)."""
    if not ea or not eb:
        return 0.0
    pa = [x * r[0] + y * r[1] for x, y in ea]
    pb = [x * r[0] + y * r[1] for x, y in eb]
    return min(max(pa), max(pb)) - max(min(pa), min(pb))


def partner(floor, reach=1.0):
    """The sloped floor across the ridge: its top climbs the other way to the
    same ridge - their high edges run side by side (sharing most of their
    length along it, within `reach` m in plan). None if there is none."""
    pa = _plane(floor)
    if pa is None or abs(pa[0].Z) > 0.999:
        return None
    na = pa[0]
    L = math.hypot(na.X, na.Y)
    r = (na.Y / L, -na.X / L)
    edge_a, z_a = high_edge(floor)
    span_a = _overlap(edge_a, edge_a, r)
    bb = floor.get_BoundingBox(None)
    pad = reach / FT
    near = DB.Outline(DB.XYZ(bb.Min.X - pad, bb.Min.Y - pad, bb.Min.Z - pad), DB.XYZ(bb.Max.X + pad, bb.Max.Y + pad, bb.Max.Z + pad))
    best = None
    for other in DB.FilteredElementCollector(floor.Document).OfClass(DB.Floor).WhereElementIsNotElementType() \
            .WherePasses(DB.BoundingBoxIntersectsFilter(near)):
        if other.Id == floor.Id:
            continue
        pb = _plane(other)
        if pb is None or abs(pb[0].Z) > 0.999:
            continue
        nb = pb[0]
        # climbing the other way: horizontal parts of the normals opposite
        if na.X * nb.X + na.Y * nb.Y > -0.5 * math.hypot(na.X, na.Y) * math.hypot(nb.X, nb.Y):
            continue
        edge_b, z_b = high_edge(other)
        if abs(z_b - z_a) > 0.3:
            continue
        shared = _overlap(edge_a, edge_b, r)
        span_b = _overlap(edge_b, edge_b, r)
        if shared < 0.5 * min(span_a, span_b) or shared < 0.5:
            continue  # not the same ridge (a strip beside it, a neighbour)
        # the two high edges side by side in plan (the ridge beam between)
        gap = min(math.hypot(xa - xb, ya - yb) for xa, ya in edge_a[::max(1, len(edge_a) // 8)]
                  for xb, yb in edge_b[::max(1, len(edge_b) // 8)])
        if gap > reach + 0.5 * span_a:
            continue
        score = shared - gap
        if best is None or score > best[0]:
            best = (score, other)
    return best[1] if best else None


class RoofModel(object):
    """Two sloped floors meeting at a ridge: floor_a's side is s < 0."""

    def __init__(self, floor_a, floor_b, models=None):
        self.a, self.b = floor_a, floor_b
        self.pa, self.pb = _plane(floor_a), _plane(floor_b)
        na = self.pa[0]
        L = math.hypot(na.X, na.Y)
        self.d = (-na.X / L, -na.Y / L)  # A's climb, towards the ridge and on into B
        self.r = (-self.d[1], self.d[0])  # along the ridge
        # the ridge: where the two planes meet, on the line through A's middle along d
        bb = floor_a.get_BoundingBox(None)
        cx, cy = (bb.Min.X + bb.Max.X) / 2.0 * FT, (bb.Min.Y + bb.Max.Y) / 2.0 * FT
        f = lambda k: _height(self.pa, cx + self.d[0] * k, cy + self.d[1] * k) - _height(self.pb, cx + self.d[0] * k, cy + self.d[1] * k)
        lo, hi = -50.0, 50.0
        for _ in range(80):
            mid = (lo + hi) / 2.0
            if (f(lo) > 0) == (f(mid) > 0):
                lo = mid
            else:
                hi = mid
        k = (lo + hi) / 2.0
        self.o = (cx + self.d[0] * k, cy + self.d[1] * k)
        self.t_a = sf.thickness_across(floor_a)[0] or 0.2
        self.t_b = sf.thickness_across(floor_b)[0] or 0.2
        self.models = models or {}
        self.tris = [self._to_frame_tri(t) for t in _top_tris(floor_a) + _top_tris(floor_b)]
        self.support_tris = []
        for m in self.models.values():
            for tri in m.support_tris:
                self.support_tris.append(self._to_frame_tri(tuple(self._local_to_world(m, p) for p in tri)))

    @staticmethod
    def _local_to_world(model, p):
        w = model.f.world(p[0], p[1], 0.0)
        return (w.X * FT, w.Y * FT)

    def to_frame(self, x, y):
        dx, dy = x - self.o[0], y - self.o[1]
        return dx * self.d[0] + dy * self.d[1], dx * self.r[0] + dy * self.r[1]

    def to_world(self, s, t):
        return self.o[0] + self.d[0] * s + self.r[0] * t, self.o[1] + self.d[1] * s + self.r[1] * t

    def _to_frame_tri(self, tri):
        return tuple(self.to_frame(x, y) for x, y in tri)

    def z_top(self, s, t):
        x, y = self.to_world(s, t)
        return min(_height(self.pa, x, y), _height(self.pb, x, y))  # the roof: the lower of the two planes

    def vigueta_positions(self, settings):
        """t of the viguetas: the ribbed layout across the roof (as A's)."""
        rb = sr.ribbed(settings)
        out = []
        for bay in sr.bays(self.tris, self.support_tris, sr.X):
            cs, _ = sr.rib_layout(bay, rb["b"], rb["s"], 0.30, float(rb.get("start") or 0.0))
            out += cs
        return sorted(set(round(c, 4) for c in out))

    def profile(self, t):
        """The roof's Profile along the vigueta at t (None where it isn't)."""
        chords = sr.line_intervals(self.tris, sr.X, t)
        if not chords:
            return None
        s0, s1 = min(c[0] for c in chords), max(c[1] for c in chords)
        if not (s0 < 0.0 < s1):
            return None
        top = [(s0, self.z_top(s0, t)), (0.0, self.z_top(0.0, t)), (s1, self.z_top(s1, t))]
        sup = sr.line_intervals(self.support_tris, sr.X, t)
        return rr.Profile(top, self.t_a, self.t_b, sup)

    def plan(self, settings, mm):
        """{t: bars} of every vigueta."""
        roof = (settings or {}).get("roof") or {}
        roof = dict(roof)
        roof.setdefault("cover", settings.get("cover") or 2.0)
        out = {}
        for t in self.vigueta_positions(settings):
            p = self.profile(t)
            if p is not None:
                out[t] = (p, rr.plan_roof(p, roof, mm))
        return out

    def generate(self, settings, mm, bar_type_for, tag=None):
        """Create the roof's vigueta bars (inside an open Transaction): equal
        bars side by side as one set along the ridge. Returns [rebar]."""
        from Autodesk.Revit.DB.Structure import Rebar, RebarStyle, RebarHookOrientation
        doc = self.a.Document
        groups = {}
        order = []
        for t, (p, bars) in sorted(self.plan(settings, mm).items()):
            for bar in bars:
                sig = (bar["name"], bar["key"], tuple((round(s, 3), round(z, 3)) for s, z in bar["path"]))
                if sig not in groups:
                    groups[sig] = []
                    order.append(sig)
                groups[sig].append(t + bar["shift"])
        normal = DB.XYZ(self.r[0], self.r[1], 0.0)
        made = []
        for sig in order:
            name, key, path = sig
            ts = sorted(groups[sig])
            runs, run = [], [ts[0]]
            for v in ts[1:]:
                if len(run) >= 2 and abs((v - run[-1]) - (run[1] - run[0])) > 1e-3:
                    runs.append(run)
                    run = []
                run.append(v)
            runs.append(run)
            mid_s = sum(s for s, _ in path) / len(path)
            host = self.a if mid_s < 0 else self.b
            for run in runs:
                t0 = run[0]
                pts = []
                for s, z in path:
                    x, y = self.to_world(s, t0)
                    pts.append(DB.XYZ(x / FT, y / FT, z / FT))
                curves = List[DB.Curve]([DB.Line.CreateBound(pts[k], pts[k + 1]) for k in range(len(pts) - 1)
                                         if pts[k].DistanceTo(pts[k + 1]) > 1e-4])
                rebar = Rebar.CreateFromCurves(doc, RebarStyle.Standard, bar_type_for(key), None, None, host, normal, curves,
                                               RebarHookOrientation.Right, RebarHookOrientation.Right, True, True)
                if len(run) > 1:
                    rebar.GetShapeDrivenAccessor().SetLayoutAsNumberWithSpacing(len(run), (run[1] - run[0]) / FT, True, True, True)
                if tag:
                    tag(rebar, host)
                made.append(rebar)
        return made
