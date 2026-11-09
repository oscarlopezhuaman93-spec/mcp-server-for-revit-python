# -*- coding: utf-8 -*-
"""Genera el acero de refuerzo (barras 3D) y su metrado en kg para
vigas de concreto (armazon estructural). La configuracion (estribos,
anclaje y el dibujo de la seccion con estribos, grapas y barras
superiores e inferiores) se guarda en cada tipo de viga. Las barras
corren por toda la viga (sus tramos del mismo tipo y eje); los estribos
se distribuyen en cada luz libre desde la cara de cada apoyo."""

__title__ = "Acero\nViga"
__author__ = "Revit MCP"

import io
import json
import math
import os
import re
import sys

SCRIPT_DIR = os.path.dirname(__file__)
EXT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", ".."))
REVIT_MCP_DIR = os.path.join(EXT_ROOT, "revit_mcp")
if REVIT_MCP_DIR not in sys.path:
    sys.path.append(REVIT_MCP_DIR)

import formwork_spatial as fw_spatial
import selection_hosts
import formwork_params as fw_params
import rebar_spec as rs
import beam_extras as bx
import rebar_columns as rc
import rebar_beams as rb
import rebar_views as rv
import sloped_plan as sp
import rebar_sloped as rsl
import utils as fw_utils

# pyRevit keeps one interpreter across clicks: reload so edits on disk
# are picked up (same as the Encofrado button).
reload(fw_utils)
reload(fw_spatial)
reload(fw_params)
reload(rs)
reload(bx)
reload(rc)
reload(rb)
reload(rv)
reload(sp)
reload(rsl)

from pyrevit import revit, DB, forms, script
from Autodesk.Revit.Exceptions import OperationCanceledException
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType
from System.Collections.Generic import List
from System.Windows import (FontWeights, HorizontalAlignment, Point, Size, Thickness,
                            VerticalAlignment, Visibility)
from Microsoft.Win32 import OpenFileDialog, SaveFileDialog
from Autodesk.Revit.DB.Structure import RebarShape, RebarStyle
from System.Windows.Input import Cursors, Key, Keyboard, ModifierKeys, MouseButton
from System.Windows import GridLength, TextWrapping
from System.Windows.Controls import Border, ComboBox, Dock, DockPanel, Grid, WrapPanel
from System.Windows.Controls import (Button, Canvas, CheckBox, ListBoxItem, Orientation, StackPanel,
                                     TextBlock, TextBox)
from System.Windows.Media import (Color, DoubleCollection, PenLineCap, PenLineJoin, PointCollection,
                                  SolidColorBrush)
from System.Windows.Shapes import Ellipse, Line, Polygon, Polyline

output = script.get_output()
doc = revit.doc

FRAMING_BIC = DB.BuiltInCategory.OST_StructuralFraming
STIRRUP_DIAMETERS = [u"6mm", u"8mm", u'1/4"', u'3/8"', u"12mm", u'1/2"']
BAR_SLOT_DEFAULTS = [u'1/2"', u'5/8"', u'3/4"', u'1"']  # the four "Barras" options
# pyRevit settings shared by the Acero buttons (column and beam): bar
# options, splice and hook lengths per diameter.
CONFIG_SECTION = "OLSTR_Acero"


def load_bar_slots():
    """The diameters of the four "Barras" options the user chose last time
    (pyRevit settings of this button), else the defaults."""
    try:
        saved = script.get_config(CONFIG_SECTION).get_option("bar_slots", u"") or u""
        keys = [rs.parse_diameter(k) for k in saved.split(u"|") if k.strip()]
        if len(keys) == len(BAR_SLOT_DEFAULTS):
            return keys
    except Exception:
        pass
    return list(BAR_SLOT_DEFAULTS)


def save_bar_slots(keys):
    """Remember the four diameters (inches written 'pulg': no quotes in the
    settings file)."""
    try:
        config = script.get_config(CONFIG_SECTION)
        config.bar_slots = u"|".join((k or u"").replace(u'"', u"pulg") for k in keys)
        script.save_config()
    except Exception:
        pass


def load_splice_settings():
    """{"on": bool, "max": m, "laps": {diameter key: cm}, "active": {keys}}:
    the splice settings (shared pyRevit settings). Every diameter's lap is
    prefilled from Norma E.060 (rs.e060_lap_cm, class B) until edited;
    only the diameters in "active" are spliced."""
    settings = {"on": True, "max": rs.MAX_BAR_LENGTH, "active": set(),
                "laps": dict((k, float(rs.e060_lap_cm(k))) for k in rs.bar_diameter_keys())}
    try:
        config = script.get_config(CONFIG_SECTION)
        # on unless turned off: stacked columns / whole beams get continuous bars
        settings["on"] = (config.get_option("splice_on", u"1") or u"1") == u"1"
        settings["max"] = float(config.get_option("splice_max", u"") or rs.MAX_BAR_LENGTH)
        for pair in (config.get_option("splice_laps", u"") or u"").split(u"|"):
            if u"=" in pair:
                key, cm = pair.split(u"=", 1)
                settings["laps"][rs.parse_diameter(key)] = float(cm)
        for key in (config.get_option("splice_active", u"") or u"").split(u"|"):
            if key.strip():
                settings["active"].add(rs.parse_diameter(key))
    except Exception:
        pass
    return settings


def save_splice_settings(settings):
    try:
        config = script.get_config(CONFIG_SECTION)
        config.splice_on = u"1" if settings["on"] else u"0"
        config.splice_max = u"{}".format(settings["max"])
        config.splice_laps = u"|".join(u"{}={}".format(k.replace(u'"', u"pulg"), v)
                                       for k, v in sorted(settings["laps"].items()))
        config.splice_active = u"|".join(k.replace(u'"', u"pulg") for k in sorted(settings["active"]))
        script.save_config()
    except Exception:
        pass


def splice_for_generation(settings):
    """What the generation takes: None when continuous bars are off, else
    {"max": m, "laps": {key: m}} with only the diameters to splice (a bar
    of another diameter past the maximum stays whole, with a warning)."""
    if not settings["on"]:
        return None
    return {"max": settings["max"],
            "laps": dict((k, settings["laps"][k] / 100.0) for k in settings["active"] if k in settings["laps"])}


def lap_rows(panel, settings, handler):
    """One row per diameter in `panel`: a check "splice this diameter" and
    its lap (cm). Returns ({key: box}, {key: check})."""
    boxes, checks = {}, {}
    panel.Children.Clear()
    for key in rs.bar_diameter_keys():
        cell = StackPanel()
        cell.Orientation = Orientation.Horizontal
        cell.Margin = Thickness(0, 0, 8, 4)
        check = CheckBox()
        check.Content = u"\u00d8{}".format(key)
        check.Width = 62
        check.VerticalAlignment = VerticalAlignment.Center
        check.IsChecked = key in settings["active"]
        check.ToolTip = u"Marcado: las barras de este diametro se empalman al superar la longitud maxima."
        check.Click += handler
        box = TextBox()
        box.Width = 34
        cm = settings["laps"].get(key)
        box.Text = u"{:g}".format(cm) if cm else u""
        box.ToolTip = u"Longitud de empalme (cm). Sugerido: E.060 clase B = {} cm (f'c 210, fy 4200).".format(
            rs.e060_lap_cm(key))
        box.TextChanged += handler
        cell.Children.Add(check)
        cell.Children.Add(box)
        panel.Children.Add(cell)
        boxes[key] = box
        checks[key] = check
    return boxes, checks


def lap_form(boxes, checks):
    """({key: cm} typed, {keys checked})."""
    laps = {}
    for key, box in boxes.items():
        try:
            cm = float((box.Text or u"").replace(u",", u"."))
        except ValueError:
            continue
        if cm > 0:
            laps[key] = cm
    return laps, set(key for key, check in checks.items() if check.IsChecked)


def load_hook_legs():
    """{diameter key: cm} - the leg of the 90-degree end hooks, per diameter
    (shared pyRevit settings)."""
    legs = {}
    try:
        for pair in (script.get_config(CONFIG_SECTION).get_option("hook_legs", u"") or u"").split(u"|"):
            if u"=" in pair:
                key, cm = pair.split(u"=", 1)
                value = float(cm)
                # typed in meters (0.25 for 25 cm): no bar is bent under 1 cm
                legs[rs.parse_diameter(key)] = value * 100.0 if 0.0 < value < 1.0 else value
    except Exception:
        pass
    return legs


def save_hook_legs(legs):
    try:
        config = script.get_config(CONFIG_SECTION)
        config.hook_legs = u"|".join(u"{}={}".format(k.replace(u'"', u"pulg"), v) for k, v in sorted(legs.items()))
        script.save_config()
    except Exception:
        pass


SNAP_PX = 12  # a click this close to a bar snaps to it
MARGIN_PX = 30


def brush(r, g, b, a=255):
    return SolidColorBrush(Color.FromArgb(a, r, g, b))


C_CONCRETE = brush(225, 228, 232)
C_OUTLINE = brush(60, 60, 60)
C_COVER = brush(150, 150, 150)
C_BAR = brush(40, 40, 40)
C_STIRRUP = brush(214, 120, 60)  # perimeter ("borde") stirrup
C_CONFINEMENT = brush(40, 150, 90)  # confinement stirrups and ties
C_DRAFT = brush(40, 110, 220)
C_CURSOR = brush(40, 110, 220, 160)
C_LABEL_BG = brush(255, 255, 255, 200)
C_REFUSED = brush(192, 57, 43)  # cursor where a stirrup corner is refused


def id_of(element_id):
    return fw_utils.element_id_value(element_id)


class BeamType(object):
    """A beam type in use in the model: its elements (`columns`, the name
    the window code shares with Acero Columna) and its beam lines."""

    def __init__(self, type_id, beams):
        self.id = type_id
        self.element = doc.GetElement(DB.ElementId(type_id))
        self.name = rc.element_name(self.element)
        self.columns = beams
        self._section = None
        self._lines = None
        self.section_error = None

    @property
    def section(self):
        if self._section is None and self.section_error is None:
            for beam in self.columns:
                try:
                    self._section = rb.BeamSection(beam)
                    break
                except Exception as e:
                    self.section_error = u"{}".format(e)
            if self._section is not None:
                self.section_error = None
        return self._section

    @property
    def lines(self):
        """The type's beam lines (one beam each, its elements in order)."""
        if self._lines is None:
            self._lines = rb.beam_lines(self.columns)
        return self._lines

    @property
    def short_name(self):
        """The end of a long family-style name, where the mark and size are
        ('SFA_VIGA ... F'C=210Kg/cm²_VS1-18_0.50x0.60m' -> 'VS1-18_0.50x0.60m')."""
        for sep in (u"cm²_", u"cm2_"):
            if sep in self.name:
                return self.name.split(sep)[-1]
        return self.name

    def config(self):
        return rb.read_type_config(self.element)

    def configured(self):
        cfg = self.config()
        return bool(cfg["EA_Estribo_Borde_Distribucion"] and cfg["EA_Seccion_Armado"])


class State(object):
    """What survives closing the window to pick beams in the model."""

    def __init__(self):
        self.checked = set()
        self.picked_ids = []
        self.active = None
        self.drafts = {}  # type id -> design being drawn (unsaved)
        self.form = None  # last form field values
        self.scope = "model"  # "pick" when beams were picked in the model
        self.line_edits = {}  # beam line key (element ids) -> its own settings (bx), edited


class _BeamFilter(ISelectionFilter):
    def AllowElement(self, element):
        category = element.Category
        return category is not None and id_of(category.Id) == int(FRAMING_BIC)

    def AllowReference(self, reference, point):
        return False


def collect_types():
    by_type = {}
    for b in (
        DB.FilteredElementCollector(doc).OfCategory(FRAMING_BIC).WhereElementIsNotElementType()
    ):
        by_type.setdefault(id_of(b.GetTypeId()), []).append(b)
    return sorted(
        [BeamType(t, beams) for t, beams in by_type.items()], key=lambda bt: bt.name
    )


SHAPE_PREVIEW_W = 200
SHAPE_PREVIEW_H = 95


def read_rebar_shapes():
    """The project's rebar shapes as Revit's own Rebar Shape Browser draws
    them (GetCurvesForBrowser: segments, bends and hooks), read fresh so
    shapes loaded meanwhile show up."""
    shapes = []
    for shape in DB.FilteredElementCollector(doc).OfClass(RebarShape):
        try:
            curves = list(shape.GetCurvesForBrowser())
        except Exception:
            continue
        strokes, lines = [], []
        for curve in curves:
            strokes.append([(q.X, q.Y) for q in curve.Tessellate()])
            if isinstance(curve, DB.Line):
                a, b = curve.GetEndPoint(0), curve.GetEndPoint(1)
                lines.append(((a.X, a.Y), (b.X, b.Y)))
        if not strokes:
            continue
        hooks = []
        for end_index in (0, 1):
            try:
                hooks.append(bool(shape.GetDefaultHookAngle(end_index)))
            except Exception:
                hooks.append(False)
        shapes.append({
            "name": rc.element_name(shape),
            "stirrup": shape.RebarStyle == RebarStyle.StirrupTie,
            "strokes": strokes,
            "lines": lines,
            "hooks": hooks,
        })
    shapes.sort(key=lambda sh: _natural_key(sh["name"]))
    return shapes


def shape_preview(strokes):
    """The shape drawn like in Revit's browser: same proportions, dark
    strokes, bends and hooks included."""
    canvas = Canvas()
    canvas.Width, canvas.Height = SHAPE_PREVIEW_W, SHAPE_PREVIEW_H
    pts = [q for stroke in strokes for q in stroke]
    xs = [x for x, _ in pts]
    ys = [y for _, y in pts]
    span = max(max(xs) - min(xs), max(ys) - min(ys)) or 1.0
    scale = min((SHAPE_PREVIEW_W - 24) / span, (SHAPE_PREVIEW_H - 16) / span)
    ox = SHAPE_PREVIEW_W / 2.0 - (max(xs) + min(xs)) / 2.0 * scale
    oy = SHAPE_PREVIEW_H / 2.0 + (max(ys) + min(ys)) / 2.0 * scale
    for stroke in strokes:
        line = Polyline()
        points = PointCollection()
        for x, y in stroke:
            points.Add(Point(ox + x * scale, oy - y * scale))
        line.Points = points
        line.Stroke = C_BAR
        line.StrokeThickness = 2
        canvas.Children.Add(line)
    return canvas

def _natural_key(name):
    """'Forma 2' before 'Forma 10'."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def _clip_path_s(points, lo, hi):
    """The polyline [(x, y, s)] kept to lo <= s <= hi (the stretch inside)."""
    out = []
    for i, p in enumerate(points):
        if lo <= p[2] <= hi:
            if i > 0 and not (lo <= points[i - 1][2] <= hi):
                q = points[i - 1]
                v = lo if q[2] < lo else hi
                t = (v - q[2]) / ((p[2] - q[2]) or 1e-9)
                out.append(tuple([q[j] + (p[j] - q[j]) * t for j in range(3)]))
            out.append(p)
        elif out:
            q = out[-1]
            v = lo if p[2] < lo else hi
            t = (v - q[2]) / ((p[2] - q[2]) or 1e-9)
            out.append(tuple([q[j] + (p[j] - q[j]) * t for j in range(3)]))
            break
    return out


def _clip_s(points, lo, hi):
    """The polygon [(s, y)] cut to lo <= s <= hi (Sutherland-Hodgman)."""
    def clip(pts, inside, cut):
        out = []
        for i in range(len(pts)):
            a, b = pts[i - 1], pts[i]
            if inside(b):
                if not inside(a):
                    out.append(cut(a, b))
                out.append(b)
            elif inside(a):
                out.append(cut(a, b))
        return out

    def at(v):
        return lambda a, b: (v, a[1] + (b[1] - a[1]) * (v - a[0]) / ((b[0] - a[0]) or 1e-9))
    pts = clip(list(points), lambda p: p[0] >= lo, at(lo)) if points else []
    return clip(pts, lambda p: p[0] <= hi, at(hi)) if pts else []


def _hull(points):
    """Convex outline [(a, b)] of the points (monotone chain)."""
    pts = sorted(set([(round(a, 4), round(b, 4)) for a, b in points]))
    if len(pts) < 3:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


class AceroWindow(forms.WPFWindow):
    def __init__(self, xaml_file_path, types, state):
        # Esc must not close the window (it cancels the current stroke,
        # see window_key).
        forms.WPFWindow.__init__(self, xaml_file_path, handle_esc=False)
        self.types = types
        self.by_id = dict((t.id, t) for t in types)
        self.state = state
        self.action = None
        self.checkboxes = {}
        self.design = rs.empty_design()
        self.undo_stack = []
        self.draft = []  # stirrup vertices / tie start being drawn (meters)
        self.cursor_m = None
        self.cursor_on_bar = False
        self.dirty = False

        self.cbo_conf.ItemsSource = List[str](STIRRUP_DIAMETERS)
        self.cbo_edge.ItemsSource = List[str](STIRRUP_DIAMETERS)
        # "Ø acero" of the sketch: which stirrup family a new stirrup/tie
        # uses. A new longitudinal bar takes the diameter of the "Barras"
        # option chosen.
        self.kind_for = {"stirrup": rs.KIND_EDGE, "tie": rs.KIND_CONFINEMENT}
        # "Barras": four options, each with the diameter chosen in its own
        # list (remembered in the user's pyRevit settings).
        self.bar_tools = [(self.rb_bar_1, self.cbo_bar_1), (self.rb_bar_2, self.cbo_bar_2),
                          (self.rb_bar_3, self.cbo_bar_3), (self.rb_bar_4, self.cbo_bar_4)]
        self._filling_bars = True
        for (radio, cbo), key in zip(self.bar_tools, load_bar_slots()):
            cbo.ItemsSource = List[str](rs.bar_diameter_keys())
            cbo.SelectedItem = key
        self._filling_bars = False
        self.bar_key = self.cbo_bar_2.SelectedItem
        self._fill_splice()
        self.cbo_anchor.ItemsSource = List[str](list(rb.ANCHORS))
        self.cbo_anchor.SelectedItem = rb.ANCHOR_HOOK
        self._updating_steel = False
        self.selected = None  # index of the stirrup whose measures are shown
        # Plan / elevation / 3D views: zoom-pan state and what they last showed.
        self.plan_nav = rv.Nav2D()
        self.elev_nav = rv.Nav2D()
        self.scene = rv.Scene3D(self.view3d)
        self.cbo_detail.ItemsSource = List[str](list(rv.DETAIL_LEVELS))
        self.cbo_detail.SelectedItem = u"Medio"
        self._views_sig = None
        self._elev_data = None
        self._support_cache = {}  # beam line key -> its supports (rb.line_supports)
        self._view_line = None  # the beam line the 3D view and elevation show
        self._filling_columns = False

        self._refresh_steel()
        # The shape clicked in the browser: the stirrups (or, a straight one,
        # the ties) drawn next are created with it. None: Revit picks.
        self.active_shape = None
        self._filling_shapes = False
        self.rebar_shapes = read_rebar_shapes()
        self._fill_shapes()
        picked_types = set(id_of(e.GetTypeId()) for e in
                           (doc.GetElement(DB.ElementId(i)) for i in state.picked_ids) if e is not None)
        for t in types:
            if picked_types and t.id not in picked_types:
                continue  # with beams picked, only their types are listed
            self.list_types.Items.Add(self._type_item(t))
        if picked_types:
            self._compact_list()
        self._refresh_picked()
        if state.form:
            self._set_form(state.form)
        active = state.active if state.active in self.by_id else (types[0].id if types else None)
        if active is not None:
            self._select_type(active)
        self._row_heights = [row.Height for row in self.grid_stirrups.RowDefinitions]
        self._show_tab(self.tab_steel.SelectedIndex == 1)

    def tab_changed(self, sender, args):
        if args.OriginalSource is self.tab_steel and hasattr(self, "_row_heights"):
            self._show_tab(self.tab_steel.SelectedIndex == 1)

    def _show_tab(self, longitudinal):
        """Two tabs over the same window: "Estribos" (first) and "Acero
        longitudinal" (bars, laps, hooks). Each shows only its settings and
        sketch tools; views, cuts and the generation (everything at once)
        are the same."""
        stirrup_only = [self.rb_stirrup, self.rb_rect, self.rb_tie, self.rb_edit, self.txt_steel, self.cbo_steel,
                        self.wrap_shape, self.button_shapes]
        for element in stirrup_only:
            element.Visibility = Visibility.Collapsed if longitudinal else Visibility.Visible
        for element in (self.exp_bars, self.exp_splice):
            element.Visibility = Visibility.Visible if longitudinal else Visibility.Collapsed
        # on the bars tab only the cover (row 4), the end anchorage (5) and
        # the save button (6, shown by the code that needs it) stay
        keep = (4, 5, 6)
        for child in self.grid_stirrups.Children:
            if Grid.GetRow(child) == 6:
                continue
            hidden = longitudinal and Grid.GetRow(child) not in keep
            child.Visibility = Visibility.Collapsed if hidden else Visibility.Visible
        for k, row in enumerate(self.grid_stirrups.RowDefinitions):
            row.Height = GridLength(0) if (longitudinal and k not in keep) else self._row_heights[k]
        self.exp_stirrups.Header = (u"Recubrimiento y anclaje" if longitudinal
                                    else u"2. Configuración de estribos")
        if longitudinal:
            self.exp_bars.IsExpanded = True
            self.exp_splice.IsExpanded = True
            if not any(rb_.IsChecked for rb_ in (self.rb_bar_1, self.rb_bar_2, self.rb_bar_3, self.rb_bar_4)):
                self.rb_bar_2.IsChecked = True
            self.txt_help.Text = (u"Barras: elige una de las 4 opciones y haz clic en la sección donde va cada barra "
                                  u"(cerca de un estribo se pega a su cara interior); Borrar quita una barra. En el "
                                  u"alzado: Dibujar acero para bastones y balancines; clic en un acero para editarlo.")
        else:
            if not (self.rb_stirrup.IsChecked or self.rb_rect.IsChecked or self.rb_tie.IsChecked
                    or self.rb_edit.IsChecked or self.rb_erase.IsChecked):
                self.rb_stirrup.IsChecked = True
            self.txt_help.Text = (u"Estribo cerrado: clic en cada barra que abraza y clic derecho (o clic en la primera) "
                                  u"para cerrarlo; abierto (U): clic en cada punto y clic derecho. Mediante dos puntos: "
                                  u"dos esquinas opuestas. Grapa: un clic donde la quieres. Editar: clic en un estribo "
                                  u"para cambiar sus medidas. Armado automático: barras en las esquinas y estribo "
                                  u"ajustados al recubrimiento, en cualquier sección. Esc cancela el trazo.")

    def auto_click(self, sender, args):
        """Bars in the corners and the edge stirrup, fitted to the cover of
        the section, whatever its shape (rs.fitted_design)."""
        t = self.by_id.get(self.state.active)
        section = t.section if t else None
        cover = self._cover_m()
        if section is None or cover is None:
            self._status(u"Elige un tipo de viga y escribe el recubrimiento.")
            return
        stirrup_key = self.cbo_edge.SelectedItem or u'3/8"'
        bar_key = self.bar_key or u'5/8"'
        try:
            design = rs.fitted_design(section.polygon_m, cover, stirrup_key, bar_key)
        except rs.SpecError as e:
            self._status(u"No se pudo armar la sección: {}".format(e))
            return
        self._push_undo()
        self.design = design
        self.draft = []
        self._select_stirrup(None)
        self._status(u"Armado automático: {} barras Ø{} y estribo Ø{} a {:g} cm de recubrimiento. "
                     u"Pulsa 'Guardar configuración' para guardarlo en el tipo.".format(
                         len(design["bars"]), bar_key, stirrup_key, cover * 100), error=False)
        self.redraw()

    # -- type list ---------------------------------------------------------
    def _type_item(self, t):
        panel = StackPanel()
        panel.Orientation = Orientation.Horizontal
        box = CheckBox()
        box.IsChecked = t.id in self.state.checked
        box.VerticalAlignment = VerticalAlignment.Center
        box.Margin = Thickness(0, 0, 6, 0)
        self.checkboxes[t.id] = box
        label = TextBlock()
        label.Text = u"{}{}  ({})".format(u"✓ " if t.configured() else u"", t.short_name, len(t.columns))
        label.TextWrapping = TextWrapping.Wrap
        label.MaxWidth = 300  # a long type name wraps instead of hiding
        panel.Children.Add(box)
        panel.Children.Add(label)
        item = ListBoxItem()
        item.Content = panel
        item.Tag = t.id
        item.ToolTip = t.name
        return item

    def _refresh_type_labels(self):
        for item in self.list_types.Items:
            t = self.by_id[item.Tag]
            item.Content.Children[1].Text = u"{}{}  ({})".format(
                u"✓ " if t.configured() else u"", t.short_name, len(t.columns))

    def checked_ids(self):
        return [t for t, box in self.checkboxes.items() if box.IsChecked]

    def all_click(self, sender, args):
        for box in self.checkboxes.values():
            box.IsChecked = bool(self.chk_all.IsChecked)

    def type_selected(self, sender, args):
        item = self.list_types.SelectedItem
        if item is None or item.Tag == self.state.active:
            return
        self._keep_draft()
        self._select_type(item.Tag)

    def _select_type(self, type_id):
        self.state.active = type_id
        t = self.by_id[type_id]
        for item in self.list_types.Items:
            if item.Tag == type_id and self.list_types.SelectedItem is not item:
                self.list_types.SelectedItem = item
                self.list_types.ScrollIntoView(item)
        cfg = t.config()
        self._set_form({
            "bars_type": cfg["EA_Barras_Tipo"],
            "conf_type": cfg["EA_Estribo_Conf_Tipo"],
            "edge_type": cfg["EA_Estribo_Borde_Tipo"],
            "conf": cfg["EA_Estribo_Conf_Diametro"] or u'3/8"',
            "conf_dist": cfg["EA_Estribo_Conf_Distribucion"],
            "edge": cfg["EA_Estribo_Borde_Diametro"] or u'3/8"',
            "edge_dist": cfg["EA_Estribo_Borde_Distribucion"],
            "cover": cfg["EA_Recubrimiento_cm"] or str(rb.DEFAULT_COVER_CM),
            "anchor": rb.anchor_of(cfg),
        })
        if type_id in self.state.drafts:
            self.design = self.state.drafts[type_id]
            self.dirty = True
        else:
            try:
                self.design = rs.design_from_text(cfg["EA_Seccion_Armado"]) or rs.empty_design()
            except rs.SpecError:
                self.design = rs.empty_design()
            self.dirty = False
        self.undo_stack = []
        self.draft = []
        self.selected = None
        self._fill_view_lines(t)
        self.plan_nav.reset()
        self.elev_nav.reset()
        self.scene._extent = None  # refit the 3D camera to the new beam
        self._update_measures()
        section = t.section
        if section is None:
            self.txt_section.Text = u"Seccion {}: {}".format(t.name, t.section_error)
        else:
            line = self._view_line
            haunch = u""
            if line is not None and line.variable:
                depths = [line.depth_at(c) for c in line.breaks]
                haunch = u" - cartela: peralte {:.2f} a {:.2f} m (se dibuja en el mayor)".format(
                    min(t_ - b_ for b_, t_ in depths), max(t_ - b_ for b_, t_ in depths))
            self.txt_section.Text = u"Seccion {}  ({:.0f} x {:.0f} cm{}){}".format(
                t.short_name, section.b * rc.FT * 100, section.h * rc.FT * 100,
                u"" if section.is_rectangle else u", irregular", haunch)
        self.redraw()

    def _keep_draft(self):
        if self.dirty and self.state.active is not None:
            self.state.drafts[self.state.active] = self.design

    # -- form --------------------------------------------------------------
    def _set_form(self, f):
        for combo, key in ((self.cbo_conf, "conf"), (self.cbo_edge, "edge")):
            combo.SelectedItem = f.get(key) if f.get(key) in STIRRUP_DIAMETERS else u'3/8"'
        self._fill_bar_types(f.get("conf_type") or u"", f.get("edge_type") or u"")
        self.bars_types = rc.read_bar_type_names(f.get("bars_type"))
        self._fill_slot_types()
        self.txt_conf_dist.Text = f.get("conf_dist") or u""
        self.txt_edge_dist.Text = f.get("edge_dist") or u""
        self.txt_cover.Text = f.get("cover") or u""
        self.cbo_anchor.SelectedItem = f.get("anchor") if f.get("anchor") in rb.ANCHORS else rb.ANCHOR_HOOK

    def _get_form(self):
        return {
            "conf": self.cbo_conf.SelectedItem or u'3/8"',
            "bars_type": json.dumps(getattr(self, "bars_types", {}), ensure_ascii=False) if getattr(self, "bars_types", None) else u"",
            "conf_type": self._chosen_type(self.cbo_conf_type),
            "edge_type": self._chosen_type(self.cbo_edge_type),
            "conf_dist": (self.txt_conf_dist.Text or u"").strip(),
            "edge": self.cbo_edge.SelectedItem or u'3/8"',
            "edge_dist": (self.txt_edge_dist.Text or u"").strip(),
            "cover": (self.txt_cover.Text or u"").strip(),
            "anchor": self.cbo_anchor.SelectedItem or rb.ANCHOR_HOOK,
        }

    def _config_from_form(self, with_drawing):
        f = self._get_form()
        config = {
            "EA_Estribo_Conf_Diametro": f["conf"],
            "EA_Estribo_Conf_Distribucion": f["conf_dist"],
            "EA_Estribo_Conf_Tipo": f["conf_type"],
            "EA_Estribo_Borde_Tipo": f["edge_type"],
            "EA_Barras_Tipo": f["bars_type"],
            "EA_Estribo_Borde_Diametro": f["edge"],
            "EA_Estribo_Borde_Distribucion": f["edge_dist"],
            "EA_Recubrimiento_cm": f["cover"],
            rb.ANCHOR_PARAM: f["anchor"],
        }
        if with_drawing:
            config["EA_Seccion_Armado"] = rs.design_to_text(self.design)
        return config

    def save_click(self, sender, args):
        self.save()

    def save(self):
        """Write the form (and, for types with the active type's section,
        the drawing) into the checked types. False if nothing was saved."""
        targets = self.checked_ids() or ([self.state.active] if self.state.active else [])
        if not targets:
            forms.alert(u"Marca al menos un tipo.", title="Acero")
            return False
        active = self.by_id.get(self.state.active)
        same_section = []
        for type_id in targets:
            s, a = self.by_id[type_id].section, active.section if active else None
            if s is not None and a is not None and all(
                abs(p[0] - q[0]) < 0.002 and abs(p[1] - q[1]) < 0.002
                for p, q in zip(s.polygon_m, a.polygon_m)
            ) and len(s.polygon_m) == len(a.polygon_m):
                same_section.append(type_id)
        has_drawing = bool(self.design["bars"] or self.design["stirrups"] or self.design["ties"])
        outside = [i + 1 for i, (kind, poly, wrap, is_open) in enumerate(self.design["stirrups"])
                   if self._outside_cover(kind, poly, wrap, is_open)]
        if outside:
            forms.alert(
                u"El estribo {} se sale del recubrimiento. Seleccionalo con 'Editar' y pulsa "
                u"'Aplicar' para acomodarlo dentro (o borralo).".format(
                    u", ".join(str(i) for i in outside)),
                title="Acero")
            return False
        try:
            for type_id in targets:
                # The drawing is checked only where it gets saved.
                rc.ColumnSpec(
                    self._config_from_form(type_id in same_section),
                    require_design=has_drawing and type_id in same_section,
                )
        except rs.SpecError as e:
            forms.alert(u"Revisa la configuracion: {}".format(e), title="Acero")
            return False
        with revit.Transaction("Acero - configuracion de vigas"):
            rb.ensure_parameters(doc)
            for type_id in targets:
                rb.write_type_config(
                    self.by_id[type_id].element,
                    self._config_from_form(type_id in same_section),
                )
            # each edited beam's own settings (spans' stirrups, extras, cuts)
            for key, settings in self.state.line_edits.items():
                text = bx.write(settings)
                for eid in key:
                    e = doc.GetElement(DB.ElementId(eid))
                    q = e.LookupParameter(rb.LINE_PARAM) if e is not None else None
                    if q is not None and not q.IsReadOnly:
                        q.Set(text)
        for type_id in same_section:
            self.state.drafts.pop(type_id, None)
        self.dirty = False
        self._refresh_type_labels()
        skipped = [self.by_id[t].name for t in targets if t not in same_section]
        if skipped and has_drawing:
            forms.alert(
                u"Configuracion de estribos guardada. El dibujo solo se guardo en los tipos "
                u"con la misma seccion; estos necesitan su propio dibujo:\n- "
                + u"\n- ".join(skipped),
                title="Acero",
            )
        return True

    # -- configuration files --------------------------------------------------
    def _file_dialog(self, dialog, file_name=u""):
        dialog.Filter = u"Configuracion de acero (*.json)|*.json"
        dialog.FileName = file_name
        folder = os.path.dirname(doc.PathName or u"")
        if folder and os.path.isdir(folder):
            dialog.InitialDirectory = folder
        return dialog.FileName if dialog.ShowDialog(self) else None

    def file_save_click(self, sender, args):
        """Save the stirrup settings and the section drawing, as they are
        now, to a file."""
        t = self.by_id.get(self.state.active)
        if t is None or t.section is None:
            forms.alert(u"Elige un tipo con seccion valida.", title="Acero")
            return
        path = self._file_dialog(SaveFileDialog(), u"Acero_{}.json".format(re.sub(r'[\\/:*?"<>|]', u"_", t.name)))
        if not path:
            return
        size = (t.section.b * rc.FT * 100, t.section.h * rc.FT * 100)
        with io.open(path, "w", encoding="utf-8") as f:
            f.write(rs.config_file_text(t.name, size, self._get_form(), self.design))
        self._status(u"Configuracion guardada en {}.".format(os.path.basename(path)), error=False)

    def file_open_click(self, sender, args):
        """Load a saved configuration into the type being edited (saved
        into the types with 'Guardar configuracion en los tipos marcados')."""
        t = self.by_id.get(self.state.active)
        if t is None or t.section is None:
            forms.alert(u"Elige primero el tipo donde cargar la configuracion.", title="Acero")
            return
        path = self._file_dialog(OpenFileDialog())
        if not path:
            return
        try:
            with io.open(path, encoding="utf-8") as f:
                name, (b, h), form, design = rs.read_config_file(f.read())
        except (IOError, rs.SpecError) as e:
            forms.alert(u"No se pudo abrir {}: {}".format(os.path.basename(path), e), title="Acero")
            return
        tb, th = t.section.b * rc.FT * 100, t.section.h * rc.FT * 100
        if design is not None and (abs(b - tb) > 0.5 or abs(h - th) > 0.5):
            if not forms.alert(
                u"El archivo es de {} ({:.0f} x {:.0f} cm) y este tipo es de {:.0f} x {:.0f} cm: "
                u"su dibujo no encaja en esta seccion.\n\nCargar solo los estribos "
                u"(diametros, distribucion, recubrimiento y anclaje)?".format(name, b, h, tb, th),
                title="Acero", yes=True, no=True):
                return
            design = None
        self._push_undo()
        self._set_form(form)
        if design is not None:
            self.design = design
        self.dirty = True
        self.draft = []
        self._select_stirrup(None)
        self._status(u"Cargada la configuracion de {}{}. Pulsa 'Guardar configuracion en los tipos "
                     u"marcados' para guardarla en el tipo.".format(
                         name, u"" if design is not None else u" (solo estribos)"), error=False)
        self.redraw()

    # -- actions -----------------------------------------------------------
    def pick_click(self, sender, args):
        self._leave("pick")

    def sketch_click(self, sender, args):
        self._leave("sketch")

    def run_click(self, sender, args):
        # No "Guardar configuracion" button: generating saves the form and
        # the drawing into the types first (the generation reads them there).
        if not self.save():
            return
        # The columns picked in the model, or else every column of the
        # checked types.
        if not self.state.picked_ids and not self.checked_ids():
            forms.alert(u"Selecciona vigas en el modelo o marca los tipos a generar.", title="Acero")
            return
        self._leave("run")

    def _leave(self, action):
        self._keep_draft()
        self.state.checked = set(self.checked_ids())
        self.state.form = self._get_form()
        self.state.scope = "pick" if self.state.picked_ids else "model"
        self.action = action
        self.Close()

    def _refresh_picked(self):
        n = len(self.state.picked_ids)
        self.txt_picked.Text = (
            u"{} viga(s) seleccionada(s) en el modelo.".format(n) if n
            else u"No hay vigas seleccionadas en el modelo."
        )

    def window_key(self, sender, args):
        """Esc cancels the stroke being drawn instead of closing the window;
        Supr deletes the selected stirrup (unless a box is being typed in)."""
        if args.Key == Key.Escape and getattr(self, "_hook_stage", None) is not None:
            self._end_hook_stage()
            args.Handled = True
        elif args.Key == Key.Escape and getattr(self, "_draft", None) is not None:
            self._draft, self._draft_view = None, None  # an extra bar being drawn
            self._changed()
            args.Handled = True
        elif args.Key == Key.Escape and getattr(self, "draft", None):
            self.draft = []
            self._status()
            self.redraw()
            args.Handled = True
        elif (args.Key in (Key.Left, Key.Right, Key.Up, Key.Down)
              and self._tool() == "edit" and getattr(self, "selected", None) is not None
              and not isinstance(Keyboard.FocusedElement, (TextBox, ComboBox))):
            self._nudge_stirrup(args.Key)
            args.Handled = True
        elif (args.Key == Key.Delete and getattr(self, "_nb_sel", None)
              and not isinstance(Keyboard.FocusedElement, TextBox)):
            tag, face = self._nb_sel  # a neighbor's bar clicked: out of this beam
            self._edit_neighbor_bar(u"nbl:{}:{}".format(tag, face), wanted=0.0)
            args.Handled = True
        elif (args.Key == Key.Delete and (getattr(self, "_main_bar", None) or getattr(self, "_main_face", None))
              and not isinstance(Keyboard.FocusedElement, TextBox)):
            self._delete_main()
            args.Handled = True
        elif (args.Key == Key.Delete and self._selected_index() is not None
              and not isinstance(Keyboard.FocusedElement, TextBox)):
            self.sel_delete_click(sender, args)
            args.Handled = True
        elif (args.Key == Key.Delete and getattr(self, "selected", None) is not None
              and not isinstance(Keyboard.FocusedElement, TextBox)):
            self.delete_stirrup_click(sender, args)
            args.Handled = True

    def _nudge_stirrup(self, key):
        """Arrow keys in Editar: the selected stirrup moves 1 cm (5 cm with
        Shift) that way, never out of the cover."""
        if self.selected >= len(self.design["stirrups"]):
            return
        step = 0.05 if Keyboard.Modifiers & ModifierKeys.Shift else 0.01
        dx, dy = {Key.Left: (-step, 0.0), Key.Right: (step, 0.0),
                  Key.Up: (0.0, step), Key.Down: (0.0, -step)}[key]
        kind, poly, wrap, is_open = self.design["stirrups"][self.selected]
        if wrap is None:
            wrap = rs.wrap_radius(poly, self.design["bars"])  # kept as it was
        moved = [(x + dx, y + dy) for x, y in poly]
        if self._outside_cover(kind, moved, wrap, is_open):
            self._status(u"Ahi el estribo se sale del recubrimiento; no se movio.")
            return
        self._status()
        self._push_undo()
        self.design["stirrups"][self.selected] = (kind, moved, wrap, is_open)
        self.redraw()
        self._update_measures()

    def _delete_main(self):
        """Supr on a main bar: the one clicked in a cut out of this beam (or
        back in, if it was out); the face clicked in the elevation: all its
        bars out. Only this beam - the type's section stays as it is."""
        settings = self._edit_settings()
        picked = getattr(self, "_main_bar", None)
        if picked:
            bx.toggle_bar_off(settings, picked[0], picked[1])
            self._main_bar = self._main_face = None
        else:
            face = self._main_face
            for x, y, _ in self.design["bars"]:
                if ((y > 0) == (face == bx.TOP)) and not bx.bar_off(settings, x, y):
                    bx.toggle_bar_off(settings, x, y)
            self._main_face = None
        self._changed()

    def delete_stirrup_click(self, sender, args):
        """Delete the selected stirrup, to draw it again."""
        if self.selected is None or self.selected >= len(self.design["stirrups"]):
            return
        self._push_undo()
        rs.remove_item(self.design, "stirrups", self.selected)
        self.draft = []
        self._select_stirrup(None)
        self._status(u"Estribo eliminado (Deshacer lo recupera).")
        self.redraw()

    # -- sketch tool and its steel ----------------------------------------
    def _tool(self):
        if self.rb_stirrup.IsChecked:
            return "stirrup"
        if self.rb_rect.IsChecked:
            return "rect"
        if self.rb_tie.IsChecked:
            return "tie"
        if any(radio.IsChecked for radio, _ in self.bar_tools):
            return "bar"
        if self.rb_edit.IsChecked:
            return "edit"
        return "erase"

    def _steel_slot(self, tool):
        """Which remembered family a tool uses: both stirrup tools share
        one; Editar shows the selected stirrup's own family."""
        return {"stirrup": "stirrup", "rect": "stirrup", "tie": "tie"}.get(tool)

    def _refresh_steel(self):
        """Fill "Ø acero" for the current tool: the two stirrup families
        with the diameters set in "2. Configuracion de estribos" (a bar
        shows the diameter chosen in "Barras", read only)."""
        tool = self._tool()
        self._updating_steel = True
        try:
            editing = tool == "edit" and self.selected is not None
            if self._steel_slot(tool) or editing:
                self.cbo_steel.ItemsSource = List[str]([
                    u"Confinamiento Ø{}".format(self.cbo_conf.SelectedItem or u'3/8"'),
                    u"Borde Ø{}".format(self.cbo_edge.SelectedItem or u'3/8"'),
                ])
                kind = (self.design["stirrups"][self.selected][0] if editing
                        else self.kind_for[self._steel_slot(tool)])
                self.cbo_steel.SelectedIndex = 1 if kind == rs.KIND_EDGE else 0
                self.cbo_steel.IsEnabled = True
            elif tool == "bar":
                self.cbo_steel.ItemsSource = List[str]([u"Barra Ø" + self.bar_key])
                self.cbo_steel.SelectedIndex = 0
                self.cbo_steel.IsEnabled = False
            else:
                self.cbo_steel.ItemsSource = None
                self.cbo_steel.IsEnabled = False
        finally:
            self._updating_steel = False

    def tool_changed(self, sender, args):
        if not hasattr(self, "kind_for"):
            return  # fired while the XAML loads
        self.draft = []
        self._status()  # the previous tool's hint ("...para la grapa") no longer applies
        for radio, cbo in self.bar_tools:
            if radio.IsChecked and cbo.SelectedItem:
                self.bar_key = cbo.SelectedItem
        if self._tool() not in ("edit",):
            self.selected = None if self._tool() == "erase" else self.selected
        self._refresh_steel()
        self.redraw()

    # -- lap splices and hook legs of the longitudinal bars -------------------
    def _diameter_boxes(self, panel, values):
        """One "Ø key [cm]" box per known diameter in `panel`: {key: box}."""
        boxes = {}
        panel.Children.Clear()
        for key in rs.bar_diameter_keys():
            cell = StackPanel()
            cell.Orientation = Orientation.Horizontal
            cell.Margin = Thickness(0, 0, 8, 4)
            label = TextBlock()
            label.Text = u"Ø{} ".format(key)
            label.Width = 44
            label.VerticalAlignment = VerticalAlignment.Center
            box = TextBox()
            box.Width = 38
            cm = values.get(key)
            box.Text = u"{:g}".format(cm) if cm else u""
            box.TextChanged += self.splice_changed
            cell.Children.Add(label)
            cell.Children.Add(box)
            panel.Children.Add(cell)
            boxes[key] = box
        return boxes

    @staticmethod
    def _cm_values(boxes):
        """{key: cm} of the boxes that read as a positive number."""
        values = {}
        for key, box in boxes.items():
            try:
                cm = float((box.Text or u"").replace(u",", u"."))
            except ValueError:
                continue
            if cm > 0:
                values[key] = cm
        return values

    def _fill_splice(self):
        """"3. Empalme y gancho": the switch, the maximum bar length and one
        box per diameter for the laps and for the hook legs, as last saved."""
        self._filling_splice = True
        try:
            settings = load_splice_settings()
            self.chk_splice.IsChecked = settings["on"]
            self.txt_splice_max.Text = u"{:g}".format(settings["max"])
            self.lap_boxes, self.lap_checks = lap_rows(self.panel_laps, settings, self.splice_changed)
            self.leg_boxes = self._diameter_boxes(self.panel_legs, load_hook_legs())
        finally:
            self._filling_splice = False

    def _splice_from_form(self):
        """The splice settings as typed ({"on", "max", "laps"}); boxes that
        don't read as a number are left out."""
        laps, active = lap_form(self.lap_boxes, self.lap_checks)
        settings = {"on": bool(self.chk_splice.IsChecked), "max": rs.MAX_BAR_LENGTH, "laps": laps, "active": active}
        try:
            settings["max"] = float((self.txt_splice_max.Text or u"").replace(u",", u".")) or rs.MAX_BAR_LENGTH
        except ValueError:
            pass
        return settings

    def _legs_from_form(self):
        """{key: m} hook legs as typed (cm; a value under 1 typed in meters)."""
        return dict((k, (cm if 0.0 < cm < 1.0 else cm / 100.0)) for k, cm in self._cm_values(self.leg_boxes).items())

    def splice_changed(self, sender, args):
        if getattr(self, "_filling_splice", True):
            return
        save_splice_settings(self._splice_from_form())
        save_hook_legs(self._cm_values(self.leg_boxes))
        self.redraw()  # the views show the laps and hooks

    def bar_slot_changed(self, sender, args):
        """A diameter chosen in one of the four "Barras" options: it is
        remembered, and that option becomes the bar tool."""
        if getattr(self, "_filling_bars", True):
            return
        save_bar_slots([cbo.SelectedItem for _, cbo in self.bar_tools])
        self._fill_slot_types()
        for radio, cbo in self.bar_tools:
            if cbo is not sender or not cbo.SelectedItem:
                continue
            if radio.IsChecked:
                self.bar_key = cbo.SelectedItem
                self._refresh_steel()
            else:
                radio.IsChecked = True  # tool_changed takes its diameter

    def steel_changed(self, sender, args):
        if getattr(self, "_updating_steel", True) or self.cbo_steel.SelectedIndex < 0:
            return
        tool = self._tool()
        kind = rs.KIND_EDGE if self.cbo_steel.SelectedIndex == 1 else rs.KIND_CONFINEMENT
        if tool == "edit" and self.selected is not None:
            _, poly, wrap, is_open = self.design["stirrups"][self.selected]
            if self.design["stirrups"][self.selected][0] != kind:
                self._push_undo()
                self.design["stirrups"][self.selected] = (kind, poly, wrap, is_open)
                self.redraw()
        elif self._steel_slot(tool):
            self.kind_for[self._steel_slot(tool)] = kind

    def _compact_list(self):
        """With beams picked, the type box holds just their types: it sits
        right under "Seleccionar todo", its own height, and the settings
        follow it (no blank box)."""
        panel = self.list_types.Parent
        bottom = [c for c in panel.Children if DockPanel.GetDock(c) == Dock.Bottom]
        items = [self.list_types] + bottom[::-1]  # bottom-docked: first is lowest
        for child in items:
            panel.Children.Remove(child)
        at = panel.Children.IndexOf(self.chk_all) + 1
        for offset, child in enumerate(items):
            panel.Children.Insert(at + offset, child)
            DockPanel.SetDock(child, Dock.Top)
        panel.LastChildFill = False
        self.list_types.MaxHeight = 240  # its own height, long names wrapped

    def shapes_toggle_click(self, sender, args):
        """Show / hide the rebar shape browser."""
        show = self.box_shapes.Visibility != Visibility.Visible
        self.box_shapes.Visibility = Visibility.Visible if show else Visibility.Collapsed
        self.col_shapes.Width = GridLength(250) if show else GridLength(0)
        self.button_shapes.Content = u"Ocultar formas" if show else u"Mostrar formas"

    def _type_names(self):
        """[AUTO_TYPE] + every bar type of the project (not the crosstie
        copies); self.type_keys: name -> its diameter key."""
        if not hasattr(self, "type_keys"):
            self.type_keys = rc.bar_type_keys(doc)
        return [AUTO_TYPE] + sorted(self.type_keys)

    def _fill_bar_types(self, conf_type=None, edge_type=None):
        """The stirrup "Tipo" lists: every bar type of the project, first
        "(automatico)"; keeps (or sets) the choice."""
        names = self._type_names()
        self._filling_types = True
        for combo, wanted in ((self.cbo_conf_type, conf_type), (self.cbo_edge_type, edge_type)):
            if wanted is None:
                wanted = self._chosen_type(combo)
            if combo.ItemsSource is None:
                combo.ItemsSource = List[str](names)
            combo.SelectedItem = wanted if wanted in names else AUTO_TYPE
        self._filling_types = False

    def stirrup_type_changed(self, sender, args):
        """A stirrup type chosen: its diameter goes beside it."""
        if getattr(self, "_filling_types", True):
            return
        key = self.type_keys.get(self._chosen_type(sender))
        diameter = self.cbo_conf if sender is self.cbo_conf_type else self.cbo_edge
        if key and key in STIRRUP_DIAMETERS and diameter.SelectedItem != key:
            diameter.SelectedItem = key
        self.config_changed(sender, args)

    def _slot_type_combos(self):
        return [self.cbo_bar_type_1, self.cbo_bar_type_2, self.cbo_bar_type_3, self.cbo_bar_type_4]

    def _fill_slot_types(self):
        """Each longitudinal bar option's "Tipo": the type chosen for its
        diameter (self.bars_types), else "(automatico)"."""
        names = self._type_names()
        self._filling_types = True
        for (_, cbo), combo in zip(self.bar_tools, self._slot_type_combos()):
            if combo.ItemsSource is None:
                combo.ItemsSource = List[str](names)
            wanted = getattr(self, "bars_types", {}).get(cbo.SelectedItem)
            combo.SelectedItem = wanted if wanted in names else AUTO_TYPE
        self._filling_types = False

    def bar_type_changed(self, sender, args):
        """A bar option's type chosen: kept for its diameter; a type of
        another diameter moves the option to that diameter."""
        if getattr(self, "_filling_types", True) or not hasattr(self, "bars_types"):
            return
        cbo = self.bar_tools[self._slot_type_combos().index(sender)][1]
        name = self._chosen_type(sender)
        key = self.type_keys.get(name) if name else None
        if key and key != cbo.SelectedItem and key in list(cbo.ItemsSource):
            self.bars_types[key] = name
            cbo.SelectedItem = key  # bar_slot_changed refreshes the types
            return
        if name:
            self.bars_types[cbo.SelectedItem] = name
        else:
            self.bars_types.pop(cbo.SelectedItem, None)
        self._fill_slot_types()

    @staticmethod
    def _chosen_type(combo):
        item = combo.SelectedItem
        return u"" if not item or item == AUTO_TYPE else item

    def stirrup_diameter_changed(self, sender, args):
        if not hasattr(self, "kind_for"):
            return
        self._refresh_steel()
        self._update_measures()  # outer measures depend on the diameter
        self.redraw()

    # -- drawing -----------------------------------------------------------
    def _frame(self):
        t = self.by_id.get(self.state.active)
        section = t.section if t else None
        width, height = self.canvas.ActualWidth, self.canvas.ActualHeight
        if section is None or width < 50 or height < 50:
            return None
        xs = [p[0] for p in section.polygon_m]
        ys = [p[1] for p in section.polygon_m]
        span_x, span_y = max(xs) - min(xs), max(ys) - min(ys)
        scale = min((width - 2 * MARGIN_PX) / span_x, (height - 2 * MARGIN_PX) / span_y)
        cx, cy = (max(xs) + min(xs)) / 2.0, (max(ys) + min(ys)) / 2.0
        fitted = (scale, width / 2.0 - cx * scale, height / 2.0 + cy * scale)
        self._plan_fitted = fitted
        scale, ox, oy = self.plan_nav.resolve(fitted)
        return section, scale, ox, oy

    def to_px(self, frame, x, y):
        _, scale, ox, oy = frame
        return Point(ox + x * scale, oy - y * scale)

    def to_m(self, frame, p):
        _, scale, ox, oy = frame
        return ((p.X - ox) / scale, (oy - p.Y) / scale)

    def snap(self, frame, p):
        """Nearest bar center within SNAP_PX, else the point on a 1 cm grid."""
        best = None
        for x, y, _ in self.design["bars"]:
            q = self.to_px(frame, x, y)
            d = ((q.X - p.X) ** 2 + (q.Y - p.Y) ** 2) ** 0.5
            if d <= SNAP_PX and (best is None or d < best[0]):
                best = (d, (x, y))
        if best:
            return best[1], True
        x, y = self.to_m(frame, p)
        return (round(x, 2), round(y, 2)), False

    def _add(self, shape):
        # Shapes never take the mouse: clicks go straight to the canvas,
        # and a shape redrawn under a still cursor can't set off another
        # MouseMove (a redraw loop that swallowed the clicks).
        shape.IsHitTestVisible = False
        self.canvas.Children.Add(shape)
        return shape

    def _polyline(self, frame, points, stroke, thickness, closed=False, dash=False, fill=None):
        shape = Polygon() if closed else Polyline()
        pts = PointCollection()
        for x, y in points:
            pts.Add(self.to_px(frame, x, y))
        shape.Points = pts
        shape.Stroke = stroke
        shape.StrokeThickness = thickness
        if dash:
            dashes = DoubleCollection()
            dashes.Add(4)
            dashes.Add(3)
            shape.StrokeDashArray = dashes
        if fill is not None:
            shape.Fill = fill
        return self._add(shape)

    def _dot(self, frame, x, y, radius_px, fill):
        e = Ellipse()
        e.Width = e.Height = 2 * radius_px
        e.Fill = fill
        p = self.to_px(frame, x, y)
        Canvas.SetLeft(e, p.X - radius_px)
        Canvas.SetTop(e, p.Y - radius_px)
        return self._add(e)

    def _family_key(self, kind):
        combo = self.cbo_edge if kind == rs.KIND_EDGE else self.cbo_conf
        return combo.SelectedItem or u'3/8"'

    def _label(self, frame, x, y, text, dx=0, dy=0):
        """Measure text centered on a section point (meters), shifted by
        (dx, dy) pixels."""
        tb = TextBlock()
        tb.Text = text
        tb.FontSize = 11
        tb.Foreground = C_DRAFT
        tb.Background = C_LABEL_BG
        tb.Measure(Size(1e4, 1e4))
        q = self.to_px(frame, x, y)
        Canvas.SetLeft(tb, q.X + dx - tb.DesiredSize.Width / 2.0)
        Canvas.SetTop(tb, q.Y + dy - tb.DesiredSize.Height / 2.0)
        return self._add(tb)

    def _side_labels(self, frame, outline, closed=True):
        """Length (cm) of each side of a stirrup's outer face, written just
        outside it; a rectangle shows only its width and its height."""
        if closed:
            pts = rs.counterclockwise(outline)
            sign = 1.0
        else:
            pts = list(outline)
            sign = 1.0 if len(pts) < 3 or rs.polygon_signed_area(pts) > 0 else -1.0
        n = len(pts)
        sides = range(n if closed else n - 1)
        numbered = True  # T1, T2... like the boxes of the measures panel
        if closed and rs.rect_measures(pts, frame[0].polygon_m) is not None:
            numbered = False
            horizontal = [k for k in range(n) if abs(pts[k][1] - pts[(k + 1) % n][1]) < 1e-6]
            vertical = [k for k in range(n) if abs(pts[k][0] - pts[(k + 1) % n][0]) < 1e-6]
            sides = horizontal[:1] + vertical[:1]
        for k in sides:
            (x1, y1), (x2, y2) = pts[k], pts[(k + 1) % n]
            length = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
            if length < 1e-6:
                continue
            nx, ny = sign * (y2 - y1) / length, -sign * (x2 - x1) / length  # outward
            text = u"{:.1f}".format(length * 100)
            if numbered:
                text = u"T{} {}".format(k + 1, text)
            self._label(frame, (x1 + x2) / 2.0, (y1 + y2) / 2.0, text,
                        dx=nx * 22, dy=-ny * 22)

    def redraw(self):
        self.canvas.Children.Clear()
        if not self.tb_tie_leg.IsKeyboardFocused:
            leg = self.design.get("tie_leg")
            text = u"{:g}".format(leg) if leg else u""
            if self.tb_tie_leg.Text != text:
                self.tb_tie_leg.Text = text
        frame = self._frame()
        if frame is None:
            return
        section, scale = frame[0], frame[1]
        bars = self.design["bars"]
        self._polyline(frame, section.polygon_m, C_OUTLINE, 2, closed=True, fill=C_CONCRETE)
        line = getattr(self, "_view_line", None)
        if line is not None and line.variable:
            # a haunch: its shallowest section too (the drawing is on the deepest)
            shallow = min((line.depth_at(c) for c in line.breaks), key=lambda bt: bt[1] - bt[0])
            self._polyline(frame, rs.haunch_polyline(section.polygon_m, line.ref_bottom, line.ref_top,
                                                     shallow[0], shallow[1]),
                           C_OUTLINE, 1.2, closed=True, dash=True)
        try:
            cover = float((self.txt_cover.Text or u"4").replace(u",", u".")) / 100.0
            self._polyline(frame, rs.offset_polygon_outward(section.polygon_m, -cover), C_COVER, 1,
                           closed=True, dash=True)
        except Exception:
            pass

        def color(kind):
            # Edge ("borde") steel in orange, confinement in green.
            return C_STIRRUP if kind == rs.KIND_EDGE else C_CONFINEMENT

        for i, (kind, poly, wrap, is_open) in enumerate(self.design["stirrups"]):
            key = self._family_key(kind)
            try:
                line = rs.stirrup_centerline(poly, bars, key, wrap, is_open)
            except rs.SpecError:
                line = poly
            ds = rs.BAR_DIAMETERS_MM[key] / 1000.0
            thickness = max(2, ds * scale)
            # as bent in Revit: round over each bar (2.5 db), 135-degree hooks
            wrap_r = (wrap if wrap is not None else rs.wrap_radius(poly, bars)) + ds / 2.0
            try:
                # bent round the project's pin (its bar type's bend), like the generation
                for part in rs.bent_stirrup(poly, wrap_r, max(6 * ds, 0.075), is_open,
                                            bend=self._bend_d(key) / 2.0 + ds / 2.0):
                    self._steel(frame, part, color(kind), thickness)
            except Exception:
                self._polyline(frame, line, color(kind), thickness, closed=not is_open)
            if i == self.selected:
                try:
                    outline = rs.stirrup_outline(poly, bars, key, wrap, is_open)
                    self._polyline(frame, outline, C_DRAFT, 1.5, closed=not is_open, dash=True)
                    self._side_labels(frame, outline, closed=not is_open)
                except rs.SpecError:
                    pass
        tie_styles = rs.design_shapes(self.design, "ties")
        for (kind, a, b), style in zip(self.design["ties"], tie_styles):
            key = self._family_key(kind)
            try:
                a2, b2 = rs.tie_centerline(a, b, bars, key)
            except rs.SpecError:
                a2, b2 = a, b
            ds = rs.BAR_DIAMETERS_MM[key] / 1000.0
            thickness = max(2, ds * scale)
            # as bent in Revit: round each end bar (135 deg, C / S 180 deg) and its leg
            radii = []
            for end in (a, b):
                bar = rs._bar_at(end, bars)
                radii.append((rs.BAR_DIAMETERS_MM[bar] / 2000.0 if bar else 0.0) + ds / 2.0)
            angle = 180 if style in rs.TIE_STYLES else 135
            leg = (self.design.get("tie_leg") or rs.tie_leg_cm(key, angle)) / 100.0
            self._steel(frame, rs.bent_tie(a, b, radii[0], radii[1], style, leg), color(kind), thickness)
        # the corner bars seated in the stirrups' bends, as generated (rb.seat_in_bends)
        shown = bars
        for kind, poly, wrap, is_open in self.design["stirrups"]:
            if is_open:
                continue
            key = self._family_key(kind)
            try:
                loop = rs.stirrup_centerline(poly, bars, key, wrap, False)
            except rs.SpecError:
                continue
            ds = rs.BAR_DIAMETERS_MM[key] / 1000.0
            shown = rb.seat_in_bends(shown, [rs.clean_polyline(loop, closed=True)], ds, self._bend_d(key))
        for x, y, key in shown:
            self._dot(frame, x, y, max(3.5, rs.BAR_DIAMETERS_MM[key] / 2000.0 * scale), C_BAR)

        tool = self._tool()
        if tool == "tie" and self.cursor_m:
            # Preview of the tie a click here would place.
            try:
                a, b = rs.auto_tie(self.cursor_m, bars)
                self._polyline(frame, [a, b], C_DRAFT, 2, dash=True)
            except rs.SpecError:
                pass
        if tool == "rect" and self.draft and self.cursor_m:
            # Rectangle between the first corner and the cursor, with the
            # outer measures it would have.
            rect = self._fitted_rect(self.draft[0], self.cursor_m)
            if rect:
                self._polyline(frame, rect, C_DRAFT, 2, closed=True, dash=True)
                key = self._family_key(self.kind_for["stirrup"])
                try:
                    self._side_labels(frame, rs.stirrup_outline(rect, bars, key))
                except rs.SpecError:
                    pass
        elif self.draft:
            pts = list(self.draft) + ([self.cursor_m] if self.cursor_m else [])
            self._polyline(frame, pts, C_DRAFT, 2)
        for x, y in self.draft:
            self._dot(frame, x, y, 4, C_DRAFT)
        if self.cursor_m:
            refused = tool in ("stirrup", "rect") and self._accept_vertex(
                self.cursor_m, self.cursor_on_bar) is None
            self._dot(frame, self.cursor_m[0], self.cursor_m[1], 4 if refused else 3,
                      C_REFUSED if refused else C_CURSOR)
        splice_now = self._splice_from_form() if hasattr(self, "lap_boxes") else None
        legs_now = self._legs_from_form() if hasattr(self, "leg_boxes") else None
        signature = (self.state.active, self._line_key(self._view_line),
                     repr(sorted(splice_now.items())) if splice_now else None,
                     repr(sorted(legs_now.items())) if legs_now else None,
                     rs.design_to_text(self.design),
                     tuple(sorted(self._get_form().items())))
        if signature != self._views_sig:
            self._views_sig = signature
            self._refresh_views()

    def _bend_d(self, key):
        """The inner bend diameter (m) of the project's stirrup bar type of
        that diameter (as generated), else 4 db."""
        cache = self.__dict__.setdefault("_bends", {})
        if key not in cache:
            ds = rs.BAR_DIAMETERS_MM[key] / 1000.0
            try:
                bt = rc._bar_type(rc.BarTypes(doc), key, rb.BEAM_MARK)
                cache[key] = bt.StirrupTieBendDiameter * rc.FT or 4.0 * ds
            except Exception:
                cache[key] = 4.0 * ds
        return cache[key]

    def _steel(self, frame, points, color, thickness):
        """A bar in plan: its color over a thin dark edge, round bends and
        ends, like Revit's fine detail."""
        for brush, width in ((C_OUTLINE, thickness + 1.5), (color, thickness)):
            shape = self._polyline(frame, points, brush, width)
            shape.StrokeLineJoin = PenLineJoin.Round
            shape.StrokeStartLineCap = shape.StrokeEndLineCap = PenLineCap.Round

    @staticmethod
    def _rect(a, b):
        """Axis-aligned rectangle with opposite corners a and b (None if
        it would be a line)."""
        x0, x1 = sorted((a[0], b[0]))
        y0, y1 = sorted((a[1], b[1]))
        if x1 - x0 < 0.02 or y1 - y0 < 0.02:
            return None
        return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]

    def _cover_bounds(self):
        """Where a rectangular stirrup's outer face must stay (the section
        faces moved in by the cover), or None for irregular sections."""
        t = self.by_id.get(self.state.active)
        section = t.section if t else None
        if section is None or not section.is_rectangle:
            return None
        try:
            cover = float((self.txt_cover.Text or u"").replace(u",", u".")) / 100.0
        except ValueError:
            return None
        return rs.cover_bounds(section.polygon_m, cover)

    def _cover_m(self):
        try:
            return float((self.txt_cover.Text or u"").replace(u",", u".")) / 100.0
        except ValueError:
            return None

    def _status(self, text=u"", error=True):
        """Warning line over the drawing (hidden when empty); a hint (not
        an error) in blue."""
        self.txt_status.Text = text
        self.txt_status.Foreground = C_REFUSED if error else C_DRAFT
        self.txt_status.Visibility = Visibility.Visible if text else Visibility.Collapsed

    def _accept_vertex(self, point, on_bar):
        """The clicked stirrup corner, moved onto the cover limit when just
        inside it; None when the stirrup would leave the cover there."""
        t = self.by_id.get(self.state.active)
        section = t.section if t else None
        cover = self._cover_m()
        if section is None or cover is None:
            return point
        radius = 0.0
        if on_bar:
            for x, y, key in self.design["bars"]:
                if abs(x - point[0]) < 1e-6 and abs(y - point[1]) < 1e-6:
                    radius = rs.BAR_DIAMETERS_MM[key] / 2000.0
        key = self._family_key(self.kind_for["stirrup"])
        # Two-point rectangles pull their corners onto the cover from 2.5
        # cm; the Estribo tool only from 1 cm, so small jogs survive.
        snap = rs.COVER_SNAP if self._tool() == "rect" else rs.FREE_CORNER_SNAP
        corner = rs.fit_vertex(point, section.polygon_m, cover, key, radius,
                               rectangular=section.is_rectangle, snap=snap)
        if corner is not None and not on_bar and section.is_rectangle:
            # The whole stirrup is pushed out by the radius of the bars it
            # wraps: a free corner must leave room for that too.
            wrap = rs.wrap_radius(list(self.draft), self.design["bars"])
            if wrap:
                corner = rs.fit_vertex(point, section.polygon_m, cover, key, wrap, rectangular=True,
                                       snap=snap)
                if corner is not None:
                    corner = self._clamp_free(corner, wrap, key, section, cover, snap)
        return corner

    def _clamp_free(self, point, wrap, key, section, cover, snap=rs.FREE_CORNER_SNAP):
        """Keep a free (not on a bar) corner of a stirrup that wraps bars of
        radius `wrap` inside the cover, snapping it onto the limit when it
        is just inside (rectangular sections)."""
        x0, y0, x1, y1 = rs.cover_bounds(section.polygon_m, cover + rs.BAR_DIAMETERS_MM[key] / 1000.0 + wrap)
        x = min(max(point[0], x0), x1)
        y = min(max(point[1], y0), y1)
        x = x0 if x - x0 <= snap else (x1 if x1 - x <= snap else x)
        y = y0 if y - y0 <= snap else (y1 if y1 - y <= snap else y)
        return (x, y)

    def _outside_cover(self, kind, poly, wrap, is_open=False):
        t = self.by_id.get(self.state.active)
        section = t.section if t else None
        cover = self._cover_m()
        if section is None or cover is None:
            return False
        try:
            return not rs.stirrup_inside_cover(poly, self.design["bars"], self._family_key(kind),
                                               wrap, section.polygon_m, cover, is_open=is_open)
        except rs.SpecError:
            return True

    def _fitted_rect(self, a, b):
        """Two-point stirrup between corners a and b, with its sides that
        reach (or nearly reach) the cover line placed on it."""
        rect = self._rect(a, b)
        bounds = self._cover_bounds()
        if rect is None or bounds is None:
            return rect
        bars = self.design["bars"]
        key = self._family_key(self.kind_for["stirrup"])
        wrap = rs.wrap_radius(rect, bars)
        try:
            outer = rs.snap_rect_to_cover(rs.outer_rect(rect, bars, key, wrap), bounds)
            fitted = rs.rect_vertices(outer, key, wrap)
        except rs.SpecError:
            return rect
        # Keep the corners on the bars they were snapped to, if any.
        return fitted if wrap == rs.wrap_radius(fitted, bars) or wrap == 0 else rect

    # -- measures of the selected stirrup ------------------------------------
    def _select_stirrup(self, index):
        self.selected = index
        self._refresh_steel()
        self._update_measures()

    def _update_measures(self):
        """Show (or hide) the outer measures of the selected stirrup. Only
        called when the selection or the drawing changes, never on a mouse
        move, so it can't overwrite what is being typed."""
        stirrups = self.design["stirrups"]
        t = self.by_id.get(self.state.active)
        section = t.section if t else None
        if self.selected is None or self.selected >= len(stirrups) or section is None:
            self.selected = None
            self.panel_measures.Visibility = Visibility.Collapsed
            return
        kind, poly, wrap, is_open = stirrups[self.selected]
        try:
            outline = rs.stirrup_outline(poly, self.design["bars"], self._family_key(kind), wrap, is_open)
        except rs.SpecError:
            self.panel_measures.Visibility = Visibility.Collapsed
            return
        self.panel_measures.Visibility = Visibility.Visible
        shape = rs.design_shapes(self.design, "stirrups")[self.selected]
        self.txt_measures_title.Text = u"Estribo {}de {}{} seleccionado - medidas exteriores (cm):".format(
            u"abierto " if is_open else u"", u"borde" if kind == rs.KIND_EDGE else u"confinamiento",
            u" ({})".format(shape) if shape else u"")
        measures = None if is_open else rs.rect_measures(outline, section.polygon_m)
        turned = None if (is_open or measures) else rs.turned_rect_measures(outline)
        self.lbl_m_width.Text, self.lbl_m_height.Text = (u"Largo ", u"Ancho ") if turned else (u"Ancho ", u"Alto ")
        if turned:
            measures = turned  # a rectangle at an angle: along its own sides
        self._fill_segment_boxes(rs.side_lengths(outline, closed=False) if is_open else [])
        if measures:
            self.panel_rect_fields.Visibility = Visibility.Visible
            for box, value in zip((self.txt_m_width, self.txt_m_height), measures[:2]):
                box.Text = u"{:.1f}".format(value * 100)
            self.txt_m_sides.Text = u""
        else:
            self.panel_rect_fields.Visibility = Visibility.Collapsed
            # Open stirrups edit their segments in boxes; other shapes
            # just list their sides.
            self.txt_m_sides.Text = u"" if is_open else u"Lados:  " + u"   ".join(
                u"T{} {:.1f}".format(k, v * 100)
                for k, v in enumerate(rs.side_lengths(rs.counterclockwise(outline)), 1))

    def _fill_segment_boxes(self, lengths):
        """One editable box per segment of an open stirrup (outer face, cm)."""
        self.panel_segments.Children.Clear()
        self.segment_boxes = []
        if not lengths:
            return
        for number, value in enumerate(lengths, 1):
            label = TextBlock()
            label.Text = u"T{} ".format(number)
            label.FontWeight = FontWeights.Bold
            label.Foreground = C_DRAFT
            label.VerticalAlignment = VerticalAlignment.Center
            self.panel_segments.Children.Add(label)
            box = TextBox()
            box.Width = 46
            box.Margin = Thickness(0, 0, 4, 0)
            box.Text = u"{:.1f}".format(value * 100)
            box.KeyDown += self.segment_key
            self.panel_segments.Children.Add(box)
            self.segment_boxes.append(box)
        button = Button()
        button.Content = u"Aplicar"
        button.Width = 65
        button.Margin = Thickness(4, 0, 0, 0)
        button.Click += self.segments_apply
        self.panel_segments.Children.Add(button)

    def segment_key(self, sender, args):
        if args.Key == Key.Enter:
            self.segments_apply(sender, args)

    def segments_apply(self, sender, args):
        """Resize the open stirrup's segments to the typed outer lengths."""
        if self.selected is None:
            return
        kind, poly, wrap, is_open = self.design["stirrups"][self.selected]
        if not is_open:
            return
        key = self._family_key(kind)
        if wrap is None:
            wrap = rs.wrap_radius(poly, self.design["bars"])
        try:
            current = rs.side_lengths(rs.stirrup_outline(poly, self.design["bars"], key, wrap, True),
                                      closed=False)
            wanted = [float((box.Text or u"").replace(u",", u".")) / 100.0 for box in self.segment_boxes]
            if len(wanted) != len(current) or min(wanted) <= 0:
                raise ValueError()
            pts = list(poly)
            for index, (now, new) in enumerate(zip(current, wanted)):
                if abs(new - now) > 0.0005:
                    pts = rs.resize_segment(pts, index, new - now)
        except ValueError:
            forms.alert(u"Escribe los tramos en cm (ej. 7.5).", title="Acero")
            return
        except rs.SpecError as e:
            forms.alert(u"{}".format(e), title="Acero")
            return
        if self._outside_cover(kind, pts, wrap, True):
            self._status(u"Con esas medidas el estribo se sale del recubrimiento; no se aplicaron.")
            return
        self._status()
        self._push_undo()
        self.design["stirrups"][self.selected] = (kind, pts, wrap, True)
        self.redraw()
        self._update_measures()

    def measures_apply(self, sender, args):
        t = self.by_id.get(self.state.active)
        if self.selected is None or t is None or t.section is None:
            return
        kind, poly, wrap, is_open = self.design["stirrups"][self.selected]
        if is_open:
            return  # open stirrups: edit them by redrawing
        if wrap is None:
            wrap = rs.wrap_radius(poly, self.design["bars"])
        key = self._family_key(kind)
        try:
            width, height = [float((box.Text or u"").replace(u",", u".")) / 100.0
                             for box in (self.txt_m_width, self.txt_m_height)]
            if width <= 0 or height <= 0:
                raise ValueError()
            outline = rs.stirrup_outline(poly, self.design["bars"], key, wrap)
            turned = (None if rs.rect_measures(outline, t.section.polygon_m)
                      else rs.turned_rect_measures(outline))
            if turned:
                # At an angle: resized along its own sides, around its center.
                new_poly = rs.resize_turned_rect(poly, width - turned[0], height - turned[1])
                if self._outside_cover(kind, new_poly, wrap, False):
                    self._update_measures()  # the boxes back to its real size
                    self._status(u"Con esas medidas el estribo se sale del recubrimiento; no se aplicaron.")
                    return
                self._status()
                self._push_undo()
                self.design["stirrups"][self.selected] = (kind, new_poly, wrap, False)
                self.redraw()
                self._update_measures()
                return
            outer = rs.outer_rect(poly, self.design["bars"], key, wrap)
            bounds = self._cover_bounds()
            if bounds:
                # Around its own center, shifted to stay inside the cover.
                outer = rs.resize_rect_in_cover(outer, width, height, bounds)
            else:
                cx, cy = (outer[0] + outer[2]) / 2.0, (outer[1] + outer[3]) / 2.0
                outer = (cx - width / 2.0, cy - height / 2.0, cx + width / 2.0, cy + height / 2.0)
            new_poly = rs.rect_vertices(outer, key, wrap)
        except ValueError:
            forms.alert(u"Escribe las medidas en cm (ej. 22 o 22.5).", title="Acero")
            return
        except rs.SpecError as e:
            forms.alert(u"{}".format(e), title="Acero")
            return
        self._push_undo()
        self.design["stirrups"][self.selected] = (kind, new_poly, wrap, False)
        self.redraw()
        self._update_measures()

    def measure_key(self, sender, args):
        if args.Key == Key.Enter:
            self.measures_apply(sender, args)

    def _push_undo(self):
        self.undo_stack.append(rs.design_to_text(self.design))
        self.dirty = True

    # -- zoom / pan of the plan ---------------------------------------------
    def plan_wheel(self, sender, args):
        if self._frame() is None:
            return
        self.plan_nav.wheel(self._plan_fitted, args.GetPosition(self.canvas), args.Delta)
        self.redraw()
        args.Handled = True

    def plan_mouse_down(self, sender, args):
        if args.ChangedButton == MouseButton.Middle and self._frame() is not None:
            self.plan_nav.start_pan(self._plan_fitted, args.GetPosition(self.canvas))
            self.canvas.CaptureMouse()

    def plan_mouse_up(self, sender, args):
        if args.ChangedButton == MouseButton.Middle:
            self.plan_nav.end_pan()
            self.canvas.ReleaseMouseCapture()

    def plan_fit_click(self, sender, args):
        self.plan_nav.reset()
        self.redraw()

    # -- elevation and 3D views ----------------------------------------------
    @staticmethod
    def _line_key(line):
        return tuple(id_of(e.Id) for e in line.elements) if line is not None else None

    def _supports(self, line):
        """rb.line_supports of a beam line, cached."""
        key = self._line_key(line)
        if key not in self._support_cache:
            try:
                self._support_cache[key] = rb.line_supports(doc, line)
            except Exception:
                self._support_cache[key] = []
        return self._support_cache[key]

    def _line_label(self, line):
        first = line.elements[0]
        level_param = first.get_Parameter(DB.BuiltInParameter.INSTANCE_REFERENCE_LEVEL_PARAM)
        level = doc.GetElement(level_param.AsElementId()) if level_param is not None else None
        return u"{} - {} tramo(s), {:.2f} m{}".format(
            id_of(first.Id), len(line.elements), line.length, u" - " + level.Name if level else u"")

    def _fill_view_lines(self, t):
        """List the type's beams (beam lines) for the 3D view / elevation.
        First choice: the one holding a beam picked in the model, else the
        longest."""
        self._filling_columns = True
        try:
            lines = t.lines if t.section is not None else []
            picked = set(self.state.picked_ids)
            # beams picked in the model: only those listed (not every beam
            # of the type - simpler to find the one to see)
            mine = [line for line in lines if any(id_of(e.Id) in picked for e in line.elements)]
            if mine:
                lines = mine
            default = next((i for i, line in enumerate(lines)
                            if any(id_of(e.Id) in picked for e in line.elements)), None)
            if default is None and lines:
                default = max(range(len(lines)), key=lambda i: lines[i].length)
            self._view_options = lines
            self.cbo_view_column.ItemsSource = List[str]([self._line_label(line) for line in lines])
            self.cbo_view_column.SelectedIndex = default if default is not None else -1
            self._view_line = lines[default] if lines else None
        finally:
            self._filling_columns = False

    NEIGHBOR_SHOWN_M = 1.5

    @staticmethod
    def _kind_text(line, name):
        """'VIGA CHATA VCH-01' / 'VIGA PERALTADA VP-01' (chata: no deeper than wide)."""
        poly = line.section.polygon_m
        b = max(p[0] for p in poly) - min(p[0] for p in poly)
        h = max(p[1] for p in poly) - min(p[1] for p in poly)
        found = re.findall(r"_([A-Z]{1,5}[A-Z0-9'\-]*)_\d", name)
        return u"VIGA {} {}".format(u"CHATA" if h <= b + 1e-6 else u"PERALTADA", found[-1] if found else u"").strip()

    def _mixed_neighbors(self, line):
        """The picked beams of other types on this line's axis, next to it
        (a flat beam in cantilever past a deep one's column): each one's
        stretch in this line's elevation frame, its kind label and the tag
        that opens it here ("line:<type id>:<first element id>")."""
        picked = set(self.state.picked_ids)
        out = []
        for tid, t in self.by_id.items():
            if tid == self.state.active or not any(id_of(e.Id) in picked for e in t.columns):
                continue
            try:
                others = t.lines
            except Exception:
                continue
            for other in others:
                if not any(id_of(e.Id) in picked for e in other.elements):
                    continue
                d_o, d_l = other.direction, line.direction  # in plan: a sloped beam on the axis too
                n_o, n_l = math.hypot(d_o.X, d_o.Y), math.hypot(d_l.X, d_l.Y)
                if n_o < 1e-6 or n_l < 1e-6 or abs(abs(d_o.X * d_l.X + d_o.Y * d_l.Y) / (n_o * n_l) - 1.0) > 1e-3:
                    continue
                pts = [line.local(other.point(0.0, y, s)) for s in (0.0, other.length)
                       for y in (min(p[1] for p in other.section.polygon_m), max(p[1] for p in other.section.polygon_m))]
                if max(abs(p[0]) for p in pts) > 0.05:
                    continue  # not on this axis
                s0, s1 = min(p[2] for p in pts), max(p[2] for p in pts)
                if s0 > line.length + rb.LINE_GAP_M or s1 < -rb.LINE_GAP_M:
                    continue  # not next to it
                xs_o = [p[0] for p in other.section.polygon_m]
                if abs(abs(other.direction.DotProduct(line.direction)) - 1.0) > 1e-3:
                    # sloped against this one (a level flat beam by a sloped deep one):
                    # its real outline - a stub along its own axis from the end by
                    # this beam -, its stirrups square to it; no run-on bars to show
                    out.append(self._sloped_neighbor(line, other, t, tid))
                    continue
                # drawn as a stub at most NEIGHBOR_SHOWN_M long: this beam keeps its scale
                s0, s1 = max(s0, -self.NEIGHBOR_SHOWN_M), min(s1, line.length + self.NEIGHBOR_SHOWN_M)
                out.append({"line": other,  # the neighbor itself (this beam's run on into it)
                            "s0": s0, "s1": s1, "y0": min(p[1] for p in pts), "y1": max(p[1] for p in pts),
                            "x0": min(xs_o), "x1": max(xs_o),
                            "label": self._kind_text(other, t.name),
                            "stirrups": [s for s in self._neighbor_stirrups(line, other, t) if s0 <= s <= s1],
                            "bars": self._neighbor_bars(line, other, t),
                            "tag": u"line:{}:{}".format(tid, id_of(other.elements[0].Id))})
        return out

    def _sloped_neighbor(self, line, other, t, tid):
        """A neighbor beam not parallel to this line (sloped against it):
        'poly' its side outline over NEIGHBOR_SHOWN_M from its end by this
        beam, 'segs' its stirrups square to its axis - in this line's frame."""
        ys = [p[1] for p in other.section.polygon_m]
        y_lo, y_hi = min(ys), max(ys)
        near0 = abs(line.local(other.point(0.0, 0.0, 0.0))[2] - line.length / 2.0)
        near1 = abs(line.local(other.point(0.0, 0.0, other.length))[2] - line.length / 2.0)
        L = min(self.NEIGHBOR_SHOWN_M, other.length)
        a, b = (0.0, L) if near0 <= near1 else (other.length - L, other.length)

        def at(s, y):
            p = line.local(other.point(0.0, y, s))
            return (p[2], p[1])
        poly = [at(a, y_lo), at(b, y_lo), at(b, y_hi), at(a, y_hi)]
        cover = 0.04
        segs = [at(s, y_lo + cover) + at(s, y_hi - cover)
                for s in self._neighbor_stirrups(line, other, t, raw=True) if a <= s <= b]
        return {"line": other, "poly": poly, "segs": segs,
                "s0": min(p[0] for p in poly), "s1": max(p[0] for p in poly),
                "y0": min(p[1] for p in poly), "y1": max(p[1] for p in poly),
                "x0": min(p[0] for p in other.section.polygon_m), "x1": max(p[0] for p in other.section.polygon_m),
                "label": self._kind_text(other, t.name), "stirrups": [], "bars": [],
                "tag": u"line:{}:{}".format(tid, id_of(other.elements[0].Id))}

    def _neighbor_stirrups(self, line, other, t, raw=False):
        """A neighbor beam's stirrups as its own type sets them (its spans,
        a cantilever from its support), in this line's elevation frame
        (`raw`: as positions along the neighbor itself, m)."""
        try:
            cfg = t.config()
            zones, rest = rs.parse_distribution(cfg["EA_Estribo_Borde_Distribucion"])
            settings = self._line_settings(other)
            supports = self._supports(other)
            spans, _, _ = rb.spans_and_ends(other, supports)
            free = rs.free_ends(spans, [(sp["s0"], sp["s1"]) for sp in supports])
            out = []
            for index, (a, b) in enumerate(spans):
                own = bx.span_text(settings, index, "edge")
                z, r = rs.parse_distribution(own) if own else (zones, rest)
                for offset, _ in rs.span_zone_positions(b - a, z, r, free.get(index)):
                    out.append(a + offset if raw else line.local(other.point(0.0, 0.0, a + offset))[2])
            return out
        except Exception:
            return []  # not set up: drawn without them

    def _neighbor_bars(self, line, other, t):
        """A neighbor beam's main bars as it generates them (rb.main_bar_run:
        its run on into this beam, its hand edits) in this line's elevation
        frame - one item per face: its paths, how far it gets inside this
        beam from the support face, the neighbor's end that faces it and the
        tags to select / edit it."""
        try:
            spec_o = rc.ColumnSpec(t.config())
            settings = self._line_settings(other)
            supports = self._supports(other)
            _, sf, ef = rb.spans_and_ends(other, supports)
            cont = rb.mixed_continuations([line, other]).get(id(other))
            splice = splice_for_generation(self._splice_from_form()) if hasattr(self, "lap_boxes") else None
            legs = self._legs_from_form() if hasattr(self, "leg_boxes") else {}
            anchor = rb.anchor_of(t.config())
        except Exception:
            return []
        # the neighbor's end facing this beam (0: its start, 1: its end)
        near0 = line.local(other.point(0.0, 0.0, 0.0))[2]
        near1 = line.local(other.point(0.0, 0.0, other.length))[2]
        mid_this = line.length / 2.0
        end = 0 if abs(near0 - mid_this) < abs(near1 - mid_this) else 1
        before = max(near0, near1) <= mid_this  # the neighbor sits before this beam's start
        tag = u"{}:{}".format(id_of(other.type_id), id_of(other.elements[0].Id))
        out = {}
        for x, y, key in bx.kept_bars(settings, spec_o.design["bars"]):  # less its deleted ones
            face = bx.TOP if y > 0 else bx.BOTTOM
            try:
                leg0, leg1, s0, s1 = rb.main_bar_run(other, settings, spec_o.design, y, key, spec_o.cover_m,
                                                     sf, ef, anchor, legs, splice, cont)
            except Exception:
                continue
            d = rs.BAR_DIAMETERS_MM[key] / 1000.0
            pts = [line.local(other.point(px, py, ps))
                   for px, py, ps in rs.beam_bar_points(x, y, d, s0, s1, 0.0, False, leg0, leg1)]
            item = out.setdefault(face, {"face": face, "paths": [], "end": end, "tag": tag})
            item["paths"].append(pts)
            item["leg"] = leg0 if end == 0 else leg1  # its bend at the end inside this beam
            ss = [p[2] for p in pts]
            item["inside"] = max(0.0, max(ss)) if before else max(0.0, line.length - min(ss))
        return list(out.values())

    def _open_line(self, tag):
        """A neighbor beam clicked in the elevation: its type and that beam
        in the window (its stirrups, its plan for a flat one, are set there)."""
        _, tid, first = tag.split(u":")
        tid, first = int(tid), int(first)
        if tid not in self.by_id:
            return
        self._keep_draft()
        self._select_type(tid)
        for index, option in enumerate(getattr(self, "_view_options", [])):
            if any(id_of(e.Id) == first for e in option.elements):
                self.cbo_view_column.SelectedIndex = index
                break

    def view_column_changed(self, sender, args):
        if getattr(self, "_filling_columns", True):
            return
        index = self.cbo_view_column.SelectedIndex
        options = getattr(self, "_view_options", [])
        if index < 0 or index >= len(options):
            return
        self._view_line = options[index]
        self.elev_nav.reset()
        self.scene._extent = None
        self._views_sig = None  # force the views to redraw for this beam
        self.redraw()

    def config_changed(self, sender, args):
        if hasattr(self, "_views_sig"):  # also fired while the XAML loads
            self.redraw()

    def _family_view(self, key_text, dist_text):
        """{"key", "zones", "rest"} of a stirrup family, or None (message)
        when its settings don't read."""
        if not (dist_text or u"").strip():
            return None, None
        try:
            key = rs.parse_diameter(key_text)
            zones, rest = rs.parse_distribution(dist_text)
        except rs.SpecError as e:
            return None, u"{}".format(e)
        return {"key": key, "zones": zones, "rest": rest}, None

    def _ridge_view(self, line, form, legs, splice, settings):
        """The main bars of `line` when it meets another sloped beam at a
        ridge, as rsl.generate_pair lays them, in this line's frame and cut
        to near the ridge: {"paths": [(points (x, y, s), radius)],
        "by_face": {face: [points]}, "ridge_end": 0/1}; None otherwise."""
        self._elev_mirror = False
        if not getattr(line, "sloped", False):
            return None
        try:
            pool = [l for t in self.by_id.values()
                    if t.id == self.state.active or t.id in self.state.checked for l in t.lines]
            pkey = tuple([id(l) for l in pool])
            if getattr(self, "_pairs_key", None) != pkey:  # once: a click redraws fast
                self._pairs_key, self._pairs = pkey, rsl.ridge_pairs(pool)
            pairs = self._pairs
        except Exception:
            return None
        found = [p for p in pairs if p[0] is line or p[1] is line]
        pair = found[0] if found else None
        # the second beam of the pair drawn mirrored: both waters keep their
        # sides as in the model (the first rising to the right, it to the left)
        self._elev_mirror = pair is not None and pair[1] is line
        if pair is None:
            return None
        a, b, ka, kb = pair
        other = b if a is line else a
        try:
            own_spec = rc.ColumnSpec(self._config_from_form(True))
        except Exception:
            own_spec = None
        try:
            other_type = self.by_id.get(id_of(other.type_id))
            other_spec = rc.ColumnSpec(other_type.config()) if other_type is not None and \
                other_type.id != self.state.active else own_spec
        except Exception:
            other_spec = own_spec
        if own_spec is None or other_spec is None:
            return None
        spec_a, spec_b = (own_spec, other_spec) if a is line else (other_spec, own_spec)
        # the other water's settings as edited here (not only as saved): a
        # change made on it shows from this side too
        other_settings = self._line_settings(other)
        set_a, set_b = (settings, other_settings) if a is line else (other_settings, settings)
        anchor = form["anchor"]
        ckey = json.dumps([id(line), set_a, set_b, rs.design_to_text(self.design), anchor, form.get("cover"),
                           form.get("edge"), form.get("edge_dist"), legs, splice], sort_keys=True, default=str)
        cache = self.__dict__.setdefault("_ridge_cache", {})
        if ckey in cache:  # the same as last drawn: no recount (zoom, a click)
            return cache[ckey]
        if len(cache) > 20:
            cache.clear()
        try:
            frame, sides, paths, _ = rsl.pair_paths(doc, pair, spec_a, spec_b, anchor, anchor, splice, legs,
                                                    set_a, set_b)
        except Exception:
            return None
        lo, hi = -1e6, 1e6  # both waters whole
        out, by_face = [], {}
        for path, key, w, shares in paths:
            pts = [line.local(frame.world(u, z, w)) for u, z in path]
            pts = _clip_path_s(pts, lo, hi)
            if len(pts) < 2:
                continue
            r = rs.BAR_DIAMETERS_MM[key] / 2000.0
            out.append((pts, r))
            # its face: its height at this beam's middle (along its run in it)
            half, y_mid = line.length / 2.0, None
            for q0, q1 in zip(pts, pts[1:]):
                lo_s, hi_s = min(q0[2], q1[2]), max(q0[2], q1[2])
                if hi_s - lo_s > 1e-6 and lo_s <= half <= hi_s:
                    y_mid = q0[1] + (q1[1] - q0[1]) * (half - q0[2]) / (q1[2] - q0[2])
                    break
            if y_mid is None:
                continue  # not along this beam (the other water's own bars)
            by_face.setdefault(bx.TOP if y_mid > 0 else bx.BOTTOM, []).append(pts)
        ridge_end = ka if a is line else kb
        own = sides[0] if a is line else sides[1]
        legs_view = {}
        for face, rows, ld_edit in ((bx.TOP, own["top"], own.get("ld_top")), (bx.BOTTOM, own["bottom"], own.get("ld_bot"))):
            if not rows:
                continue
            (eave, _), key, _ = rows[0]
            eave_leg = own["leg"].get(eave, 0.0)
            if ld_edit is not None:
                ridge_leg = float(ld_edit)
            elif face == bx.TOP and sides[0].get("ld_top") is None and sides[1].get("ld_top") is None:
                ridge_leg = 0.0  # continuous over the ridge
            else:
                ridge_leg = rs.e060_lap_cm(key, factor=1.0) / 100.0
            legs_view[face] = (ridge_leg, eave_leg) if ridge_end == 0 else (eave_leg, ridge_leg)
        result = {"paths": out, "by_face": by_face, "ridge_end": ridge_end, "legs": legs_view,
                  "stirrups": self._other_stirrups(line, other, form)}
        cache[ckey] = result
        return result

    def _other_stirrups(self, line, other, form):
        """The other water's stirrups (its type's distribution in its clear
        span), as segments [(s, y), (s, y)] in this line's elevation."""
        try:
            supports = self._supports(other)
            spans, _, _ = rb.spans_and_ends(other, supports)
            fam, _ = self._family_view(form["edge"], form["edge_dist"])
            if fam is None or not spans:
                return []
            cover = float((form.get("cover") or u"4").replace(u",", u".")) / 100.0
        except Exception:
            return []
        out = []
        for a, b in spans:
            for start, n, spacing, _, side in rs.span_stirrup_sets(b - a, fam["zones"], fam["rest"], None):
                for k in range(n):
                    sp = a + start + k * spacing
                    p = line.local(other.point(0.0, other.ref_bottom + cover, sp))
                    q = line.local(other.point(0.0, other.ref_top - cover, sp))
                    out.append(((p[2], p[1]), (q[2], q[1])))
        return out

    def _elev_tilt(self):
        """The slope (degrees, + rising with s) of the beam shown, 0 when it
        is level: its elevation is drawn at it, as in the project."""
        line = getattr(self, "_view_line", None)
        if line is None or not getattr(line, "sloped", False):
            return 0.0
        a, b = line.point(0.0, 0.0, 0.0), line.point(0.0, 0.0, 1.0)
        return math.degrees(math.atan2(b.Z - a.Z, math.hypot(b.X - a.X, b.Y - a.Y)))

    def _ridge_partner(self, line):
        """Triangles (this line's local x, y, s) of the sloped beam meeting
        this sloped one at an end in its own vertical plane - the other water
        of the roof - shown with it in the elevation and the 3D view."""
        if not getattr(line, "sloped", False):
            return []
        cache = self.__dict__.setdefault("_partner_cache", {})
        key = tuple([id_of(e.Id) for e in line.elements])
        if key in cache:
            return cache[key]
        mine = set(key)
        d0 = line.point(0.0, 0.0, 1.0) - line.point(0.0, 0.0, 0.0)
        plan = DB.XYZ(d0.X, d0.Y, 0.0)
        out = []
        for s_end in (0.0, line.length):
            p = line.point(0.0, 0.0, s_end)
            pad = DB.XYZ(1.5, 1.5, 1.5)  # ft
            near = (DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_StructuralFraming)
                    .WhereElementIsNotElementType()
                    .WherePasses(DB.BoundingBoxIntersectsFilter(DB.Outline(p - pad, p + pad))))
            for e in near:
                if id_of(e.Id) in mine or not isinstance(getattr(e, "Location", None), DB.LocationCurve):
                    continue
                c = e.Location.Curve
                d = c.GetEndPoint(1) - c.GetEndPoint(0)
                if abs(d.Z) < 0.05 * d.GetLength():
                    continue  # level: a ridge or edge beam, not the other water
                if abs(plan.CrossProduct(DB.XYZ(d.X, d.Y, 0.0)).Z) > 0.05 * plan.GetLength() * d.GetLength():
                    continue  # another vertical plane
                tris = []
                for solid in rc._solids(e.get_Geometry(DB.Options())):
                    for face in solid.Faces:
                        mesh = face.Triangulate()
                        for i in range(mesh.NumTriangles):
                            tri = mesh.get_Triangle(i)
                            tris.append(tuple([line.local(tri.get_Vertex(k)) for k in range(3)]))
                if tris:
                    out.append(tris)
                    mine.add(id_of(e.Id))
        cache[key] = out
        return out

    def _views_data(self):
        """(elevation data, 3D data) of the beam chosen in "Viga:" with the
        active type as configured and drawn right now (unsaved changes
        included) - what rb.generate_line would build."""
        t = self.by_id.get(self.state.active)
        line = self._view_line
        if t is None or t.section is None or line is None:
            return None, None
        f = self._get_form()
        bars = self.design["bars"]
        supports = self._supports(line)
        spans, start_face, end_face = rb.spans_and_ends(line, supports)
        messages = []
        edge, edge_msg = self._family_view(f["edge"], f["edge_dist"])
        conf, conf_msg = self._family_view(f["conf"], f["conf_dist"])
        messages += [m for m in (edge_msg and u"Borde: " + edge_msg, conf_msg and u"Confinamiento: " + conf_msg) if m]
        if edge is None and not edge_msg:
            messages.append(u"Falta la distribucion del estribo de borde")
        if not spans:
            messages.append(u"La viga no tiene luz libre entre apoyos")
        settings = self._line_settings(line)
        free = rs.free_ends(spans, [(sp["s0"], sp["s1"]) for sp in supports])  # cantilevers
        span_data, span_fams, span_labels = [], [], []
        for index, (a, b) in enumerate(spans):
            item = {"a": a, "b": b}
            fams = {}
            for name, family, key_text in (("edge", edge, f["edge"]), ("conf", conf, f["conf"])):
                own = bx.span_text(settings, index, name)
                if own:
                    family, msg = self._family_view(key_text, own)
                    if msg:
                        messages.append(u"Tramo {}: {}".format(index + 1, msg))
                if family:
                    fams[name] = family
                    item[name] = dict(family, tagged=rs.span_zone_positions(b - a, family["zones"], family["rest"],
                                                                            free.get(index)))
            own = bx.span_text(settings, index, "edge")
            span_labels.append((a, b, u"\u00d8{}: {}{}".format(f["edge"], own or u"(del tipo)",
                                                                 u"  (voladizo)" if index in free else u""),
                                u"span:{}".format(index)))
            span_data.append(item)
            span_fams.append(fams)
        # stirrups and ties for the 3D view, stacked like they are generated
        families = {rs.KIND_EDGE: edge, rs.KIND_CONFINEMENT: conf}
        drawn = [(kind, poly, wrap, is_open) for kind, poly, wrap, is_open in self.design["stirrups"]]
        drawn += [(kind, [a, b], None, None) for kind, a, b in self.design["ties"]]
        drawn = [item for item in drawn if families.get(item[0]) is not None]
        lifts = rs.stack_lifts([(kind, rs.BAR_DIAMETERS_MM[families[kind]["key"]] / 1000.0, is_open is None)
                                for kind, _, _, is_open in drawn])
        loops = []
        for (kind, poly, wrap, is_open), lift in zip(drawn, lifts):
            family = families[kind]
            try:
                if is_open is None:  # a tie
                    pts, closed = list(rs.tie_centerline(poly[0], poly[1], bars, family["key"])), False
                else:
                    pts = rs.stirrup_centerline(poly, bars, family["key"], wrap, is_open)
                    closed = not is_open
            except rs.SpecError:
                continue
            ss = []
            name = "edge" if kind == rs.KIND_EDGE else "conf"
            for index, (a, b) in enumerate(spans):
                fam = span_fams[index].get(name) or family
                for start, count, spacing, _, side in rs.span_stirrup_sets(b - a, fam["zones"], fam["rest"],
                                                                           free.get(index)):
                    ss += [a + start + j * spacing + side * lift for j in range(count)]
            radius = rs.BAR_DIAMETERS_MM[family["key"]] / 2000.0
            if line.variable:  # a haunch: each stirrup fitted to its section
                loops += [(kind, line.polygon_at(pts, s_), closed, [s_], radius) for s_ in ss]
            else:
                loops.append((kind, pts, closed, ss, radius))
        # longitudinal bars along the whole beam
        try:
            cover = float(f["cover"].replace(u",", u".")) / 100.0
        except ValueError:
            cover = rb.DEFAULT_COVER_CM / 100.0
        bar_start, bar_end = start_face + cover, end_face - cover
        splice = splice_for_generation(self._splice_from_form()) if hasattr(self, "lap_boxes") else None
        legs = self._legs_from_form() if hasattr(self, "leg_boxes") else {}
        confinement = rs.confinement_length(edge["zones"]) if edge else 0.0
        bar_paths, laps = [], []
        missing_legs, missing_laps = set(), set()
        hooked = f["anchor"] == rb.ANCHOR_HOOK
        hook_view = []
        main_view = {}  # face -> its main bars' paths, ends and legs (clicked to edit)
        # how far a bar made longer may go (rb.generate_line): the far face of
        # the flat beam it runs into less the cover, else its support's
        mixed = self._mixed_neighbors(line)
        # this beam's run on into its neighbors, as generated (rb.mixed_continuations)
        cont = rb.mixed_continuations([line] + [m["line"] for m in mixed]).get(id(line)) if mixed else None
        for x, y, key in bx.kept_bars(settings, bars):  # less the ones deleted for this beam
            d = rs.BAR_DIAMETERS_MM[key] / 1000.0
            top = y > 0
            face = bx.TOP if top else bx.BOTTOM
            # the bar as generated (rb.main_bar_run, one logic): its hooks cut to the
            # depth there is, its run on into a neighbor, this face's hand edits
            leg0, leg1, b_start, b_end = rb.main_bar_run(line, settings, self.design, y, key, cover, start_face,
                                                         end_face, f["anchor"], legs, splice, cont)
            if not hook_view:
                hook_view = [(bar_start, leg0, u"hook:0"), (bar_end, leg1, u"hook:1")]
            leg = max(leg0, leg1)
            lap, pieces = 0.0, [(b_start, b_end)]
            if splice is not None and b_end - b_start + leg0 + leg1 > splice["max"] + 1e-6:
                lap = splice["laps"].get(key, 0.0)
                if not lap:
                    missing_laps.add(key)
                else:
                    pieces, _ = rs.lap_pieces(b_start, b_end, rs.beam_lap_zones(spans, top, confinement, lap),
                                              lap, splice["max"] - leg)
            for i, (s0, s1) in enumerate(pieces):
                last = i == len(pieces) - 1
                path = rs.beam_bar_points(x, y, d, s0, s1, lap, not last,
                                          leg0 if i == 0 else 0.0, leg1 if last else 0.0)
                if line.variable:  # along the sloped face of a haunch
                    path = rs.follow_profile(path, line.shift_at(y), line.breaks)
                bar_paths.append((path, d / 2.0))
                main_view.setdefault(face, {"paths": [], "ends": (b_start, b_end), "legs": (leg0, leg1),
                                            "edit": bx.main_edit(settings, face),
                                            # what it really got longer, up to the concrete's face
                                            "shown": (round(bar_start - b_start, 3), round(b_end - bar_end, 3))})
                main_view[face]["paths"].append(path)
                if not last:
                    laps.append((round(s1 - lap, 3), round(s1, 3), key, top))
        # a gable frame (two sloped beams at a ridge): its main bars as
        # rsl.generate_pair makes them - over the ridge into the other water,
        # plumb hooks into the eave support - not a level beam's hooks
        ridge = self._ridge_view(line, f, legs, splice, settings)
        if ridge is not None:
            mains = [id(p) for mv in main_view.values() for p in mv["paths"]]
            kept = [bp for bp in bar_paths if id(bp[0]) not in mains]
            bar_paths[:] = kept + ridge["paths"]
            for face, paths in ridge["by_face"].items():
                if face in main_view:
                    main_view[face]["paths"] = paths
                    if face in ridge["legs"]:  # what "Doblez" shows: the eave hook, the run past the ridge
                        main_view[face]["legs"] = ridge["legs"][face]
            hook_view = [h for h in hook_view if h[2] != u"hook:{}".format(ridge["ridge_end"])]
        # the beam's extra bars (bastones, balancines), as rb.generate_line lays them
        extras_view = []
        for index, extra in enumerate(settings["extras"]):
            key = extra["d"]
            d = rs.BAR_DIAMETERS_MM[key] / 1000.0
            s0, s1, hook0, hook1 = bx.extra_ends(extra, bar_start, bar_end)
            if s1 - s0 < 0.1:
                continue
            leg0, leg1 = bx.extra_legs(extra)  # its own hooks
            positions = bx.placed_positions(extra, bars, rs.BAR_DIAMETERS_MM)
            for k_pos, (x, y) in enumerate(positions):
                path = rs.beam_bar_points(x, y, d, s0, s1, 0.0, False, leg0, leg1)
                if line.variable:
                    path = rs.follow_profile(path, line.shift_at(y), line.breaks)
                bar_paths.append((path, d / 2.0))
                if k_pos == 0:
                    extras_view.append({
                        "face": extra["face"], "path": path, "tag": u"extra:{}".format(index),
                        "len_tag": u"len:{}".format(index), "index": index,
                        "label": u"{}\u00d8{}".format(int(extra["n"]), extra["d"]),
                        "s0": extra["s0"], "s1": extra["s1"]})
        # the dimensions, always from a column face, packed in rows per face
        rows, n_rows = {}, {}
        for face in (bx.TOP, bx.BOTTOM):
            r = bx.cota_rows_all(settings["extras"], spans, start_face, end_face, face) if spans else {}
            rows.update(r)
            n_rows[face] = 1 + max(list(r.values()) + [-1])
        for item in extras_view:
            i = item["index"]
            cs = bx.extra_cotas(settings["extras"][i], spans, start_face, end_face) if spans else []
            item["cotas"] = [dict(c, row=rows.get((i, j), 0), tag=u"cota:{}:{}".format(i, j)) for j, c in enumerate(cs)]
            item["row"] = min([c["row"] for c in item["cotas"]] + [0])
        top_rows, bottom_rows = n_rows.get(bx.TOP, 0), n_rows.get(bx.BOTTOM, 0)
        # the cuts: automatic (one per stretch of the same steel) until moved
        cuts = bx.two_cuts(settings, spans)  # 1-1 and 2-2, moved by hand
        self._beam_now = (line, spans, start_face, end_face, bars, settings, cuts)
        if missing_laps:
            messages.append(u"Sin empalme (diametro no marcado): barras de " + u", ".join(sorted(missing_laps)))
        polygon = line.section.polygon_m
        supports_view = [{"label": s["label"], "s0": s["s0"], "s1": s["s1"], "under": s.get("under", False)}
                         for s in supports]
        # the concrete as it is: each element's outline (elevation) and its
        # stretches between slope changes with their sections (3D)
        outlines, pieces = [], []
        for r0, r1 in line.ranges:
            cuts = [r0] + [b for b in line.breaks if r0 + 1e-6 < b < r1 - 1e-6] + [r1]
            outlines.append([(c, line.depth_at(c)[1]) for c in cuts]
                            + [(c, line.depth_at(c)[0]) for c in reversed(cuts)])
            pieces += [(c0, line.polygon_at(polygon, c0), c1, line.polygon_at(polygon, c1))
                       for c0, c1 in zip(cuts, cuts[1:])]
        # the 3D view: the neighbors' concrete too (a flat beam past this one),
        # each a box, and their main bars as generated (running on into this beam)
        # this beam's main bars at an end by a neighbor in line (a flat beam by
        # a deep one): how far they get into it, from the support face - a cota
        # to edit (user, 2026-11-03)
        main_into = []
        for face_key, mv in (main_view.items() if cont else []):
            for end in (0, 1):
                if (cont.get("reach") or [0.0, 0.0])[end] > 0:
                    face_s = start_face if end == 0 else end_face
                    e = mv["ends"][end]
                    raw = (face_s - e) if end == 0 else (e - face_s)  # < 0: short of the face (cover)
                    main_into.append({"face": face_key, "end": end, "face_s": face_s, "end_s": e,
                                      "inside": max(0.0, raw), "raw": raw})
        neighbor_paths = []
        for m in mixed:
            box = [(m["x0"], m["y0"]), (m["x1"], m["y0"]), (m["x1"], m["y1"]), (m["x0"], m["y1"])]
            pieces.append((m["s0"], box, m["s1"], box))
            for item in m.get("bars") or []:
                neighbor_paths += [(pts, 0.007) for pts in item["paths"]]
        elev = {
            "polygon": polygon,
            "length": line.length,
            "ranges": line.ranges,
            "supports": supports_view,
            "spans": span_data,
            "bar_paths": bar_paths,
            "laps": sorted(set(laps)),
            "cover": cover,
            "message": messages[0] if messages else None,
            "outlines": outlines,
            "bottom": line.bottom_points,
            "top": line.top_points,
            "extras": extras_view,
            "cota_rows": (top_rows, bottom_rows),
            "draft": getattr(self, "_draft_view", None),
            "draft_hook": getattr(self, "_hook_view", None),
            "span_labels": span_labels,
            "cuts": [(c, k + 1) for k, c in enumerate(cuts)],
            "hooks": hook_view,
            "selected": self._selected_index(),
            # the picked beams of other types on this axis (a flat beam past
            # a deep one), and each span's kind over it
            "mixed": mixed,
            # the main bars of each face: one clicked shows orange with its ends to edit
            "main": main_view,
            "main_into": main_into,
            "main_selected": getattr(self, "_main_face", None),
            "nb_selected": getattr(self, "_nb_sel", None),  # a neighbor's bar clicked: (its tag, face)
            "kinds": [((a + b) / 2.0, self._kind_text(line, t.name)) for a, b in spans],
        }
        partners = self._ridge_partner(line)  # the other water of the roof: drawn with it
        clipped = [_hull([(q[2], q[1]) for tri in tris for q in tri]) for tris in partners]  # whole: both waters
        elev["partners"] = [p for p in clipped if len(p) >= 3]
        elev["tilt"] = self._elev_tilt()  # its supports drawn plumb in the turned elevation
        elev["other_stirrups"] = (ridge or {}).get("stirrups") or []
        scene = {
            "polygon": polygon,
            "length": line.length,
            "ranges": line.ranges,
            "bar_paths": bar_paths + neighbor_paths,
            "loops": loops,
            "supports_mesh": [tri for s in supports for tri in s["triangles"]]
                             + [tri for tris in partners for tri in tris],
            "pieces": pieces,
        }
        return elev, scene

    def _refresh_views(self):
        try:
            elev, scene = self._views_data()
        except Exception as e:
            elev, scene = None, None
            self._status(u"No se pudieron dibujar el alzado y la vista 3D: {}".format(e))
        self._elev_data = elev
        self._scene_data = scene
        if self._selected_index() is None and hasattr(self, "sel_bar"):
            self.sel_bar.Visibility = Visibility.Collapsed  # another beam: nothing selected
        self._draw_elevation()
        self._build_3d()

    def _elev_fitted(self):
        width, height = self.canvas_elev.ActualWidth, self.canvas_elev.ActualHeight
        if self._elev_data is None or width < 50 or height < 50:
            return None
        return rv.fit_frame(width, height, *rv.beam_elevation_extent(self._elev_data),
                            margins=(20, 16, 20, 16))

    def _draw_elevation(self):
        fitted = self._elev_fitted()
        if fitted is None:
            self.canvas_elev.Children.Clear()
            return
        rv.draw_beam_elevation(self.canvas_elev, self._elev_data, self.elev_nav.resolve(fitted))
        self._tilt_elevation()
        self._draw_cuts()

    def _tilt_elevation(self):
        """A sloped beam's elevation turned to its slope (as in the project),
        shrunk to stay in view; the mouse is read in the canvas's own
        coordinates (GetPosition undoes the turn), so every click and cota
        works as on a level beam."""
        from System.Windows.Media import TransformGroup, ScaleTransform, RotateTransform
        canvas = self.canvas_elev
        tilt = self._elev_tilt()
        if abs(tilt) < 0.5:
            canvas.RenderTransform = None
            return
        w, h = canvas.ActualWidth, canvas.ActualHeight
        r = math.radians(abs(tilt))
        # the drawing's own size (not the canvas's): turned, it is fitted back in
        s0, y0, s1, y1 = rv.beam_elevation_extent(self._elev_data)
        scale = self.elev_nav.resolve(self._elev_fitted())[0]
        cw, ch = (s1 - s0) * scale, (y1 - y0) * scale
        k = min(1.0, w / (cw * math.cos(r) + ch * math.sin(r) or 1.0), h / (cw * math.sin(r) + ch * math.cos(r) or 1.0))
        k = max(k, 0.5)
        mirror = bool(getattr(self, "_elev_mirror", False))
        group = TransformGroup()
        group.Children.Add(ScaleTransform(-k if mirror else k, k, w / 2.0, h / 2.0))
        # up to the right when rising with s; mirrored, up to the left
        group.Children.Add(RotateTransform(tilt if mirror else -tilt, w / 2.0, h / 2.0))
        canvas.RenderTransform = group
        if mirror:  # its texts turned back, to be read
            from System.Windows.Controls import TextBlock
            from System.Windows import Point as _Point
            for child in canvas.Children:
                if isinstance(child, TextBlock):
                    child.RenderTransformOrigin = _Point(0.5, 0.5)
                    child.RenderTransform = ScaleTransform(-1.0, 1.0)

    # -- the beam's own settings: spans' stirrups, extras, cuts -----------------
    def _line_settings(self, line):
        """The beam's settings (bx): edited here, else saved on it."""
        key = self._line_key(line)
        if key in self.state.line_edits:
            return self.state.line_edits[key]
        cache = self.__dict__.setdefault("_line_read", {})
        if key not in cache:
            try:
                cache[key] = rb.read_line_settings(line)
            except Exception:
                cache[key] = bx.default_settings()
        return cache[key]

    def _edit_settings(self):
        """The current beam's settings, to change (kept until saved)."""
        line = self._view_line
        key = self._line_key(line)
        if key not in self.state.line_edits:
            self.state.line_edits[key] = bx.read(bx.write(self._line_settings(line)))
        return self.state.line_edits[key]

    def _changed(self):
        self._views_sig = None
        self.redraw()

    def _main_face(self, face):
        """(count, key) of the main bars on a face: the defaults of an extra."""
        keys = [k for x, y, k in self.design["bars"] if (y > 0) == (face == bx.TOP)]
        if not keys:
            return 2, u'5/8"'
        return 2, max(set(keys), key=keys.count)

    def _draw_cuts(self):
        panel = self.panel_cuts
        panel.Children.Clear()
        now = getattr(self, "_beam_now", None)
        if not now:
            return
        line, spans, start_face, end_face, bars, settings, cuts = now
        poly = line.section.polygon_m
        xs = [p[0] for p in poly]
        ys = [p[1] for p in poly]
        k = min(80.0 / max(max(xs) - min(xs), 1e-3), 110.0 / max(max(ys) - min(ys), 1e-3))
        sel = self._selected_index()
        for number, s_cut in enumerate(cuts, 1):
            box = Canvas()
            box.Width, box.Height = 250, 160
            box.Background = brush(255, 255, 255)
            cx, cy = 10.0 - min(xs) * k, 18.0 + max(ys) * k

            def P(x, y):
                return Point(cx + x * k, cy - y * k)
            shape = Polygon()
            pc = PointCollection()
            for x, y in poly:
                pc.Add(P(x, y))
            shape.Points = pc
            shape.Stroke = brush(60, 60, 60)
            shape.Fill = brush(236, 238, 241)
            box.Children.Add(shape)
            picked_bar = getattr(self, "_main_bar", None)
            for x, y, key in bars:  # click one to select it, Supr: out of this beam (or back in)
                off = bx.bar_off(settings, x, y)
                on = picked_bar is not None and abs(picked_bar[0] - x) < 1e-3 and abs(picked_bar[1] - y) < 1e-3
                color = brush(240, 140, 20) if on else (brush(205, 205, 205) if off else brush(30, 30, 30))
                dot = self._cut_dot(box, P(x, y), rs.BAR_DIAMETERS_MM[key] / 2000.0 * k, color, 7.0 if on else 5.0)
                dot.Tag = u"mbar:{}:{}".format(x, y)
                dot.Cursor = Cursors.Hand
            placed = []
            for i_extra, extra in enumerate(settings["extras"]):
                if extra["s0"] - 1e-6 <= s_cut <= extra["s1"] + 1e-6:
                    color = brush(226, 75, 74) if extra["face"] == bx.TOP else brush(29, 158, 117)
                    if i_extra == sel:
                        color = brush(240, 140, 20)
                    pts = bx.placed_positions(extra, bars, rs.BAR_DIAMETERS_MM)
                    for k_bar, (x, y) in enumerate(pts):
                        dot = self._cut_dot(box, P(x, y), rs.BAR_DIAMETERS_MM[extra["d"]] / 2000.0 * k, color, 6.0)
                        dot.Tag = u"dot:{}:{}".format(i_extra, k_bar)
                        dot.Cursor = Cursors.SizeAll
                    placed.append((extra, pts))
            box.Tag = (k, cx, cy, poly)
            box.MouseLeftButtonDown += self.cut_dot_down
            box.MouseMove += self.cut_dot_move
            box.MouseLeftButtonUp += self.cut_dot_up
            lines = [u"{}-{}  (s = {:.2f} m)".format(number, number, s_cut)]
            for face, name in ((bx.TOP, u"Sup"), (bx.BOTTOM, u"Inf")):
                text, area = bx.steel_at(s_cut, bx.kept_bars(settings, bars), settings["extras"],
                                         rs.BAR_DIAMETERS_MM, face)
                lines.append(u"{}: {}".format(name, text))
                lines.append(u"      = {:.2f} cm\u00b2".format(area))
            for extra, pts in placed:  # where the extras sit (x;y cm, see position_text)
                lines.append(u"+{}\u00d8{} {}: {}".format(int(extra["n"]), extra["d"],
                                                          u"sup" if extra["face"] == bx.TOP else u"inf",
                                                          bx.position_text(pts, poly, extra["face"])))
            label = TextBlock()
            label.Text = u"\n".join(lines)
            label.FontSize = 10
            Canvas.SetLeft(label, 100)
            Canvas.SetTop(label, 4)
            box.Children.Add(label)
            frame = Border()
            frame.BorderBrush = brush(200, 200, 210)
            frame.BorderThickness = Thickness(1)
            frame.Margin = Thickness(0, 0, 6, 0)
            frame.Child = box
            panel.Children.Add(frame)
        self._draw_neighbor_sections(panel, bx.kept_bars(settings, bars), k)
        self._draw_stirrup_plan(panel, line, poly)

    def _draw_neighbor_sections(self, panel, bars, k):
        """Each neighbor beam's section (a flat beam past this one), at this
        cuts' scale: its outline and the main bars of this beam that reach
        into it (a bottom bar run on into the flat beam shows there)."""
        elev = self._elev_data or {}
        main = elev.get("main") or {}
        for m in elev.get("mixed") or []:
            mid = (m["s0"] + m["s1"]) / 2.0
            inside = []
            for x, y, key in bars:
                ends = (main.get(bx.TOP if y > 0 else bx.BOTTOM) or {}).get("ends")
                if ends and ends[0] - 1e-6 <= mid <= ends[1] + 1e-6 and m["y0"] - 1e-6 <= y <= m["y1"] + 1e-6:
                    inside.append((x, y, key))
            box = Canvas()
            box.Width, box.Height = 250, 160
            box.Background = brush(255, 255, 255)
            cx, cy = 10.0 - m["x0"] * k, 18.0 + m["y1"] * k

            def P(x, y):
                return Point(cx + x * k, cy - y * k)
            shape = Polygon()
            pc = PointCollection()
            for x, y in ((m["x0"], m["y0"]), (m["x1"], m["y0"]), (m["x1"], m["y1"]), (m["x0"], m["y1"])):
                pc.Add(P(x, y))
            shape.Points = pc
            shape.Stroke = brush(150, 120, 80)
            shape.Fill = brush(236, 228, 214)
            box.Children.Add(shape)
            for x, y, key in inside:
                self._cut_dot(box, P(x, y), rs.BAR_DIAMETERS_MM[key] / 2000.0 * k, brush(240, 140, 20))
            label = TextBlock()
            label.Text = u"SECCIÓN\n{}\n(s = {:.2f} m)\nbarras que entran: {}".format(m["label"], mid, len(inside))
            label.FontSize = 10
            Canvas.SetLeft(label, 100)
            Canvas.SetTop(label, 4)
            box.Children.Add(label)
            frame = Border()
            frame.BorderBrush = brush(150, 120, 80)
            frame.BorderThickness = Thickness(1)
            frame.Margin = Thickness(0, 0, 6, 0)
            frame.Child = box
            panel.Children.Add(frame)

    def _draw_stirrup_plan(self, panel, line, poly):
        """A flat beam (h <= b) or a cantilever: its plan beside the cuts,
        every stirrup drawn where it is generated (the spans' "tagged"
        positions of _views_data) and counted."""
        elev = self._elev_data
        if not elev or not elev["spans"]:
            return
        xs = [p[0] for p in poly]
        ys = [p[1] for p in poly]
        b, h = max(xs) - min(xs), max(ys) - min(ys)
        cantilever = any(u"(voladizo)" in lbl[2] for lbl in elev["span_labels"])
        if h > b + 1e-6 and not cantilever:
            return
        s_min = min([sp["a"] for sp in elev["spans"]] + [s["s0"] for s in elev["supports"]])
        s_max = max([sp["b"] for sp in elev["spans"]] + [s["s1"] for s in elev["supports"]])
        box = Canvas()
        box.Width, box.Height = 340, 160
        box.Background = brush(255, 255, 255)
        k = min((box.Width - 30) / max(s_max - s_min, 1e-3), 60.0 / max(b, 1e-3))
        x0, yc = 15.0, 92.0

        def P(s, w):
            return Point(x0 + (s - s_min) * k, yc - w * k)

        def rect(s0, s1, w0, w1, stroke, fill):
            shape = Polygon()
            pc = PointCollection()
            for q in (P(s0, w0), P(s1, w0), P(s1, w1), P(s0, w1)):
                pc.Add(q)
            shape.Points = pc
            shape.Stroke = stroke
            shape.Fill = fill
            box.Children.Add(shape)

        rect(max(s_min, 0.0), min(s_max, elev["length"]), -b / 2.0, b / 2.0, brush(60, 60, 60), brush(236, 238, 241))
        for sup in elev["supports"]:
            rect(sup["s0"], sup["s1"], -b / 2.0 - 0.06, b / 2.0 + 0.06, brush(90, 90, 90), brush(205, 205, 210))
            tag = TextBlock()
            tag.Text = sup["label"]
            tag.FontSize = 9
            q = P((sup["s0"] + sup["s1"]) / 2.0, -b / 2.0 - 0.06)
            Canvas.SetLeft(tag, q.X - 20)
            Canvas.SetTop(tag, q.Y + 2)
            box.Children.Add(tag)
        for lbl in elev["span_labels"]:
            if u"(voladizo)" in lbl[2]:
                free_s = lbl[1] if not any(abs(s["s0"] - lbl[1]) < 0.03 for s in elev["supports"]) else lbl[0]
                tag = TextBlock()
                tag.Text = u"borde libre"
                tag.FontSize = 9
                q = P(free_s, -b / 2.0)
                Canvas.SetLeft(tag, q.X - 50)
                Canvas.SetTop(tag, q.Y + 2)
                box.Children.Add(tag)
        cover = elev["cover"]
        total, key, texts = 0, None, []
        for sp in elev["spans"]:
            edge = sp.get("edge")
            if not edge:
                continue
            key = edge["key"]
            for s_rel, _ in edge["tagged"]:
                st = Line()
                p0, p1 = P(sp["a"] + s_rel, -b / 2.0 + cover), P(sp["a"] + s_rel, b / 2.0 - cover)
                st.X1, st.Y1, st.X2, st.Y2 = p0.X, p0.Y, p1.X, p1.Y
                st.Stroke = brush(24, 95, 165)
                st.StrokeThickness = 1.5
                box.Children.Add(st)
            total += len(edge["tagged"])
            texts.append(u"luz {:.2f} m: {} est.".format(sp["b"] - sp["a"], len(edge["tagged"])))
        label = TextBlock()
        kind = u" en ".join(n for n, on in ((u"viga chata", h <= b + 1e-6), (u"voladizo", cantilever)) if on)
        label.Text = u"PLANTA - {}\n{} estribos Ø{}   ({})".format(kind, total, key or u"-", u"; ".join(texts))
        label.FontSize = 10
        Canvas.SetLeft(label, 8)
        Canvas.SetTop(label, 4)
        box.Children.Add(label)
        frame = Border()
        frame.BorderBrush = brush(24, 95, 165)
        frame.BorderThickness = Thickness(1)
        frame.Margin = Thickness(0, 0, 6, 0)
        frame.Child = box
        panel.Children.Add(frame)

    @staticmethod
    def _cut_dot(canvas, center, radius, fill, minimum=4.0):
        from System.Windows.Shapes import Ellipse
        e = Ellipse()
        e.Width = e.Height = max(minimum, 2 * radius)
        e.Fill = fill
        Canvas.SetLeft(e, center.X - e.Width / 2.0)
        Canvas.SetTop(e, center.Y - e.Height / 2.0)
        canvas.Children.Add(e)
        return e

    # -- an extra's bars dragged in a cut: where they really go ----------------
    def cut_dot_down(self, sender, args):
        tag = getattr(args.OriginalSource, "Tag", None)
        if isinstance(tag, basestring) and tag.startswith(u"mbar:"):  # a main bar: selected (Supr: out)
            x, y = [float(v) for v in tag[5:].split(u":")]
            same = getattr(self, "_main_bar", None) == (x, y)
            self._main_bar = None if same else (x, y)
            self._main_face = None if same else (bx.TOP if y > 0 else bx.BOTTOM)
            self._changed()
            args.Handled = True
            return
        if not (isinstance(tag, basestring) and tag.startswith(u"dot:")):
            return
        i_extra, k_bar = [int(t) for t in tag[4:].split(u":")]
        self._dot_drag = (sender, args.OriginalSource, i_extra, k_bar, args.GetPosition(sender), False)
        sender.CaptureMouse()
        args.Handled = True

    def cut_dot_move(self, sender, args):
        drag = getattr(self, "_dot_drag", None)
        if drag is None or drag[0] is not sender:
            return
        box, dot, i_extra, k_bar, start, _ = drag
        pt = args.GetPosition(box)
        if abs(pt.X - start.X) + abs(pt.Y - start.Y) < 2:
            return
        k, cx, cy, poly = box.Tag
        x, y = bx.snap_to_bars((pt.X - cx) / k, (cy - pt.Y) / k, self.design["bars"])
        Canvas.SetLeft(dot, cx + x * k - dot.Width / 2.0)
        Canvas.SetTop(dot, cy - y * k - dot.Height / 2.0)
        self._dot_drag = (box, dot, i_extra, k_bar, start, True)

    def cut_dot_up(self, sender, args):
        drag = getattr(self, "_dot_drag", None)
        if drag is None:
            return
        self._dot_drag = None
        box, dot, i_extra, k_bar, start, moved = drag
        box.ReleaseMouseCapture()
        if moved:
            k, cx, cy, poly = box.Tag
            pt = args.GetPosition(box)
            x = round(((pt.X - cx) / k) / 0.005) * 0.005  # to 5 mm
            y = round(((cy - pt.Y) / k) / 0.005) * 0.005
            x, y = bx.snap_to_bars(x, y, self.design["bars"])  # lined up with the main bars
            xs = [q[0] for q in poly]
            ys = [q[1] for q in poly]
            x = max(min(xs) + 0.02, min(max(xs) - 0.02, x))
            y = max(min(ys) + 0.02, min(max(ys) - 0.02, y))
            extra = self._edit_settings()["extras"][i_extra]
            bx.set_position(extra, self.design["bars"], rs.BAR_DIAMETERS_MM, k_bar, x, y)
        self._select(i_extra)

    # -- the selected extra: edited in the bar over the elevation -------------
    def _selected_index(self):
        sel = getattr(self, "_sel", None)
        line = getattr(self, "_view_line", None)
        if sel is None or line is None or sel[0] != self._line_key(line):
            return None
        extras = self._line_settings(line)["extras"]
        return sel[1] if sel[1] < len(extras) else None

    def _select(self, index):
        line = getattr(self, "_view_line", None)
        self._sel = (self._line_key(line), index) if (index is not None and line is not None) else None
        self._changed()
        self._show_sel()

    def _show_sel(self):
        index = self._selected_index()
        now = getattr(self, "_beam_now", None)
        if index is None or not now:
            self.sel_bar.Visibility = Visibility.Collapsed
            return
        line, spans, start_face, end_face, bars = now[0], now[1], now[2], now[3], now[4]
        extra = self._line_settings(line)["extras"][index]
        self.sel_bar.Visibility = Visibility.Visible
        self._sel_filling = True
        self.sel_title.Text = u"Bast\u00f3n:" if extra["face"] == bx.TOP else u"Balanc\u00edn:"
        self.sel_n.Text = u"{}".format(int(extra["n"]))
        if self.sel_d.Items.Count == 0:
            for key in sorted(rs.BAR_DIAMETERS_MM, key=lambda q: rs.BAR_DIAMETERS_MM[q]):
                self.sel_d.Items.Add(key)
        self.sel_d.SelectedItem = extra["d"]
        cotas = dict((c["end"], c) for c in bx.extra_cotas(extra, spans, start_face, end_face))
        both = len(cotas) == 2
        for end, box, lbl, unit in ((0, self.sel_c0, self.sel_lbl0, self.sel_m0),
                                    (1, self.sel_c1, self.sel_lbl1, self.sel_m1)):
            c = cotas.get(end)
            shown = Visibility.Visible if c else Visibility.Collapsed
            box.Visibility = lbl.Visibility = unit.Visibility = shown
            box.Text = u"{:.2f}".format(c["hi"] - c["lo"]) if c else u""
            lbl.Text = (u"Izq. " if end == 0 else u"Der. ") if both else u"Largo "
        h0, h1 = bx.extra_legs(extra)
        self.sel_h0.Text = u"{:.2f}".format(h0)
        self.sel_h1.Text = u"{:.2f}".format(h1)
        self._sel_filling = False

    def sel_apply_click(self, sender, args):
        index = self._selected_index()
        now = getattr(self, "_beam_now", None)
        if index is None or not now:
            return
        line, spans, start_face, end_face, bars = now[0], now[1], now[2], now[3], now[4]
        extra = self._edit_settings()["extras"][index]
        try:
            n = int(self.sel_n.Text.strip())
            if n < 1:
                raise ValueError()
            cotas = dict((c["end"], c) for c in bx.extra_cotas(extra, spans, start_face, end_face))
            values = {}
            for end, box in ((0, self.sel_c0), (1, self.sel_c1)):
                if end in cotas and box.Text.strip():
                    values[end] = float(box.Text.strip().replace(u",", u"."))
            legs = [max(0.0, float((box.Text.strip() or u"0").replace(u",", u".")))
                    for box in (self.sel_h0, self.sel_h1)]
        except ValueError:
            forms.alert(u"Revisa la cantidad y las distancias (m), por ejemplo 2 y 1.20", title=u"Acero Viga")
            return
        changed_n = n != int(extra["n"]) or (self.sel_d.SelectedItem or extra["d"]) != extra["d"]
        extra["n"], extra["d"] = n, self.sel_d.SelectedItem or extra["d"]
        for end, value in values.items():
            bx.set_cota(extra, cotas[end], value)
        extra["h0"], extra["h1"] = round(legs[0], 3), round(legs[1], 3)
        if changed_n:
            extra.pop("pos", None)  # a new count: laid out again
        self._select(index)

    def sel_key(self, sender, args):
        if args.Key == Key.Enter:
            self.sel_apply_click(sender, args)
            args.Handled = True

    def sel_lost_focus(self, sender, args):
        if not getattr(self, "_sel_filling", False) and self._selected_index() is not None:
            self.sel_apply_click(sender, args)

    def sel_d_changed(self, sender, args):
        if not getattr(self, "_sel_filling", False) and self._selected_index() is not None:
            self.sel_apply_click(sender, args)

    def sel_auto_pos_click(self, sender, args):
        index = self._selected_index()
        if index is None:
            return
        self._edit_settings()["extras"][index].pop("pos", None)
        self._select(index)

    def sel_delete_click(self, sender, args):
        index = self._selected_index()
        if index is None:
            return
        del self._edit_settings()["extras"][index]
        self._select(None)

    def sel_close_click(self, sender, args):
        self._select(None)

    def _extra_near(self, args, scale):
        """Index of the extra bar drawn under the mouse (6 px)."""
        data = self._elev_data
        if not data:
            return None
        s, _ = self._elev_s(args)
        y = self._elev_y(args)
        k = rv._beam_deepen(data)
        best = None
        for item in data.get("extras") or []:
            pts = [(p[2], p[1] * k) for p in item["path"]]
            for (ax, ay), (bx_, by) in zip(pts, pts[1:]):
                dx, dy = bx_ - ax, by - ay
                t = 0.0 if dx * dx + dy * dy < 1e-12 else max(0.0, min(1.0, ((s - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy)))
                d = ((ax + t * dx - s) ** 2 + (ay + t * dy - y) ** 2) ** 0.5 * scale
                if d < 6 and (best is None or d < best[0]):
                    best = (d, item["index"])
        return best[1] if best else None

    def elev_tool_click(self, sender, args):
        self._draft = None
        self._draft_view = None
        self._hook_stage = None
        self._hook_view = None

    def auto_cuts_click(self, sender, args):
        if getattr(self, "_view_line", None) is None:
            return
        self._edit_settings()["cuts"] = []  # empty: automatic
        self._changed()

    def _elev_y(self, args):
        """Drawing height under the mouse (the elevation's frame)."""
        fitted = self._elev_fitted()
        scale, ox, oy = self.elev_nav.resolve(fitted)
        return (oy - args.GetPosition(self.canvas_elev).Y) / scale

    def _face_at(self, args):
        """bx.TOP over the middle of the beam's drawn depth, else bx.BOTTOM."""
        data = self._elev_data
        k = rv._beam_deepen(data)
        ys = [q[1] * k for q in data["polygon"]]
        return bx.TOP if self._elev_y(args) >= (min(ys) + max(ys)) / 2.0 else bx.BOTTOM

    def _snap(self, s, scale):
        now = self._beam_now
        line, spans, start_face, end_face = now[0], now[1], now[2], now[3]
        return bx.snap_s(max(start_face, min(end_face, s)), bx.faces_of(spans, start_face, end_face), 10.0 / scale)

    def _end_near(self, args, scale):
        """(extra index, 0 / 1) of the dimension tick under the mouse."""
        data = self._elev_data
        if not data:
            return None
        s, _ = self._elev_s(args)
        y = self._elev_y(args)
        best = None
        for item in data.get("extras") or []:
            for c in item.get("cotas") or []:
                yc = rv.beam_cota_y(data, item["face"], c["row"])
                end, se = c["end"], (item["s0"] if c["end"] == 0 else item["s1"])
                d = max(abs(se - s), abs(yc - y)) * scale
                if d < 9 and (best is None or d < best[0]):
                    best = (d, (item["index"], end))
        return best[1] if best else None

    def _main_near(self, s, y, scale):
        """The face (bx.TOP / bx.BOTTOM) whose main bar passes under the
        click (within 8 px of its drawn path), else None."""
        data = getattr(self, "_elev_data", None)
        if not data or s is None:
            return None
        k = rv._beam_deepen(data)
        best = None
        for face, item in (data.get("main") or {}).items():
            for pts in item["paths"]:
                for p, q in zip(pts, pts[1:]):
                    ax, ay, bx_, by = p[2], p[1] * k, q[2], q[1] * k
                    dx, dy = bx_ - ax, by - ay
                    t = max(0.0, min(1.0, ((s - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy or 1e-12)))
                    d = math.hypot(ax + t * dx - s, ay + t * dy - y) * scale
                    if d < 8 and (best is None or d < best[0]):
                        best = (d, face)
        return best[1] if best else None

    def _nb_near(self, s, y, scale):
        """(neighbor tag, face) of a neighbor beam's main bar under the click
        (within 8 px of its drawn path), else None."""
        data = getattr(self, "_elev_data", None)
        if not data or s is None:
            return None
        k = rv._beam_deepen(data)
        best = None
        for m in data.get("mixed") or []:
            for item in m.get("bars") or []:
                for pts in item["paths"]:
                    for p, q in zip(pts, pts[1:]):
                        ax, ay, bx_, by = p[2], p[1] * k, q[2], q[1] * k
                        dx, dy = bx_ - ax, by - ay
                        t = max(0.0, min(1.0, ((s - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy or 1e-12)))
                        d = math.hypot(ax + t * dx - s, ay + t * dy - y) * scale
                        if d < 8 and (best is None or d < best[0]):
                            best = (d, (item["tag"], item["face"]))
        return best[1] if best else None

    def _edit_neighbor_bar(self, tag, wanted=None):
        """'nbl:<type id>:<element id>:<face>' clicked: how far that neighbor
        beam's bars get inside this one, from the support face - set as the
        neighbor's own hand edit of that end (kept until saved, as any beam's)."""
        _, tid, first, face = tag.split(u":")
        tid, first = int(tid), int(first)
        t = self.by_id.get(tid)
        other = next((l for l in (t.lines if t else []) if id_of(l.elements[0].Id) == first), None)
        data = getattr(self, "_elev_data", None) or {}
        item = next((b for m in data.get("mixed") or [] for b in m.get("bars") or []
                     if b["tag"] == u"{}:{}".format(tid, first) and b["face"] == face), None)
        if other is None or item is None:
            return
        if wanted is None:  # asked; given ('Quitar' / Supr: 0, out of this beam)
            value = forms.ask_for_string(
                default=u"{:.2f}".format(item.get("inside", 0.0)),
                prompt=u"Cuánto entran las barras {} de esta viga ({}) dentro de la viga, desde la cara del apoyo (m):".format(
                    u"superiores" if face == bx.TOP else u"inferiores", t.short_name),
                title=u"Acero longitudinal")
            if value is None:
                return
            try:
                wanted = max(0.0, float(value.strip().replace(u",", u".")))
            except ValueError:
                return
        key = self._line_key(other)
        if key not in self.state.line_edits:
            self.state.line_edits[key] = bx.read(bx.write(self._line_settings(other)))
        main = self.state.line_edits[key].setdefault("main", {}).setdefault(face, {})
        d_key = u"d{}".format(item["end"])
        main[d_key] = float(main.get(d_key) or 0.0) + (wanted - item.get("inside", 0.0))
        self._changed()

    def _edit_neighbor_leg(self, tag):
        """'nbh:<type id>:<element id>:<face>' clicked: the bend (leg) of that
        neighbor beam's bars at their end inside this one - its own 'Doblez'
        hand edit of that end (m, 0 straight, empty automatic)."""
        _, tid, first, face = tag.split(u":")
        tid, first = int(tid), int(first)
        t = self.by_id.get(tid)
        other = next((l for l in (t.lines if t else []) if id_of(l.elements[0].Id) == first), None)
        data = getattr(self, "_elev_data", None) or {}
        item = next((b for m in data.get("mixed") or [] for b in m.get("bars") or []
                     if b["tag"] == u"{}:{}".format(tid, first) and b["face"] == face), None)
        if other is None or item is None:
            return
        value = forms.ask_for_string(
            default=u"{:.2f}".format(item.get("leg", 0.0)),
            prompt=u"Doblez de las barras {} de esta viga ({}) en su extremo dentro de la viga (m, 0 = recta, vacío = automático):".format(
                u"superiores" if face == bx.TOP else u"inferiores", t.short_name),
            title=u"Acero longitudinal")
        if value is None:
            return
        value = value.strip().replace(u",", u".")
        key = self._line_key(other)
        if key not in self.state.line_edits:
            self.state.line_edits[key] = bx.read(bx.write(self._line_settings(other)))
        main = self.state.line_edits[key].setdefault("main", {}).setdefault(face, {})
        try:
            main[u"h{}".format(item["end"])] = float(value) if value else None
        except ValueError:
            return
        self._changed()

    def _edit_into(self, tag):
        """'into:<face>:<end>' clicked: how far this beam's main bars of that
        face get into the neighbor beam there, from the support face - kept
        as the face's own hand edit of that end (its 'Largo')."""
        _, face, end = tag.split(u":")
        end = int(end)
        item = next((i for i in (getattr(self, "_elev_data", None) or {}).get("main_into") or []
                     if i["face"] == face and i["end"] == end), None)
        if item is None:
            return
        value = forms.ask_for_string(
            default=u"{:.2f}".format(item["inside"]),
            prompt=u"Cuánto entran las barras {} dentro de la viga vecina, desde la cara del apoyo (m):".format(
                u"superiores" if face == bx.TOP else u"inferiores"),
            title=u"Acero longitudinal")
        if value is None:
            return
        try:
            wanted = max(0.0, float(value.strip().replace(u",", u".")))
        except ValueError:
            return
        main = self._edit_settings().setdefault("main", {}).setdefault(face, {})
        d_key = u"d{}".format(end)
        main[d_key] = float(main.get(d_key) or 0.0) + (wanted - item.get("raw", item["inside"]))
        self._changed()

    def _edit_main(self, tag):
        """'main:<face>:d0' / 'h1'... clicked: that end of the face's main bars
        - how much longer (+) or shorter (-) there, or its leg (0 straight)."""
        _, face, what = tag.split(u":")
        main = self._edit_settings().setdefault("main", {}).setdefault(face, {})
        if what.startswith(u"d"):
            prompt = u"Alargar (+) o acortar (-) las barras {} en este extremo (m):".format(
                u"superiores" if face == bx.TOP else u"inferiores")
            current = u"{:.2f}".format(float(main.get(what) or 0.0))
        else:
            prompt = u"Doblez de las barras {} en este extremo (m, 0 = recta, vacío = automático):".format(
                u"superiores" if face == bx.TOP else u"inferiores")
            current = u"" if main.get(what) is None else u"{:.2f}".format(float(main[what]))
        value = forms.ask_for_string(default=current, prompt=prompt, title=u"Acero longitudinal")
        if value is None:
            return
        value = value.strip().replace(u",", u".")
        try:
            main[what] = None if (not value and what.startswith(u"h")) else float(value or 0.0)
        except ValueError:
            return
        self._changed()

    def _elev_s(self, args):
        """s (m along the beam) under the mouse, and the frame's scale."""
        fitted = self._elev_fitted()
        if fitted is None:
            return None, None
        scale, ox, oy = self.elev_nav.resolve(fitted)
        return (args.GetPosition(self.canvas_elev).X - ox) / scale, scale

    def _cut_near(self, s, scale):
        now = getattr(self, "_beam_now", None)
        if not now:
            return None
        cuts = now[6]
        best = None
        for i, c in enumerate(cuts):
            d = abs(c - s) * scale
            if d < 16 and (best is None or d < best[0]):  # an easy grab
                best = (d, i)
        return best[1] if best else None

    def _edit_tag(self, tag):
        now = getattr(self, "_beam_now", None)
        if not now:
            return
        line, spans, start_face, end_face, bars, _, cuts = now
        if tag.startswith(u"span:"):
            index = int(tag[5:])
            st = self._edit_settings()
            f = self._get_form()
            value = forms.ask_for_string(
                default=bx.span_text(st, index, "edge") or f["edge_dist"], title=u"Acero Viga",
                prompt=u"Tramo {} ({:.2f} m): distribucion del estribo de borde, ej. 1@0.05, 10@0.10, rto@0.20 "
                       u"(vacio: la del tipo):".format(index + 1, spans[index][1] - spans[index][0]))
            if value is None:
                return
            if value.strip():
                try:
                    rs.parse_distribution(value)
                except rs.SpecError as e:
                    forms.alert(u"{}".format(e), title=u"Acero Viga")
                    return
            bx.set_span_text(st, index, "edge", value if value.strip() != f["edge_dist"].strip() else u"")
            self._changed()
        elif tag.startswith(u"cota:"):
            index, j = [int(t) for t in tag[5:].split(u":")]
            extra = self._edit_settings()["extras"][index]
            cota = bx.extra_cotas(extra, spans, start_face, end_face)[j]
            value = forms.ask_for_string(default=u"{:.2f}".format(cota["hi"] - cota["lo"]), title=u"Acero Viga",
                                         prompt=u"Distancia (m) desde la cara de la columna:")
            if value is None:
                return
            try:
                bx.set_cota(extra, cota, float(value.replace(u",", u".")))
            except ValueError:
                forms.alert(u"Escribe un numero, por ejemplo 1.20", title=u"Acero Viga")
                return
            self._changed()
        elif tag.startswith(u"len:"):
            index = int(tag[4:])
            st = self._edit_settings()
            extra = st["extras"][index]
            value = forms.ask_for_string(default=u"{:.2f}".format(extra["s1"] - extra["s0"]), title=u"Acero Viga",
                                         prompt=u"Longitud (m) del {} (queda fijo su extremo en el apoyo):".format(
                                             u"baston" if extra["kind"] == bx.BASTON else u"balancin"))
            if value is None:
                return
            try:
                bx.set_length(extra, float(value.replace(u",", u".")), bx.faces_of(spans, start_face, end_face))
            except ValueError:
                forms.alert(u"Escribe un numero, por ejemplo 1.20", title=u"Acero Viga")
                return
            self._changed()
        elif tag.startswith(u"hook:"):
            end = int(tag[5:])
            st = self._edit_settings()
            current = (st.get("hooks") or {}).get(str(end))
            value = forms.ask_for_string(
                default=u"" if current is None else u"{:.2f}".format(current), title=u"Acero Viga",
                prompt=u"Gancho de 90 de las barras al {} de la viga: largo de la pata (m); "
                       u"0 = recto; vacio = el de la tabla (12 db si no esta):".format(u"inicio" if end == 0 else u"final"))
            if value is None:
                return
            hooks = st.setdefault("hooks", {})
            if not value.strip():
                hooks.pop(str(end), None)
            else:
                try:
                    hooks[str(end)] = max(0.0, float(value.replace(u",", u".")))
                except ValueError:
                    forms.alert(u"Escribe un numero, por ejemplo 0.25", title=u"Acero Viga")
                    return
            self._changed()
        elif tag.startswith(u"extra:"):
            self._select(int(tag[6:]))
        elif tag.startswith(u"extra_old:"):
            index = int(tag[10:])
            st = self._edit_settings()
            extra = st["extras"][index]
            choice = forms.SelectFromList.show(
                [u"Cantidad y diametro", u"Largos", u"Borrar"], title=bx.extra_label(extra, spans, start_face, end_face),
                button_name=u"Elegir", multiselect=False)
            if choice == u"Borrar":
                del st["extras"][index]
            elif choice == u"Cantidad y diametro":
                value = forms.ask_for_string(default=u"{} {}".format(int(extra["n"]), extra["d"].rstrip(u'"')),
                                             title=u"Acero Viga", prompt=u"Cantidad y diametro, ej. 2 5/8:")
                if value is None:
                    return
                try:
                    extra["n"], extra["d"] = bx.parse_count_diameter(value, rs.BAR_DIAMETERS_MM)
                except ValueError:
                    forms.alert(u"Escribe, por ejemplo, 2 5/8", title=u"Acero Viga")
                    return
            elif choice == u"Largos":
                if extra["kind"] == bx.BASTON:
                    g = bx.gaps(spans, start_face, end_face)[bx.nearest_gap((extra["s0"] + extra["s1"]) / 2.0,
                                                                            spans, start_face, end_face)]
                    left, right = max(0.0, g[0] - extra["s0"]), max(0.0, extra["s1"] - g[1])
                    prompt = u"Largo a cada lado de la cara del apoyo (m), izquierda|derecha:"
                else:
                    k = bx.span_at((extra["s0"] + extra["s1"]) / 2.0, spans)
                    a, b = spans[k] if k is not None else (extra["s0"], extra["s1"])
                    left, right = extra["s0"] - a, b - extra["s1"]
                    prompt = u"Distancia libre a cada cara del tramo (m), izquierda|derecha:"
                value = forms.ask_for_string(default=u"{:.2f}|{:.2f}".format(left, right), title=u"Acero Viga",
                                             prompt=prompt)
                if value is None:
                    return
                try:
                    left, right = bx.parse_lengths(value)
                except ValueError:
                    forms.alert(u"Escribe, por ejemplo, 1.20|1.30", title=u"Acero Viga")
                    return
                if extra["kind"] == bx.BASTON:
                    extra["s0"], extra["s1"] = round(g[0] - left, 3), round(g[1] + right, 3)
                else:
                    extra["s0"], extra["s1"] = round(a + left, 3), round(b - right, 3)
            else:
                return
            self._changed()

    def elev_mouse_down(self, sender, args):
        fitted = self._elev_fitted()
        if fitted is None:
            return
        now = getattr(self, "_beam_now", None)
        if args.ChangedButton == MouseButton.Left and now:
            tag = getattr(args.OriginalSource, "Tag", None)
            if isinstance(tag, basestring) and tag.startswith(u"line:"):  # a neighbor beam: open it here
                self._open_line(tag)
                args.Handled = True
                return
            if isinstance(tag, basestring) and tag.startswith(u"main:"):  # a main bar's end: its length / leg
                self._edit_main(tag)
                args.Handled = True
                return
            if isinstance(tag, basestring) and tag.startswith(u"into:"):  # this beam's bar into the neighbor
                self._edit_into(tag)
                args.Handled = True
                return
            if isinstance(tag, basestring) and tag.startswith(u"nbx:"):  # a neighbor's bar: out of this beam
                self._edit_neighbor_bar(u"nbl:" + tag[4:], wanted=0.0)
                args.Handled = True
                return
            if isinstance(tag, basestring) and tag.startswith(u"nbh:"):  # a neighbor's bar: its bend inside
                self._edit_neighbor_leg(tag)
                args.Handled = True
                return
            if isinstance(tag, basestring) and tag.startswith(u"nbl:"):  # a neighbor's bar: how far inside
                self._edit_neighbor_bar(tag)
                args.Handled = True
                return
            if isinstance(tag, basestring) and (tag.startswith(u"span:") or tag.startswith(u"extra:")
                                                 or tag.startswith(u"len:") or tag.startswith(u"cota:")
                                                 or tag.startswith(u"hook:")):
                self._edit_tag(tag)
                args.Handled = True
                return
            s, scale = self._elev_s(args)
            line, spans, start_face, end_face, bars, _, cuts = now
            if getattr(self, "_hook_stage", None) is not None:
                pick = self._hook_pick(args, scale)
                if pick is not None and pick[1] >= 0.05:
                    end, leg = pick
                    self._edit_settings()["extras"][self._hook_stage]["h{}".format(end)] = leg
                    self._hook_view = None
                    self._changed()
                    return  # the other end may get one too
                self._end_hook_stage()
                return
            near = self._cut_near(s, scale)
            if near is not None and getattr(self, "_draft", None) is None:
                # a cut is moved first, whatever tool is on
                st = self._edit_settings()
                st["cuts"] = list(cuts)
                self._drag_cut = near
                self.canvas_elev.CaptureMouse()
                return
            if self.tool_draw.IsChecked:
                s = self._snap(s, scale)
                if getattr(self, "_draft", None) is None:
                    self._draft = (s, self._face_at(args))  # the first click: where it starts
                    return
                s_a, face = self._draft
                self._draft, self._draft_view = None, None
                if abs(s - s_a) >= 0.10:
                    n, key = self._main_face(face)
                    extras = self._edit_settings()["extras"]
                    extras.append(bx.drawn_extra(s_a, s, face, n, key))
                    self._hook_stage = len(extras) - 1  # next: its hooks, if drawn
                    self._changed()
                    return
                self._changed()
                return
            end = self._end_near(args, scale)
            if end is not None:
                self._drag_end = end
                self.canvas_elev.CaptureMouse()
                return
            near = self._cut_near(s, scale)
            if near is not None:  # a cut is moved, whatever tool is on (it fixes the cuts)
                st = self._edit_settings()
                st["cuts"] = list(cuts)
                self._drag_cut = near
                self.canvas_elev.CaptureMouse()
                return
            hit = self._extra_near(args, scale)
            if hit is not None:
                self._main_face = None
                self._select(hit)
                return
            y_click = self._elev_y(args)
            face = self._main_near(s, y_click, scale)
            nb = None if face else self._nb_near(s, y_click, scale)  # else a neighbor beam's bar
            if (face, nb) != (getattr(self, "_main_face", None), getattr(self, "_nb_sel", None)):
                self._main_face, self._nb_sel = face, nb  # a bar clicked (or off one): orange to edit
                self._main_bar = None  # the whole face (a bar of it: picked in a cut)
                self._views_sig = None
                self.redraw()
                return
        if args.ChangedButton == MouseButton.Right and getattr(self, "_hook_stage", None) is not None:
            self._end_hook_stage()
            return
        if args.ChangedButton in (MouseButton.Middle, MouseButton.Left):
            self.elev_nav.start_pan(fitted, args.GetPosition(self.canvas_elev))
            self.canvas_elev.CaptureMouse()

    def _build_3d(self):
        rv.build_beam(self.scene, getattr(self, "_scene_data", None), self.cbo_detail.SelectedItem or u"Medio")

    def elev_resized(self, sender, args):
        if hasattr(self, "_views_sig"):
            self._draw_elevation()

    def elev_wheel(self, sender, args):
        fitted = self._elev_fitted()
        if fitted is None:
            return
        self.elev_nav.wheel(fitted, args.GetPosition(self.canvas_elev), args.Delta)
        self._draw_elevation()
        args.Handled = True

    def _end_hook_stage(self):
        self._hook_stage = None
        self._hook_view = None
        self.tool_draw.IsChecked = False
        self._changed()

    def _hook_pick(self, args, scale):
        """(end 0 / 1, leg m) of the hook drawn from the nearer end of the bar
        just drawn: how far the mouse went down (top bar) or up (bottom one)
        from it, to 5 cm; None away from both ends (40 px)."""
        extra = self._edit_settings()["extras"][self._hook_stage]
        s, _ = self._elev_s(args)
        y = self._elev_y(args)
        end = 0 if abs(s - extra["s0"]) <= abs(s - extra["s1"]) else 1
        if abs(s - extra["s{}".format(end)]) * scale > 40:
            return None
        k = rv._beam_deepen(self._elev_data)
        pts = bx.placed_positions(extra, self.design["bars"], rs.BAR_DIAMETERS_MM)
        y_bar = pts[0][1] if pts else 0.0
        leg = (y_bar * k - y) / k if extra["face"] == bx.TOP else (y - y_bar * k) / k
        ys = [q[1] for q in self._view_line.section.polygon_m]
        leg = max(0.0, min(leg, max(ys) - min(ys) - 0.08))
        return end, round(leg / 0.05) * 0.05

    def elev_mouse_move(self, sender, args):
        if getattr(self, "_drag_cut", None) is None and getattr(self, "_beam_now", None):
            s_h, scale_h = self._elev_s(args)
            if scale_h:  # over a cut: the cursor says it can be dragged
                self.canvas_elev.Cursor = Cursors.SizeWE if self._cut_near(s_h, scale_h) is not None else None
        if getattr(self, "_hook_stage", None) is not None and self._elev_data:
            s, scale = self._elev_s(args)
            pick = self._hook_pick(args, scale)
            extra = self._edit_settings()["extras"][self._hook_stage]
            if pick is None:
                self._hook_view = None
            else:
                pts = bx.placed_positions(extra, self.design["bars"], rs.BAR_DIAMETERS_MM)
                self._hook_view = (extra["s{}".format(pick[0])], pts[0][1] if pts else 0.0, pick[1], extra["face"])
            self._elev_data["draft_hook"] = self._hook_view
            self._draw_elevation()
            return
        if getattr(self, "_draft", None) is not None and self._elev_data:
            s, scale = self._elev_s(args)
            s_a, face = self._draft
            self._draft_view = (s_a, self._snap(s, scale), face)
            self._elev_data["draft"] = self._draft_view
            self._draw_elevation()
            return
        drag_end = getattr(self, "_drag_end", None)
        if drag_end is not None:
            s, scale = self._elev_s(args)
            index, end = drag_end
            extra = self._edit_settings()["extras"][index]
            s = self._snap(s, scale)
            if end == 0 and s < extra["s1"] - 0.1:
                extra["s0"] = s
            elif end == 1 and s > extra["s0"] + 0.1:
                extra["s1"] = s
            self._changed()
            return
        drag = getattr(self, "_drag_cut", None)
        if drag is not None:
            s, _ = self._elev_s(args)
            if s is not None:
                st = self._edit_settings()
                line = self._view_line
                st["cuts"][drag] = round(max(0.0, min(line.length, s)), 3)
                self._changed()
            return
        if self.elev_nav.pan(args.GetPosition(self.canvas_elev)):
            self._draw_elevation()

    def elev_mouse_up(self, sender, args):
        if getattr(self, "_drag_end", None) is not None:
            self._drag_end = None
            self.canvas_elev.ReleaseMouseCapture()
            return
        if getattr(self, "_drag_cut", None) is not None:
            st = self._edit_settings()
            st["cuts"] = sorted(st["cuts"])
            self._drag_cut = None
            self._changed()
        self.elev_nav.end_pan()
        self.canvas_elev.ReleaseMouseCapture()

    def elev_fit_click(self, sender, args):
        self.elev_nav.reset()
        self._draw_elevation()

    def detail_changed(self, sender, args):
        if hasattr(self, "scene"):
            self._build_3d()

    def view3d_wheel(self, sender, args):
        self.scene.wheel(args.Delta)
        args.Handled = True

    def view3d_mouse_down(self, sender, args):
        if args.ChangedButton in (MouseButton.Left, MouseButton.Middle):
            self.scene.start_drag(args.GetPosition(self.border3d), args.ChangedButton == MouseButton.Left)
            self.border3d.CaptureMouse()

    def view3d_mouse_move(self, sender, args):
        self.scene.drag(args.GetPosition(self.border3d))

    def view3d_mouse_up(self, sender, args):
        self.scene.end_drag()
        self.border3d.ReleaseMouseCapture()

    def view3d_fit_click(self, sender, args):
        self.scene.fit()

    def view3d_resized(self, sender, args):
        if hasattr(self, "scene"):
            self.scene.fit()  # the fit depends on the viewport's shape

    def canvas_resized(self, sender, args):
        self.redraw()

    def canvas_move(self, sender, args):
        if self.plan_nav.pan(args.GetPosition(self.canvas)):
            self.redraw()
            return
        frame = self._frame()
        if frame is None:
            return
        cursor, on_bar = self.snap(frame, args.GetPosition(self.canvas))
        if self._tool() == "bar" and not on_bar:
            # preview where the bar will go (against a stirrup, lined up)
            cursor = self._place_bar(self.to_m(frame, args.GetPosition(self.canvas)))
        if cursor == self.cursor_m:
            return  # same snapped point: nothing to redraw
        self.cursor_m = cursor
        self.cursor_on_bar = on_bar
        self.redraw()

    def canvas_left(self, sender, args):
        frame = self._frame()
        if frame is None:
            return
        p = args.GetPosition(self.canvas)
        point, on_bar = self.snap(frame, p)
        tool = self._tool()
        if tool == "bar":
            if not on_bar:
                # against the stirrup, facing the bars already there
                point = self._place_bar(self.to_m(frame, p))
                self._push_undo()
                self.design["bars"].append((point[0], point[1], self.bar_key))
        elif tool in ("stirrup", "rect"):
            if tool == "stirrup" and self.draft:
                # Free corner within 1 cm of the previous corner's x or y:
                # line them up, so legs come out straight (moving the free
                # one when the other is a bar).
                px, py = self.draft[-1]
                prev_on_bar = any(abs(x - px) < 1e-6 and abs(y - py) < 1e-6
                                  for x, y, _ in self.design["bars"])
                if not on_bar:
                    point = (px if abs(point[0] - px) <= 0.01 else point[0],
                             py if abs(point[1] - py) <= 0.01 else point[1])
                elif not prev_on_bar:
                    self.draft[-1] = (point[0] if abs(point[0] - px) <= 0.01 else px,
                                      point[1] if abs(point[1] - py) <= 0.01 else py)
            corner = self._accept_vertex(point, on_bar)
            if corner is None:
                self._status(u"Fuera del recubrimiento: haz clic dentro de la linea punteada.")
            elif tool == "stirrup":
                self._status()
                if len(self.draft) >= 3 and corner == self.draft[0] and not self.rb_shape_open.IsChecked:
                    self._close_stirrup()
                elif not self.draft or corner != self.draft[-1]:
                    self.draft.append(corner)
            elif not self.draft:
                self._status()
                self.draft = [corner]
            else:
                rect = self._fitted_rect(self.draft[0], corner)
                if rect:
                    self.draft = rect
                    if not self._close_stirrup():
                        self.draft = []
        elif tool == "tie":
            self._place_tie(self.to_m(frame, p))
        elif tool == "edit":
            self._select_stirrup(self._stirrup_at(frame, p))
        elif tool == "erase":
            self._erase(frame, p)
        self.redraw()

    def canvas_right(self, sender, args):
        is_open = bool(self.rb_shape_open.IsChecked)
        if self._tool() == "stirrup" and len(self.draft) >= (2 if is_open else 3):
            self._close_stirrup(is_open)
        else:
            self.draft = []
        self.redraw()

    def _close_stirrup(self, is_open=False):
        """Add the drawn stirrup (it keeps the radius of the bars it wraps,
        so editing its measures later can't shift it) and show them.
        `is_open`: a U-shaped stirrup, its ends not joined."""
        pts = list(self.draft)
        kind = self.kind_for["stirrup"]
        wrap = rs.wrap_radius(pts, self.design["bars"])
        t = self.by_id.get(self.state.active)
        section = t.section if t else None
        cover = self._cover_m()
        if wrap and section is not None and section.is_rectangle and cover is not None:
            # Free corners placed before the first wrapped bar: bring them
            # inside the limit that bar's radius sets.
            bar_points = set((round(x, 6), round(y, 6)) for x, y, _ in self.design["bars"])
            key = self._family_key(kind)
            pts = [p if (round(p[0], 6), round(p[1], 6)) in bar_points
                   else self._clamp_free(p, wrap, key, section, cover) for p in pts]
        pts = rs.clean_polyline(pts, closed=not is_open)
        if len(pts) < (2 if is_open else 3):
            self._status(u"El estribo necesita mas puntos (quedaron en linea recta).")
            return False
        if self._outside_cover(kind, pts, wrap, is_open):
            self._status(u"Ese estribo se sale del recubrimiento; corrige sus esquinas (Deshacer quita la ultima).")
            return False
        shape = self.active_shape if self.active_shape and not self.active_shape["tie"] else None
        if shape and (shape["closed"] == is_open or shape["points"] != len(pts)):
            self._status(u"La forma {} es un estribo {} de {} {}; el dibujado tiene {}. "
                         u"Corrigelo o quita la forma elegida.".format(
                             shape["name"], u"cerrado" if shape["closed"] else u"abierto",
                             shape["points"], u"esquinas" if shape["closed"] else u"puntos",
                             len(pts)))
            return False
        self._status()
        self._push_undo()
        if not is_open:
            pts = rs.start_on_bar(pts, self.design["bars"])  # the hooks wrap a vertical bar
        rs.add_item(self.design, "stirrups", (kind, pts, wrap, is_open), shape["name"] if shape else None)
        self.draft = []
        self._select_stirrup(len(self.design["stirrups"]) - 1)
        return True

    def _stirrup_at(self, frame, p):
        """Index of the stirrup under the click, or None: the one whose
        outline passes nearest (within SNAP_PX), else the smallest closed
        stirrup the click falls inside."""
        m = self.to_m(frame, p)
        tol = SNAP_PX / frame[1]
        near = None
        inside = None
        for i, (kind, poly, wrap, is_open) in enumerate(self.design["stirrups"]):
            try:
                outline = rs.stirrup_outline(poly, self.design["bars"], self._family_key(kind), wrap, is_open)
            except rs.SpecError:
                outline = poly
            dist = rs.distance_to_polyline if is_open else rs.distance_to_polygon
            d = min(dist(m, outline), dist(m, poly))
            if d <= tol and (near is None or d < near[0]):
                near = (d, i)
            if not is_open and rs.point_in_polygon(m, outline):
                area = abs(rs.polygon_signed_area(outline))
                if inside is None or area < inside[0]:
                    inside = (area, i)
        if near:
            return near[1]
        return inside[1] if inside else None

    def _place_tie(self, click_m):
        """One click = one crosstie between the facing bars nearest to it."""
        try:
            a, b = rs.auto_tie(click_m, self.design["bars"])
        except rs.SpecError:
            forms.alert(u"No hay dos barras enfrentadas cerca de ese punto para la grapa.",
                        title="Acero")
            return
        for _, c, d in self.design["ties"]:
            if set([c, d]) == set([a, b]):
                return  # already there
        self._push_undo()
        shape = self.active_shape
        rs.add_item(self.design, "ties", (self.kind_for["tie"], a, b),
                    shape["name"] if shape and shape["tie"] else self._tie_style())

    def _tie_style(self):
        """The crosstie type chosen in "Grapa:": rs.TIE_C / rs.TIE_S (180-
        degree hooks) or None (135-degree stirrup hooks)."""
        if self.rb_tie_c.IsChecked:
            return rs.TIE_C
        if self.rb_tie_s.IsChecked:
            return rs.TIE_S
        return None

    def tie_leg_changed(self, sender, args):
        """The "Pata (cm)" box: the crosstie hook leg of the drawing (blank =
        the E.060 minimum)."""
        if not hasattr(self, "design"):
            return  # fired while the XAML loads
        text = (self.tb_tie_leg.Text or "").strip().replace(",", ".")
        try:
            leg = float(text) if text else None
        except ValueError:
            return
        if leg is not None and leg <= 0:
            leg = None
        if self.design.get("tie_leg") != leg:
            self.design["tie_leg"] = leg
            self.redraw()

    def tie_style_changed(self, sender, args):
        """Choosing a crosstie type takes the Grapa tool."""
        if not hasattr(self, "kind_for"):
            return  # fired while the XAML loads
        self.rb_tie.IsChecked = True
        self.redraw()

    def _erase(self, frame, p):
        """Remove the bar, tie or stirrup nearest to the click."""
        _, scale = frame[0], frame[1]
        m = self.to_m(frame, p)
        tol = SNAP_PX / scale
        candidates = []
        for i, (x, y, _) in enumerate(self.design["bars"]):
            candidates.append((((x - m[0]) ** 2 + (y - m[1]) ** 2) ** 0.5, "bars", i))
        for i, (_, a, b) in enumerate(self.design["ties"]):
            candidates.append((rs.distance_to_polygon(m, [a, b]), "ties", i))
        for i, (_, poly, _, is_open) in enumerate(self.design["stirrups"]):
            dist = rs.distance_to_polyline if is_open else rs.distance_to_polygon
            candidates.append((dist(m, poly), "stirrups", i))
        # each tab erases only its own steel (bars / stirrups and ties)
        candidates = [c for c in candidates if c[0] <= tol and (c[1] == "bars") == (self.tab_steel.SelectedIndex == 1)]
        if not candidates and self.tab_steel.SelectedIndex != 1:
            # Not on any bar, tie or edge: the stirrup the click is inside.
            index = self._stirrup_at(frame, p)
            if index is not None:
                candidates = [(0.0, "stirrups", index)]
        if candidates:
            _, kind, i = min(candidates)
            self._push_undo()
            if kind == "bars":
                del self.design[kind][i]
            else:
                rs.remove_item(self.design, kind, i)  # with its shape
            self._select_stirrup(None)

    def _place_bar(self, click_m):
        """Where a new bar goes (rs.place_bar): against the inner face of
        the nearest closed stirrup (in its corner near one), lined up with
        the bars already placed."""
        stirrups = []
        for kind, poly, wrap, is_open in self.design["stirrups"]:
            if is_open:
                continue
            key = self._family_key(kind)
            try:
                stirrups.append((rs.stirrup_outline(poly, self.design["bars"], key, wrap), key))
            except rs.SpecError:
                continue
        x, y = rs.place_bar(click_m, self.bar_key, self.design["bars"], stirrups)
        return (round(x, 4), round(y, 4))

    def undo_click(self, sender, args):
        if self.draft:
            self.draft = self.draft[:-1]
        elif self.undo_stack:
            self.design = rs.design_from_text(self.undo_stack.pop()) or rs.empty_design()
            self._select_stirrup(None)
        self.redraw()

    # -- rebar shape browser (right panel) ---------------------------------
    def _fill_shapes(self):
        text = (self.txt_shape_filter.Text or u"").strip().lower()
        only_stirrups = bool(self.chk_shape_stirrups.IsChecked)
        self._filling_shapes = True
        try:
            self.list_shapes.Items.Clear()
            for shape in self.rebar_shapes:
                if text and text not in shape["name"].lower():
                    continue
                if only_stirrups and not shape["stirrup"]:
                    continue
                panel = StackPanel()
                panel.Children.Add(shape_preview(shape["strokes"]))
                name = TextBlock()
                name.Text = shape["name"]
                name.HorizontalAlignment = HorizontalAlignment.Center
                panel.Children.Add(name)
                item = ListBoxItem()
                item.Content = panel
                item.Tag = shape
                self.list_shapes.Items.Add(item)
                if self.active_shape and self.active_shape["name"] == shape["name"]:
                    self.list_shapes.SelectedItem = item
        finally:
            self._filling_shapes = False

    def shape_filter_changed(self, sender, args):
        if hasattr(self, "rebar_shapes"):  # also fired while the XAML loads
            self._fill_shapes()

    def shape_load_click(self, sender, args):
        """Load rebar shape families (.rfa) without leaving Acero (Revit is
        blocked while this window is open)."""
        dialog = OpenFileDialog()
        dialog.Filter = u"Formas de armadura (*.rfa)|*.rfa"
        dialog.Multiselect = True
        if not dialog.ShowDialog(self):
            return
        loaded = 0
        with revit.Transaction("Acero - cargar formas de armadura"):
            for path in dialog.FileNames:
                try:
                    if doc.LoadFamily(path):
                        loaded += 1
                except Exception:
                    pass
        self.rebar_shapes = read_rebar_shapes()
        self._fill_shapes()
        self._status(u"Formas cargadas: {} de {}.".format(loaded, len(dialog.FileNames)))

    def shape_selected(self, sender, args):
        """A click on a shape chooses it for the next stirrups (a straight
        one, for the ties): nothing is drawn, the stirrup is drawn by hand."""
        if getattr(self, "_filling_shapes", True):
            return
        item = self.list_shapes.SelectedItem
        if item is None:
            return
        shape = item.Tag
        try:
            vertices, closed = rs.shape_outline(shape["lines"], shape["hooks"][0], shape["hooks"][1])
        except rs.SpecError:
            vertices, closed = [], False
        if len(vertices) < 2:
            self._set_active_shape(None)
            self._status(u"La forma {} no se puede usar para estribos ni grapas.".format(shape["name"]))
            return
        straight = not closed and len(vertices) == 2
        if straight and not (shape["stirrup"] or any(shape["hooks"])):
            # A straight bar without hooks (M_00) is the longitudinal bar,
            # not a tie: Revit already gives it to the vertical bars.
            self._set_active_shape(None)
            if self._tool() != "bar":
                self.rb_bar_2.IsChecked = True
            self._status(u"{} es la barra recta de las barras longitudinales (Revit ya la usa en ellas): "
                         u"elige el diametro en 'Barras' y haz clic en la planta.".format(shape["name"]),
                         error=False)
            return
        self._set_active_shape({
            "name": shape["name"],
            "closed": closed,
            "points": len(vertices),
            "tie": straight,
        })

    def shape_clear_click(self, sender, args):
        self._set_active_shape(None)

    def _set_active_shape(self, shape):
        """Make `shape` the one new stirrups/ties take and set the sketch
        up for it: a closed or open (U) stirrup, or the Grapa tool."""
        self.active_shape = shape
        self.draft = []
        if shape is None:
            self._filling_shapes = True
            self.list_shapes.SelectedItem = None
            self._filling_shapes = False
            self.txt_active_shape.Text = u"la que elija Revit"
            self.rb_shape_closed.IsEnabled = self.rb_shape_open.IsEnabled = True
            self._status()
        else:
            self.txt_active_shape.Text = shape["name"]
            if shape["tie"]:
                self.rb_tie.IsChecked = True
                how = u"haz clic entre dos barras enfrentadas para la grapa"
            else:
                (self.rb_shape_closed if shape["closed"] else self.rb_shape_open).IsChecked = True
                self.rb_shape_closed.IsEnabled = self.rb_shape_open.IsEnabled = False
                if not shape["closed"] or not self.rb_rect.IsChecked:
                    self.rb_stirrup.IsChecked = True
                how = (u"dibuja el estribo en la planta ({} {})".format(
                    shape["points"], u"esquinas" if shape["closed"] else u"puntos, clic derecho para terminar"))
            self._status(u"Forma {}: {}.".format(shape["name"], how), error=False)
        self.redraw()

    def clear_click(self, sender, args):
        self._push_undo()
        self.design = rs.empty_design()
        self.draft = []
        self._select_stirrup(None)
        self.redraw()

def pick_beams(state):
    uidoc = revit.uidoc
    try:
        refs = uidoc.Selection.PickObjects(
            ObjectType.Element, _BeamFilter(),
            "Selecciona las vigas y pulsa Finalizar",
        )
    except OperationCanceledException:
        return
    picked = [doc.GetElement(r.ElementId) for r in refs]
    state.picked_ids = [id_of(b.Id) for b in picked if b is not None]
    state.checked |= set(id_of(b.GetTypeId()) for b in picked if b is not None)
    if state.picked_ids:
        state.scope = "pick"
        # show the type (and, in the views, the beam) just picked
        state.active = id_of(doc.GetElement(DB.ElementId(state.picked_ids[0])).GetTypeId())


class SketchState(object):
    """The Boceto bar being drawn: its beam and its points (world, ft)."""

    def __init__(self):
        self.beam_id = None
        self.points = []
        self.key = u'5/8"'
        self.count = 2
        self.cover = 4.0
        self.message = None


class BocetoWindow(forms.WPFWindow):
    """A longitudinal bar drawn point by point in a view (each point a
    bend), its segment lengths typed in, created on the beam."""

    def __init__(self, xaml_file_path, sketch):
        forms.WPFWindow.__init__(self, xaml_file_path)
        self.sketch = sketch
        self.action = None
        keys = rs.bar_diameter_keys()
        self.cbo_d.ItemsSource = List[str](keys)
        self.cbo_d.SelectedItem = sketch.key if sketch.key in keys else keys[0]
        self.txt_n.Text = str(sketch.count)
        self.txt_cover.Text = u"{:g}".format(sketch.cover)
        self.boxes = []
        beam = doc.GetElement(DB.ElementId(sketch.beam_id)) if sketch.beam_id else None
        self.txt_host.Text = (u"Viga {} - {} puntos".format(sketch.beam_id, len(sketch.points)) if beam
                              else u"Sin barra dibujada.")
        self._segments()
        self._status(sketch.message or u"", error=False)
        sketch.message = None

    def _status(self, text, error=True):
        self.txt_status.Text = text
        self.txt_status.Foreground = brush(176, 32, 32) if error else brush(22, 50, 79)

    def _plane_points(self):
        """(u, z) in m of the points in the beam's vertical plane."""
        if not self.sketch.points:
            return []
        beam = doc.GetElement(DB.ElementId(self.sketch.beam_id))
        o, e, _ = rsl.beam_plane(beam)
        return [((p - o).DotProduct(e) * rc.FT, p.Z * rc.FT) for p in self.sketch.points]

    def _segments(self):
        self.pnl_segments.Children.Clear()
        self.boxes = []
        pts = self._plane_points()
        for k, length in enumerate(sp.segment_lengths(pts)):
            row = DockPanel()
            row.Margin = Thickness(0, 0, 0, 3)
            label = TextBlock()
            label.Text = u"Tramo {}:".format(k + 1)
            label.Width = 70
            label.VerticalAlignment = VerticalAlignment.Center
            box = TextBox()
            box.Text = u"{:.3f}".format(length)
            row.Children.Add(label)
            row.Children.Add(box)
            self.pnl_segments.Children.Add(row)
            self.boxes.append(box)
        self.txt_total.Text = (u"Longitud total: {:.2f} m".format(sum(sp.segment_lengths(pts))) if pts else u"")
        self.redraw()

    def _read(self):
        try:
            self.sketch.key = self.cbo_d.SelectedItem
            self.sketch.count = max(1, int(self.txt_n.Text))
            self.sketch.cover = float(self.txt_cover.Text.replace(u",", u"."))
            return True
        except ValueError:
            self._status(u"Cantidad y recubrimiento deben ser numeros.")
            return False

    def apply_click(self, sender, args):
        if not self.sketch.points:
            return
        try:
            lengths = [float(b.Text.replace(u",", u".")) for b in self.boxes]
        except ValueError:
            self._status(u"Las longitudes deben ser numeros (m).")
            return
        pts = [(p.X, p.Y, p.Z) for p in self.sketch.points]
        new = sp.resize_path(pts, [L / rc.FT for L in lengths])
        self.sketch.points = [DB.XYZ(*p) for p in new]
        self._segments()
        self._status(u"Longitudes aplicadas.", error=False)

    def draw_click(self, sender, args):
        if self._read():
            self.action = "draw"
            self.Close()

    def create_click(self, sender, args):
        if not self.sketch.points or len(self.sketch.points) < 2:
            self._status(u"Primero dibuja la barra en la vista.")
            return
        if self._read():
            self.apply_click(None, None)
            self.action = "create"
            self.Close()

    def close_click(self, sender, args):
        self.action = None
        self.Close()

    def resized(self, sender, args):
        self.redraw()

    def redraw(self):
        canvas = self.canvas
        canvas.Children.Clear()
        pts = self._plane_points()
        w, h = canvas.ActualWidth, canvas.ActualHeight
        if len(pts) < 2 or w < 10 or h < 10:
            return
        us, zs = [p[0] for p in pts], [p[1] for p in pts]
        span = max(max(us) - min(us), max(zs) - min(zs), 0.1)
        scale = min((w - 80) / span, (h - 80) / span)
        cx, cz = (max(us) + min(us)) / 2.0, (max(zs) + min(zs)) / 2.0

        def at(p):
            return Point(w / 2.0 + (p[0] - cx) * scale, h / 2.0 - (p[1] - cz) * scale)
        line = Polyline()
        line.Points = PointCollection([at(p) for p in pts])
        line.Stroke = brush(31, 63, 214)
        line.StrokeThickness = 3
        line.StrokeLineJoin = PenLineJoin.Round
        canvas.Children.Add(line)
        for k, p in enumerate(pts):
            q = at(p)
            dot = Ellipse()
            dot.Width = dot.Height = 10
            dot.Fill = brush(255, 179, 71)
            dot.Stroke = brush(22, 50, 79)
            Canvas.SetLeft(dot, q.X - 5)
            Canvas.SetTop(dot, q.Y - 5)
            canvas.Children.Add(dot)
        for k, (a, b) in enumerate(zip(pts, pts[1:])):
            qa, qb = at(a), at(b)
            text = TextBlock()
            text.Text = u"{}: {:.2f}".format(k + 1, math.hypot(b[0] - a[0], b[1] - a[1]))
            text.FontWeight = FontWeights.Bold
            text.Foreground = brush(22, 50, 79)
            Canvas.SetLeft(text, (qa.X + qb.X) / 2.0 + 6)
            Canvas.SetTop(text, (qa.Y + qb.Y) / 2.0 - 18)
            canvas.Children.Add(text)


def sketch_draw(sketch):
    """Pick the beam (unless one is drawn on), then the bar's points in
    the active view on the beam's vertical plane, Esc to finish."""
    uidoc = revit.uidoc
    try:
        ref = uidoc.Selection.PickObject(ObjectType.Element, _BeamFilter(), u"Selecciona la viga de la barra")
    except OperationCanceledException:
        return
    beam = doc.GetElement(ref.ElementId)
    o, e, across = rsl.beam_plane(beam)
    view = uidoc.ActiveView
    t = DB.Transaction(doc, u"Plano de boceto")
    t.Start()
    try:
        view.SketchPlane = DB.SketchPlane.Create(doc, DB.Plane.CreateByNormalAndOrigin(across, o))
        t.Commit()
    except Exception:
        t.RollBack()
        sketch.message = u"Dibuja en una elevacion, corte o vista 3D donde se vea la viga."
        return
    points = []
    while True:
        try:
            points.append(uidoc.Selection.PickPoint(
                u"Punto {} de la barra (cada punto un doblez; Esc para terminar)".format(len(points) + 1)))
        except OperationCanceledException:
            break
    if len(points) < 2:
        sketch.message = u"Se necesitan al menos dos puntos."
        return
    sketch.beam_id = id_of(beam.Id)
    sketch.points = rsl.onto_plane(points, beam)


def sketch_create(sketch):
    beam = doc.GetElement(DB.ElementId(sketch.beam_id))
    t = DB.Transaction(doc, u"Acero Viga - boceto")
    t.Start()
    try:
        rb.ensure_parameters(doc)
        bar_type = rc._bar_type(rc.BarTypes(doc), sketch.key, rb.BEAM_MARK)
        bars = rsl.sketch_bar(doc, beam, bar_type, sketch.points, sketch.count, sketch.cover / 100.0, sketch.key)
        t.Commit()
        total = sum(sp.segment_lengths([(p.X, p.Y, p.Z) for p in sketch.points])) * rc.FT
        sketch.message = u"Creadas {} barra(s) {} de {:.2f} m en la viga {}.".format(
            len(bars), sketch.key, total, sketch.beam_id)
        sketch.points = []
    except Exception as e:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        sketch.message = u"No se pudo crear la barra: {}".format(e)


def sketch_flow():
    sketch = SketchState()
    xaml_sketch = os.path.join(SCRIPT_DIR, "BocetoForm.xaml")
    while True:
        window = BocetoWindow(xaml_sketch, sketch)
        window.ShowDialog()
        if window.action == "draw":
            sketch_draw(sketch)
        elif window.action == "create":
            sketch_create(sketch)
        else:
            return


AUTO_TYPE = u"(automatico)"


# --- main -------------------------------------------------------------------
types = collect_types()
if not types:
    forms.alert("El modelo no tiene vigas (armazon estructural).", title="Acero")
    script.exit()

state = State()
preselected = selection_hosts.expand(doc, revit.uidoc.Selection.GetElementIds())  # a bar / brick: its element
preselected = [e for e in preselected if e is not None and _BeamFilter().AllowElement(e)]
if preselected:
    state.picked_ids = [id_of(b.Id) for b in preselected]
    state.checked = set(id_of(b.GetTypeId()) for b in preselected)
    state.scope = "pick"
    state.active = id_of(preselected[0].GetTypeId())

xaml = os.path.join(SCRIPT_DIR, "AceroForm.xaml")
while True:
    window = AceroWindow(xaml, types, state)
    window.ShowDialog()
    if window.action == "pick":
        pick_beams(state)
        continue
    if window.action == "sketch":
        sketch_flow()
        continue
    if window.action != "run":
        script.exit()
    break

by_id = dict((t.id, t) for t in types)
# The bars run whole beams (beam lines): picking one element of a beam
# builds the whole beam.
if state.scope == "pick":
    picked = set(state.picked_ids)
    picked_types = sorted(set(id_of(doc.GetElement(DB.ElementId(i)).GetTypeId()) for i in picked
                              if doc.GetElement(DB.ElementId(i)) is not None))
    lines = [line for type_id in picked_types for line in by_id[type_id].lines
             if any(id_of(e.Id) in picked for e in line.elements)]
    scope_label = u"vigas seleccionadas"
    all_lines = [line for type_id in picked_types for line in by_id[type_id].lines]
    if False:  # the window generates the picked beams only
        only_picked = u"Solo las vigas seleccionadas ({})".format(len(lines))
        all_of_type = u"Todas las vigas de {} ({})".format(
            u", ".join(by_id[t].name for t in picked_types), len(all_lines))
        choice = forms.CommandSwitchWindow.show(
            [only_picked, all_of_type], message=u"Vigas donde generar el acero:")
        if not choice:
            script.exit()
        if choice == all_of_type:
            lines = all_lines
            scope_label = u"todas las vigas de su tipo"
else:
    lines = [line for type_id in state.checked for line in by_id[type_id].lines]
    scope_label = u"tipos marcados, todo el modelo"

specs = {}
spec_errors = {}
for type_id in set(id_of(line.type_id) for line in lines):
    try:
        specs[type_id] = rc.ColumnSpec(by_id[type_id].config())
    except rs.SpecError as e:
        spec_errors[type_id] = u"{}".format(e)
with_spec = [line for line in lines if id_of(line.type_id) in specs]
if not with_spec:
    forms.alert(
        u"Ninguna de las {} vigas del alcance tiene su tipo configurado.".format(len(lines)),
        title="Acero",
    )
    script.exit()

splice = splice_for_generation(load_splice_settings())
legs = dict((k, cm / 100.0) for k, cm in load_hook_legs().items())
# a diameter without its hook leg in the table takes 12 db (bx.hook_leg)

elements = sum(len(line.elements) for line in with_spec)
dry_run = False  # straight to the model: no mode question

bar_types = rc.BarTypes(doc)
hooks = rc.StirrupHooks(doc)
rebar_shapes = rc.RebarShapes(doc)
warnings = [u"{}: sin generar - {}".format(by_id[t].name, e) for t, e in spec_errors.items()]
KINDS = (rc.LONGITUDINAL, rc.EDGE, rc.CONFINEMENT)
by_type = {}  # type name -> {"n": beams, "e": elements, kind: kg}
total_bars = 0

class _DropFailing(DB.IFailuresPreprocessor):
    """Revit's warnings noted and dismissed; a bar Revit refuses (no rebar
    shape fits it...) gets Revit's own fix - that bar alone left out - with
    no dialog stopping everything; the messages go to the summary."""

    def __init__(self):
        self.messages = []

    def PreprocessFailures(self, accessor):
        resolved = False
        for f in accessor.GetFailureMessages():
            self.messages.append(f.GetDescriptionText())
            if f.GetSeverity() == DB.FailureSeverity.Warning:
                accessor.DeleteWarning(f)
            elif f.HasResolutions():
                accessor.ResolveFailure(f)
                resolved = True
        return DB.FailureProcessingResult.ProceedWithCommit if resolved else DB.FailureProcessingResult.Continue


t = DB.Transaction(doc, "Acero Viga")
_failures = _DropFailing()
_opts = t.GetFailureHandlingOptions()
_opts.SetFailuresPreprocessor(_failures)
_opts.SetForcedModalHandling(False)
t.SetFailureHandlingOptions(_opts)
t.Start()
try:
    rb.ensure_parameters(doc)
    done = []
    # gable frames: two sloped beams meeting at a ridge get their bars bent
    # across it (rsl), their stirrups as any beam
    ridge = rsl.ridge_pairs(with_spec)
    in_ridge = set(id(l) for a, b, _, _ in ridge for l in (a, b))
    # a deep beam and a flat one on one axis: their top bars run on, lapped
    # past the support between them (rb.mixed_continuations)
    mixed = rb.mixed_continuations(with_spec)

    def settings_of(line):
        key = tuple(id_of(e.Id) for e in line.elements)
        return state.line_edits.get(key) or rb.read_line_settings(line)
    with forms.ProgressBar(title="Acero: {value} de {max_value} vigas") as pb:
        for i, line in enumerate(with_spec):
            bt = by_id[id_of(line.type_id)]
            sub = DB.SubTransaction(doc)
            sub.Start()
            try:
                line_settings = settings_of(line)
                created, found = rb.generate_line(
                    doc, line, specs[bt.id], rb.anchor_of(bt.config()), bar_types, hooks,
                    rebar_shapes, splice, legs, line_settings=line_settings,
                    longitudinal=id(line) not in in_ridge, continue_top=mixed.get(id(line)))
                sub.Commit()
                done.append((line, bt.name, created))
                warnings += [u"{} (viga {}): {}".format(bt.name, id_of(line.elements[0].Id), w) for w in found]
            except Exception as e:
                sub.RollBack()
                warnings.append(u"Viga {} ({}): {}".format(id_of(line.elements[0].Id), bt.name, e))
            pb.update_progress(i + 1, len(with_spec))
    by_line = dict((id(line), created) for line, _, created in done)
    for pair in ridge:
        a, b = pair[0], pair[1]
        if id(a) not in by_line or id(b) not in by_line:
            continue
        ta, tb = by_id[id_of(a.type_id)], by_id[id_of(b.type_id)]
        sub = DB.SubTransaction(doc)
        sub.Start()
        try:
            created, found = rsl.generate_pair(
                doc, pair, specs[ta.id], specs[tb.id], rb.anchor_of(ta.config()), rb.anchor_of(tb.config()),
                bar_types, splice, legs, settings_of(a), settings_of(b))
            sub.Commit()
            for element_id, items in created.items():
                for line in (a, b):
                    if element_id in by_line[id(line)]:
                        by_line[id(line)][element_id].extend(items)
            warnings += [u"{} (cumbrera {}): {}".format(ta.name, id_of(a.elements[0].Id), w) for w in found]
        except Exception as e:
            sub.RollBack()
            warnings.append(u"Viga {} (cumbrera): {}".format(id_of(a.elements[0].Id), e))

    doc.Regenerate()  # bar lengths are only known after a regeneration
    for line, type_name, created in done:
        agg = by_type.setdefault(type_name, dict([("n", 0), ("e", 0)] + [(k, 0.0) for k in KINDS]))
        agg["n"] += 1
        for element in line.elements:
            kg, bars = rb.record_weight(element, created[id_of(element.Id)])
            agg["e"] += 1
            for kind in KINDS:
                agg[kind] += kg[kind]
            total_bars += bars

    if dry_run:
        t.RollBack()
    else:
        t.Commit()
except Exception:
    if t.HasStarted() and not t.HasEnded():
        t.RollBack()
    raise

# No report window: only what failed, in one message.
failed = [w for w in warnings if u"sin generar" in w or w.startswith(u"Viga ")]
refused = sorted(set(m for m in _failures.messages if m))  # what Revit refused (that bar left out)
if refused:
    failed.append(u"Revit no aceptó algunas barras (se omitieron solo esas): " + u"; ".join(refused[:3]))
if failed:
    forms.alert(u"No se pudo generar:\n- " + u"\n- ".join(failed[:10]), title="Acero")
