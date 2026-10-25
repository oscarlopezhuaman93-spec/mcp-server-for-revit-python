# -*- coding: utf-8 -*-
"""Vehicle ramp from a sketched axis (Modelado > Rampa): straight and arc
segments drawn in plan, each from where the one before ends. The profile
climbs at one main slope with a transition at each end at half of it (RNE
A.010: slope up to 15 %, transitions at half). The ramp is cut into
pieces where the axis changes (line / arc) or the slope does, each piece a
floor (Revit side, ramp_create): a straight one planar with a slope arrow,
an arc one warped along the axis. Pure Python; meters, plan (x, y).

A segment: {"kind": "line", "a": (x, y), "b": (x, y)} or
{"kind": "arc", "a": (x, y), "m": (x, y) a point on it, "b": (x, y)}."""
import math

MAX_SLOPE = 0.15


def arc_through(a, m, b):
    """(centre, radius, start angle, sweep signed) of the arc a -> m -> b."""
    ax, ay = a
    bx, by = b
    mx, my = m
    d = 2.0 * (ax * (my - by) + mx * (by - ay) + bx * (ay - my))
    if abs(d) < 1e-12:
        return None  # the three points on a line
    ux = ((ax * ax + ay * ay) * (my - by) + (mx * mx + my * my) * (by - ay) + (bx * bx + by * by) * (ay - my)) / d
    uy = ((ax * ax + ay * ay) * (bx - mx) + (mx * mx + my * my) * (ax - bx) + (bx * bx + by * by) * (mx - ax)) / d
    r = math.hypot(ax - ux, ay - uy)
    t0 = math.atan2(ay - uy, ax - ux)
    tm = math.atan2(my - uy, mx - ux)
    t1 = math.atan2(by - uy, bx - ux)
    ccw = (tm - t0) % (2 * math.pi) < (t1 - t0) % (2 * math.pi)
    sweep = (t1 - t0) % (2 * math.pi) if ccw else -((t0 - t1) % (2 * math.pi))
    return (ux, uy), r, t0, sweep


def seg_length(seg):
    if seg["kind"] == "line":
        return math.hypot(seg["b"][0] - seg["a"][0], seg["b"][1] - seg["a"][1])
    arc = arc_through(seg["a"], seg["m"], seg["b"])
    return abs(arc[1] * arc[3]) if arc else math.hypot(seg["b"][0] - seg["a"][0], seg["b"][1] - seg["a"][1])


def point_at(seg, u):
    """(point, unit direction) at length u along the segment."""
    if seg["kind"] == "line":
        (ax, ay), (bx, by) = seg["a"], seg["b"]
        L = math.hypot(bx - ax, by - ay) or 1.0
        d = ((bx - ax) / L, (by - ay) / L)
        return (ax + d[0] * u, ay + d[1] * u), d
    (cx, cy), r, t0, sweep = arc_through(seg["a"], seg["m"], seg["b"])
    t = t0 + (u / r) * (1 if sweep > 0 else -1)
    sgn = 1 if sweep > 0 else -1
    return (cx + r * math.cos(t), cy + r * math.sin(t)), (-math.sin(t) * sgn, math.cos(t) * sgn)


def edges_at(seg, u, width):
    """The ramp's left and right edge points at length u (left of the way up)."""
    (x, y), (dx, dy) = point_at(seg, u)
    n = (-dy, dx)
    h = width / 2.0
    return (x + n[0] * h, y + n[1] * h), (x - n[0] * h, y - n[1] * h)


class Profile(object):
    """z(s) along the axis: transitions of `transition` m at each end at half
    the main slope, the main slope between, `rise` m up in all."""

    def __init__(self, length, rise, transition):
        self.length, self.rise = length, rise
        self.transition = max(0.0, min(transition, length / 3.0))
        self.slope = rise / (length - self.transition) if length > self.transition else 0.0

    def z(self, s):
        T, g, L = self.transition, self.slope, self.length
        if s <= T:
            return s * g / 2.0
        if s >= L - T:
            return self.rise - (L - s) * g / 2.0
        return T * g / 2.0 + (s - T) * g

    def breaks(self):
        return [b for b in (self.transition, self.length - self.transition) if 0 < b < self.length]


def plan_ramp(segments, width, rise, transition, max_slope=MAX_SLOPE, arc_step=1.0):
    """{"length", "slope", "transition_slope", "pieces", "warnings"}: the
    pieces bottom up, each {"kind", "seg", "u0", "u1", "s0", "s1", "z0", "z1"}
    plus, for a line, "outline" (4 corners) and "arrow" ((tail), (head)) and
    "slope"; for an arc, "left" / "right" edge points with their heights
    every `arc_step` m ((x, y, z))."""
    lengths = [seg_length(s) for s in segments]
    total = sum(lengths)
    warnings = []
    if total <= 0:
        return {"length": 0.0, "slope": 0.0, "transition_slope": 0.0, "pieces": [], "warnings": []}
    prof = Profile(total, rise, transition)
    if prof.slope > max_slope + 1e-9:
        warnings.append(u"Pendiente {:.1f} % mayor que {:.0f} %: alarga el eje o baja el desnivel".format(
            prof.slope * 100, max_slope * 100))
    if prof.transition < transition - 1e-6:
        warnings.append(u"Transiciones acortadas a {:.2f} m (el eje es corto)".format(prof.transition))
    cuts = sorted(set([0.0, total] + prof.breaks() + [sum(lengths[:k]) for k in range(1, len(lengths))]))
    pieces = []
    start = 0.0
    for k, seg in enumerate(segments):
        end = start + lengths[k]
        inside = [c for c in cuts if start - 1e-9 <= c <= end + 1e-9]
        for s0, s1 in zip(inside, inside[1:]):
            if s1 - s0 < 1e-4:
                continue
            u0, u1 = s0 - start, s1 - start
            piece = {"kind": seg["kind"], "seg": k, "u0": u0, "u1": u1, "s0": s0, "s1": s1,
                     "z0": prof.z(s0), "z1": prof.z(s1)}
            if seg["kind"] == "line":
                l0, r0 = edges_at(seg, u0, width)
                l1, r1 = edges_at(seg, u1, width)
                piece["outline"] = [r0, r1, l1, l0]
                piece["arrow"] = (point_at(seg, u0)[0], point_at(seg, u1)[0])
                piece["slope"] = (piece["z1"] - piece["z0"]) / (s1 - s0)
            else:
                n = max(2, int(math.ceil((u1 - u0) / arc_step)))
                left, right = [], []
                for i in range(n + 1):
                    u = u0 + (u1 - u0) * i / float(n)
                    l, r = edges_at(seg, u, width)
                    z = prof.z(start + u)
                    left.append((l[0], l[1], z))
                    right.append((r[0], r[1], z))
                piece["left"], piece["right"] = left, right
            pieces.append(piece)
        start = end
    return {"length": total, "slope": prof.slope, "transition_slope": prof.slope / 2.0,
            "transition": prof.transition, "pieces": pieces, "warnings": warnings, "profile": prof}
