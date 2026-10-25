# -*- coding: utf-8 -*-
"""Concrete stair from a sketch (Modelado > Escalera): the runs drawn in
plan (start and end clicked, like Revit's run tool) become straight runs
with the risers of the whole height shared out, each run rising from where
the one before ends, and a flat landing between consecutive runs. Pure
Python: the Revit side (stair_create) builds each run as its own straight
stair - so its waist stays straight - and each landing as a floor.

Lengths in meters, plan points (x, y)."""
import math


def riser_count(height, max_riser):
    """Risers of the whole stair: the fewest that keep each one at or under
    `max_riser`."""
    if height <= 0 or max_riser <= 0:
        return 0
    return int(math.ceil(height / max_riser - 1e-9))


def _unit(a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    return (dx / length, dy / length) if length > 1e-9 else (0.0, 0.0), length


def plan_stair(runs, height, max_riser, tread, width):
    """`runs`: [(start, end)] drawn in plan, in climbing order. Returns
    {"total", "riser", "runs": [{"start", "end", "risers", "z0", "z1"}],
     "landings": [{"poly", "z"}], "remaining", "warnings"}. Each run takes
    the risers its drawn length holds, the last one what is left; its end
    is moved to fit them exactly. A run reaching a landing ends with a
    tread at the landing's height (as many treads as risers: its waist
    meets the landing, no gap under a landing thinner than a riser); the
    last run ends with its riser at the upper floor (one tread fewer)."""
    total = riser_count(height, max_riser)
    riser = height / total if total else 0.0
    out, warnings = [], []
    left = total
    for k, (a, b) in enumerate(runs):
        d, length = _unit(a, b)
        if length < tread * 0.5 or left <= 0:
            continue
        last = k == len(runs) - 1
        n = left if last else max(1, min(int(round(length / tread)), left))
        last = last or n == left  # it reaches the top
        z0 = (total - left) * riser
        treads = n - 1 if last else n
        end = (a[0] + d[0] * treads * tread, a[1] + d[1] * treads * tread)
        if abs(treads * tread - length) > tread * 0.5 + 1e-6:
            warnings.append(u"Tramo {}: dibujado de {:.2f} m, queda de {:.2f} m para sus {} contrahuellas"
                            .format(k + 1, length, treads * tread, n))
        out.append({"start": a, "end": end, "dir": d, "risers": n, "z0": z0, "z1": z0 + n * riser,
                    "top_tread": not last})
        left -= n
    landings = []
    for r0, r1 in zip(out, out[1:]):
        landings.append({"poly": landing_between(r0, r1, width), "z": r0["z1"]})
    return {"total": total, "riser": riser, "runs": out, "landings": landings,
            "remaining": left, "warnings": warnings}


def landing_between(r0, r1, width):
    """The flat landing joining run r0's top to run r1's foot: a rectangle
    in r0's direction starting at r0's top riser (the end of its line, as
    Revit builds a straight run), deep enough to reach the far side of r1's
    foot (at least the width, but for a run straight on) and as wide as both
    runs' ends together."""
    d = r0["dir"]
    n = (-d[1], d[0])
    top = r0["end"]
    half = width / 2.0
    d1 = r1["dir"]
    n1 = (-d1[1], d1[0])
    foot = r1["start"]
    loc = []
    for s in (-1, 1):
        p = (foot[0] + n1[0] * s * half, foot[1] + n1[1] * s * half)
        loc.append(((p[0] - top[0]) * d[0] + (p[1] - top[1]) * d[1],
                    (p[0] - top[0]) * n[0] + (p[1] - top[1]) * n[1]))
    reach = max(u for u, _ in loc)
    straight_on = d1[0] * d[0] + d1[1] * d[1] > 0.5
    along = reach if straight_on else max(width, reach)
    lat = [v for _, v in loc] + [-half, half]
    lo, hi = min(lat), max(lat)
    return [(top[0] + d[0] * u + n[0] * v, top[1] + d[1] * u + n[1] * v)
            for u, v in ((0.0, lo), (along, lo), (along, hi), (0.0, hi))]

def comfort(riser, tread):
    """2 risers + 1 tread (m): 0.60 to 0.64 is the usual rule."""
    value = 2 * riser + tread
    return value, 0.60 - 1e-9 <= value <= 0.64 + 1e-9
