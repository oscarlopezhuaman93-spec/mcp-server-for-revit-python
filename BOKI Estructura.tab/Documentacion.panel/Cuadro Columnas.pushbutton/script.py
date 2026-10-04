# -*- coding: utf-8 -*-
"""Cuadro de columnas: una leyenda nueva con una fila por tipo de columna
(seccion dibujada con su acero, niveles y estribos), con el formato de
la leyenda del proyecto."""

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
VIEW_SCALE = 50
SECTION_SCALE = 20  # the sections drawn at 1:20 inside the 1:50 legend
TEXT_TITLE = (u"DETALLES 3mm N", u"5mm Arial")
TEXT_HEAD = (u"DETALLES 2.5mm N", u"3.5mm Arial")
TEXT_BODY = (u"DETALLES 2.5mm", u"2.5mm Arial")
REGION_TITLE = u"Leyenda"
REGION_HEAD = u"Leyenda 2"
LINE_THICK = u"LINEA GRUESA"
LINE_STIRRUP = u"Estribos"


def _name(element):
    p = element.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM)
    return p.AsString() if p else element.Name


class ColumnType(object):
    """One column type: its instances, levels, section and steel."""

    def __init__(self, column_type, columns):
        self.type = column_type
        self.name = _name(column_type)
        self.mark = rc.type_mark(self.name) or self.name
        self.columns = columns
        cfg = rc.read_type_config(column_type)
        self.cfg = cfg
        try:
            self.design = rs.design_from_text(cfg.get("EA_Seccion_Armado"))
        except rs.SpecError:
            self.design = None
        self.polygon = []
        for c in columns:
            try:
                self.polygon = list(rc.Section(c).polygon_m)
                break
            except Exception:
                continue
        levels = {}
        for c in columns:
            for bip in (DB.BuiltInParameter.FAMILY_BASE_LEVEL_PARAM, DB.BuiltInParameter.FAMILY_TOP_LEVEL_PARAM):
                p = c.get_Parameter(bip)
                lv = doc.GetElement(p.AsElementId()) if p else None
                if isinstance(lv, DB.Level):
                    levels[lv.Id] = lv
        ordered = sorted(levels.values(), key=lambda l: l.Elevation)
        self.levels = [ordered[0].Name, ordered[-1].Name] if len(ordered) > 1 else [l.Name for l in ordered]

    def label(self):
        state = u"con armado" if self.design and self.design["bars"] else u"SIN ARMADO (solo la seccion)"
        size = ct.size_text(self.polygon) if self.polygon else u"?"
        return u"{}  ({} m, {} columnas) - {}".format(self.mark, size, len(self.columns), state)


def collect_types():
    by_type = {}
    for c in (DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_StructuralColumns)
              .WhereElementIsNotElementType()):
        by_type.setdefault(c.GetTypeId().IntegerValue, []).append(c)
    found = [ColumnType(doc.GetElement(DB.ElementId(tid)), cols) for tid, cols in by_type.items()]
    return sorted(found, key=lambda t: ct.natural_key(t.mark))


class Drawer(object):
    """Lines, filled regions and texts in a legend; positions in paper mm
    from the table's top-left corner (y down)."""

    def __init__(self, view):
        self.view = view
        self.f = view.Scale / 304.8  # paper mm -> feet in the view
        self.texts = {}
        styles = doc.Settings.Categories.get_Item(DB.BuiltInCategory.OST_Lines).SubCategories
        self.styles = dict((s.Name, s.GetGraphicsStyle(DB.GraphicsStyleType.Projection)) for s in styles)
        self.regions = dict((_name(t), t) for t in DB.FilteredElementCollector(doc).OfClass(DB.FilledRegionType))
        self.text_types = dict((_name(t), t) for t in DB.FilteredElementCollector(doc).OfClass(DB.TextNoteType))

    def p(self, x, y):
        return DB.XYZ(x * self.f, -y * self.f, 0.0)

    def line(self, a, b, style=None):
        if math.hypot(b[0] - a[0], b[1] - a[1]) < 0.05:
            return
        curve = doc.Create.NewDetailCurve(self.view, DB.Line.CreateBound(self.p(*a), self.p(*b)))
        gs = self.styles.get(style) if style else None
        if gs is not None:
            curve.LineStyle = gs
        return curve

    def polyline(self, pts, closed=False, style=None):
        for a, b in zip(pts, pts[1:] + (pts[:1] if closed else [])):
            self.line(a, b, style)

    def region(self, pts, type_name, fallback=u"Solid Black"):
        rtype = self.regions.get(type_name) or self.regions.get(fallback)
        if rtype is None:
            return
        loop = DB.CurveLoop()
        for a, b in zip(pts, pts[1:] + pts[:1]):
            loop.Append(DB.Line.CreateBound(self.p(*a), self.p(*b)))
        DB.FilledRegion.Create(doc, rtype.Id, self.view.Id, List[DB.CurveLoop]([loop]))

    def circle(self, cx, cy, r):
        rtype = self.regions.get(u"Solid Black")
        if rtype is None:
            return
        c = self.p(cx, cy)
        rad = r * self.f
        loop = DB.CurveLoop()
        loop.Append(DB.Arc.Create(c, rad, 0.0, math.pi, DB.XYZ.BasisX, DB.XYZ.BasisY))
        loop.Append(DB.Arc.Create(c, rad, math.pi, 2 * math.pi, DB.XYZ.BasisX, DB.XYZ.BasisY))
        DB.FilledRegion.Create(doc, rtype.Id, self.view.Id, List[DB.CurveLoop]([loop]))

    def text(self, x, y, text, kind=TEXT_BODY, rotate=False, align=u"center"):
        ttype = None
        for name in kind:
            ttype = self.text_types.get(name)
            if ttype:
                break
        opts = DB.TextNoteOptions(ttype.Id if ttype else doc.GetDefaultElementTypeId(DB.ElementTypeGroup.TextNoteType))
        opts.HorizontalAlignment = {u"center": DB.HorizontalTextAlignment.Center,
                                    u"left": DB.HorizontalTextAlignment.Left}[align]
        opts.VerticalAlignment = DB.VerticalTextAlignment.Middle
        if rotate:
            opts.Rotation = math.pi / 2.0
        return DB.TextNote.Create(doc, self.view.Id, self.p(x, y), text, opts)


def draw_section(d, item, x0, x1, y0, y1):
    """The type's section in the cell, at 1:SECTION_SCALE, with its bars,
    stirrups, ties, size and bar text."""
    poly = item.polygon
    if not poly:
        d.text((x0 + x1) / 2.0, (y0 + y1) / 2.0, u"(sin seccion)")
        return
    k = 1000.0 / SECTION_SCALE  # paper mm per m
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    w, h = (max(xs) - min(xs)) * k, (max(ys) - min(ys)) * k
    lying = w > (x1 - x0) - 20.0  # too wide: drawn turned 90 degrees
    if lying:
        w, h = h, w
    cx = (x0 + x1) / 2.0
    cy = y0 + 5.0 + h / 2.0 + 1.0
    mx, my = (max(xs) + min(xs)) / 2.0, (max(ys) + min(ys)) / 2.0

    def to_paper(p):
        u, v = (p[0] - mx) * k, (p[1] - my) * k
        if lying:
            u, v = -v, u
        return (cx + u, cy - v)

    d.polyline([to_paper(p) for p in poly], closed=True, style=LINE_THICK)
    design = item.design
    if design:
        for kind, pts, wrap, is_open in design.get("stirrups", []):
            d.polyline([to_paper(p) for p in pts], closed=not is_open, style=LINE_STIRRUP)
        for kind, a, b in design.get("ties", []):
            d.line(to_paper(a), to_paper(b), LINE_STIRRUP)
        for x, y, key in design["bars"]:
            px, py = to_paper((x, y))
            d.circle(px, py, max(0.5, MM[key] / 2000.0 * k))
    # sizes and the bar text under the section
    d.text(cx, cy + h / 2.0 + 3.0, u"{:.2f}".format((max(ys) - min(ys)) if lying else (max(xs) - min(xs))))
    d.text(cx - w / 2.0 - 3.0, cy, u"{:.2f}".format((max(xs) - min(xs)) if lying else (max(ys) - min(ys))),
           rotate=True)
    if design and design["bars"]:
        d.text(cx, cy + h / 2.0 + 8.0, ct.bars_text(design["bars"], MM))


def draw_stirrup_icon(d, cx, cy):
    """A small closed stirrup with its 135-degree hooks (FORMA)."""
    d.polyline([(cx - 6, cy - 3), (cx + 6, cy - 3), (cx + 6, cy + 3), (cx - 6, cy + 3)], closed=True)
    d.line((cx + 6, cy - 3), (cx + 3.5, cy - 0.5))
    d.line((cx + 5, cy - 3), (cx + 2.5, cy - 0.5))


def draw_table(view, items, title):
    d = Drawer(view)
    xs, total = ct.column_x()
    heights = [ct.row_height(it.polygon, SECTION_SCALE) for it in items]
    y_body = ct.TITLE_H + ct.GROUP_H + ct.HEAD_H
    bottom = y_body + sum(heights)
    split = xs[3]  # COLUMNAS | ESTRIBOS
    # bands
    d.region([(0, 0), (total, 0), (total, ct.TITLE_H), (0, ct.TITLE_H)], REGION_TITLE)
    d.region([(0, ct.TITLE_H), (total, ct.TITLE_H), (total, ct.TITLE_H + ct.GROUP_H), (0, ct.TITLE_H + ct.GROUP_H)],
             REGION_HEAD)
    # grid
    d.polyline([(0, 0), (total, 0), (total, bottom), (0, bottom)], closed=True)
    for y in (ct.TITLE_H, ct.TITLE_H + ct.GROUP_H, y_body):
        d.line((0, y), (total, y))
    y = y_body
    for h in heights[:-1]:
        y += h
        d.line((0, y), (total, y))
    d.line((split, ct.TITLE_H), (split, ct.TITLE_H + ct.GROUP_H))
    for x in xs[1:]:
        d.line((x, ct.TITLE_H + ct.GROUP_H), (x, bottom))
    # headers
    d.text(total / 2.0, ct.TITLE_H / 2.0, title, TEXT_TITLE)
    d.text(split / 2.0, ct.TITLE_H + ct.GROUP_H / 2.0, u"COLUMNAS", TEXT_HEAD)
    d.text((split + total) / 2.0, ct.TITLE_H + ct.GROUP_H / 2.0, u"ESTRIBOS", TEXT_HEAD)
    widths = [w for _, w in ct.COLUMNS]
    for (name, w), x in zip(ct.COLUMNS, xs):
        d.text(x + w / 2.0, ct.TITLE_H + ct.GROUP_H + ct.HEAD_H / 2.0, name, TEXT_HEAD)
    # rows
    y = y_body
    for item, h in zip(items, heights):
        mid = y + h / 2.0
        d.text(xs[0] + widths[0] / 2.0, mid, item.mark, TEXT_HEAD, rotate=True)
        draw_section(d, item, xs[1], xs[1] + widths[1], y, y + h)
        d.text(xs[2] + widths[2] / 2.0, mid, ct.levels_text(item.levels))
        edge_d = (item.cfg.get("EA_Estribo_Borde_Diametro") or u"").strip()
        edge_s = ct.spacing_text(item.cfg.get("EA_Estribo_Borde_Distribucion"))
        conf_d = (item.cfg.get("EA_Estribo_Conf_Diametro") or u"").strip()
        conf_s = ct.spacing_text(item.cfg.get("EA_Estribo_Conf_Distribucion"))
        if edge_d or edge_s:
            draw_stirrup_icon(d, xs[3] + widths[3] / 2.0, mid)
        diam = edge_d if not conf_d or conf_d == edge_d else u"{}\n{}".format(edge_d, conf_d)
        d.text(xs[4] + widths[4] / 2.0, mid, diam or u"-")
        spacing = edge_s if not conf_s else u"{}\nConf.: {}".format(edge_s, conf_s)
        d.text(xs[5] + widths[5] / 2.0, mid, spacing or u"-")
        y += h
    return d


def new_legend(name):
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
    view.Scale = VIEW_SCALE
    return view


def make(items, title):
    t = DB.Transaction(doc, "Cuadro de columnas")
    t.Start()
    try:
        view = new_legend(title)
        if view is None:
            t.RollBack()
            forms.alert(u"El proyecto no tiene ninguna leyenda: crea una (Vista > Leyendas) y vuelve a intentar.",
                        title="Cuadro de columnas")
            return None
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

        options = [Option(t, checked=bool(t.design and t.design["bars"])) for t in types]
        chosen = forms.SelectFromList.show(options, title=u"Cuadro de columnas - tipos a incluir",
                                           button_name=u"Crear cuadro", multiselect=True, width=640, height=520)
        if chosen:
            title = forms.ask_for_string(default=u"CUADRO DE COLUMNAS", title=u"Cuadro de columnas",
                                         prompt=u"Titulo del cuadro (tambien es el nombre de la leyenda):")
            if title:
                view = make(chosen, title.strip())
                if view is not None:
                    revit.uidoc.ActiveView = view
