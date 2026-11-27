# -*- coding: utf-8 -*-
"""Cuadro de vigas: the beams' table, drawn like the columns' one
(column_table: the same drawer, bands, section drawing, dimensions and
labels) - a column per beam type: TIPO, BXH,
ESTRIBOS, DIAMETRO (all the bars counted per diameter) and DETALLE SECCION. Pure Python (paper mm)."""
import column_table as ct

TIPO_H = 8.0
ROW_H = 8.0
STIRRUP_H = 12.0


def split_bars(bars):
    """(top, bottom) bars of a beam's drawing: above / below its middle."""
    return [b for b in bars if b[1] > 0], [b for b in bars if b[1] <= 0]


# fitted cells (2026-11-27, the user, as the column and footing tables but
# the Cuadro Vigas' own rule): each type's column as wide as its texts and
# its section with the dimensions on its left and the labels stacked on its
# right; the section row as tall as the tallest section or its labels
CHAR_W = 0.68  # a character's width per mm of text height (Bahnschrift)
FIT_MIN_W = 45.0
LABEL_STEP = 7.0  # the labels stacked on the right (Cuadro Vigas' annotate)


def _tw(text, size):
    return len(text or u"") * size * CHAR_W


def _labels(item, diameters_mm):
    """The labels the section gets (its own, as section_notes_v1): one per
    diameter and the stirrup's."""
    bars = item.design["bars"] if item.design else []
    out = [u"{}Ø{}".format(sum(1 for b in bars if b[2] == k), k) for k in set(b[2] for b in bars)]
    if item.design and item.design.get("stirrups"):
        out.append(u"Est. Ø{}".format((item.cfg.get("EA_Estribo_Borde_Diametro") or u"").strip()))
    return out


def fit_width(item, scale, diameters_mm):
    w, _ = ct.section_size_paper(item.polygon, scale)
    labels = _labels(item, diameters_mm)
    # the section is centered: each side as wide as the wider one (the labels)
    side = max(14.0, 9.0 + max([_tw(t, 2.2) for t in labels] + [0.0]) + 4.0)
    need = [w + 2.0 * side]
    bars = item.design["bars"] if item.design else []
    diam = (item.cfg.get("EA_Estribo_Borde_Diametro") or u"").strip()
    dist = ct.spacing_text(item.cfg.get("EA_Estribo_Borde_Distribucion"))
    need.append(_tw(u"Ø {}: {}".format(diam, dist) if dist else u"(sin estribos)", 2.1) + 8.0)
    need.append(_tw(ct.bars_text(bars, diameters_mm) if bars else u"-", 3.0) + 8.0)
    need.append(_tw(ct.shape_text(item.polygon) if item.polygon else u"-", 3.0) + 8.0)
    need.append(_tw(item.mark, 3.0) + 8.0)
    return max(need + [FIT_MIN_W])


def fit_section_height(items, scale, diameters_mm):
    """The tallest section (or its stacked labels) plus its dimension under
    it and the scale note."""
    h = 6.0
    for it in items:
        _, sh = ct.section_size_paper(it.polygon, scale)
        h = max(h, sh, 3.0 + LABEL_STEP * len(_labels(it, diameters_mm)))
    return h + 28.0


def draw_table(d, items, title, scale, detail, diameters_mm, fit=True):
    """The whole table (items: .mark, .polygon, .design, .cfg). fit
    (default): cells fitted to their contents. Returns (width, height) in
    paper mm."""
    widths = ([fit_width(it, scale, diameters_mm) for it in items] if fit
              else [ct.table_type_width(it.polygon, scale) for it in items])
    if fit:  # a long title: the extra shared out
        need = _tw(title, 4.5) + 10.0 - ct.LABEL_W
        if sum(widths) < need:
            widths = [w + (need - sum(widths)) / len(widths) for w in widths]
    xs = [ct.LABEL_W]
    for w in widths[:-1]:
        xs.append(xs[-1] + w)
    total = ct.LABEL_W + sum(widths)
    rows = [(u"TIPO", TIPO_H), (u"BXH", ROW_H), (u"DIAMETRO (Ø)", ROW_H), (u"ESTRIBOS", STIRRUP_H),
            (u"DETALLE\nSECCION", fit_section_height(items, scale, diameters_mm) if fit
             else ct.table_section_height([it.polygon for it in items], scale))]
    ys = [ct.TITLE_H]
    for _, h in rows:
        ys.append(ys[-1] + h)
    bottom = ys[-1]
    d.region([[(0, 0), (total, 0), (total, ct.TITLE_H), (0, ct.TITLE_H)]], ct.BAND_TITLE)
    d.region([[(ct.LABEL_W, ys[0]), (total, ys[0]), (total, ys[1]), (ct.LABEL_W, ys[1])]], ct.BAND_TYPES)
    d.region([[(0, ys[0]), (ct.LABEL_W, ys[0]), (ct.LABEL_W, bottom), (0, bottom)]], ct.BAND_SIDE)
    d.polyline([(0, 0), (total, 0), (total, bottom), (0, bottom)], True, u"BOKI Cuadro Grilla")
    for y in ys[:-1]:
        d.line((0, y), (total, y), u"BOKI Cuadro Grilla")
    for x in xs:
        d.line((x, ct.TITLE_H), (x, bottom), u"BOKI Cuadro Grilla")
    d.text(total / 2.0, ct.TITLE_H / 2.0, title, u"BOKI Cuadro Titulo")
    for (name, h), y in zip(rows, ys):
        d.text(ct.LABEL_W / 2.0, y + h / 2.0, name, u"BOKI Cuadro Cabecera")
    for item, x, w in zip(items, xs, widths):
        mid = x + w / 2.0
        d.text(mid, ys[0] + TIPO_H / 2.0, item.mark, u"BOKI Cuadro Cabecera")
        d.text(mid, ys[1] + ROW_H / 2.0, ct.shape_text(item.polygon) if item.polygon else u"-", u"BOKI Cuadro 3mm")
        bars = item.design["bars"] if item.design else []
        d.text(mid, ys[2] + rows[2][1] / 2.0, ct.bars_text(bars, diameters_mm) if bars else u"-", u"BOKI Cuadro 3mm")
        diam = (item.cfg.get("EA_Estribo_Borde_Diametro") or u"").strip()
        dist = ct.spacing_text(item.cfg.get("EA_Estribo_Borde_Distribucion"))
        d.text(mid, ys[3] + STIRRUP_H / 2.0, (u"Ø {}: {}".format(diam, dist) if dist else u"(sin estribos)"),
               u"BOKI Cuadro 2mm")
        ct.draw_section(d, item, x, x + w, ys[4], ys[5], scale, detail, diameters_mm)
    return total, bottom
