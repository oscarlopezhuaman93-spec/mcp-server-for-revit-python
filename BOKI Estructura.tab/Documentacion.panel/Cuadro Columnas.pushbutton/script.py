# -*- coding: utf-8 -*-
"""Cuadro de columnas y placas: con vista previa (escala y nivel de
detalle) antes de crear la leyenda; una columna por tipo (TIPO, BXH,
distribucion de estribos por nivel, diametros y la seccion con su acero),
sin cotas ni anotaciones en las secciones."""

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
from System.Windows import Point, Size, Thickness, TextAlignment, FontWeights
from System.Windows.Controls import Canvas, CheckBox, TextBlock
from System.Windows.Media import (Color, SolidColorBrush, PathGeometry, PathFigure, LineSegment,
                                  FillRule, PointCollection)
from System.Windows.Shapes import Ellipse, Line, Path, Polygon, Polyline

doc = revit.doc
MM = rs.BAR_DIAMETERS_MM
TITLE = u"CUADRO DE COLUMNAS Y PLACAS"

# colors (r, g, b): the user's sample
C_TEXT = (0, 170, 230)
C_DIM = (230, 190, 0)
C_SCALE = (230, 30, 30)
C_CONCRETE = (230, 30, 30)
C_STIRRUP = (30, 60, 230)
C_BAR = (230, 0, 230)
C_GRID = (160, 160, 160)

TEXTS = {  # text types made for the table: name -> (paper mm, color)
    # on the bands: black bold, as the project legend (Revit's dark theme
    # shows it white; it prints black on the light bands)
    u"BOKI Cuadro Titulo": (5.0, (0, 0, 0)),
    u"BOKI Cuadro Cabecera": (3.0, (0, 0, 0)),
    u"BOKI Cuadro 3mm": (3.0, C_TEXT),
    u"BOKI Cuadro 2mm": (2.0, C_TEXT),
    u"BOKI Cuadro Escala": (2.2, C_SCALE),
}
LINES = {  # line styles: name -> (color, weight)
    u"BOKI Cuadro Grilla": (C_GRID, 1),
    u"BOKI Cuadro Concreto": (C_CONCRETE, 5),
    u"BOKI Cuadro Estribo": (C_STIRRUP, 1),
    u"BOKI Cuadro Barra": (C_BAR, 1),
    # black: Revit shows it white on its dark background, prints black - as
    # rebar at fine detail
    u"BOKI Cuadro Acero Fino": ((0, 0, 0), 1),
}
REGIONS = {  # solid fills: name -> color
    u"BOKI Cuadro Barra": C_BAR,
    u"BOKI Cuadro Estribo": C_STIRRUP,
}
REGIONS.update(ct.BAND_COLORS)
BOLD = (u"BOKI Cuadro Titulo", u"BOKI Cuadro Cabecera")
TRANSPARENCIES = (0, 10, 20, 30, 40, 50)  # % of the bands
PREVIEW_COLORS = dict([(k, v[1]) for k, v in TEXTS.items()] + [(k, v[0]) for k, v in LINES.items()])
PREVIEW_COLORS[u"BOKI Cuadro Acero Fino"] = (225, 225, 225)
# the preview shows the legend as Revit's dark theme does (measured on an
# exported legend): black text white, the light bands slate
PREVIEW_COLORS[u"BOKI Cuadro Titulo"] = (245, 245, 245)
PREVIEW_COLORS[u"BOKI Cuadro Cabecera"] = (245, 245, 245)
PREVIEW_BANDS = {ct.BAND_TITLE: (85, 108, 118), ct.BAND_TYPES: (72, 96, 109), ct.BAND_SIDE: (112, 129, 158)}
PREVIEW_BG = (33, 40, 48)


def _name(element):
    p = element.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM)
    return p.AsString() if p else element.Name


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
        self.levels = (ct.levels_between(levels, low * 0.3048 - 0.3, high * 0.3048 - 0.3)
                       if low is not None else [])

    def has_steel(self):
        return bool(self.design and self.design["bars"])

    def label(self):
        state = u"con armado" if self.has_steel() else u"SIN ARMADO"
        return u"{}  ({}, {} col.) - {}".format(self.mark, ct.shape_text(self.polygon) if self.polygon else u"?",
                                               len(self.columns), state)


def collect_types():
    levels = [(l.Name, l.Elevation * 0.3048) for l in DB.FilteredElementCollector(doc).OfClass(DB.Level)]
    by_type = {}
    for c in (DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_StructuralColumns)
              .WhereElementIsNotElementType()):
        by_type.setdefault(c.GetTypeId().IntegerValue, []).append(c)
    found = [ColumnType(doc.GetElement(DB.ElementId(tid)), cols, levels) for tid, cols in by_type.items()]
    return sorted(found, key=lambda t: ct.natural_key(t.mark))


# --- Revit ------------------------------------------------------------------------
def ensure_styles():
    """The table's own text types, line styles and solid fills (made once,
    then reused)."""
    texts = dict((_name(t), t) for t in DB.FilteredElementCollector(doc).OfClass(DB.TextNoteType))
    base = texts.get(u"DETALLES 2.5mm") or texts.get(u"2.5mm Arial") or list(texts.values())[0]
    for name, (size, rgb) in TEXTS.items():
        t = texts.get(name) or base.Duplicate(name)
        t.get_Parameter(DB.BuiltInParameter.TEXT_SIZE).Set(size / 304.8)
        t.get_Parameter(DB.BuiltInParameter.LINE_COLOR).Set(rgb[0] + rgb[1] * 256 + rgb[2] * 65536)
        bold = t.get_Parameter(DB.BuiltInParameter.TEXT_STYLE_BOLD)
        if bold is not None and not bold.IsReadOnly:
            bold.Set(1 if name in BOLD else 0)
        bg = t.get_Parameter(DB.BuiltInParameter.TEXT_BACKGROUND)
        if bg is not None and not bg.IsReadOnly:
            bg.Set(1)  # transparent
    cat = doc.Settings.Categories.get_Item(DB.BuiltInCategory.OST_Lines)
    subs = dict((s.Name, s) for s in cat.SubCategories)
    for name, (rgb, weight) in LINES.items():
        s = subs.get(name) or doc.Settings.Categories.NewSubcategory(cat, name)
        s.LineColor = DB.Color(rgb[0], rgb[1], rgb[2])
        s.SetLineWeight(weight, DB.GraphicsStyleType.Projection)
    regions = dict((_name(t), t) for t in DB.FilteredElementCollector(doc).OfClass(DB.FilledRegionType))
    solid = [t for t in regions.values() if _name(t) in (u"Solid Black", u"Relleno solido")]
    base_r = solid[0] if solid else list(regions.values())[0]
    for name, rgb in REGIONS.items():
        t = regions.get(name) or base_r.Duplicate(name)
        t.ForegroundPatternColor = DB.Color(rgb[0], rgb[1], rgb[2])


class RevitDrawer(object):
    """The drawer (column_table) on a legend: detail lines, filled regions,
    text notes."""

    def __init__(self, view, transparency=0):
        self.view = view
        self.transparency = transparency  # % for the colored bands
        self.f = view.Scale / 304.8  # paper mm -> feet in the view
        styles = doc.Settings.Categories.get_Item(DB.BuiltInCategory.OST_Lines).SubCategories
        self.styles = dict((s.Name, s.GetGraphicsStyle(DB.GraphicsStyleType.Projection)) for s in styles)
        self.regions = dict((_name(t), t) for t in DB.FilteredElementCollector(doc).OfClass(DB.FilledRegionType))
        self.text_types = dict((_name(t), t) for t in DB.FilteredElementCollector(doc).OfClass(DB.TextNoteType))

    def p(self, x, y):
        return DB.XYZ(x * self.f, -y * self.f, 0.0)

    def _curve(self, curve, style):
        c = doc.Create.NewDetailCurve(self.view, curve)
        gs = self.styles.get(style)
        if gs is not None:
            c.LineStyle = gs

    def line(self, a, b, style):
        if math.hypot(b[0] - a[0], b[1] - a[1]) * self.f < 0.003:
            return
        self._curve(DB.Line.CreateBound(self.p(*a), self.p(*b)), style)

    def polyline(self, pts, closed, style):
        for a, b in zip(pts, pts[1:] + (pts[:1] if closed else [])):
            self.line(a, b, style)

    def _loop(self, pts):
        clean = []
        for q in pts:
            if not clean or math.hypot(q[0] - clean[-1][0], q[1] - clean[-1][1]) * self.f > 0.003:
                clean.append(q)
        if math.hypot(clean[0][0] - clean[-1][0], clean[0][1] - clean[-1][1]) * self.f < 0.003:
            clean.pop()
        loop = DB.CurveLoop()
        for a, b in zip(clean, clean[1:] + clean[:1]):
            loop.Append(DB.Line.CreateBound(self.p(*a), self.p(*b)))
        return loop

    def _edge(self, region, name):
        gs = self.styles.get(name)
        if gs is not None:
            region.SetLineStyleId(gs.Id)

    def region(self, loops, fill):
        rtype = self.regions.get(fill)
        if rtype is None:
            return
        try:
            r = DB.FilledRegion.Create(doc, rtype.Id, self.view.Id, List[DB.CurveLoop]([self._loop(l) for l in loops]))
            self._edge(r, fill)
            if fill in ct.BAND_COLORS:
                self._edge(r, u"BOKI Cuadro Grilla")
                if self.transparency:
                    ogs = DB.OverrideGraphicSettings()
                    ogs.SetSurfaceTransparency(int(self.transparency))
                    self.view.SetElementOverrides(r.Id, ogs)
        except Exception:
            pass  # a degenerate piece: left out rather than failing the table

    def _arcs(self, cx, cy, r):
        c, rad = self.p(cx, cy), r * self.f
        return [DB.Arc.Create(c, rad, 0.0, math.pi, DB.XYZ.BasisX, DB.XYZ.BasisY),
                DB.Arc.Create(c, rad, math.pi, 2 * math.pi, DB.XYZ.BasisX, DB.XYZ.BasisY)]

    def circle(self, cx, cy, r, fill):
        rtype = self.regions.get(fill)
        if rtype is None:
            return
        loop = DB.CurveLoop()
        for arc in self._arcs(cx, cy, r):
            loop.Append(arc)
        self._edge(DB.FilledRegion.Create(doc, rtype.Id, self.view.Id, List[DB.CurveLoop]([loop])), fill)

    def ring(self, cx, cy, r, style):
        for arc in self._arcs(cx, cy, r):
            self._curve(arc, style)

    def text(self, x, y, text, kind, rotate=False, align=u"center"):
        ttype = self.text_types.get(kind)
        opts = DB.TextNoteOptions(ttype.Id if ttype else doc.GetDefaultElementTypeId(DB.ElementTypeGroup.TextNoteType))
        opts.HorizontalAlignment = {u"center": DB.HorizontalTextAlignment.Center,
                                    u"left": DB.HorizontalTextAlignment.Left}[align]
        opts.VerticalAlignment = DB.VerticalTextAlignment.Middle
        if rotate:
            opts.Rotation = math.pi / 2.0
        DB.TextNote.Create(doc, self.view.Id, self.p(x, y), text, opts)


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


def make(items, title, scale, detail, transparency=20):
    t = DB.Transaction(doc, "Cuadro de columnas")
    t.Start()
    try:
        view = new_legend(title, scale)
        if view is None:
            t.RollBack()
            forms.alert(u"El proyecto no tiene ninguna leyenda: crea una (Vista > Leyendas) y vuelve a intentar.",
                        title="Cuadro de columnas")
            return None
        ensure_styles()
        ct.draw_table(RevitDrawer(view, transparency), items, title, scale, detail, MM)
        t.Commit()
    except Exception:
        t.RollBack()
        raise
    return view


# --- Preview ----------------------------------------------------------------------
def _brush(rgb):
    return SolidColorBrush(Color.FromRgb(rgb[0], rgb[1], rgb[2]))


class CanvasDrawer(object):
    """The same drawer on a WPF canvas: the preview shows what the legend
    will hold."""

    def __init__(self, canvas, zoom, transparency=0):
        self.canvas = canvas
        self.z = zoom  # pixels per paper mm
        self.transparency = transparency

    def _color(self, name):
        return _brush(PREVIEW_COLORS.get(name) or REGIONS.get(name) or (200, 200, 200))

    def _width(self, style):
        return 2.0 if style == u"BOKI Cuadro Concreto" else 1.0

    def line(self, a, b, style):
        ln = Line()
        ln.X1, ln.Y1, ln.X2, ln.Y2 = a[0] * self.z, a[1] * self.z, b[0] * self.z, b[1] * self.z
        ln.Stroke = self._color(style)
        ln.StrokeThickness = self._width(style)
        self.canvas.Children.Add(ln)

    def polyline(self, pts, closed, style):
        shape = Polygon() if closed else Polyline()
        pc = PointCollection()
        for x, y in pts:
            pc.Add(Point(x * self.z, y * self.z))
        shape.Points = pc
        shape.Stroke = self._color(style)
        shape.StrokeThickness = self._width(style)
        self.canvas.Children.Add(shape)

    def region(self, loops, fill):
        geo = PathGeometry()
        geo.FillRule = FillRule.EvenOdd
        for loop in loops:
            fig = PathFigure()
            fig.StartPoint = Point(loop[0][0] * self.z, loop[0][1] * self.z)
            for x, y in loop[1:]:
                fig.Segments.Add(LineSegment(Point(x * self.z, y * self.z), True))
            fig.IsClosed = True
            geo.Figures.Add(fig)
        path = Path()
        path.Data = geo
        path.Fill = self._color(fill)
        if fill in PREVIEW_BANDS:
            # on screen a transparent band mixes with the dark background
            k = self.transparency / 100.0
            path.Fill = _brush(tuple(int(c * (1 - k) + b * k) for c, b in zip(PREVIEW_BANDS[fill], PREVIEW_BG)))
        self.canvas.Children.Add(path)

    def _ellipse(self, cx, cy, r):
        e = Ellipse()
        e.Width = e.Height = max(1.0, 2 * r * self.z)
        Canvas.SetLeft(e, cx * self.z - e.Width / 2.0)
        Canvas.SetTop(e, cy * self.z - e.Height / 2.0)
        self.canvas.Children.Add(e)
        return e

    def circle(self, cx, cy, r, fill):
        self._ellipse(cx, cy, r).Fill = self._color(fill)

    def ring(self, cx, cy, r, style):
        e = self._ellipse(cx, cy, r)
        e.Stroke = self._color(style)
        e.StrokeThickness = 1.0

    def text(self, x, y, text, kind, rotate=False, align=u"center"):
        size = TEXTS.get(kind, (2.0, C_TEXT))[0]
        tb = TextBlock()
        tb.Text = text
        tb.FontSize = max(6.0, size * self.z * 1.35)
        tb.Foreground = self._color(kind)
        if kind in BOLD:
            tb.FontWeight = FontWeights.Bold
        tb.TextAlignment = TextAlignment.Center if align == u"center" else TextAlignment.Left
        tb.Measure(Size(1e5, 1e5))
        w, h = tb.DesiredSize.Width, tb.DesiredSize.Height
        left = x * self.z - (w / 2.0 if align == u"center" else 0.0)
        Canvas.SetLeft(tb, left)
        Canvas.SetTop(tb, y * self.z - h / 2.0)
        self.canvas.Children.Add(tb)


class CuadroWindow(forms.WPFWindow):
    def __init__(self, xaml_file_path, types):
        forms.WPFWindow.__init__(self, xaml_file_path)
        self.types = types
        self.result = None
        self._ready = False
        self.txt_title.Text = TITLE
        self.cbo_scale.ItemsSource = [str(s) for s in ct.SCALES]
        self.cbo_scale.SelectedItem = u"10"
        self.cbo_detail.ItemsSource = list(ct.DETAILS)
        self.cbo_detail.SelectedItem = ct.DETAIL_HIGH
        self.cbo_alpha.ItemsSource = [str(v) for v in TRANSPARENCIES]
        self.cbo_alpha.SelectedItem = u"20"
        self.boxes = []
        for t in types:
            cb = CheckBox()
            cb.Content = t.label()
            cb.IsChecked = t.has_steel()
            cb.Margin = Thickness(0, 2, 0, 2)
            cb.Click += self.options_changed
            self.boxes.append((cb, t))
            self.panel_types.Children.Add(cb)
        self._ready = True
        self.SizeChanged += self.options_changed
        self.draw()

    def chosen(self):
        return [t for cb, t in self.boxes if cb.IsChecked]

    def options(self):
        return (self.chosen(), (self.txt_title.Text or u"").strip() or TITLE,
                int(self.cbo_scale.SelectedItem or 10), self.cbo_detail.SelectedItem or ct.DETAIL_HIGH,
                int(self.cbo_alpha.SelectedItem or 20))

    def draw(self):
        canvas = self.canvas_preview
        canvas.Children.Clear()
        items, title, scale, detail, alpha = self.options()
        if not items:
            self.txt_status.Text = u"Marca al menos un tipo."
            return
        # size first (a throwaway pass), then fit the width of the panel
        class _Size(object):
            def __getattr__(self, name):
                return lambda *a, **k: None
        w, h = ct.draw_table(_Size(), items, title, scale, detail, MM)
        avail = max(300.0, (self.scroll_preview.ActualWidth or 1000.0) - 30.0)
        zoom = max(2.0, min(6.0, avail / w))
        ct.draw_table(CanvasDrawer(canvas, zoom, alpha), items, title, scale, detail, MM)
        canvas.Width, canvas.Height = w * zoom + 2, h * zoom + 2
        self.txt_status.Text = u"{} tipo(s) - escala 1:{} - detalle {} - {:.0f} x {:.0f} mm en papel".format(
            len(items), scale, detail.lower(), w, h)

    def options_changed(self, sender, args):
        if self._ready:
            self.draw()

    def _set_all(self, test):
        for cb, t in self.boxes:
            cb.IsChecked = test(t)
        self.draw()

    def check_with_steel(self, sender, args):
        self._set_all(lambda t: t.has_steel())

    def check_all(self, sender, args):
        self._set_all(lambda t: True)

    def check_none(self, sender, args):
        self._set_all(lambda t: False)

    def cancel_click(self, sender, args):
        self.Close()

    def create_click(self, sender, args):
        items, title, scale, detail, alpha = self.options()
        if not items:
            forms.alert(u"Marca al menos un tipo.", title="Cuadro de columnas")
            return
        self.result = (items, title, scale, detail, alpha)
        self.Close()


if __name__ == "__main__":
    types = collect_types()
    if not types:
        forms.alert(u"El modelo no tiene columnas.", title="Cuadro de columnas")
    else:
        window = CuadroWindow(os.path.join(SCRIPT_DIR, "CuadroForm.xaml"), types)
        window.ShowDialog()
        if window.result:
            view = make(*window.result)
            if view is not None:
                revit.uidoc.ActiveView = view
