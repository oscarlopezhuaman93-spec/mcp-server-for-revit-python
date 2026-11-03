# -*- coding: utf-8 -*-
"""Cuadro de viguetas: the ribbed slabs' ("losa aligerada") joist table,
drawn like the columns' and beams' ones (column_table: the same drawer,
bands, section drawing, dimensions and labels) - a column per vigueta
type (the Acero Losa settings, slab_rebar.ribbed): TIPO, DIMENSIONES,
ACERO INFERIOR, ACERO SUPERIOR, TEMPERATURA, DETALLE SECCION (the T with
its bricks under the flange). Pure Python (paper mm)."""
import re

import column_table as ct

TIPO_H = 8.0
ROW_H = 8.0
TOP_H = 12.0


def nominal_thickness(type_name, thickness_m):
    """The slab's nominal thickness: 'e=0.20m' in its type name (a sloped
    slab's type carries its true, larger one in the parameter), else the
    parameter."""
    found = re.findall(r"e\s*=\s*(\d+(?:[.,]\d+)?)", type_name or u"")
    if found:
        return round(float(found[0].replace(u",", u".")), 3)
    return round(thickness_m, 3)


def frac_text(frac):
    """1/3 -> 'L/3', 0.25 -> 'L/4' (a plain fraction else)."""
    if frac and frac > 0:
        n = 1.0 / frac
        if abs(n - round(n)) < 0.02:
            return u"L/{}".format(int(round(n)))
        return u"{:.2f}L".format(frac)
    return u"-"


class Joist(object):
    """One vigueta type: its section (T: flange as wide as the spacing, web
    b, slab `top` over the bricks) and steel."""

    def __init__(self, h, al, cover_cm, diameters_mm, slabs=()):
        self.h, self.b, self.s, self.top = h, al["b"], al["s"], al["top"]
        self.al = al
        self.cover = (cover_cm or 2.0) / 100.0
        self.slabs = list(slabs)
        self.mark = u"VIGUETA e={:.2f}".format(h)
        b2, s2 = self.b / 2.0, self.s / 2.0
        self.polygon = [(-b2, 0.0), (b2, 0.0), (b2, h - self.top), (s2, h - self.top), (s2, h),
                        (-s2, h), (-s2, h - self.top), (-b2, h - self.top)]
        # the bricks under the flange, each side of the web
        self.bricks = [[(x0, 0.0), (x1, 0.0), (x1, h - self.top), (x0, h - self.top)]
                       for x0, x1 in ((-s2, -b2), (b2, s2))]
        inf, sup = al["inf"]["d"], al["sup"]["d"]
        d_inf = diameters_mm.get(inf, 12.7) / 1000.0
        d_sup = diameters_mm.get(sup, 12.7) / 1000.0
        temp = al.get("temp") or {}
        d_temp = diameters_mm.get(temp.get("d"), 6.4) / 1000.0 if temp.get("on") else 0.0
        self.temp_y = h - self.cover - d_temp / 2.0 if d_temp else None
        self.temp_d = d_temp
        self.design = {"stirrups": [], "ties": [],
                       "bars": [(0.0, self.cover + d_inf / 2.0, inf),
                                (0.0, h - self.cover - d_temp - d_sup / 2.0, sup)]}
        self.cfg = {}

    def key(self):
        al = self.al
        temp = al.get("temp") or {}
        return (self.h, round(self.b, 3), round(self.s, 3), round(self.top, 3), al["inf"]["d"], al["sup"]["d"],
                round(al["sup"].get("frac") or 0, 3), round(al["sup"].get("end_frac") or 0, 3),
                bool(temp.get("on")), temp.get("d"), round(temp.get("s") or 0, 3), round(self.cover, 3))

    def rows(self):
        al = self.al
        temp = al.get("temp") or {}
        top = u"1Ø{} bastones {}".format(al["sup"]["d"], frac_text(al["sup"].get("frac")))
        if al["sup"].get("end_frac"):
            top += u"\n(extremos {})".format(frac_text(al["sup"]["end_frac"]))
        return [
            u"b={:.2f} h={:.2f} @{:.2f}".format(self.b, self.h, self.s),
            u"1Ø{} corrido".format(al["inf"]["d"]),
            top,
            (u"Ø{} @{:.2f}".format(temp.get("d"), temp.get("s") or 0) if temp.get("on") else u"-"),
        ]


def group(joists):
    """One Joist per distinct type (its slabs together), by thickness."""
    out = {}
    for j in joists:
        k = j.key()
        if k in out:
            out[k].slabs += j.slabs
        else:
            out[k] = j
    found = sorted(out.values(), key=lambda j: (j.h, j.s, j.b))
    seen = {}
    for j in found:  # two kinds of one thickness: VIGUETA e=0.20, VIGUETA e=0.20 (2)
        n = seen.get(j.mark, 0) + 1
        seen[j.mark] = n
        if n > 1:
            j.mark = u"{} ({})".format(j.mark, n)
    return found


def draw_table(d, items, title, scale, detail, diameters_mm):
    """The whole table. Returns (width, height) in paper mm."""
    widths = [ct.table_type_width(it.polygon, scale) for it in items]
    xs = [ct.LABEL_W]
    for w in widths[:-1]:
        xs.append(xs[-1] + w)
    total = ct.LABEL_W + sum(widths)
    rows = [(u"TIPO", TIPO_H), (u"DIMENSIONES", ROW_H), (u"ACERO\nINFERIOR", TOP_H),
            (u"ACERO\nSUPERIOR", TOP_H), (u"TEMPERATURA", ROW_H),
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
        for k, text in enumerate(item.rows(), 1):
            d.text(mid, ys[k] + rows[k][1] / 2.0, text, u"BOKI Cuadro 3mm" if k < 3 else u"BOKI Cuadro 2mm")
        draw_bricks_and_temp(d, item, x, x + w, ys[5], ys[6], scale, detail)
        ct.draw_section(d, item, x, x + w, ys[5], ys[6], scale, detail, diameters_mm)
    return total, bottom


def draw_bricks_and_temp(d, item, x0, x1, y0, y1, scale, detail):
    """The bricks (outline and a cross) and the temperature bar (a line
    along the flange: it runs across the viguetas), placed as
    ct.draw_section places the section."""
    k = 1000.0 / scale
    xs = [p[0] for p in item.polygon]
    ys = [p[1] for p in item.polygon]
    mx, my = (max(xs) + min(xs)) / 2.0, (max(ys) + min(ys)) / 2.0
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0 - 3.0

    def P(p):
        return (cx + (p[0] - mx) * k, cy - (p[1] - my) * k)

    for brick in item.bricks:
        pts = [P(q) for q in brick]
        d.polyline(pts, True, u"BOKI Cuadro Grilla")
        d.line(pts[0], pts[2], u"BOKI Cuadro Grilla")
        d.line(pts[1], pts[3], u"BOKI Cuadro Grilla")
    if item.temp_y is not None:
        s2 = item.s / 2.0 - item.cover
        a, b = P((-s2, item.temp_y)), P((s2, item.temp_y))
        if detail == ct.DETAIL_HIGH:
            r = item.temp_d / 2.0 * k
            d.polyline([(a[0], a[1] - r), (b[0], b[1] - r), (b[0], b[1] + r), (a[0], a[1] + r)], True,
                       u"BOKI Cuadro Acero Fino")
        else:
            d.line(a, b, u"BOKI Cuadro Barra")
