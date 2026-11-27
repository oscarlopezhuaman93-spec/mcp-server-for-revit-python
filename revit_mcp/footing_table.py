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


def steel_text(count, key, spacing, irregular=False):
    """"16Ø5/8\"@ 0.15" (spacing None: "@ var.", zones of their own);
    an irregular footing only its diameter and spacing: "Ø5/8\"@ 0.15"
    (the count changes along it - see the plan)."""
    if not count or not key:
        return u"-"
    at = u"@ var." if spacing is None else u"@ {:.2f}".format(spacing)
    if irregular:
        return u"Ø{}{}".format(key, at)
    return u"{}Ø{}{}".format(count, key, at)


def is_rectangle(edges, tol=0.01):
    """True when a plan outline [((x, y), (x, y))] is one rectangle: 4
    edges whose area fills their bounding box (within 1%: a few mm of a
    slightly skewed corner in the model still count as a rectangle)."""
    if len(edges) != 4:
        return False
    pts = [p for e in edges for p in e]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    box = (max(xs) - min(xs)) * (max(ys) - min(ys))
    # shoelace on the chained edges
    chain = [edges[0][0], edges[0][1]]
    rest = list(edges[1:])
    while rest:
        for k, (a, b) in enumerate(rest):
            if _close(a, chain[-1]):
                chain.append(b)
                rest.pop(k)
                break
            if _close(b, chain[-1]):
                chain.append(a)
                rest.pop(k)
                break
        else:
            return False
    n = len(chain) - 1
    area = abs(sum(chain[i][0] * chain[i + 1][1] - chain[i + 1][0] * chain[i][1] for i in range(n))) / 2.0
    return abs(area - box) <= tol * max(box, 1e-6)


def _close(a, b, tol=1e-3):
    return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol


def same_sizes(sizes, tol=0.005):
    """True when every (b, L, h) of a type's footings is the same."""
    return all(all(abs(s[i] - sizes[0][i]) <= tol for i in range(3)) for s in sizes[1:])


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


def column_x(widths=None):
    xs, x = [], 0.0
    for w in (widths or [w for _, w in COLUMNS]):
        xs.append(x)
        x += w
    return xs, x


# fitted cells (2026-11-27, the user: cells too big, texts with too much
# room): each column as wide as its longest text, each row as tall as its
# four steel lines - the Cuadro Zapatas' own rule
CHAR_W = 0.68  # a character's width per mm of text height (Bahnschrift, measured on the legend)
SIZE_HEAD, SIZE_VALUE = 2.5, 2.4
PAD = 7.0  # mm of margin in a cell (both sides)
LINE = 3.9  # mm between the steel lines of a row
FIT_TITLE_H, FIT_HEAD1_H, FIT_HEAD2_H = 10.0, 6.5, 6.0
FIT_ROW_H = 4 * LINE + 2.0


def _tw(text, size):
    return max(len(line) for line in (text or u"").split(u"\n")) * size * CHAR_W


def fit_widths(items, solado):
    def num(v):
        return u"{:.2f}".format(v) if v is not None else u"-"
    marks = [it.mark for it in items] + [u"TIPO"]
    out = [max(_tw(m, SIZE_HEAD) for m in marks) + PAD]
    for i, head, key in ((1, u"b (m)", "b"), (2, u"L (m)", "L"), (3, u"h (m)", "h")):
        out.append(max([_tw(head, SIZE_HEAD)] + [_tw(num(getattr(it, key)), SIZE_VALUE) for it in items]) + PAD)
    out.append(max(_tw(u"Solado", SIZE_HEAD), _tw(num(solado), SIZE_VALUE)) + PAD)
    for side in (u"b", u"L"):
        texts = [u"As superior", u"As inferior"] + [u"  " + it.steel[f][side] for it in items for f in (u"sup", u"inf")]
        out.append(max(_tw(t, SIZE_VALUE) for t in texts) + PAD)
    out.append(max([_tw(u"ALTURA", SIZE_HEAD)] + [_tw(it.df, SIZE_VALUE) for it in items]) + PAD)
    # the group headers fit too: DIMENSIONES over b..Solado, ACERO over the two
    need = _tw(u"DIMENSIONES", SIZE_HEAD) + PAD
    have = sum(out[1:5])
    if have < need:
        out[1:5] = [w + (need - have) / 4.0 for w in out[1:5]]
    return out


def draw_table(d, items, title, solado, fit=True):
    """The table (items: .mark, .b, .L, .h, .df, .steel {"sup"/"inf": {"b",
    "L"}: text}) through a drawer (as column_table: line, polyline,
    region, text). fit (default): cells fitted to their texts. Returns
    (width, height) in paper mm."""
    widths = fit_widths(items, solado) if fit else [w for _, w in COLUMNS]
    xs, total = column_x(widths)
    TITLE_H, HEAD1_H, HEAD2_H, ROW_H = ((FIT_TITLE_H, FIT_HEAD1_H, FIT_HEAD2_H, FIT_ROW_H) if fit else
                                        (globals()["TITLE_H"], globals()["HEAD1_H"], globals()["HEAD2_H"],
                                         globals()["ROW_H"]))
    total = max(total, _tw(title, 3.8) + 2 * PAD) if fit else total
    if fit and total > xs[-1] + widths[-1] + 1e-6:  # a long title: the extra to the steel columns
        extra = total - (xs[-1] + widths[-1])
        widths[5] += extra / 2.0
        widths[6] += extra / 2.0
        xs, total = column_x(widths)
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
    for i in (3, 4, 6):  # inside the groups
        d.line((xs[i], y_head2), (xs[i], bottom), L_GRID)
    # b | L: in the header, and in the rows that give their sizes (an
    # irregular one reads "ver planta" across both cells)
    d.line((xs[2], y_head2), (xs[2], y_body), L_GRID)
    for k, item in enumerate(items):
        if not getattr(item, "irregular", False):
            d.line((xs[2], y_body + ROW_H * k), (xs[2], y_body + ROW_H * (k + 1)), L_GRID)
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
        cells = ((3, item.h), (4, solado))
        if getattr(item, "irregular", False):
            d.text(xs[2], mid, u"ver planta", T_VALUE)  # across b and L
        else:
            cells = ((1, item.b), (2, item.L)) + cells
        for i, value in cells:
            d.text(xs[i] + widths[i] / 2.0, mid, u"{:.2f}".format(value) if value is not None else u"-", T_VALUE)
        for i, side in ((5, u"b"), (6, u"L")):
            x = xs[i] + (PAD / 2.0 if fit else 2.0)
            step = LINE if fit else 5.5
            y0 = y + (1.0 + LINE / 2.0 if fit else 3.6)
            d.text(x, y0, u"As superior", T_LABEL, align=u"left")
            d.text(x + 2.0, y0 + step, item.steel[u"sup"][side], T_VALUE, align=u"left")
            d.text(x, y0 + 2 * step, u"As inferior", T_LABEL, align=u"left")
            d.text(x + 2.0, y0 + 3 * step, item.steel[u"inf"][side], T_VALUE, align=u"left")
        d.text(xs[7] + widths[7] / 2.0, mid, item.df, T_VALUE)
    return total, bottom
