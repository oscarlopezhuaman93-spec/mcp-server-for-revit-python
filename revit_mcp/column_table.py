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


def type_width(polygon, scale=None):
    w, _ = section_size_paper(polygon, scale)
    return max(MIN_TYPE_W, w + SECTION_ROOM + 30.0)


def section_row_height(polygons, scale=None):
    return max([section_size_paper(p, scale)[1] for p in polygons] + [10.0]) + SECTION_ROOM + 20.0


# --- The table, drawn through a "drawer" ------------------------------------------
# The same drawing goes to the preview window (WPF) and to the Revit legend:
# a drawer has line(a, b, style), polyline(pts, closed, style),
# region(loops, fill), circle(cx, cy, r, fill), ring(cx, cy, r, style) and
# text(x, y, text, kind, rotate=False, align="center"); positions in paper mm
# from the table's top-left corner, y down. Style / fill / kind names are
# the Revit ones (made by the button); the preview maps them to colors.
BAND_TITLE = u"BOKI Cuadro Banda Titulo"
BAND_TYPES = u"BOKI Cuadro Banda Tipos"
BAND_SIDE = u"BOKI Cuadro Banda Lateral"
# the bands' colors (r, g, b) as printed: the project legend's own fills
# ("Leyenda", "Leyenda 2") and a third shade - light blues that Revit's dark
# theme shows as slate, as in the project legend
BAND_COLORS = {BAND_TITLE: (153, 222, 255), BAND_TYPES: (213, 241, 255), BAND_SIDE: (232, 238, 250)}
DETAIL_LOW, DETAIL_MEDIUM, DETAIL_HIGH = u"Bajo", u"Medio", u"Alto"
DETAILS = (DETAIL_LOW, DETAIL_MEDIUM, DETAIL_HIGH)
SCALES = (10, 15, 20, 25, 50)
TABLE_SECTION_ROOM = 22.0  # paper mm around a section: no dimensions nor labels


def table_type_width(polygon, scale):
    w, _ = section_size_paper(polygon, scale)
    return max(MIN_TYPE_W, w + TABLE_SECTION_ROOM + 20.0)


def table_section_height(polygons, scale):
    return max([section_size_paper(p, scale)[1] for p in polygons] + [10.0]) + TABLE_SECTION_ROOM + 12.0


def _stirrup_diameter(cfg, kind, diameters_mm, confinement=u"confinamiento"):
    key = cfg.get("EA_Estribo_Conf_Diametro") if kind == confinement else None
    key = (key or cfg.get("EA_Estribo_Borde_Diametro") or u'3/8"').strip()
    return diameters_mm.get(key, 9.5) / 1000.0


def draw_stirrup_icon(d, x, y, w=3.0, h=4.0):
    """The little stirrup in the distribution lines."""
    d.polyline([(x, y - h / 2), (x + w, y - h / 2), (x + w, y + h / 2), (x, y + h / 2)], True,
               u"BOKI Cuadro Estribo")
    d.line((x, y - h / 2 + 0.2), (x + 1.2, y - h / 2 + 1.4), u"BOKI Cuadro Estribo")


def _inside(poly, x, y):
    hit = False
    for (ax, ay), (bx, by) in zip(poly, poly[1:] + poly[:1]):
        if (ay > y) != (by > y) and x < ax + (bx - ax) * (y - ay) / (by - ay):
            hit = not hit
    return hit


def fitted_bars(design, cfg, diameters_mm):
    """The bars drawn against the stirrup (2026-11-26): a bar on (or near)
    a stirrup leg is moved inward until it touches the leg's inner face -
    centre at half the stirrup plus the bar's radius from the leg's axis -,
    as they are tied. Section frame (m); only the drawing of the table."""
    out = []
    loops = []
    for kind, pts, wrap, is_open in design.get("stirrups", []):
        if not is_open:
            loops.append((list(pts), _stirrup_diameter(cfg, kind, diameters_mm)))
    for x, y, key in design["bars"]:
        r = diameters_mm.get(key, 16.0) / 2000.0
        for _ in range(2):  # twice: a corner bar settles against both legs
            for pts, dia in loops:
                target = dia / 2.0 + r
                for (ax, ay), (bx, by) in zip(pts, pts[1:] + pts[:1]):
                    ux, uy = bx - ax, by - ay
                    length = math.hypot(ux, uy)
                    if length < 1e-6:
                        continue
                    ux, uy = ux / length, uy / length
                    t = (x - ax) * ux + (y - ay) * uy
                    if t < -target or t > length + target:
                        continue
                    nx, ny = -uy, ux  # a normal; flipped to point inside the stirrup
                    mx, my = ax + ux * length / 2.0, ay + uy * length / 2.0
                    if not _inside(pts, mx + nx * 0.002, my + ny * 0.002):
                        nx, ny = -nx, -ny
                    dist = (x - ax) * nx + (y - ay) * ny  # > 0 inside
                    if -target < dist < target:
                        x, y = x + nx * (target - dist), y + ny * (target - dist)
        # in a corner: seated in the bend (stirrup_band: centerline radius 2
        # diameters, its centre that far in from both legs)
        for pts, dia in loops:
            bend = 2.0 * dia
            n = len(pts)
            for i in range(n):
                a, b, c = pts[i - 1], pts[i], pts[(i + 1) % n]
                u = (a[0] - b[0], a[1] - b[1])
                v = (c[0] - b[0], c[1] - b[1])
                lu, lv = math.hypot(*u), math.hypot(*v)
                if lu < 1e-6 or lv < 1e-6 or abs(u[0] * v[0] + u[1] * v[1]) > 1e-6 * lu * lv:
                    continue  # only square corners
                u, v = (u[0] / lu, u[1] / lu), (v[0] / lv, v[1] / lv)
                if not _inside(pts, b[0] + (u[0] + v[0]) * 0.002, b[1] + (u[1] + v[1]) * 0.002):
                    continue  # a re-entrant corner: no bend to sit in
                du = (x - b[0]) * u[0] + (y - b[1]) * u[1]
                dv = (x - b[0]) * v[0] + (y - b[1]) * v[1]
                if not (0 < du < bend and 0 < dv < bend):
                    continue
                cx, cy = b[0] + (u[0] + v[0]) * bend, b[1] + (u[1] + v[1]) * bend
                reach = bend - dia / 2.0 - r
                dist = math.hypot(x - cx, y - cy)
                if reach > 0 and dist > reach:
                    x, y = cx + (x - cx) / dist * reach, cy + (y - cy) / dist * reach
        out.append((x, y, key))
    return out


def draw_section(d, item, x0, x1, y0, y1, scale, detail, diameters_mm):
    """The section drawn true to size at 1:scale, its steel at the chosen
    detail: HIGH - every bar and stirrup by its outline, bends and hooks
    (as Revit shows rebar at fine detail); MEDIUM - solid colored bars;
    LOW - a line per stirrup, a dot per bar. No dimensions nor labels."""
    poly = item.polygon
    if not poly:
        d.text((x0 + x1) / 2.0, (y0 + y1) / 2.0, u"(sin seccion)", u"BOKI Cuadro 2mm")
        return
    k = 1000.0 / scale  # paper mm per section meter
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    mx, my = (max(xs) + min(xs)) / 2.0, (max(ys) + min(ys)) / 2.0
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0 - 3.0

    def P(p):
        return (cx + (p[0] - mx) * k, cy - (p[1] - my) * k)

    design = item.design
    if design:
        for kind, pts, wrap, is_open in design.get("stirrups", []):
            dia = _stirrup_diameter(item.cfg, kind, diameters_mm)
            pts = list(pts)
            if detail == DETAIL_LOW:
                d.polyline([P(q) for q in pts], not is_open, u"BOKI Cuadro Estribo")
                if not is_open:
                    for s, e in hook_tails(pts, dia):
                        d.line(P(s), P(e), u"BOKI Cuadro Estribo")
                continue
            pieces = []
            if is_open:
                pieces = [[bar_strip(a, b, dia)] for a, b in zip(pts, pts[1:])]
            else:
                pieces = [list(stirrup_band(pts, dia))]
                pieces += [[bar_strip(s, e, dia)] for s, e in hook_tails(pts, dia)]
            for loops in pieces:
                if detail == DETAIL_HIGH:
                    for loop in loops:
                        d.polyline([P(q) for q in loop], True, u"BOKI Cuadro Acero Fino")
                else:
                    d.region([[P(q) for q in loop] for loop in loops], u"BOKI Cuadro Estribo")
        for kind, a, b in design.get("ties", []):
            dia = _stirrup_diameter(item.cfg, u"confinamiento", diameters_mm)
            if detail == DETAIL_LOW:
                d.line(P(a), P(b), u"BOKI Cuadro Estribo")
            elif detail == DETAIL_HIGH:
                d.polyline([P(q) for q in bar_strip(a, b, dia)], True, u"BOKI Cuadro Acero Fino")
            else:
                d.region([[P(q) for q in bar_strip(a, b, dia)]], u"BOKI Cuadro Estribo")
        for x, y, key in fitted_bars(design, item.cfg, diameters_mm):
            px, py = P((x, y))
            r = diameters_mm[key] / 2000.0 * k
            if detail == DETAIL_HIGH:
                d.ring(px, py, r, u"BOKI Cuadro Acero Fino")
            elif detail == DETAIL_MEDIUM:
                d.circle(px, py, r, u"BOKI Cuadro Barra")
            else:
                d.circle(px, py, max(0.35, r * 0.6), u"BOKI Cuadro Barra")
    d.polyline([P(p) for p in poly], True, u"BOKI Cuadro Concreto")
    d.text(x0 + 3.0, y1 - 4.0, u"ESC. 1/ {}".format(scale), u"BOKI Cuadro Escala", align=u"left")
    annotate = getattr(d, "annotate", None)
    if callable(annotate) and design:
        notes = section_notes(item, P, diameters_mm)
        notes["scale"] = scale
        annotate(notes)


def _cluster(pts, index, tol=0.03):
    groups = []
    for p in sorted(pts, key=lambda q: q[index]):
        if groups and abs(p[index] - groups[-1][-1][index]) <= tol:
            groups[-1].append(p)
        else:
            groups.append([p])
    return groups


def label_groups(bars, tol=0.03):
    """The bars labelled as the user's table (2026-11-26), in the section
    frame (m, y up): [{"side": "right"/"top"/"right_col", "key", "lines":
    [[(x, y)...]]}] - first the rows of one diameter (2+ bars), labelled on
    the right, a leader along each row; then the rest by columns of one
    diameter: on top (a leader down each column), or on the right with a
    leader per bar for the outermost column; what is left, one label per
    diameter on the right. Neighbour rows / columns of the same diameter
    share one label."""
    left = list(bars)
    out = []
    rows = _cluster(left, 1, tol)
    picked = []
    for row in rows:
        keys = set(b[2] for b in row)
        if len(row) >= 2 and len(keys) == 1:
            picked.append(row)
    taken = set(b for row in picked for b in row)
    left = [b for b in left if b not in taken]
    for row in picked:  # rows top -> bottom, neighbours merged
        prev = out[-1] if out else None
        if (prev and prev["side"] == u"right" and prev["key"] == row[0][2]
                and rows.index(row) - prev["last"] == 1):
            prev["lines"].append(row)
            prev["last"] = rows.index(row)
        else:
            out.append({"side": u"right", "key": row[0][2], "lines": [row], "last": rows.index(row)})
    if left:
        x_max = max(b[0] for b in bars)
        cols = _cluster(left, 0, tol)
        rest = []
        for col in cols:
            if len(set(b[2] for b in col)) != 1:
                rest += col
                continue
            if abs(col[0][0] - x_max) <= tol:
                out.append({"side": u"right_col", "key": col[0][2], "lines": [[b] for b in col]})
                continue
            prev = out[-1] if out else None
            if prev and prev["side"] == u"top" and prev["key"] == col[0][2] and prev["last"] == cols.index(col) - 1:
                prev["lines"].append(col)
                prev["last"] = cols.index(col)
            else:
                out.append({"side": u"top", "key": col[0][2], "lines": [col], "last": cols.index(col)})
        by_key = {}
        for b in rest:
            by_key.setdefault(b[2], []).append(b)
        for key in sorted(by_key):
            out.append({"side": u"right_col", "key": key, "lines": [[b] for b in by_key[key]]})
    for g in out:
        g.pop("last", None)
        g["count"] = sum(len(line) for line in g["lines"])
    return out


def _right_edge(polygon, y):
    """The polygon's rightmost x at height y."""
    best = None
    for (ax, ay), (bx, by) in zip(polygon, polygon[1:] + polygon[:1]):
        if min(ay, by) - 1e-9 <= y <= max(ay, by) + 1e-9:
            x = ax if abs(by - ay) < 1e-9 else ax + (bx - ax) * (y - ay) / (by - ay)
            x = max(ax, bx) if abs(by - ay) < 1e-9 else x
            best = x if best is None else max(best, x)
    return best if best is not None else max(p[0] for p in polygon)


def section_notes(item, P, diameters_mm):
    """What a drawn section is dimensioned and labeled with (paper mm, y
    down): {"outline", "labels": [{"text", "at" (the text's top-left, or
    top-right when "align" is "right"), "align", "leaders": [(elbow, end)]}]}
    - the user's table (2026-11-26): "2Ø5/8\"" by rows on the right (a
    leader along the row to its far bar), by columns on top (a leader down
    the column), no stirrup label (its distribution has its own row)."""
    design = item.design
    poly = item.polygon
    top_y = min(P(p)[1] for p in poly)
    labels = []
    n_top = 0
    for g in label_groups(fitted_bars(design, item.cfg, diameters_mm)):
        text = u"{}Ø{}".format(g["count"], g["key"])
        if g["side"] == u"right":
            rows = g["lines"]
            first = rows[0]
            xr = P((_right_edge(poly, first[0][1]), first[0][1]))[0]
            ty = P(first[0])[1]
            tx = xr + (2.2 if len(rows) == 1 else 4.7)
            ends = [P(min(row, key=lambda b: b[0])) for row in rows]
            labels.append({"text": text, "at": (tx, ty - 1.6), "align": u"left",
                           "leaders": [((tx, ty), e) for e in ends]})
        elif g["side"] == u"right_col":
            pts = [P(line[0]) for line in g["lines"]]
            xr = max(P((_right_edge(poly, line[0][1]), line[0][1]))[0] for line in g["lines"])
            ym = sum(p[1] for p in pts) / len(pts)
            tx = xr + 1.9
            labels.append({"text": text, "at": (tx, ym - 1.5), "align": u"left",
                           "leaders": [((tx, ym), p) for p in pts]})
        else:
            ey = top_y - 3.8 - 1.5 * n_top
            n_top += 1
            ends = [P(min(col, key=lambda b: b[1])) for col in g["lines"]]  # the lowest bar
            leaders = [((e[0], ey), e) for e in ends]
            if len(ends) == 1:
                labels.append({"text": text, "at": (ends[0][0] + 1.7, ey - 1.5), "align": u"left",
                               "leaders": leaders})
            else:
                labels.append({"text": text, "at": (min(e[0] for e in ends) - 1.7, ey - 1.5), "align": u"right",
                               "leaders": leaders})
    return {"outline": [P(p) for p in poly], "labels": labels}


def draw_table(d, items, title, scale, detail, diameters_mm):
    """The whole table: a column per type (items: .mark, .polygon,
    .design, .cfg, .levels). Returns (width, height) in paper mm."""
    widths = [table_type_width(it.polygon, scale) for it in items]
    xs = [LABEL_W]
    for w in widths[:-1]:
        xs.append(xs[-1] + w)
    total = LABEL_W + sum(widths)
    n_levels = max([len(it.levels) for it in items] + [1])
    rows = [(u"TIPO", TIPO_H), (u"BXH", BXH_H),
            (u"DISTRIBUCION\nDE ESTRIBO", LEVEL_LINE_H + 4.0),  # one line, as the user's table
            (u"DIAMETRO (Ø)", DIAM_H),
            (u"DETALLE\nSECCION", table_section_height([it.polygon for it in items], scale))]
    ys = [TITLE_H]
    for _, h in rows:
        ys.append(ys[-1] + h)
    bottom = ys[-1]
    # colored bands first (under the grid and the texts): the title row, the
    # types' row and the row names' column, each its own shade
    d.region([[(0, 0), (total, 0), (total, TITLE_H), (0, TITLE_H)]], BAND_TITLE)
    d.region([[(LABEL_W, ys[0]), (total, ys[0]), (total, ys[1]), (LABEL_W, ys[1])]], BAND_TYPES)
    d.region([[(0, ys[0]), (LABEL_W, ys[0]), (LABEL_W, bottom), (0, bottom)]], BAND_SIDE)
    d.polyline([(0, 0), (total, 0), (total, bottom), (0, bottom)], True, u"BOKI Cuadro Grilla")
    for y in ys[:-1]:
        d.line((0, y), (total, y), u"BOKI Cuadro Grilla")
    for x in xs:
        d.line((x, TITLE_H), (x, bottom), u"BOKI Cuadro Grilla")
    d.text(total / 2.0, TITLE_H / 2.0, title, u"BOKI Cuadro Titulo")
    for (name, h), y in zip(rows, ys):
        d.text(LABEL_W / 2.0, y + h / 2.0, name, u"BOKI Cuadro Cabecera")
    for item, x, w in zip(items, xs, widths):
        mid = x + w / 2.0
        d.text(mid, ys[0] + TIPO_H / 2.0, item.mark, u"BOKI Cuadro Cabecera")
        d.text(mid, ys[1] + BXH_H / 2.0, shape_text(item.polygon) if item.polygon else u"-", u"BOKI Cuadro 2.8mm")
        dist = spacing_text(item.cfg.get("EA_Estribo_Borde_Distribucion"))
        diam = (item.cfg.get("EA_Estribo_Borde_Diametro") or u"").strip()
        count = len(item.design.get("stirrups", [])) if item.design else 0
        if dist:
            # one line, centered, no level name (the type's setup is the same on every level)
            line = u"Ø {}: {}".format(diam, dist)
            span = 7.5 + len(line) * 2.1 * 0.62
            x_start = mid - span / 2.0
            yl = ys[2] + rows[2][1] / 2.0
            d.text(x_start, yl, u"{}".format(max(count, 1)), u"BOKI Cuadro 2mm", align=u"left")
            draw_stirrup_icon(d, x_start + 3.0, yl)
            d.text(x_start + 7.5, yl, line, u"BOKI Cuadro 2mm", align=u"left")
        else:
            d.text(mid, ys[2] + rows[2][1] / 2.0, u"(sin estribos configurados)", u"BOKI Cuadro 2mm")
        bars = item.design["bars"] if item.design else []
        d.text(mid, ys[3] + DIAM_H / 2.0, bars_text(bars, diameters_mm) if bars else u"-", u"BOKI Cuadro 2.8mm")
        draw_section(d, item, x, x + w, ys[4], ys[5], scale, detail, diameters_mm)
    return total, bottom
