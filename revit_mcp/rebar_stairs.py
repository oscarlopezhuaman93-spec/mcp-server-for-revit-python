# -*- coding: utf-8 -*-
"""Rebar of cast-in-place stairs for the "Acero Escalera" window.

A stair is read as its "tramos": the straight pieces of its runs' walking
path. Each tramo is cut by the vertical plane along its axis: the cut
(s along the axis from the tramo start, z up, m) holds the steps, the
waist (garganta) and the landings it runs into. Its bars:

- inferior: along the soffit (the cut's lower boundary), one cover up;
- superior (bastones): along the top of the waist, at the ends and at
  every bend of the soffit, `neg` m each way;
- temperatura: across the tramo, on the inferior bars, every `s` m;
- pasos: across the tramo, one in the nose of every step.

Each longitudinal bar end runs into what it touches (a footing below:
down into it with a foot; a wall / beam / slab beyond: straight into it)
or, when free, ends with a 90 degree leg towards the other face. Pure
helpers first (tested in tests/unit/test_rebar_stairs_pure.py), the
Revit reading at the end.
"""
import math

INF = u"inferior"
SUP = u"superior"
TEMP = u"temperatura"
STEP = u"pasos"
GROUPS = (INF, SUP, TEMP, STEP)


def default_settings():
    """The stair's settings ("EA_Esc_Acero", JSON on the instance)."""
    return {
        "cover": 2.5,  # cm
        INF: {"on": True, "d": u'1/2"', "s": 0.20, "t": u""},
        SUP: {"on": True, "d": u'3/8"', "s": 0.20, "t": u"", "neg": None},
        TEMP: {"on": True, "d": u'3/8"', "s": 0.25, "t": u""},
        STEP: {"on": False, "d": u'3/8"', "t": u""},
        "ends": {},  # "k:start" / "k:end" -> {"anc": m, "leg": m} edited values
        "off": [],  # tramos left without steel
    }


# --- Profile (pure) -------------------------------------------------------------
def _area(poly):
    n = len(poly)
    return sum(poly[i][0] * poly[(i + 1) % n][1] - poly[(i + 1) % n][0] * poly[i][1] for i in range(n)) / 2.0


def soffit(poly):
    """The lower boundary of a cut [(s, z)]: its downward-facing edges
    chained by s -> polyline [(s, z)] from the lowest s."""
    sign = 1.0 if _area(poly) > 0 else -1.0
    n = len(poly)
    edges = []
    for i in range(n):
        (a, b), (c, d) = poly[i], poly[(i + 1) % n]
        ds, dz = c - a, d - b
        length = math.hypot(ds, dz)
        if length < 1e-6:
            continue
        nz = -ds / length * sign  # outward normal's z (outside on the right of a ccw loop)
        if nz < -0.2:
            edges.append(((a, b), (c, d)) if a < c else ((c, d), (a, b)))
    edges.sort()
    path = []
    for p, q in edges:
        if not path:
            path = [p, q]
        elif abs(p[0] - path[-1][0]) < 1e-3 and abs(p[1] - path[-1][1]) < 1e-3:
            path.append(q)
        elif p[0] >= path[-1][0] - 1e-3:
            path += [p, q]  # a gap (a step underneath): joined straight
    return merge_straight(path)


def merge_straight(path, tol=1e-4):
    """The polyline without its middle points on a straight line."""
    out = []
    for p in path:
        if out and math.hypot(p[0] - out[-1][0], p[1] - out[-1][1]) < 1e-6:
            continue
        if len(out) >= 2:
            a, b = out[-2], out[-1]
            cross = (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])
            if abs(cross) < tol:
                out[-1] = p
                continue
        out.append(p)
    return out


def offset_up(path, dist):
    """The polyline moved `dist` m to its upper side (left of +s)."""
    lines = []
    for a, b in zip(path, path[1:]):
        ds, dz = b[0] - a[0], b[1] - a[1]
        length = math.hypot(ds, dz) or 1.0
        ns, nz = -dz / length, ds / length
        lines.append(((a[0] + ns * dist, a[1] + nz * dist), (b[0] + ns * dist, b[1] + nz * dist)))
    out = [lines[0][0]]
    for (p, q), (r, t) in zip(lines, lines[1:]):
        d1 = (q[0] - p[0], q[1] - p[1])
        d2 = (t[0] - r[0], t[1] - r[1])
        det = d1[0] * d2[1] - d1[1] * d2[0]
        if abs(det) < 1e-12:
            out.append(q)
            continue
        k = ((r[0] - p[0]) * d2[1] - (r[1] - p[1]) * d2[0]) / det
        out.append((p[0] + d1[0] * k, p[1] + d1[1] * k))
    out.append(lines[-1][1])
    return out


def clip_s(path, s0, s1):
    """The part of a polyline (s increasing) between s0 and s1."""
    out = []
    for a, b in zip(path, path[1:]):
        lo, hi = a[0], b[0]
        if hi < s0 - 1e-9 or lo > s1 + 1e-9:
            continue
        def at(s):
            if abs(hi - lo) < 1e-12:
                return a
            t = (s - lo) / (hi - lo)
            return (s, a[1] + (b[1] - a[1]) * t)
        p = at(max(lo, s0)) if lo < s0 else a
        q = at(min(hi, s1)) if hi > s1 else b
        if not out:
            out.append(p)
        elif math.hypot(p[0] - out[-1][0], p[1] - out[-1][1]) > 1e-6:
            out.append(p)
        out.append(q)
    return merge_straight(out)


def path_length(path):
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(path, path[1:]))


def sub_path(path, l0, l1):
    """The part of the polyline between developed lengths l0 and l1."""
    out, walked = [], 0.0
    for a, b in zip(path, path[1:]):
        seg = math.hypot(b[0] - a[0], b[1] - a[1])
        lo, hi = walked, walked + seg
        if hi >= l0 - 1e-9 and lo <= l1 + 1e-9 and seg > 1e-12:
            t0 = max(0.0, (l0 - lo) / seg)
            t1 = min(1.0, (l1 - lo) / seg)
            p = (a[0] + (b[0] - a[0]) * t0, a[1] + (b[1] - a[1]) * t0)
            q = (a[0] + (b[0] - a[0]) * t1, a[1] + (b[1] - a[1]) * t1)
            if not out:
                out.append(p)
            if math.hypot(q[0] - out[-1][0], q[1] - out[-1][1]) > 1e-6:
                out.append(q)
        walked = hi
    return out


def bends(path, min_deg=5.0):
    """Developed lengths of the polyline's bends (direction change over
    `min_deg`)."""
    out, walked = [], 0.0
    for a, b, c in zip(path, path[1:], path[2:]):
        walked += math.hypot(b[0] - a[0], b[1] - a[1])
        u = math.atan2(b[1] - a[1], b[0] - a[0])
        v = math.atan2(c[1] - b[1], c[0] - b[0])
        turn = abs((v - u + math.pi) % (2 * math.pi) - math.pi)
        if math.degrees(turn) > min_deg:
            out.append(walked)
    return out


def noses(poly, min_step=0.05):
    """The step noses of a cut [(s, z)]: convex corners between a vertical
    riser below and a level tread on its +s side (the stair going up
    with s), sorted by s."""
    sign = 1.0 if _area(poly) > 0 else -1.0
    n = len(poly)
    out = []
    for i in range(n):
        p0, p, p1 = poly[i - 1], poly[i], poly[(i + 1) % n]
        e1 = (p[0] - p0[0], p[1] - p0[1])
        e2 = (p1[0] - p[0], p1[1] - p[1])
        if math.hypot(*e1) < min_step or math.hypot(*e2) < min_step:
            continue
        convex = (e1[0] * e2[1] - e1[1] * e2[0]) * sign > 0
        if not convex:
            continue
        # one edge vertical, the other level
        vert = [e for e in (e1, e2) if abs(e[0]) < 1e-3]
        level = [e for e in (e1, e2) if abs(e[1]) < 1e-3]
        if len(vert) != 1 or len(level) != 1:
            continue
        # the riser lies below the corner, the tread on its +s side
        riser_end = p0 if vert[0] is e1 else p1
        tread_end = p1 if level[0] is e2 else p0
        if riser_end[1] < p[1] - 1e-3 and tread_end[0] > p[0] + 1e-3:
            out.append(p)
    return sorted(out)


def ends_of(path):
    """(start point, unit direction going out), (end point, direction out)."""
    def unit(a, b):
        d = math.hypot(b[0] - a[0], b[1] - a[1]) or 1.0
        return ((b[0] - a[0]) / d, (b[1] - a[1]) / d)
    return (path[0], unit(path[1], path[0])), (path[-1], unit(path[-2], path[-1]))


def with_ends(path, start=None, end=None):
    """The bar path with its end pieces: each a list of points added
    beyond that end (start ones listed going away from the bar)."""
    pts = list(path)
    if start:
        pts = list(reversed(start)) + pts
    if end:
        pts = pts + list(end)
    return merge_straight(pts)


HOOK_STRAIGHT = u"recto"
HOOK_90 = u"90"
HOOK_180 = u"180"
HOOK_NAMES = ((HOOK_STRAIGHT, u"Recto (solo longitud)"), (HOOK_90, u"Doblez 90° (pata)"),
              (HOOK_180, u"Gancho 180°"))


def hook_180(diameter_m):
    """(bend diameter, straight return) of a 180-degree hook, E.060 7.1 /
    7.2: return 4 db, at least 0.065 m; bend 8 db here (room for Revit's
    own bend radius)."""
    return 8 * diameter_m, max(0.065, 4 * diameter_m)


def end_piece(point, out_dir, kind, anchor, leg, up, hook=HOOK_90, diameter=0.0127):
    """Points added beyond a bar end. kind: "down" - into a support below
    (anchor m down, then the hook going out), "beyond" - straight on into
    what it meets (anchor m) then the hook towards the other face,
    "free" - only the hook towards the other face. hook: HOOK_STRAIGHT
    (none), HOOK_90 (a leg of `leg` m) or HOOK_180 (turned back). `up`:
    the other face is above (an inferior bar)."""
    x, z = point
    pts = []
    if kind == "down":
        if anchor <= 0.01:
            return pts
        x, z = x, z - anchor
        pts.append((x, z))
        last = (0.0, -1.0)
        side = (1.0 if out_dir[0] >= 0 else -1.0, 0.0)  # the foot goes out
    else:
        if kind == "beyond" and anchor > 0.01:
            x, z = x + out_dir[0] * anchor, z + out_dir[1] * anchor
            pts.append((x, z))
        last = out_dir
        side = (0.0, 1.0 if up else -1.0)
    if hook == HOOK_90 and leg > 0.01:
        pts.append((x + side[0] * leg, z + side[1] * leg))
    elif hook == HOOK_180:
        bend, back = hook_180(diameter)
        a = (x + side[0] * bend, z + side[1] * bend)
        pts += [a, (a[0] - last[0] * back, a[1] - last[1] * back)]
    return pts


def hook_leg(diameter_m):
    """90-degree hook leg, E.060: 12 bar diameters, at least 0.10 m."""
    return max(0.10, round(12 * diameter_m + 0.004, 2))


def hooked_anchorage(diameter_m):
    """Development length of a hooked bar in tension, E.060 12.5 with
    f'c 210 and fy 4200 (0.075 fy / sqrt(f'c) db ~ 22 db), at least 0.15 m."""
    return max(0.15, round(22 * diameter_m + 0.004, 2))


def stations(path, spacing, margin):
    """Points every `spacing` m along the polyline's developed length,
    `margin` m from each end, centered: [(length, (s, z), piece index)]."""
    total = path_length(path)
    usable = total - 2 * margin
    if usable < 0:
        return []
    n = int(usable / spacing + 1e-6) + 1
    start = margin + (usable - (n - 1) * spacing) / 2.0
    out = []
    for k in range(n):
        l = start + k * spacing
        walked = 0.0
        for i, (a, b) in enumerate(zip(path, path[1:])):
            seg = math.hypot(b[0] - a[0], b[1] - a[1])
            if l <= walked + seg + 1e-9 or i == len(path) - 2:
                t = 0.0 if seg < 1e-12 else min(1.0, (l - walked) / seg)
                out.append((l, (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t), i))
                break
            walked += seg
    return out


def point_inside(points, p):
    """Even-odd test of p in the polygon [(u, z)]."""
    inside = False
    n = len(points)
    for i in range(n):
        (a, b), (c, d) = points[i], points[(i + 1) % n]
        if (b > p[1]) != (d > p[1]):
            x = a + (p[1] - b) * (c - a) / (d - b)
            if x > p[0]:
                inside = not inside
    return inside


def end_kind(point, out_dir, neighbors, probe=0.06):
    """What a bar end at `point` (going out along out_dir) meets in the
    cut: ("down", label, depth) when a neighbor lies under it, ("beyond",
    label, room) when one lies straight ahead, else ("free", None, 0).
    `neighbors`: [(label, polygon [(s, z)])]."""
    x, z = point
    for label, pts in neighbors:
        if point_inside(pts, (x, z - probe - 0.15)):
            zs = [q[1] for q in pts]
            return "down", label, max(0.0, z - min(zs))
    ahead = (x + out_dir[0] * probe * 2, z + out_dir[1] * probe * 2)
    for label, pts in neighbors:
        if point_inside(pts, ahead):
            return "beyond", label, ray_exit(pts, point, out_dir, probe * 2)
    return "free", None, 0.0


def ray_exit(poly, point, direction, past=0.0):
    """Distance from `point` along `direction` to where the ray leaves the
    polygon (its first edge crossing beyond `past` m)."""
    x, z = point
    best = None
    n = len(poly)
    for i in range(n):
        (a, b), (c, d) = poly[i], poly[(i + 1) % n]
        ex, ez = c - a, d - b
        det = direction[0] * (-ez) - direction[1] * (-ex)
        if abs(det) < 1e-12:
            continue
        rx, rz = a - x, b - z
        t = (rx * (-ez) - rz * (-ex)) / det  # along the ray
        u = (direction[0] * rz - direction[1] * rx) / det  # along the edge
        if -1e-9 <= u <= 1 + 1e-9 and t > past and (best is None or t < best):
            best = t
    return best or past


def end_defaults(kind, room, diameter_m, thickness, cover):
    """(anchor, leg) proposed for an end of this kind."""
    leg = hook_leg(diameter_m)
    if kind == "down":
        return round(max(0.0, room - 0.075 - diameter_m), 2), leg
    if kind == "beyond":
        return round(min(hooked_anchorage(diameter_m), max(0.0, room - cover)), 2), \
            round(min(leg, max(0.0, thickness - 2 * cover - diameter_m)), 2)
    return 0.0, round(max(0.0, thickness - 2 * cover - diameter_m), 2)


def plan_tramo(profile, neighbors, settings, thickness, diameters_mm, edits=None, key=0):
    """The bars of one tramo in its cut: {group: [{"path": [(s, z)],
    "kind": "long" / "cross"}]} plus "ends": {"k:start"...: (kind, label,
    anchor, leg)} (what the elevation shows and edits).
    profile: the cut polygon; thickness: the waist (m)."""
    cover = float(settings.get("cover") or 2.5) / 100.0
    edits = edits or {}
    out = {INF: [], SUP: [], TEMP: [], STEP: [], "ends": {}}
    base = soffit(profile)
    if len(base) < 2:
        return out
    s_lo = min(p[0] for p in profile)
    s_hi = max(p[0] for p in profile)

    def ends_for(path, group, d):
        up = group == INF
        pieces = []
        for which, (pt, out_dir) in zip(("start", "end"), ends_of(path)):
            # into a slab, beam or wall the bar goes on level (bent at the
            # end of a slope), as on site
            # - unless only going on along the slope reaches it (an
            # inferior bar arriving under the slab)
            level = (1.0 if out_dir[0] >= 0 else -1.0, 0.0)
            kind, label, room = end_kind(pt, level, neighbors)
            if kind == "free" and abs(out_dir[1]) > 0.05:
                kind, label, room = end_kind(pt, out_dir, neighbors)
            else:
                out_dir = level
            anc, leg = end_defaults(kind, room, d, thickness, cover)
            tag = u"{}:{}:{}".format(key, group, which)
            e = edits.get(tag) or {}
            anc = float(e.get("anc", anc))
            leg = float(e.get("leg", leg))
            # never out of the concrete it goes into
            if kind == "beyond":
                anc = min(anc, max(0.0, round(room - cover, 2)))
            elif kind == "down":
                anc = min(anc, max(0.0, round(room - 0.075, 2)))
            hook = e.get("hook") or (HOOK_90 if leg > 0.01 else HOOK_STRAIGHT)
            out["ends"][tag] = (kind, label, anc, leg, hook)
            pieces.append(end_piece(pt, out_dir, kind, anc, leg, up, hook, d))
        return pieces

    inf = settings.get(INF, {})
    d_inf = diameters_mm.get(inf.get("d"), 12.7) / 1000.0
    low = clip_s(offset_up(base, cover + d_inf / 2.0), s_lo + cover, s_hi - cover)
    if inf.get("on") and len(low) >= 2:
        a, b = ends_for(low, INF, d_inf)
        out[INF].append({"path": with_ends(low, a, b), "kind": "long", "body": low})
    sup = settings.get(SUP, {})
    d_sup = diameters_mm.get(sup.get("d"), 9.5) / 1000.0
    high = clip_s(offset_up(base, thickness - cover - d_sup / 2.0), s_lo + cover, s_hi - cover)
    if sup.get("on") and len(high) >= 2:
        total = path_length(high)
        neg = sup.get("neg")
        neg = float(neg) if neg not in (None, u"") else max(0.6, round(total / 4.0 / 0.05) * 0.05)
        out["neg"] = neg
        spans = [(0.0, neg), (total - neg, total)]
        spans += [(max(0.0, l - neg), min(total, l + neg)) for l in bends(high)]
        spans.sort()
        merged = []
        for lo, hi in spans:
            if merged and lo <= merged[-1][1] + 0.05:
                merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
            else:
                merged.append((lo, hi))
        for k, (lo, hi) in enumerate(merged):
            piece = sub_path(high, lo, hi)
            if len(piece) < 2:
                continue
            start = end = None
            ends = ends_for(piece, SUP, d_sup) if (lo < 1e-6 or hi > total - 1e-6) else ([], [])
            if lo < 1e-6:
                start = ends[0]
            else:
                start = [(piece[0][0], piece[0][1] - max(0.0, thickness - 2 * cover - d_sup))]
            if hi > total - 1e-6:
                end = ends[1]
            else:
                end = [(piece[-1][0], piece[-1][1] - max(0.0, thickness - 2 * cover - d_sup))]
            out[SUP].append({"path": with_ends(piece, start, end), "kind": "long", "body": piece})
    temp = settings.get(TEMP, {})
    if temp.get("on") and len(low) >= 2:
        d_t = diameters_mm.get(temp.get("d"), 9.5) / 1000.0
        layer = offset_up(low, (d_inf + d_t) / 2.0)
        for l, p, i in stations(layer, float(temp.get("s") or 0.25), 0.05):
            out[TEMP].append({"at": p, "kind": "cross", "piece": i})
    step = settings.get(STEP, {})
    if step.get("on"):
        d_p = diameters_mm.get(step.get("d"), 9.5) / 1000.0
        c = cover + d_p / 2.0
        for s, z in noses(profile):
            out[STEP].append({"at": (s - c, z - c), "kind": "cross", "piece": 0})
    return out


def group_cross(items, tol=1e-3):
    """Cross bars (temperatura / pasos) grouped into Revit sets: runs of
    consecutive points evenly spaced on one straight line ->
    [(first point, count, spacing, unit direction (ds, dz))]."""
    pts = [it["at"] for it in items]
    sets = []
    k = 0
    while k < len(pts):
        run = [pts[k]]
        j = k + 1
        while j < len(pts):
            a, b = run[-1], pts[j]
            step = math.hypot(b[0] - a[0], b[1] - a[1])
            if len(run) >= 2:
                p, q = run[0], run[1]
                d0 = math.hypot(q[0] - p[0], q[1] - p[1])
                cross = (q[0] - p[0]) * (b[1] - a[1]) - (q[1] - p[1]) * (b[0] - a[0])
                if abs(step - d0) > tol * 10 or abs(cross) > tol * max(d0, 1e-6) * 10:
                    break
            run.append(b)
            j += 1
        if len(run) > 1:
            d = math.hypot(run[1][0] - run[0][0], run[1][1] - run[0][1])
            direction = ((run[1][0] - run[0][0]) / d, (run[1][1] - run[0][1]) / d)
        else:
            d, direction = 0.0, (1.0, 0.0)
        sets.append((run[0], len(run), d, direction))
        k = j
    return sets


# --- Revit (geometry of the stair) -----------------------------------------------
try:
    from pyrevit import DB
    from System.Collections.Generic import List
    import rebar_foundation as rf
except Exception:  # the pure tests
    DB = None

FT = 0.3048


class Tramo(object):
    """One straight piece of a run's walking path, in the stair's local
    frame (m): start p0 (x, y), unit direction d, length, its width across
    t0..t1 (from the axis, + on the left of d) and its cut."""

    def __init__(self, index, p0, d, length):
        self.index = index
        self.p0 = p0
        self.d = d
        self.n = (-d[1], d[0])
        self.length = length
        self.t0, self.t1 = -0.6, 0.6
        self.profile = []
        self.neighbors = []

    def plan(self, s, t):
        return (self.p0[0] + self.d[0] * s + self.n[0] * t, self.p0[1] + self.d[1] * s + self.n[1] * t)

    def local3d(self, s, t, z):
        x, y = self.plan(s, t)
        return (x, y, z)


def section_along(f, p0, d, solids, s_from, s_to, thin=0.001):
    """Cut of `solids` by the vertical plane through p0 along d (local
    frame of the Foundation `f`) between s_from and s_to:
    [[(s, z)...]] outlines."""
    n = (-d[1], d[0])
    x0, x1, y0, y1, z0, z1 = f.extent
    pad = 1.0
    corners = []
    for s, t in ((s_from, -thin), (s_to, -thin), (s_to, thin), (s_from, thin)):
        corners.append(f.world(p0[0] + d[0] * s + n[0] * t, p0[1] + d[1] * s + n[1] * t, z0 - pad))
    loop = DB.CurveLoop()
    for k in range(4):
        loop.Append(DB.Line.CreateBound(corners[k], corners[(k + 1) % 4]))
    slab = DB.GeometryCreationUtilities.CreateExtrusionGeometry(
        List[DB.CurveLoop]([loop]), DB.XYZ.BasisZ, (z1 - z0 + 2 * pad) / FT)
    out = []
    for solid in solids:
        try:
            cut = DB.BooleanOperationsUtils.ExecuteBooleanOperation(solid, slab, DB.BooleanOperationsType.Intersect)
        except Exception:
            continue
        if cut is None or cut.Volume <= 0:
            continue
        for face in cut.Faces:
            if not isinstance(face, DB.PlanarFace):
                continue
            v = f._vector(face.FaceNormal)
            if v[0] * n[0] + v[1] * n[1] < 0.99:
                continue
            for edges in face.EdgeLoops:
                pts = []
                for edge in edges:
                    c = edge.AsCurveFollowingFace(face)
                    p = f.local(c.GetEndPoint(0))
                    pts.append(((p[0] - p0[0]) * d[0] + (p[1] - p0[1]) * d[1], p[2]))
                out.append(pts)
    return out


class StairModel(object):
    """A stair (Revit Stairs) read for its rebar: the Foundation frame
    (solids, faces, neighbors) and its tramos with their cuts."""

    def __init__(self, stairs):
        self.element = stairs
        self.f = rf.Foundation(stairs)
        doc = stairs.Document
        self.thickness = 0.15
        self.tramos = []
        for rid in stairs.GetStairsRuns():
            run = doc.GetElement(rid)
            rt = doc.GetElement(run.GetTypeId())
            p = rt.get_Parameter(DB.BuiltInParameter.STAIRS_RUNTYPE_STRUCTURAL_DEPTH) if rt else None
            if p is not None and p.AsDouble() > 0:
                self.thickness = p.AsDouble() * FT
            for curve in run.GetStairsPath():
                a, b = self.f.local(curve.GetEndPoint(0)), self.f.local(curve.GetEndPoint(1))
                length = math.hypot(b[0] - a[0], b[1] - a[1])
                if length < 0.2:
                    continue
                d = ((b[0] - a[0]) / length, (b[1] - a[1]) / length)
                self.tramos.append(Tramo(len(self.tramos), (a[0], a[1]), d, length))
        self.touching = self.f.touching()
        x0, x1, y0, y1, _, _ = self.f.extent
        reach = math.hypot(x1 - x0, y1 - y0) + 1.0
        for t in self.tramos:
            # the width: the cut across its middle, the span holding the axis
            mid = (t.p0[0] + t.d[0] * t.length / 2.0, t.p0[1] + t.d[1] * t.length / 2.0)
            across = section_along(self.f, mid, t.n, self.f.solids, -reach, reach)
            spans = []
            for pts in across:
                us = [q[0] for q in pts]
                spans.append((min(us), max(us)))
            holding = [sp for sp in spans if sp[0] - 1e-3 <= 0.0 <= sp[1] + 1e-3]
            if holding:
                t.t0, t.t1 = min(sp[0] for sp in holding), max(sp[1] for sp in holding)
            cuts = section_along(self.f, t.p0, t.d, self.f.solids, -reach, reach)
            if cuts:
                t.profile = max(cuts, key=lambda pts: abs(_area(pts)))
            t.neighbors = []
            for label, other in self.touching:
                for pts in section_along(self.f, t.p0, t.d, rf._solids(other), -reach, reach):
                    t.neighbors.append((label, pts))

    def world(self, tramo, s, t, z):
        x, y = tramo.plan(s, t)
        return self.f.world(x, y, z)

    def span_at(self, tramo, s, z):
        """(t0, t1) of the concrete across the tramo at station s and height
        z (the span holding its axis, or the widest one), or None."""
        key = (tramo.index, round(s, 3))
        cache = self.__dict__.setdefault("_across", {})
        if key not in cache:
            x0, x1, y0, y1, _, _ = self.f.extent
            reach = math.hypot(x1 - x0, y1 - y0) + 1.0
            cache[key] = section_along(self.f, tramo.plan(s, 0.0), tramo.n, self.f.solids, -reach, reach)
        spans = []
        for pts in cache[key]:
            n = len(pts)
            xs = []
            for i in range(n):
                (a, b), (c, d) = pts[i], pts[(i + 1) % n]
                if (b <= z < d) or (d <= z < b):
                    xs.append(a + (z - b) * (c - a) / (d - b))
            xs.sort()
            spans += [(xs[k], xs[k + 1]) for k in range(0, len(xs) - 1, 2)]
        if not spans:
            return None
        mid = (tramo.t0 + tramo.t1) / 2.0
        holding = [sp for sp in spans if sp[0] <= mid <= sp[1]]
        return holding[0] if holding else max(spans, key=lambda sp: sp[1] - sp[0])


def plan_all(model, settings, diameters_mm):
    """{tramo index: plan_tramo(...)} of the stair's active tramos."""
    off = set(settings.get("off") or [])
    return dict((t.index, plan_tramo(t.profile, t.neighbors, settings, model.thickness, diameters_mm,
                                     settings.get("ends"), key=t.index))
                for t in model.tramos if t.index not in off and t.profile)


def generate(model, settings, diameters_mm, bar_type_for, tag=None):
    """Create the stair's bars in Revit (inside an open Transaction):
    bar_type_for(group, key) -> RebarBarType. Returns [(rebar, tramo
    index, group)]."""
    from Autodesk.Revit.DB.Structure import Rebar, RebarStyle, RebarHookOrientation
    doc = model.element.Document
    f = model.f
    cover = float(settings.get("cover") or 2.5) / 100.0
    made = []

    def vec(x, y, z):
        return f.ux * x + f.uy * y + DB.XYZ.BasisZ * z

    def create(points, group, key, normal, count, spacing, index):
        curves = List[DB.Curve]([DB.Line.CreateBound(points[k], points[k + 1])
                                 for k in range(len(points) - 1)
                                 if points[k].DistanceTo(points[k + 1]) > 1e-4])
        rebar = Rebar.CreateFromCurves(doc, RebarStyle.Standard, bar_type_for(group, key), None, None,
                                       model.element, normal, curves,
                                       RebarHookOrientation.Right, RebarHookOrientation.Right, True, True)
        if count > 1:
            rebar.GetShapeDrivenAccessor().SetLayoutAsNumberWithSpacing(count, spacing / FT, True, True, True)
        if tag:
            tag(rebar)
        made.append((rebar, index, group))

    plans = plan_all(model, settings, diameters_mm)
    for t in model.tramos:
        plan = plans.get(t.index)
        if plan is None:
            continue
        n_world = vec(t.n[0], t.n[1], 0.0).Normalize()
        for group in (INF, SUP):
            key = settings[group]["d"]
            d = diameters_mm[key] / 1000.0
            lo, hi = t.t0 + cover + d / 2.0, t.t1 - cover - d / 2.0
            positions = rf.bar_positions(lo, hi, float(settings[group]["s"]))
            spacing = positions[1] - positions[0] if len(positions) > 1 else 0.0
            for bar in plan[group]:
                pts = [model.world(t, s, positions[0], z) for s, z in bar["path"]]
                create(pts, group, key, n_world, len(positions), spacing, t.index)
        for group in (TEMP, STEP):
            if not plan[group]:
                continue
            key = settings[group]["d"]
            d = diameters_mm[key] / 1000.0
            # each cross bar spans the concrete really there (a nose bar
            # where the stair narrows or turns), none where there is none
            runs = []
            for item in plan[group]:
                span = model.span_at(t, item["at"][0], item["at"][1])
                if span is None or span[1] - span[0] < 2 * cover + 0.1:
                    continue
                span = (round(span[0] + cover + d / 2.0, 3), round(span[1] - cover - d / 2.0, 3))
                if runs and runs[-1][0] == span:
                    runs[-1][1].append(item)
                else:
                    runs.append((span, [item]))
            for (a, b), items in runs:
                for (s, z), count, spacing, (ds, dz) in group_cross(items):
                    pts = [model.world(t, s, a, z), model.world(t, s, b, z)]
                    normal = (vec(t.d[0], t.d[1], 0.0) * ds + DB.XYZ.BasisZ * dz).Normalize()
                    create(pts, group, key, normal, count, spacing, t.index)
    return made
