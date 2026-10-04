# -*- coding: utf-8 -*-
"""Cuadro de zapatas / losa de cimentacion: con vista previa antes de crear
la leyenda; una fila por tipo con dimensiones (b, L, h, solado), acero
(superior e inferior en cada sentido, de Acero Cimentacion) y altura Df."""

__title__ = "Cuadro\nZapatas"
__author__ = "Revit MCP"

import json
import math
import os
import sys

SCRIPT_DIR = os.path.dirname(__file__)
EXT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", ".."))
REVIT_MCP_DIR = os.path.join(EXT_ROOT, "revit_mcp")
if REVIT_MCP_DIR not in sys.path:
    sys.path.append(REVIT_MCP_DIR)

import rebar_spec as rs
import rebar_foundation as rf
import footing_table as ft

reload(rs)
reload(rf)
reload(ft)

from pyrevit import revit, DB, forms
from System.Collections.Generic import List
from System.Windows import Point, Size, Thickness, TextAlignment, FontWeights
from System.Windows.Controls import Canvas, CheckBox, Orientation, StackPanel, TextBlock
from System.Windows.Media import Color, SolidColorBrush, PathGeometry, PathFigure, LineSegment, FillRule, PointCollection
from System.Windows.Shapes import Line, Path, Polygon, Polyline

doc = revit.doc
MM = rs.BAR_DIAMETERS_MM
TITLE = u"CUADRO DE ZAPATAS"
STEEL_PARAM = "EA_Cim_Acero"
COVERS_PARAM = "EA_Cim_Recubrimientos"

C_VALUE = (0, 170, 230)
C_LABEL = (230, 140, 0)
TEXTS = {  # name -> (paper mm, color, bold); black: Revit's dark theme shows it white
    ft.T_TITLE: (5.0, (0, 0, 0), True),
    ft.T_HEAD: (3.0, (0, 0, 0), True),
    ft.T_TYPE: (4.0, (0, 0, 0), True),
    ft.T_VALUE: (3.0, C_VALUE, False),
    ft.T_LABEL: (2.5, C_LABEL, False),
}
LINES = {ft.L_GRID: ((160, 160, 160), 1)}
# the preview shows the legend as Revit's dark theme does (measured on an
# exported legend)
PREVIEW_TEXT = {(0, 0, 0): (245, 245, 245)}
PREVIEW_BANDS = {ft.BAND_TITLE: (85, 108, 118), ft.BAND_HEAD: (72, 96, 109), ft.BAND_SIDE: (112, 129, 158)}
PREVIEW_BG = (33, 40, 48)


def _name(element):
    p = element.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM)
    return p.AsString() if p else element.Name


def _json(element, name):
    p = element.LookupParameter(name)
    try:
        return json.loads(p.AsString() or u"{}") if p is not None else {}
    except ValueError:
        return {}


class FootingType(object):
    """One foundation type: plan sizes, height, steel texts and Df."""

    def __init__(self, ftype, elements):
        self.type = ftype
        self.name = _name(ftype)
        self.mark = ft.footing_mark(self.name)
        self.elements = elements
        self.b = self.L = self.h = None
        saved = _json(ftype, STEEL_PARAM)
        self.has_steel = bool(saved)
        # (count, diameter, spacing) per layer and side; the texts are made
        # per row (an irregular footing shows only diameter and spacing)
        self.steel_raw = {u"sup": {u"b": None, u"L": None}, u"inf": {u"b": None, u"L": None}}
        f, sizes, rectangles = None, [], True
        for e in elements:
            try:
                fe = rf.Foundation(e)
            except Exception:
                continue
            x0, x1, y0, y1, z0, z1 = fe.extent
            sizes.append((min(x1 - x0, y1 - y0), max(x1 - x0, y1 - y0), z1 - z0))
            rectangles = rectangles and ft.is_rectangle(rf.plan_outline(fe))
            f = f or fe
        # irregular: a plan that is no rectangle, or footings of this type of
        # different sizes - b and L read "ver planta"
        self.irregular_auto = bool(sizes) and not (rectangles and ft.same_sizes(sizes))
        if f is not None:
            x0, x1, y0, y1, z0, z1 = f.extent
            dx, dy = x1 - x0, y1 - y0
            self.b, self.L, self.h = sizes[0]
            if saved:
                self._steel(f, saved, dx >= dy)
        self.bottoms = []
        for e in elements:
            bb = e.get_BoundingBox(None)
            if bb is not None:
                self.bottoms.append(bb.Min.Z * 0.3048)

    def _steel(self, f, saved, long_x):
        covers = dict((int(k), float(v)) for k, v in _json(self.type, COVERS_PARAM).items())
        planner = rf.BarPlanner(f, covers, MM)
        steel = rf.default_steel()
        for layer in (rf.BOTTOM, rf.TOP):
            steel[layer].update(saved.get(layer) or {})
        for layer, tag in ((rf.TOP, u"sup"), (rf.BOTTOM, u"inf")):
            m = steel[layer]
            if not m.get("on"):
                continue
            # across b: the bars running along L; with L along x those are
            # the X bars (spread along y)
            for side, axis in ((u"b", u"x" if long_x else u"y"), (u"L", u"y" if long_x else u"x")):
                view = rf.FRONT if axis == u"x" else rf.SIDE
                key = m["d" + axis]
                lo, hi = planner._range(view, MM[key] / 1000.0)
                positions = rf.mesh_positions(lo, hi, m, axis)
                spacing = ft.mesh_spacing(positions, m.get("m" + axis), float(m.get("s" + axis) or 0.2),
                                          m.get("z" + axis))
                self.steel_raw[tag][side] = (len(positions), key, spacing)

    def df(self, ref):
        return ft.df_text([ref - z for z in self.bottoms])

    def is_footing(self):
        return self.mark.startswith(u"Z")

    def steel_texts(self, irregular):
        out = {}
        for tag, sides in self.steel_raw.items():
            out[tag] = dict((side, ft.steel_text(raw[0], raw[1], raw[2], irregular) if raw else u"-")
                            for side, raw in sides.items())
        return out

    def label(self):
        size = u"{:.2f} x {:.2f} x {:.2f}".format(self.b, self.L, self.h) if self.b else u"?"
        return u"{}  ({} m, {} elem.) - {}".format(self.mark, size, len(self.elements),
                                                  u"con acero" if self.has_steel else u"sin acero")


def collect_types():
    by = {}
    for e in (DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_StructuralFoundation)
              .WhereElementIsNotElementType()):
        by.setdefault(e.GetTypeId().IntegerValue, []).append(e)
    found = [FootingType(doc.GetElement(DB.ElementId(tid)), els) for tid, els in by.items()]
    return sorted(found, key=lambda t: (not t.is_footing(), ft.natural_key(t.mark)))


class Row(object):
    """What the table draws of a type (Df from the chosen level)."""

    def __init__(self, item, ref, irregular):
        self.mark, self.b, self.L, self.h = item.mark, item.b, item.L, item.h
        self.irregular = irregular
        self.steel = item.steel_texts(irregular)
        self.df = item.df(ref)


# --- Revit ------------------------------------------------------------------------
def ensure_styles():
    texts = dict((_name(t), t) for t in DB.FilteredElementCollector(doc).OfClass(DB.TextNoteType))
    base = texts.get(u"DETALLES 2.5mm") or texts.get(u"2.5mm Arial") or list(texts.values())[0]
    for name, (size, rgb, bold) in TEXTS.items():
        t = texts.get(name) or base.Duplicate(name)
        t.get_Parameter(DB.BuiltInParameter.TEXT_SIZE).Set(size / 304.8)
        t.get_Parameter(DB.BuiltInParameter.LINE_COLOR).Set(rgb[0] + rgb[1] * 256 + rgb[2] * 65536)
        p = t.get_Parameter(DB.BuiltInParameter.TEXT_STYLE_BOLD)
        if p is not None and not p.IsReadOnly:
            p.Set(1 if bold else 0)
        p = t.get_Parameter(DB.BuiltInParameter.TEXT_BACKGROUND)
        if p is not None and not p.IsReadOnly:
            p.Set(1)
    cat = doc.Settings.Categories.get_Item(DB.BuiltInCategory.OST_Lines)
    subs = dict((s.Name, s) for s in cat.SubCategories)
    for name, (rgb, weight) in LINES.items():
        s = subs.get(name) or doc.Settings.Categories.NewSubcategory(cat, name)
        s.LineColor = DB.Color(rgb[0], rgb[1], rgb[2])
        s.SetLineWeight(weight, DB.GraphicsStyleType.Projection)
    regions = dict((_name(t), t) for t in DB.FilteredElementCollector(doc).OfClass(DB.FilledRegionType))
    solid = [t for t in regions.values() if _name(t) in (u"Solid Black", u"Leyenda")]
    base_r = solid[0] if solid else list(regions.values())[0]
    for name, rgb in ft.BAND_COLORS.items():
        t = regions.get(name) or base_r.Duplicate(name)
        t.ForegroundPatternColor = DB.Color(rgb[0], rgb[1], rgb[2])


class RevitDrawer(object):
    def __init__(self, view, transparency):
        self.view = view
        self.transparency = transparency
        self.f = view.Scale / 304.8
        styles = doc.Settings.Categories.get_Item(DB.BuiltInCategory.OST_Lines).SubCategories
        self.styles = dict((s.Name, s.GetGraphicsStyle(DB.GraphicsStyleType.Projection)) for s in styles)
        self.regions = dict((_name(t), t) for t in DB.FilteredElementCollector(doc).OfClass(DB.FilledRegionType))
        self.text_types = dict((_name(t), t) for t in DB.FilteredElementCollector(doc).OfClass(DB.TextNoteType))

    def p(self, x, y):
        return DB.XYZ(x * self.f, -y * self.f, 0.0)

    def line(self, a, b, style):
        if math.hypot(b[0] - a[0], b[1] - a[1]) * self.f < 0.003:
            return
        c = doc.Create.NewDetailCurve(self.view, DB.Line.CreateBound(self.p(*a), self.p(*b)))
        gs = self.styles.get(style)
        if gs is not None:
            c.LineStyle = gs

    def polyline(self, pts, closed, style):
        for a, b in zip(pts, pts[1:] + (pts[:1] if closed else [])):
            self.line(a, b, style)

    def region(self, loops, fill):
        rtype = self.regions.get(fill)
        if rtype is None:
            return
        curves = []
        for loop in loops:
            cl = DB.CurveLoop()
            for a, b in zip(loop, loop[1:] + loop[:1]):
                cl.Append(DB.Line.CreateBound(self.p(*a), self.p(*b)))
            curves.append(cl)
        r = DB.FilledRegion.Create(doc, rtype.Id, self.view.Id, List[DB.CurveLoop](curves))
        gs = self.styles.get(ft.L_GRID)
        if gs is not None:
            r.SetLineStyleId(gs.Id)
        if self.transparency:
            ogs = DB.OverrideGraphicSettings()
            ogs.SetSurfaceTransparency(int(self.transparency))
            self.view.SetElementOverrides(r.Id, ogs)

    def text(self, x, y, text, kind, rotate=False, align=u"center"):
        ttype = self.text_types.get(kind)
        opts = DB.TextNoteOptions(ttype.Id if ttype else doc.GetDefaultElementTypeId(DB.ElementTypeGroup.TextNoteType))
        opts.HorizontalAlignment = {u"center": DB.HorizontalTextAlignment.Center,
                                    u"left": DB.HorizontalTextAlignment.Left}[align]
        opts.VerticalAlignment = DB.VerticalTextAlignment.Middle
        DB.TextNote.Create(doc, self.view.Id, self.p(x, y), text, opts)


def new_legend(name, scale):
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
    return view


def make(rows, title, scale, transparency, solado):
    t = DB.Transaction(doc, "Cuadro de zapatas")
    t.Start()
    try:
        view = new_legend(title, scale)
        if view is None:
            t.RollBack()
            forms.alert(u"El proyecto no tiene ninguna leyenda: crea una (Vista > Leyendas) y vuelve a intentar.",
                        title="Cuadro de zapatas")
            return None
        ensure_styles()
        ft.draw_table(RevitDrawer(view, transparency), rows, title, solado)
        t.Commit()
    except Exception:
        t.RollBack()
        raise
    return view


# --- Preview ----------------------------------------------------------------------
def _brush(rgb):
    return SolidColorBrush(Color.FromRgb(int(rgb[0]), int(rgb[1]), int(rgb[2])))


class CanvasDrawer(object):
    def __init__(self, canvas, zoom, transparency):
        self.canvas = canvas
        self.z = zoom
        self.transparency = transparency

    def _line_brush(self, style):
        return _brush(LINES.get(style, ((200, 200, 200), 1))[0])

    def line(self, a, b, style):
        ln = Line()
        ln.X1, ln.Y1, ln.X2, ln.Y2 = a[0] * self.z, a[1] * self.z, b[0] * self.z, b[1] * self.z
        ln.Stroke = self._line_brush(style)
        ln.StrokeThickness = 1.0
        self.canvas.Children.Add(ln)

    def polyline(self, pts, closed, style):
        shape = Polygon() if closed else Polyline()
        pc = PointCollection()
        for x, y in pts:
            pc.Add(Point(x * self.z, y * self.z))
        shape.Points = pc
        shape.Stroke = self._line_brush(style)
        shape.StrokeThickness = 1.0
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
        k = self.transparency / 100.0
        path.Fill = _brush(tuple(c * (1 - k) + b * k for c, b in zip(PREVIEW_BANDS[fill], PREVIEW_BG)))
        self.canvas.Children.Add(path)

    def text(self, x, y, text, kind, rotate=False, align=u"center"):
        size, rgb, bold = TEXTS.get(kind, (2.5, (200, 200, 200), False))
        tb = TextBlock()
        tb.Text = text
        tb.FontSize = max(6.0, size * self.z * 1.35)
        tb.Foreground = _brush(PREVIEW_TEXT.get(rgb, rgb))
        if bold:
            tb.FontWeight = FontWeights.Bold
        tb.TextAlignment = TextAlignment.Center if align == u"center" else TextAlignment.Left
        tb.Measure(Size(1e5, 1e5))
        w, h = tb.DesiredSize.Width, tb.DesiredSize.Height
        Canvas.SetLeft(tb, x * self.z - (w / 2.0 if align == u"center" else 0.0))
        Canvas.SetTop(tb, y * self.z - h / 2.0)
        self.canvas.Children.Add(tb)


class _Null(object):
    def __getattr__(self, name):
        return lambda *a, **k: None


class CuadroWindow(forms.WPFWindow):
    def __init__(self, xaml_file_path, types):
        forms.WPFWindow.__init__(self, xaml_file_path)
        self.types = types
        self.result = None
        self._ready = False
        self.txt_title.Text = TITLE
        self.cbo_scale.ItemsSource = [str(s) for s in ft.SCALES]
        self.cbo_scale.SelectedItem = u"50"
        self.cbo_alpha.ItemsSource = [str(v) for v in ft.TRANSPARENCIES]
        self.cbo_alpha.SelectedItem = u"20"
        self.txt_solado.Text = u"0.10"
        levels = sorted(DB.FilteredElementCollector(doc).OfClass(DB.Level), key=lambda l: l.Elevation)
        self.levels = dict((l.Name, l.Elevation * 0.3048) for l in levels)
        self.cbo_level.ItemsSource = [l.Name for l in levels]
        ground = [l.Name for l in levels if u"NTN" in l.Name.upper()] or \
            [min(levels, key=lambda l: abs(l.Elevation)).Name]
        self.cbo_level.SelectedItem = ground[0]
        self.boxes = []
        for t in types:
            row = StackPanel()
            row.Orientation = Orientation.Horizontal
            row.Margin = Thickness(0, 2, 0, 2)
            cb = CheckBox()
            cb.Content = t.label()
            cb.IsChecked = t.is_footing()
            cb.Width = 300
            cb.Click += self.options_changed
            irr = CheckBox()
            irr.Content = u"ver planta"
            irr.IsChecked = t.irregular_auto
            irr.ToolTip = (u"b y L dicen 'ver planta' y el acero solo diametro y espaciado. "
                           u"Marcado solo si la planta no es rectangular o hay zapatas de este tipo de distinto tamano.")
            irr.Click += self.options_changed
            row.Children.Add(cb)
            row.Children.Add(irr)
            self.boxes.append((cb, t, irr))
            self.panel_types.Children.Add(row)
        self._ready = True
        self.SizeChanged += self.options_changed
        self.draw()

    def options(self):
        try:
            solado = float((self.txt_solado.Text or u"").replace(u",", u"."))
        except ValueError:
            solado = None
        ref = self.levels.get(self.cbo_level.SelectedItem, 0.0)
        rows = [Row(t, ref, bool(irr.IsChecked)) for cb, t, irr in self.boxes if cb.IsChecked]
        return (rows, (self.txt_title.Text or u"").strip() or TITLE, int(self.cbo_scale.SelectedItem or 50),
                int(self.cbo_alpha.SelectedItem or 20), solado)

    def draw(self):
        canvas = self.canvas_preview
        canvas.Children.Clear()
        rows, title, scale, alpha, solado = self.options()
        if not rows:
            self.txt_status.Text = u"Marca al menos un tipo."
            return
        w, h = ft.draw_table(_Null(), rows, title, solado)
        avail_w = max(300.0, (self.scroll_preview.ActualWidth or 1000.0) - 30.0)
        avail_h = max(200.0, (self.scroll_preview.ActualHeight or 700.0) - 20.0)
        zoom = max(1.5, min(6.0, avail_w / w, avail_h / h))  # the whole table in sight
        ft.draw_table(CanvasDrawer(canvas, zoom, alpha), rows, title, solado)
        canvas.Width, canvas.Height = w * zoom + 2, h * zoom + 2
        self.txt_status.Text = u"{} tipo(s) - escala 1:{} - {:.0f} x {:.0f} mm en papel".format(len(rows), scale, w, h)

    def options_changed(self, sender, args):
        if self._ready:
            self.draw()

    def _set_all(self, test):
        for cb, t, irr in self.boxes:
            cb.IsChecked = test(t)
        self.draw()

    def check_footings(self, sender, args):
        self._set_all(lambda t: t.is_footing())

    def check_with_steel(self, sender, args):
        self._set_all(lambda t: t.has_steel)

    def check_all(self, sender, args):
        self._set_all(lambda t: True)

    def check_none(self, sender, args):
        self._set_all(lambda t: False)

    def cancel_click(self, sender, args):
        self.Close()

    def create_click(self, sender, args):
        rows, title, scale, alpha, solado = self.options()
        if not rows:
            forms.alert(u"Marca al menos un tipo.", title="Cuadro de zapatas")
            return
        self.result = (rows, title, scale, alpha, solado)
        self.Close()


if __name__ == "__main__":
    types = collect_types()
    if not types:
        forms.alert(u"El modelo no tiene cimentaciones.", title="Cuadro de zapatas")
    else:
        window = CuadroWindow(os.path.join(SCRIPT_DIR, "CuadroForm.xaml"), types)
        window.ShowDialog()
        if window.result:
            view = make(*window.result)
            if view is not None:
                revit.uidoc.ActiveView = view
