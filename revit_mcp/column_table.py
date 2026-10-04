# -*- coding: utf-8 -*-
"""The column schedule ("Cuadro de columnas y placas") drawn in a Revit
legend, after the user's sample: a column per column type with the rows
TIPO, BXH, DISTRIBUCION DE ESTRIBO (one line per level), DIAMETRO and
DETALLE SECCION - the section at 1:25 drawn true to size (stirrups and
ties as bars of their own diameter with bent corners and 135-degree
hooks, longitudinal bars as filled circles), with chained dimensions
and the bar groups labelled.

Pure geometry and text here (tests/unit/test_column_table.py); the
Revit drawing lives in the "Cuadro Columnas" button.
"""
import math
import re

SECTION_SCALE = 25
LABEL_W = 34.0  # paper mm, the row names' column
MIN_TYPE_W = 100.0
TITLE_H = 12.0
TIPO_H = 8.0
BXH_H = 8.0
LEVEL_LINE_H = 5.5
DIAM_H = 8.0
SECTION_ROOM = 46.0  # paper mm around a section (dimensions, bar labels, scale)


def natural_key(text):
    """C-2 before C-10."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", text or u"")]


# --- texts ----------------------------------------------------------------------
def spacing_text(distribution):
    """"1@0.05, 5@0.10, rto @0.20" -> "1@0.05, 5@0.10, Resto @ 0.20 C/Extr."
    (the sample's wording); u"" for a blank one."""
    parts = []
    for item in (distribution or u"").replace(u";", u",").split(u","):
        item = item.strip().replace(u" ", u"")
        if not item:
            continue
        m = re.match(r"^(\d+|rto|r|resto)@([\d.]+)$", item, re.I)
        if not m:
            parts.append(item)
            continue
        value = float(m.group(2))
        value = value / 100.0 if value >= 2.0 else value  # cm written
        if m.group(1).isdigit():
            parts.append(u"{}@{:.2f}".format(m.group(1), value))
        else:
            parts.append(u"Resto @ {:.2f} C/Extr.".format(value))
    return u", ".join(parts)


def bars_text(bars, diameters_mm):
    """[(x, y, key)] -> "6 Ø5/8\" + 2 Ø1/2\"" (largest first)."""
    count = {}
    for _, _, key in bars:
        count[key] = count.get(key, 0) + 1
    keys = sorted(count, key=lambda k: -diameters_mm.get(k, 0))
    return u" + ".join(u"{} Ø{}".format(count[k], k) for k in keys)


def shape_text(polygon):
    """BXH: "0.30m x 0.40m" for a rectangle, TIPO "L" / "T" / "+" by the
    number of corners, else "IRREGULAR"."""
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    n = len(polygon)
    if n == 4:
        return u"{:.2f}m x {:.2f}m".format(max(xs) - min(xs), max(ys) - min(ys))
    return {6: u'TIPO "L"', 8: u'TIPO "T"', 12: u'TIPO "+"'}.get(n, u"IRREGULAR")


def levels_between(levels, low, high):
    """The (name, elevation) levels from `low` up to below `high` (m)."""
    return [name for name, z in sorted(levels, key=lambda l: l[1]) if low - 1e-3 <= z < high - 1e-3]


# --- geometry (meters, section frame) ------------------------------------------
def _area(points):
    n = len(points)
    return sum(points[i][0] * points[(i + 1) % n][1] - points[(i + 1) % n][0] * points[i][1] for i in range(n)) / 2.0


def offset_polygon(points, dist):
    """The closed polygon moved `dist` m inwards (negative: outwards)."""
    n = len(points)
    sign = 1.0 if _area(points) > 0 else -1.0
    lines = []
    for i in range(n):
        (ax, ay), (bx, by) = points[i], points[(i + 1) % n]
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / length * sign, dx / length * sign
        lines.append(((ax + nx * dist, ay + ny * dist), (dx, dy)))
    out = []
    for i in range(n):
        (p, d), (q, e) = lines[i - 1], lines[i]
        det = d[0] * e[1] - d[1] * e[0]
        if abs(det) < 1e-12:
            out.append(q)
            continue
        t = ((q[0] - p[0]) * e[1] - (q[1] - p[1]) * e[0]) / det
        out.append((p[0] + d[0] * t, p[1] + d[1] * t))
    return out


def rounded(points, radius, steps=5):
    """The closed polygon with every corner bent to `radius` (arcs sampled
    in `steps` pieces): a bar's bend."""
    n = len(points)
    out = []
    for i in range(n):
        a, b, c = points[i - 1], points[i], points[(i + 1) % n]
        u = (a[0] - b[0], a[1] - b[1])
        v = (c[0] - b[0], c[1] - b[1])
        lu, lv = math.hypot(*u), math.hypot(*v)
        if lu < 1e-9 or lv < 1e-9 or radius <= 1e-9:
            out.append(b)
            continue
        u = (u[0] / lu, u[1] / lu)
        v = (v[0] / lv, v[1] / lv)
        cos_t = max(-1.0, min(1.0, u[0] * v[0] + u[1] * v[1]))
        theta = math.acos(cos_t)
        if theta > math.pi - 1e-3:  # straight
            out.append(b)
            continue
        t = min(radius / math.tan(theta / 2.0), lu / 2.0, lv / 2.0)
        r = t * math.tan(theta / 2.0)
        p1 = (b[0] + u[0] * t, b[1] + u[1] * t)
        p2 = (b[0] + v[0] * t, b[1] + v[1] * t)
        bis = (u[0] + v[0], u[1] + v[1])
        lb = math.hypot(*bis)
        center = (b[0] + bis[0] / lb * r / math.sin(theta / 2.0), b[1] + bis[1] / lb * r / math.sin(theta / 2.0))
        a1 = math.atan2(p1[1] - center[1], p1[0] - center[0])
        a2 = math.atan2(p2[1] - center[1], p2[0] - center[0])
        sweep = (a2 - a1 + math.pi) % (2 * math.pi) - math.pi
        for k in range(steps + 1):
            ang = a1 + sweep * k / float(steps)
            out.append((center[0] + r * math.cos(ang), center[1] + r * math.sin(ang)))
    return out


def stirrup_band(centerline, diameter, bend=None):
    """(outer, inner) loops of a closed stirrup drawn as a bar of its own
    diameter around its centerline, corners bent (centerline radius
    `bend`, by default 2 diameters)."""
    bend = 2.0 * diameter if bend is None else bend
    outer = rounded(offset_polygon(centerline, -diameter / 2.0), bend + diameter / 2.0)
    inner = rounded(offset_polygon(centerline, diameter / 2.0), max(bend - diameter / 2.0, 1e-4))
    return outer, inner


def bar_strip(a, b, diameter):
    """The 4 corners of a straight piece of bar from a to b."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy) or 1.0
    nx, ny = -dy / length * diameter / 2.0, dx / length * diameter / 2.0
    return [(a[0] + nx, a[1] + ny), (b[0] + nx, b[1] + ny), (b[0] - nx, b[1] - ny), (a[0] - nx, a[1] - ny)]


def hook_corner(centerline):
    """Index of the corner the 135-degree hooks go at: the top-left one."""
    return max(range(len(centerline)), key=lambda i: centerline[i][1] - centerline[i][0])


def hook_tails(centerline, diameter, length=None):
    """The two 135-degree hook tails at the hook corner, into the core:
    [(start, end)] (m)."""
    length = max(6.0 * diameter, 0.075) if length is None else length
    i = hook_corner(centerline)
    b = centerline[i]
    sides = []
    for j in (i - 1, (i + 1) % len(centerline)):
        a = centerline[j]
        d = math.hypot(a[0] - b[0], a[1] - b[1]) or 1.0
        sides.append(((a[0] - b[0]) / d, (a[1] - b[1]) / d))
    # both tails parallel, along the corner's bisector into the core (135
    # degrees from each side), each starting where its bend ends
    w = (sides[0][0] + sides[1][0], sides[0][1] + sides[1][1])
    lw = math.hypot(*w) or 1.0
    w = (w[0] / lw, w[1] / lw)
    tails = []
    for side in sides:
        s = (b[0] + side[0] * 2.5 * diameter, b[1] + side[1] * 2.5 * diameter)
        tails.append((s, (s[0] + w[0] * length, s[1] + w[1] * length)))
    return tails


def chain(values, tol=0.005):
    """Sorted distinct values (dimension chain stops)."""
    out = []
    for v in sorted(values):
        if not out or v - out[-1] > tol:
            out.append(v)
    return out


def bar_groups(bars, tol=0.03):
    """The bars of each diameter grouped for labelling: by rows (same y)
    or by columns (same x), whichever gives fewer groups ->
    [{"key", "axis": "row"/"col", "at": y or x, "bars": [(x, y)]}]."""
    by_key = {}
    for x, y, key in bars:
        by_key.setdefault(key, []).append((x, y))
    out = []
    for key in sorted(by_key):
        pts = by_key[key]

        def cluster(index):
            groups = []
            for p in sorted(pts, key=lambda q: q[index]):
                if groups and abs(p[index] - groups[-1][-1][index]) <= tol:
                    groups[-1].append(p)
                else:
                    groups.append([p])
            return groups
        rows, cols = cluster(1), cluster(0)
        axis, groups = ("row", rows) if len(rows) <= len(cols) else ("col", cols)
        for g in groups:
            k = 1 if axis == "row" else 0
            out.append({"key": key, "axis": axis, "at": sum(p[k] for p in g) / len(g), "bars": g})
    return out


SECTION_SCALES = (10, 15, 20, 25, 50)
MAX_SECTION_MM = 95.0  # the largest side of a drawn section, paper mm


def section_scale(polygon):
    """The largest scale (1:10, 1:15...) at which the section's longer side
    fits in MAX_SECTION_MM: the steel reads clearly."""
    if not polygon:
        return SECTION_SCALE
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    side = max(max(xs) - min(xs), max(ys) - min(ys)) * 1000.0
    for s in SECTION_SCALES:
        if side / s <= MAX_SECTION_MM:
            return s
    return SECTION_SCALES[-1]


def section_size_paper(polygon, scale=None):
    """(width, height) in paper mm of the section drawn at 1:scale."""
    if not polygon:
        return 0.0, 0.0
    scale = scale or section_scale(polygon)
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    return (max(xs) - min(xs)) * 1000.0 / scale, (max(ys) - min(ys)) * 1000.0 / scale


def type_width(polygon):
    w, _ = section_size_paper(polygon)
    return max(MIN_TYPE_W, w + SECTION_ROOM + 30.0)


def section_row_height(polygons):
    return max([section_size_paper(p)[1] for p in polygons] + [10.0]) + SECTION_ROOM + 20.0
