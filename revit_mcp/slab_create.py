# -*- coding: utf-8 -*-
"""Shapes of the slabs the "Crear Losas" window makes: a region (a closed
outline in plan, m) becomes one floor (flat or one-way slope) or several
sloped floors meeting at a ridge (dos aguas, cuatro aguas).

Each piece: {"poly": [(x, y)], "arrow": ((x, y), (x, y)) or None, "slope"}
- the arrow starts on the piece's low edge (it stays at the slab's height)
and points uphill. Pure (tests/unit/test_slab_create.py)."""
import math

FLAT, ONE, TWO, FOUR = u"plana", u"una agua", u"dos aguas", u"cuatro aguas"
SHAPES = (FLAT, ONE, TWO, FOUR)


def area(poly):
    a = 0.0
    for (x0, y0), (x1, y1) in zip(poly, poly[1:] + poly[:1]):
        a += x0 * y1 - x1 * y0
    return a / 2.0


def ccw(poly):
    return poly if area(poly) > 0 else list(reversed(poly))


def clean(poly, tol=0.005):
    """The outline without points closer than `tol` (m) nor points on a
    straight side (Revit refuses sides shorter than about 1 mm)."""
    pts = []
    for p in poly:
        if not pts or math.hypot(p[0] - pts[-1][0], p[1] - pts[-1][1]) > tol:
            pts.append(p)
    while len(pts) > 3 and math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1]) <= tol:
        pts.pop()
    changed = True
    while changed and len(pts) > 3:
        changed = False
        for i in range(len(pts)):
            a, b, c = pts[i - 1], pts[i], pts[(i + 1) % len(pts)]
            cross = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
            if abs(cross) < tol * max(math.hypot(c[0] - a[0], c[1] - a[1]), 1e-9):
                del pts[i]
                changed = True
                break
    return pts


def rotate(pts, deg):
    r = math.radians(deg)
    c, s = math.cos(r), math.sin(r)
    return [(x * c - y * s, x * s + y * c) for x, y in pts]


def clip(poly, a, b, c):
    """The part of the polygon where a*x + b*y <= c (Sutherland-Hodgman)."""
    out = []
    n = len(poly)
    for i in range(n):
        p, q = poly[i], poly[(i + 1) % n]
        fp, fq = a * p[0] + b * p[1] - c, a * q[0] + b * q[1] - c
        if fp <= 1e-9:
            out.append(p)
        if (fp < -1e-9 and fq > 1e-9) or (fp > 1e-9 and fq < -1e-9):
            t = fp / (fp - fq)
            out.append((p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t))
    clean = []
    for p in out:
        if not clean or math.hypot(p[0] - clean[-1][0], p[1] - clean[-1][1]) > 1e-6:
            clean.append(p)
    if len(clean) > 1 and math.hypot(clean[0][0] - clean[-1][0], clean[0][1] - clean[-1][1]) < 1e-6:
        clean.pop()
    return clean if len(clean) >= 3 and abs(area(clean)) > 1e-4 else []


def clip_convex(poly, convex):
    """The polygon cut to a convex one (each of its edges a half plane)."""
    convex = ccw(convex)
    out = poly
    for (x0, y0), (x1, y1) in zip(convex, convex[1:] + convex[:1]):
        # inside = left of the edge: -(y1-y0)*x + (x1-x0)*y >= ... -> as <= form
        a, b = (y1 - y0), -(x1 - x0)
        out = clip(out, a, b, a * x0 + b * y0)
        if not out:
            return []
    return out


def frame(poly, angle):
    """(the polygon turned so `angle` is +x, its box x0, x1, y0, y1)."""
    q = rotate(poly, -angle)
    xs = [p[0] for p in q]
    ys = [p[1] for p in q]
    return q, min(xs), max(xs), min(ys), max(ys)


def long_side_angle(poly):
    """The direction (deg) of the polygon's longest side."""
    best = None
    for (x0, y0), (x1, y1) in zip(poly, poly[1:] + poly[:1]):
        L = math.hypot(x1 - x0, y1 - y0)
        if best is None or L > best[0]:
            best = (L, math.degrees(math.atan2(y1 - y0, x1 - x0)) % 180.0)
    return best[1] if best else 0.0


def pieces(poly, shape, slope, angle=None, ridge_height=None):
    """The floors of a region. `angle` (deg): one-way - the direction it
    rises; dos / cuatro aguas - the ridge's direction; None: along the
    longest side (a one-way slope then rises across it). `slope` = rise /
    run; with ridge_height (m) the slope comes from it."""
    poly = ccw(clean(list(poly)))
    if shape == FLAT or not slope and not ridge_height:
        return [{"poly": poly, "arrow": None, "slope": 0.0}]
    if angle is None:
        angle = long_side_angle(poly) + (90.0 if shape == ONE else 0.0)
    q, x0, x1, y0, y1 = frame(poly, angle)

    def back(pts):
        return rotate(pts, angle)

    def piece(sub, tail, head, run):
        s = (ridge_height / run) if (ridge_height and run > 1e-6) else slope
        a, b = back([tail, head])
        return {"poly": clean(back(sub)), "arrow": (a, b), "slope": s}
    ym = (y0 + y1) / 2.0
    xm = (x0 + x1) / 2.0
    if shape == ONE:  # rises along +x of the frame
        return [piece(q, (x0, ym), (x1, ym), x1 - x0)]
    if shape == TWO:  # the ridge along x at the middle in y
        low = clip(q, 0.0, 1.0, ym)  # y <= ym: rises towards +y
        high = clip(q, 0.0, -1.0, -ym)  # y >= ym: rises towards -y
        out = []
        if low:
            out.append(piece(low, (xm, y0), (xm, ym), ym - y0))
        if high:
            out.append(piece(high, (xm, y1), (xm, ym), y1 - ym))
        return out
    # FOUR (hip roof): the ridge along x, half the depth in from each end
    h = (y1 - y0) / 2.0
    ra, rb = (x0 + h, ym), (x1 - h, ym)
    if ra[0] > rb[0]:  # deeper than long: a pyramid
        ra = rb = (xm, ym)
    planes = [
        ([(x0, y0), (x1, y0), rb, ra], (xm, y0), (xm, ym), ym - y0),          # front, rises +y
        ([(x1, y1), (x0, y1), ra, rb], (xm, y1), (xm, ym), y1 - ym),          # back, rises -y
        ([(x0, y1), (x0, y0), ra], (x0, ym), (ra[0], ym), max(ra[0] - x0, 1e-6)),   # left end
        ([(x1, y0), (x1, y1), rb], (x1, ym), (rb[0], ym), max(x1 - rb[0], 1e-6)),   # right end
    ]
    out = []
    for convex, tail, head, run in planes:
        if abs(area(convex)) < 1e-6:
            continue
        sub = clip_convex(q, convex)
        if sub:
            # a hip plane keeps the same slope as the others (rise / run)
            out.append(piece(sub, tail, head, run))
    return out


def ridge_rise(region_poly, shape, slope, angle=None):
    """How high (m) the ridge (or the high edge) gets over the eaves."""
    ps = pieces(region_poly, shape, slope, angle)
    best = 0.0
    for p in ps:
        if not p["arrow"]:
            continue
        (ax, ay), (bx, by) = p["arrow"]
        best = max(best, p["slope"] * math.hypot(bx - ax, by - ay))
    return best


def push_edge(poly, direction, at, to, tol=0.01):
    """The outline with its side at `at` (m, along the plan `direction`,
    a unit (x, y)) moved out to `to`: every corner on that side (within
    `tol`) pushed along `direction`. A sloped slab's high edge carried to a
    ridge beam's axis."""
    dx, dy = direction
    out = []
    for x, y in poly:
        s = x * dx + y * dy
        if abs(s - at) <= tol:
            x, y = x + dx * (to - s), y + dy * (to - s)
        out.append((x, y))
    return out
