# -*- coding: utf-8 -*-
"""The column schedule ("Cuadro de columnas") drawn in a Revit legend, in
the format of the project's own legend: a row per column type with TIPO,
SECCION Y ACERO (the section drawn with its bars, stirrups and ties),
NIVEL, and the stirrups' FORMA, diameter and ESPACIADO.

Pure helpers (text and layout, tests/unit/test_column_table.py); the
drawing itself lives in the "Cuadro Columnas" button.
"""
import math
import re

# column widths (paper mm) and header heights, after the project's legend
COLUMNS = ((u"TIPO", 16.0), (u"SECCION Y ACERO", 72.0), (u"NIVEL", 28.0),
           (u"FORMA", 18.0), (u"Ø", 12.0), (u"ESPACIADO (cm)", 48.0))
TITLE_H = 10.0
GROUP_H = 7.0
HEAD_H = 7.0
MIN_ROW_H = 36.0
SECTION_MARGIN = 18.0  # paper mm around the drawn section (dimensions, bar text)


def natural_key(text):
    """C-2 before C-10."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", text or u"")]


def spacing_text(distribution):
    """"1@0.05, 5@0.10, rto @0.20" -> "1@5, 5@10, r@20cm" (cm, as the
    project's legend writes it); u"" for a blank one."""
    parts = []
    for item in (distribution or u"").replace(u";", u",").split(u","):
        item = item.strip().replace(u" ", u"")
        if not item:
            continue
        m = re.match(r"^(\d+|rto|r|resto)@([\d.]+)$", item, re.I)
        if not m:
            parts.append(item)
            continue
        count, value = m.group(1), float(m.group(2))
        cm = value * 100.0 if value < 2.0 else value  # meters, or already cm
        label = u"r" if not count.isdigit() else count
        parts.append(u"{}@{:g}".format(label, round(cm, 1)))
    if not parts:
        return u""
    return u", ".join(parts) + u"cm"


def bars_text(bars, diameters_mm):
    """[(x, y, key)] -> "4Ø3/4\" + 2Ø5/8\"" (largest first)."""
    count = {}
    for _, _, key in bars:
        count[key] = count.get(key, 0) + 1
    keys = sorted(count, key=lambda k: -diameters_mm.get(k, 0))
    return u" + ".join(u"{}Ø{}".format(count[k], k) for k in keys)


def size_text(polygon):
    """"0.20 x 0.60" of the section's bounding box (m)."""
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    return u"{:.2f} x {:.2f}".format(max(xs) - min(xs), max(ys) - min(ys))


def levels_text(names):
    """The levels a type runs through, lowest first: one, or "first a last"."""
    if not names:
        return u""
    if len(names) == 1:
        return names[0]
    return u"{}\na\n{}".format(names[0], names[-1])


def row_height(polygon, section_scale):
    """Paper mm of a row: the section drawn at 1:section_scale plus room."""
    if not polygon:
        return MIN_ROW_H
    ys = [p[1] for p in polygon]
    xs = [p[0] for p in polygon]
    h = (max(ys) - min(ys)) * 1000.0 / section_scale
    w = (max(xs) - min(xs)) * 1000.0 / section_scale
    if w > COLUMNS[1][1] - 20.0:  # a wide section: drawn lying down
        h = w
    return max(MIN_ROW_H, h + SECTION_MARGIN)


def column_x():
    """Paper mm where each column starts, and the total width."""
    xs, x = [], 0.0
    for _, w in COLUMNS:
        xs.append(x)
        x += w
    return xs, x


def circle_points(cx, cy, r, n=16):
    return [(cx + r * math.cos(2 * math.pi * k / n), cy + r * math.sin(2 * math.pi * k / n)) for k in range(n)]
