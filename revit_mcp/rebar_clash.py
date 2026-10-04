# -*- coding: utf-8 -*-
"""Clashes between reinforcing bars - the "Interferencias" button.

Bars come as straight pieces (arcs already cut into pieces):
(rebar id, bar index, a (x, y, z), b, radius), meters. Two bars of
different Rebar elements clash when their centerlines come closer than
the sum of their radii (less a tolerance: bars laid one on the other,
as a mesh's two layers, just touch). A grid of cells keeps it fast.

Pure (tests/unit/test_rebar_clash.py).
"""
import math


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def segment_distance(p1, q1, p2, q2):
    """(distance, point on 1, point on 2) between the segments p1-q1 and
    p2-q2 (closest points, clamped)."""
    d1, d2, r = _sub(q1, p1), _sub(q2, p2), _sub(p1, p2)
    a, e, f = _dot(d1, d1), _dot(d2, d2), _dot(d2, r)
    eps = 1e-12
    if a <= eps and e <= eps:
        s = t = 0.0
    elif a <= eps:
        s, t = 0.0, max(0.0, min(1.0, f / e))
    else:
        c = _dot(d1, r)
        if e <= eps:
            t, s = 0.0, max(0.0, min(1.0, -c / a))
        else:
            b = _dot(d1, d2)
            denom = a * e - b * b
            s = max(0.0, min(1.0, (b * f - c * e) / denom)) if denom > eps else 0.0
            t = (b * s + f) / e
            if t < 0.0:
                t, s = 0.0, max(0.0, min(1.0, -c / a))
            elif t > 1.0:
                t, s = 1.0, max(0.0, min(1.0, (b - c) / a))
    c1 = (p1[0] + d1[0] * s, p1[1] + d1[1] * s, p1[2] + d1[2] * s)
    c2 = (p2[0] + d2[0] * t, p2[1] + d2[1] * t, p2[2] + d2[2] * t)
    return math.sqrt(_dot(_sub(c1, c2), _sub(c1, c2))), c1, c2


def _cells(a, b, size):
    """The grid cells a piece's box touches (a list: IronPython trips on
    nested generators here)."""
    x0, x1 = int(math.floor(min(a[0], b[0]) / size)), int(math.floor(max(a[0], b[0]) / size))
    y0, y1 = int(math.floor(min(a[1], b[1]) / size)), int(math.floor(max(a[1], b[1]) / size))
    z0, z1 = int(math.floor(min(a[2], b[2]) / size)), int(math.floor(max(a[2], b[2]) / size))
    out = []
    for i in range(x0, x1 + 1):
        for j in range(y0, y1 + 1):
            for k in range(z0, z1 + 1):
                out.append((i, j, k))
    return out


def find_clashes(pieces, tol=0.001, cell=0.5):
    """Clashes between pieces of different Rebar elements:
    [{"a": rebar id, "b": rebar id, "point": (x, y, z), "overlap": m}],
    one per pair of Rebar elements (the deepest overlap)."""
    grid = {}
    for n, piece in enumerate(pieces):
        for c in _cells(piece[2], piece[3], cell):
            grid.setdefault(c, []).append(n)
    best = {}
    seen = set()
    for members in grid.values():
        for x in range(len(members)):
            for y in range(x + 1, len(members)):
                i, j = members[x], members[y]
                if (i, j) in seen:
                    continue
                seen.add((i, j))
                pa, pb = pieces[i], pieces[j]
                if pa[0] == pb[0]:
                    continue  # the same Rebar element (its own bends and set)
                dist, c1, c2 = segment_distance(pa[2], pa[3], pb[2], pb[3])
                overlap = pa[4] + pb[4] - dist
                if overlap <= tol:
                    continue
                key = (min(pa[0], pb[0]), max(pa[0], pb[0]))
                if key not in best or overlap > best[key]["overlap"]:
                    mid = tuple((u + v) / 2.0 for u, v in zip(c1, c2))
                    best[key] = {"a": key[0], "b": key[1], "point": mid, "overlap": overlap}
    return sorted(best.values(), key=lambda c: -c["overlap"])


def arc_pieces(points, max_angle_deg=15.0):
    """Consecutive pairs of a polyline (already tessellated)."""
    return list(zip(points, points[1:]))


def to_csv(rows):
    head = u"TIPO;ELEMENTO A;ELEMENTO B;DETALLE;X (m);Y (m);Z (m)"
    lines = [u";".join(u"{}".format(v).replace(u";", u",") for v in r) for r in rows]
    return u"\r\n".join([head] + lines) + u"\r\n"
