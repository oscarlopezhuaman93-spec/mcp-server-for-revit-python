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


def draw_table(d, items, title, scale, detail, diameters_mm):
    """The whole table (items: .mark, .polygon, .design, .cfg). Returns
    (width, height) in paper mm."""
    widths = [ct.table_type_width(it.polygon, scale) for it in items]
    xs = [ct.LABEL_W]
    for w in widths[:-1]:
        xs.append(xs[-1] + w)
    total = ct.LABEL_W + sum(widths)
    rows = [(u"TIPO", TIPO_H), (u"BXH", ROW_H), (u"DIAMETRO (Ø)", ROW_H), (u"ESTRIBOS", STIRRUP_H),
            (u"DETALLE\nSECCION", ct.table_section_height([it.polygon for it in items], scale))]
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
