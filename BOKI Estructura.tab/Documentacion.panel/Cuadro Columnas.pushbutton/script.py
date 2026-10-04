# -*- coding: utf-8 -*-
"""Cuadro de columnas y placas: una leyenda nueva con una columna por tipo
(TIPO, BXH, distribucion de estribos por nivel, diametros y el detalle de
la seccion a 1/25 con el acero dibujado a su tamano real)."""

__title__ = "Cuadro\nColumnas"
__author__ = "Revit MCP"

import math
import os
import sys

SCRIPT_DIR = os.path.dirname(__file__)
EXT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", ".."))
REVIT_MCP_DIR = os.path.join(EXT_ROOT, "revit_mcp")
if REVIT_MCP_DIR not in sys.path:
    sys.path.append(REVIT_MCP_DIR)

import rebar_spec as rs
import rebar_columns as rc
import column_table as ct

reload(rs)
reload(rc)
reload(ct)

from pyrevit import revit, DB, forms
from System.Collections.Generic import List

doc = revit.doc
MM = rs.BAR_DIAMETERS_MM
TITLE = u"CUADRO DE COLUMNAS Y PLACAS"

# the sample's colors (r, g, b)
C_TEXT = (0, 170, 230)
C_DIM = (230, 190, 0)
C_SCALE = (230, 30, 30)
C_CONCRETE = (230, 30, 30)
C_STIRRUP = (30, 60, 230)
C_BAR = (230, 0, 230)
C_GRID = (160, 160, 160)

# text types made for the table: name -> (paper mm, color)
TEXTS = {
    u"BOKI Cuadro Titulo": (5.0, C_TEXT),
    u"BOKI Cuadro 3mm": (3.0, C_TEXT),
    u"BOKI Cuadro 2mm": (2.0, C_TEXT),
    u"BOKI Cuadro Cota": (1.8, C_DIM),
    u"BOKI Cuadro Escala": (2.2, C_SCALE),
}
LINES = {  # line styles: name -> (color, weight)
    u"BOKI Cuadro Grilla": (C_GRID, 1),
    u"BOKI Cuadro Concreto": (C_CONCRETE, 5),
    u"BOKI Cuadro Cota": (C_DIM, 1),
    u"BOKI Cuadro Estribo": (C_STIRRUP, 1),
    u"BOKI Cuadro Barra": (C_BAR, 1),
}
REGIONS = {  # filled region types (solid): name -> color
    u"BOKI Cuadro Barra": C_BAR,
    u"BOKI Cuadro Estribo": C_STIRRUP,
}


def _name(element):
    p = element.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM)
    return p.AsString() if p else element.Name


def _color(rgb):
    return DB.Color(rgb[0], rgb[1], rgb[2])


class ColumnType(object):
    """One column type: its instances, levels, section and steel."""

    def __init__(self, column_type, columns, levels):
        self.type = column_type
        self.name = _name(column_type)
        self.mark = rc.type_mark(self.name) or self.name
        self.columns = columns
        self.cfg = rc.read_type_config(column_type)
        try:
            self.design = rs.design_from_text(self.cfg.get("EA_Seccion_Armado"))
        except rs.SpecError:
            self.design = None
        self.polygon = []
        for c in columns:
            try:
                self.polygon = list(rc.Section(c).polygon_m)
                break
            except Exception:
                continue
        low, high = None, None
        for c in columns:
            bb = c.get_BoundingBox(None)
            if bb is None:
                continue
            low = bb.Min.Z if low is None else min(low, bb.Min.Z)
            high = bb.Max.Z if high is None else max(high, bb.Max.Z)
        self.levels = ct.levels_between(levels, (low or 0) * 0.3048 - 0.3, (high or 0) * 0.3048 - 0.3) \
            if low is not None else []

    def has_steel(self):
        return bool(self.design and self.design["bars"])

    def label(self):
        state = u"con armado" if self.has_steel() else u"SIN ARMADO (solo la seccion)"
        return u"{}  ({}, {} columnas) - {}".format(self.mark, ct.shape_text(self.polygon) if self.polygon else u"?",
                                                   len(self.columns), state)


def collect_types():
    levels = [(l.Name, l.Elevation * 0.3048) for l in DB.FilteredElementCollector(doc).OfClass(DB.Level)]
    by_type = {}
    for c in (DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_StructuralColumns)
              .WhereElementIsNotElementType()):
        by_type.setdefault(c.GetTypeId().IntegerValue, []).append(c)
    found = [ColumnType(doc.GetElement(DB.ElementId(tid)), cols, levels) for tid, cols in by_type.items()]
    return sorted(found, key=lambda t: ct.natural_key(t.mark))


def ensure_styles():
    """The table's own text types, line styles and solid fills (made once,
    then reused)."""
    texts = dict((_name(t), t) for t in DB.FilteredElementCollector(doc).OfClass(DB.TextNoteType))
    base = texts.get(u"DETALLES 2.5mm") or texts.get(u"2.5mm Arial") or list(texts.values())[0]
    for name, (size, rgb) in TEXTS.items():
        t = texts.get(name)
        if t is None:
            t = base.Duplicate(name)
            texts[name] = t
        t.get_Parameter(DB.BuiltInParameter.TEXT_SIZE).Set(size / 304.8)
        t.get_Parameter(DB.BuiltInParameter.LINE_COLOR).Set(rgb[0] + rgb[1] * 256 + rgb[2] * 65536)
        bg = t.get_Parameter(DB.BuiltInParameter.TEXT_BACKGROUND)
        if bg is not None and not bg.IsReadOnly:
            bg.Set(1)  # transparent
    cat = doc.Settings.Categories.get_Item(DB.BuiltInCategory.OST_Lines)
    subs = dict((s.Name, s) for s in cat.SubCategories)
    for name, (rgb, weight) in LINES.items():
        s = subs.get(name)
        if s is None:
            s = doc.Settings.Categories.NewSubcategory(cat, name)
        s.LineColor = _color(rgb)
        s.SetLineWeight(weight, DB.GraphicsStyleType.Projection)
    regions = dict((_name(t), t) for t in DB.FilteredElementCollector(doc).OfClass(DB.FilledRegionType))
    solid = [t for t in regions.values() if _name(t) in (u"Solid Black", u"Relleno solido", u"Sólido negro")]
    base_r = solid[0] if solid else list(regions.values())[0]
    for name, rgb in REGIONS.items():
        t = regions.get(name)
        if t is None:
            t = base_r.Duplicate(name)
        t.ForegroundPatternColor = _color(rgb)


class Drawer(object):
    """Lines, filled regions and texts in a legend; positions in paper mm
    from the table's top-left corner (y down)."""

    def __init__(self, view):
        self.view = view
        self.f = view.Scale / 304.8  # paper mm -> feet in the view
        styles = doc.Settings.Categories.get_Item(DB.BuiltInCategory.OST_Lines).SubCategories
        self.styles = dict((s.Name, s.GetGraphicsStyle(DB.GraphicsStyleType.Projection)) for s in styles)
        self.regions = dict((_name(t), t) for t in DB.FilteredElementCollector(doc).OfClass(DB.FilledRegionType))
        self.text_types = dict((_name(t), t) for t in DB.FilteredElementCollector(doc).OfClass(DB.TextNoteType))

    def p(self, x, y):
        return DB.XYZ(x * self.f, -y * self.f, 0.0)

    def line(self, a, b, style=u"BOKI Cuadro Grilla"):
        if math.hypot(b[0] - a[0], b[1] - a[1]) < 0.05:
            return
        curve = doc.Create.NewDetailCurve(self.view, DB.Line.CreateBound(self.p(*a), self.p(*b)))
        gs = self.styles.get(style)
        if gs is not None:
            curve.LineStyle = gs

    def polyline(self, pts, closed=False, style=u"BOKI Cuadro Grilla"):
        for a, b in zip(pts, pts[1:] + (pts[:1] if closed else [])):
            self.line(a, b, style)

    def _loop(self, pts):
        loop = DB.CurveLoop()
        clean = []
        for q in pts:
            if not clean or math.hypot(q[0] - clean[-1][0], q[1] - clean[-1][1]) > 0.02:
                clean.append(q)
        if math.hypot(clean[0][0] - clean[-1][0], clean[0][1] - clean[-1][1]) < 0.02:
            clean.pop()
        for a, b in zip(clean, clean[1:] + clean[:1]):
            loop.Append(DB.Line.CreateBound(self.p(*a), self.p(*b)))
        return loop

    def _edge(self, region, type_name):
        """The region's boundary in its own color (the line style of the same name)."""
        gs = self.styles.get(type_name)
        if gs is not None:
            region.SetLineStyleId(gs.Id)

    def region(self, loops, type_name):
        rtype = self.regions.get(type_name)
        if rtype is None:
            return
        try:
            r = DB.FilledRegion.Create(doc, rtype.Id, self.view.Id, List[DB.CurveLoop]([self._loop(l) for l in loops]))
            self._edge(r, type_name)
        except Exception:
            pass  # a degenerate piece: left out rather than failing the table

    def circle(self, cx, cy, r, type_name):
        rtype = self.regions.get(type_name)
        if rtype is None:
            return
        c, rad = self.p(cx, cy), r * self.f
        loop = DB.CurveLoop()
        loop.Append(DB.Arc.Create(c, rad, 0.0, math.pi, DB.XYZ.BasisX, DB.XYZ.BasisY))
        loop.Append(DB.Arc.Create(c, rad, math.pi, 2 * math.pi, DB.XYZ.BasisX, DB.XYZ.BasisY))
        self._edge(DB.FilledRegion.Create(doc, rtype.Id, self.view.Id, List[DB.CurveLoop]([loop])), type_name)

    def text(self, x, y, text, kind=u"BOKI Cuadro 2mm", rotate=False, align=u"center"):
        ttype = self.text_types.get(kind)
        opts = DB.TextNoteOptions(ttype.Id if ttype else doc.GetDefaultElementTypeId(DB.ElementTypeGroup.TextNoteType))
        opts.HorizontalAlignment = {u"center": DB.HorizontalTextAlignment.Center,
                                    u"left": DB.HorizontalTextAlignment.Left,
                                    u"right": DB.HorizontalTextAlignment.Right}[align]
        opts.VerticalAlignment = DB.VerticalTextAlignment.Middle
        if rotate:
            opts.Rotation = math.pi / 2.0
        return DB.TextNote.Create(doc, self.view.Id, self.p(x, y), text, opts)


def stirrup_diameter(item, kind):
    key = item.cfg.get("EA_Estribo_Conf_Diametro") if kind == rs.KIND_CONFINEMENT else None
    key = (key or item.cfg.get("EA_Estribo_Borde_Diametro") or u'3/8"').strip()
    return MM.get(key, 9.5) / 1000.0


def draw_stirrup_icon(d, x, y, w=3.0, h=4.0):
    """The little stirrup in the distribution lines."""
    d.polyline([(x, y - h / 2), (x + w, y - h / 2), (x + w, y + h / 2), (x, y + h / 2)], closed=True,
               style=u"BOKI Cuadro Estribo")
    d.line((x, y - h / 2 + 0.2), (x + 1.2, y - h / 2 + 1.4), u"BOKI Cuadro Estribo")


def draw_section(d, item, x0, x1, y0, y1):
    """The section at 1:25, steel true to size, dimensions and bar labels."""
    poly = item.polygon
    if not poly:
        d.text((x0 + x1) / 2.0, (y0 + y1) / 2.0, u"(sin seccion)")
        return
    scale = ct.section_scale(poly)
    K = 1000.0 / scale  # paper mm per section meter
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    mx, my = (max(xs) + min(xs)) / 2.0, (max(ys) + min(ys)) / 2.0
    w, h = (max(xs) - min(xs)) * K, (max(ys) - min(ys)) * K
    cx = (x0 + x1) / 2.0 + 4.0
    cy = y0 + 22.0 + h / 2.0

    def P(p):
        return (cx + (p[0] - mx) * K, cy - (p[1] - my) * K)

    top, bottom, left, right = cy - h / 2.0, cy + h / 2.0, cx - w / 2.0, cx + w / 2.0
    design = item.design
    # steel first, the concrete outline over it
    if design:
        for kind, pts, wrap, is_open in design.get("stirrups", []):
            dia = stirrup_diameter(item, kind)
            if is_open:
                for a, b in zip(pts, pts[1:]):
                    d.region([[P(q) for q in ct.bar_strip(a, b, dia)]], u"BOKI Cuadro Estribo")
                continue
            outer, inner = ct.stirrup_band(list(pts), dia)
            d.region([[P(q) for q in outer], [P(q) for q in inner]], u"BOKI Cuadro Estribo")
            for s, e in ct.hook_tails(list(pts), dia):
                d.region([[P(q) for q in ct.bar_strip(s, e, dia)]], u"BOKI Cuadro Estribo")
        for kind, a, b in design.get("ties", []):
            dia = stirrup_diameter(item, rs.KIND_CONFINEMENT)
            d.region([[P(q) for q in ct.bar_strip(a, b, dia)]], u"BOKI Cuadro Estribo")
        for x, y, key in design["bars"]:
            px, py = P((x, y))
            d.circle(px, py, MM[key] / 2000.0 * K, u"BOKI Cuadro Barra")
    d.polyline([P(p) for p in poly], closed=True, style=u"BOKI Cuadro Concreto")
    # chained dimensions: along the top and down the left side
    xs_c = ct.chain(xs)
    ys_c = ct.chain(ys)
    yd = top - 5.0
    for a, b in zip(xs_c, xs_c[1:]):
        pa, pb = P((a, max(ys)))[0], P((b, max(ys)))[0]
        d.line((pa, yd), (pb, yd), u"BOKI Cuadro Cota")
        d.text((pa + pb) / 2.0, yd - 1.6, u"{:.2f}".format(b - a), u"BOKI Cuadro Cota")
    for v in xs_c:
        px = P((v, 0))[0]
        d.line((px, yd - 1.0), (px, top - 0.8), u"BOKI Cuadro Cota")
    xd = left - 5.0
    for a, b in zip(ys_c, ys_c[1:]):
        pa, pb = P((0, a))[1], P((0, b))[1]
        d.line((xd, pa), (xd, pb), u"BOKI Cuadro Cota")
        d.text(xd - 1.8, (pa + pb) / 2.0, u"{:.2f}".format(b - a), u"BOKI Cuadro Cota", rotate=True)
    for v in ys_c:
        py = P((0, v))[1]
        d.line((xd - 1.0, py), (left - 0.8, py), u"BOKI Cuadro Cota")
    # the bar groups, each on a reference line with a tick to every bar
    if design and design["bars"]:
        stacks = {"above": 0, "below": 0, "left": 0, "right": 0}
        for g in ct.bar_groups(design["bars"]):
            pts = [P(q) for q in g["bars"]]
            label = u"{}Ø{}".format(len(g["bars"]), g["key"])
            if g["axis"] == "row":
                side = "above" if g["at"] >= my else "below"
                k = stacks[side]
                stacks[side] += 1
                yl = (top - 9.0 - 3.2 * k) if side == "above" else (bottom + 4.0 + 3.2 * k)
                xa, xb = min(p[0] for p in pts), max(p[0] for p in pts)
                d.line((xa, yl), (max(xb, xa + 1.0), yl), u"BOKI Cuadro Cota")
                for p in pts:
                    d.line((p[0], yl), p, u"BOKI Cuadro Cota")
                d.text(max(xb, xa + 1.0) + 0.8, yl - 1.0, label, u"BOKI Cuadro Cota", align=u"left")
            else:
                side = "right" if g["at"] >= mx else "left"
                k = stacks[side]
                stacks[side] += 1
                xl = (right + 4.0 + 3.2 * k) if side == "right" else (left - 10.0 - 3.2 * k)
                ya, yb = min(p[1] for p in pts), max(p[1] for p in pts)
                d.line((xl, ya), (xl, max(yb, ya + 1.0)), u"BOKI Cuadro Cota")
                for p in pts:
                    d.line((xl, p[1]), p, u"BOKI Cuadro Cota")
                d.text(xl, max(yb, ya + 1.0) + 2.0, label, u"BOKI Cuadro Cota")
    conf = ct.spacing_text(item.cfg.get("EA_Estribo_Conf_Distribucion"))
    if conf:
        d.text(cx, y1 - 10.0, u"Estribo de confinamiento Ø {}: {}".format(
            (item.cfg.get("EA_Estribo_Conf_Diametro") or u"").strip(), conf), u"BOKI Cuadro 2mm")
    d.text(x0 + 3.0, y1 - 4.0, u"ESC. 1/ {}".format(scale), u"BOKI Cuadro Escala", align=u"left")


def draw_table(view, items, title):
    d = Drawer(view)
    widths = [ct.type_width(it.polygon) for it in items]
    xs = [ct.LABEL_W]
    for w in widths[:-1]:
        xs.append(xs[-1] + w)
    total = ct.LABEL_W + sum(widths)
    n_levels = max([len(it.levels) for it in items] + [1])
    rows = [(u"TIPO", ct.TIPO_H), (u"BXH", ct.BXH_H),
            (u"DISTRIBUCION\nDE ESTRIBO", n_levels * ct.LEVEL_LINE_H + 4.0),
            (u"DIAMETRO (Ø)", ct.DIAM_H),
            (u"DETALLE\nSECCION", ct.section_row_height([it.polygon for it in items]))]
    ys = [ct.TITLE_H]
    for _, h in rows:
        ys.append(ys[-1] + h)
    bottom = ys[-1]
    # grid
    d.polyline([(0, 0), (total, 0), (total, bottom), (0, bottom)], closed=True)
    for y in ys[:-1]:
        d.line((0, y), (total, y))
    for x in xs:
        d.line((x, ct.TITLE_H), (x, bottom))
    d.text(total / 2.0, ct.TITLE_H / 2.0, title, u"BOKI Cuadro Titulo")
    for (name, h), y in zip(rows, ys):
        d.text(ct.LABEL_W / 2.0, y + h / 2.0, name, u"BOKI Cuadro 3mm" if h < 10 else u"BOKI Cuadro 2mm")
    for item, x, w in zip(items, xs, widths):
        mid = x + w / 2.0
        d.text(mid, ys[0] + ct.TIPO_H / 2.0, item.mark, u"BOKI Cuadro 3mm")
        d.text(mid, ys[1] + ct.BXH_H / 2.0, ct.shape_text(item.polygon) if item.polygon else u"-", u"BOKI Cuadro 3mm")
        # the stirrups, level by level
        dist = ct.spacing_text(item.cfg.get("EA_Estribo_Borde_Distribucion"))
        diam = (item.cfg.get("EA_Estribo_Borde_Diametro") or u"").strip()
        count = len(item.design.get("stirrups", [])) if item.design else 0
        if dist:
            for k, level in enumerate(item.levels):
                yl = ys[2] + 2.0 + ct.LEVEL_LINE_H * (k + 0.5)
                d.text(x + 2.0, yl, u"{}:".format(level), u"BOKI Cuadro 2mm", align=u"left")
                d.text(x + 31.0, yl, u"{}".format(max(count, 1)), u"BOKI Cuadro 2mm", align=u"left")
                draw_stirrup_icon(d, x + 34.0, yl)
                d.text(x + 38.5, yl, u"Ø {}: {}".format(diam, dist), u"BOKI Cuadro 2mm", align=u"left")
        else:
            d.text(mid, ys[2] + rows[2][1] / 2.0, u"(sin estribos configurados)", u"BOKI Cuadro 2mm")
        d.text(mid, ys[3] + ct.DIAM_H / 2.0,
               ct.bars_text(item.design["bars"], MM) if item.has_steel() else u"-", u"BOKI Cuadro 3mm")
        draw_section(d, item, x, x + w, ys[4], ys[5])
    return d


def new_legend(name, scale):
    """A new empty legend (Revit only makes one by duplicating another)."""
    legends = [v for v in DB.FilteredElementCollector(doc).OfClass(DB.View)
               if v.ViewType == DB.ViewType.Legend and not v.IsTemplate]
    if not legends:
        return None
    view = doc.GetElement(legends[0].Duplicate(DB.ViewDuplicateOption.Duplicate))
    taken = set(v.Name for v in legends)
    final, k = name, 2
    while final in taken:
        final = u"{} ({})".format(name, k)
        k += 1
    view.Name = final
    view.Scale = scale
    view.DetailLevel = DB.ViewDetailLevel.Fine
    return view


def make(items, title):
    t = DB.Transaction(doc, "Cuadro de columnas")
    t.Start()
    try:
        view = new_legend(title, ct.SECTION_SCALE)
        if view is None:
            t.RollBack()
            forms.alert(u"El proyecto no tiene ninguna leyenda: crea una (Vista > Leyendas) y vuelve a intentar.",
                        title="Cuadro de columnas")
            return None
        ensure_styles()
        draw_table(view, items, title)
        t.Commit()
    except Exception:
        t.RollBack()
        raise
    return view


if __name__ == "__main__":
    types = collect_types()
    if not types:
        forms.alert(u"El modelo no tiene columnas.", title="Cuadro de columnas")
    else:
        class Option(forms.TemplateListItem):
            @property
            def name(self):
                return self.item.label()

        options = [Option(t, checked=t.has_steel()) for t in types]
        chosen = forms.SelectFromList.show(options, title=u"Cuadro de columnas - tipos a incluir",
                                           button_name=u"Crear cuadro", multiselect=True, width=640, height=520)
        if chosen:
            title = forms.ask_for_string(default=TITLE, title=u"Cuadro de columnas",
                                         prompt=u"Titulo del cuadro (tambien es el nombre de la leyenda):")
            if title:
                view = make(chosen, title.strip())
                if view is not None:
                    revit.uidoc.ActiveView = view
