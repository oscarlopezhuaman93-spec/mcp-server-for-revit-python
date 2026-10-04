# -*- coding: utf-8 -*-
"""The footing schedule ("Cuadro de zapatas / losa de cimentacion") drawn
in a Revit legend, after the user's sample: a row per foundation type
with TIPO, DIMENSIONES (b, L, h, Solado), ACERO (across b and across L,
each with its top and bottom layer) and ALTURA Df; colored bands as the
column schedule.

Pure text and layout here (tests/unit/test_footing_table.py); the Revit
reading and the drawers live in the "Cuadro Zapatas" button. Kept apart
from column_table on purpose (each window independent).
"""
import re

# column widths (paper mm)
COLUMNS = (u"TIPO", 24.0), (u"b", 16.0), (u"L", 16.0), (u"h", 16.0), (u"Solado", 18.0), \
    (u"Acero b", 48.0), (u"Acero L", 48.0), (u"Df", 22.0)
TITLE_H = 12.0
HEAD1_H = 8.0
HEAD2_H = 7.0
ROW_H = 24.0
SCALES = (25, 50, 75, 100)
TRANSPARENCIES = (0, 10, 20, 30, 40, 50)

BAND_TITLE = u"BOKI Zapatas Banda Titulo"
BAND_HEAD = u"BOKI Zapatas Banda Encabezado"
BAND_SIDE = u"BOKI Zapatas Banda Lateral"
# as printed: the project legend's own light blues (Revit's dark theme shows
# them slate) and a third shade for the TIPO column
BAND_COLORS = {BAND_TITLE: (153, 222, 255), BAND_HEAD: (213, 241, 255), BAND_SIDE: (232, 238, 250)}

T_TITLE = u"BOKI Zapatas Titulo"
T_HEAD = u"BOKI Zapatas Cabecera"
T_TYPE = u"BOKI Zapatas Tipo"
T_VALUE = u"BOKI Zapatas Valor"
T_LABEL = u"BOKI Zapatas Etiqueta"
L_GRID = u"BOKI Zapatas Grilla"


def natural_key(text):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", text or u"")]


def footing_mark(type_name):
    """'Z-2' / 'CC 1-1' / 'CE' out of a foundation type name, else the name."""
    name = (type_name or u"").upper()
    m = re.search(r"(?<![A-Z0-9])(Z-\d+|ZC-?\d+|ZA-?\d+|LC-?\d+|CC[_ -]?\d+-\d+|CC-?\d+|CE|VC-?\d+)", name)
    if not m:
        return type_name
    mark = m.group(1)
    if mark.startswith(u"CC") and re.match(r"CC[_ -]?\d+-\d+", mark):
        mark = u"CC " + re.sub(r"^CC[_ -]?", u"", mark)
    return mark


def steel_text(count, key, spacing):
    """"16Ø5/8\"@ 0.15" (spacing None: "@ var.", zones of their own)."""
    if not count or not key:
        return u"-"
    at = u"@ var." if spacing is None else u"@ {:.2f}".format(spacing)
    return u"{}Ø{}{}".format(count, key, at)


def mesh_spacing(positions, mode, spacing, zones):
    """The spacing shown for a mesh direction: its own, the even one of a
    quantity, None with zones (each its own)."""
    if zones:
        return None
    if mode in (u"Espaciado", u"Ambos") or len(positions) < 2:
        return spacing
    return round((positions[-1] - positions[0]) / (len(positions) - 1), 2)


def df_text(values):
    """Df of a type: one value, or the distinct ones (instances at several depths)."""
    distinct = []
    for v in sorted(values):
        if not distinct or abs(v - distinct[-1]) > 0.005:
            distinct.append(v)
    return u" / ".join(u"{:.2f}".format(v) for v in distinct) if distinct else u"-"


def column_x():
    xs, x = [], 0.0
    for _, w in COLUMNS:
        xs.append(x)
        x += w
    return xs, x


def draw_table(d, items, title, solado):
    """The table (items: .mark, .b, .L, .h, .df, .steel {"sup"/"inf": {"b",
    "L"}: text}) through a drawer (as column_table: line, polyline,
    region, text). Returns (width, height) in paper mm."""
    xs, total = column_x()
    widths = [w for _, w in COLUMNS]
    y_head2 = TITLE_H + HEAD1_H
    y_body = y_head2 + HEAD2_H
    bottom = y_body + ROW_H * len(items)
    # bands first
    d.region([[(0, 0), (total, 0), (total, TITLE_H), (0, TITLE_H)]], BAND_TITLE)
    d.region([[(0, TITLE_H), (total, TITLE_H), (total, y_body), (0, y_body)]], BAND_HEAD)
    d.region([[(0, y_body), (widths[0], y_body), (widths[0], bottom), (0, bottom)]], BAND_SIDE)
    # grid
    d.polyline([(0, 0), (total, 0), (total, bottom), (0, bottom)], True, L_GRID)
    d.line((0, TITLE_H), (total, TITLE_H), L_GRID)
    d.line((xs[1], y_head2), (xs[7], y_head2), L_GRID)  # under DIMENSIONES / ACERO
    for k in range(len(items) + 1):
        d.line((0, y_body + ROW_H * k), (total, y_body + ROW_H * k), L_GRID)
    for i in (1, 5, 7):  # full height: TIPO | DIMENSIONES | ACERO | Df
        d.line((xs[i], TITLE_H), (xs[i], bottom), L_GRID)
    for i in (2, 3, 4, 6):  # inside the groups
        d.line((xs[i], y_head2), (xs[i], bottom), L_GRID)
    # headers
    d.text(total / 2.0, TITLE_H / 2.0, title, T_TITLE)
    d.text(xs[0] + widths[0] / 2.0, (TITLE_H + y_body) / 2.0, u"TIPO", T_HEAD)
    d.text((xs[1] + xs[5]) / 2.0, TITLE_H + HEAD1_H / 2.0, u"DIMENSIONES", T_HEAD)
    d.text((xs[5] + xs[7]) / 2.0, TITLE_H + HEAD1_H / 2.0, u"ACERO", T_HEAD)
    d.text(xs[7] + widths[7] / 2.0, (TITLE_H + y_body) / 2.0, u"ALTURA\nDf (m)", T_HEAD)
    for i, name in ((1, u"b (m)"), (2, u"L (m)"), (3, u"h (m)"), (4, u"Solado"), (5, u"b (m)"), (6, u"L (m)")):
        d.text(xs[i] + widths[i] / 2.0, y_head2 + HEAD2_H / 2.0, name, T_HEAD)
    # rows
    for k, item in enumerate(items):
        y = y_body + ROW_H * k
        mid = y + ROW_H / 2.0
        d.text(xs[0] + widths[0] / 2.0, mid, item.mark, T_TYPE)
        for i, value in ((1, item.b), (2, item.L), (3, item.h), (4, solado)):
            d.text(xs[i] + widths[i] / 2.0, mid, u"{:.2f}".format(value) if value is not None else u"-", T_VALUE)
        for i, side in ((5, u"b"), (6, u"L")):
            x = xs[i] + 2.0
            d.text(x, y + 3.6, u"As superior", T_LABEL, align=u"left")
            d.text(x + 2.0, y + 9.0, item.steel[u"sup"][side], T_VALUE, align=u"left")
            d.text(x, y + 14.6, u"As inferior", T_LABEL, align=u"left")
            d.text(x + 2.0, y + 20.0, item.steel[u"inf"][side], T_VALUE, align=u"left")
        d.text(xs[7] + widths[7] / 2.0, mid, item.df, T_VALUE)
    return total, bottom
