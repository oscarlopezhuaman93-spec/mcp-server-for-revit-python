# -*- coding: utf-8 -*-
"""Longitudinal bars of a gable frame (Acero Viga): two sloped beams
meeting at a ridge, drawn in their common vertical plane - u horizontal,
z up, meters. Pure Python (rebar_sloped places them in Revit).

A bar row of a beam is (eave, d): the point where the bar ends at its
eave (the far face of the support less the cover) and the unit direction
of the bar up to the ridge.
- the top bars run on over the ridge, bent there, down to the other eave;
- a bottom bar crosses the ridge and is anchored `ld` along the other
  beam, under its top bars;
- at an eave a bar drops plumb into the support (the hook leg).
(The detailing agreed with the user.)"""
import math


def intersect(p, d, q, e):
    """Point where the line p + t d meets q + s e (None: parallel)."""
    den = d[0] * e[1] - d[1] * e[0]
    if abs(den) < 1e-9:
        return None
    t = ((q[0] - p[0]) * e[1] - (q[1] - p[1]) * e[0]) / den
    return (p[0] + t * d[0], p[1] + t * d[1])


def down_normal(d):
    """The unit normal of direction d pointing down."""
    n = (d[1], -d[0])
    return n if n[1] < 0 else (-n[0], -n[1])


def length(path):
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(path, path[1:]))


def _along(p, d, t):
    return (p[0] + d[0] * t, p[1] + d[1] * t)


def _with_leg(eave, leg):
    return [(eave[0], eave[1] - leg), eave] if leg > 0 else [eave]


def top_path(row_a, row_b, leg_a, leg_b):
    """A top bar over the ridge: eave a (hook down) -> ridge -> eave b."""
    ridge = intersect(row_a[0], row_a[1], row_b[0], row_b[1])
    if ridge is None:
        return None
    return _with_leg(row_a[0], leg_a) + [ridge] + list(reversed(_with_leg(row_b[0], leg_b)))


def anchored_path(row, other_top, leg, ld, gap):
    """A bar of one beam crossing the ridge and anchored `ld` along the
    other beam, `gap` under that beam's top bar row `other_top`."""
    q0, e = other_top
    n = down_normal(e)
    under = (q0[0] + n[0] * gap, q0[1] + n[1] * gap)
    bend = intersect(row[0], row[1], under, e)
    if bend is None:
        return None
    back = (-e[0], -e[1])  # from the ridge down the other beam
    return _with_leg(row[0], leg) + [bend, _along(bend, back, ld)]


def split_top(path, ridge_index, lap):
    """A top bar too long for one piece, cut at the ridge: each half runs
    `lap` past the ridge along the other beam (the lap)."""
    first = path[:ridge_index + 1]
    second = path[ridge_index:]
    a, r, b = path[ridge_index - 1], path[ridge_index], path[ridge_index + 1]

    def unit(p, q):
        L = math.hypot(q[0] - p[0], q[1] - p[1]) or 1.0
        return ((q[0] - p[0]) / L, (q[1] - p[1]) / L)
    first = first + [_along(r, unit(r, b), lap)]
    second = [_along(r, unit(r, a), lap)] + second
    return [first, second]


def frame_paths(top_a, top_b, bottom_a, bottom_b, legs, ld, gaps, lap, max_length):
    """All bar paths of a ridge pair: ({"kind", "path", "i"} list, warnings).
    top_a / top_b / bottom_a / bottom_b: [(row, key)] with the rows of each
    beam (a top row of b paired with the one of a at the same index; the
    leftover ones anchored like the bottom bars); legs / ld / gaps: {key: m}
    (gap: from the other beam's top row to an anchored bar, centers)."""
    out, warnings = [], []
    n = min(len(top_a), len(top_b))
    for i in range(n):
        (ra, ka), (rb, kb) = top_a[i], top_b[i]
        path = top_path(ra, rb, legs.get(ka, 0.0), legs.get(kb, 0.0))
        if path is None:
            continue
        ridge_index = 2 if legs.get(ka, 0.0) > 0 else 1
        pieces = [path]
        if length(path) > max_length + 1e-6 and lap:
            pieces = split_top(path, ridge_index, lap.get(ka) or 0.0)
        for piece in pieces:
            if length(piece) > max_length + 1e-6:
                warnings.append(u"Barra superior {}: {:.2f} m (mas de {:.0f} m)".format(ka, length(piece), max_length))
            out.append({"kind": "top", "key": ka, "path": piece, "i": i})
    rows_b_top = [r for r, _ in top_b] or [r for r, _ in bottom_b]
    rows_a_top = [r for r, _ in top_a] or [r for r, _ in bottom_a]
    for side, rows, other in ((u"a", top_a[n:] + bottom_a, rows_b_top), (u"b", top_b[n:] + bottom_b, rows_a_top)):
        if not other:
            continue
        for row, key in rows:
            path = anchored_path(row, other[0], legs.get(key, 0.0), ld.get(key, 0.0), gaps.get(key, 0.0))
            if path is None:
                continue
            if length(path) > max_length + 1e-6:
                warnings.append(u"Barra {}: {:.2f} m (mas de {:.0f} m)".format(key, length(path), max_length))
            out.append({"kind": "anchored", "side": side, "key": key, "path": path})
    return out, warnings


def resize_path(points, lengths):
    """A sketched bar with its segments set to `lengths` (m), each keeping
    its direction, from the first point (Boceto: lengths typed in)."""
    out = [points[0]]
    for k, (a, b) in enumerate(zip(points, points[1:])):
        dims = len(a)
        L = math.sqrt(sum((b[i] - a[i]) ** 2 for i in range(dims))) or 1.0
        want = lengths[k] if k < len(lengths) and lengths[k] > 0 else L
        last = out[-1]
        out.append(tuple(last[i] + (b[i] - a[i]) / L * want for i in range(dims)))
    return out


def segment_lengths(points):
    return [math.sqrt(sum((b[i] - a[i]) ** 2 for i in range(len(a)))) for a, b in zip(points, points[1:])]
