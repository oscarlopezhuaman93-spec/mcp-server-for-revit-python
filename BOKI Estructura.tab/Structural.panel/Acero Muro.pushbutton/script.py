# -*- coding: utf-8 -*-
"""Acero Muro: la ventana de Acero Columna para muros. Genera el acero de
refuerzo (barras 3D) y su metrado en kg. La configuracion (longitudinal, estribos, nucleo y
el dibujo de la seccion con estribos, grapas y barras a mano) se guarda
en cada tipo de columna."""

__title__ = "Acero\nMuro"
__author__ = "Revit MCP"

import io
import json
import os
import re
import sys

SCRIPT_DIR = os.path.dirname(__file__)
EXT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", ".."))
REVIT_MCP_DIR = os.path.join(EXT_ROOT, "revit_mcp")
if REVIT_MCP_DIR not in sys.path:
    sys.path.append(REVIT_MCP_DIR)

import formwork_spatial as fw_spatial
import formwork_params as fw_params
import rebar_spec as rs
import rebar_columns as rc
import rebar_views as rv
import rebar_foundation as rf
import utils as fw_utils

# pyRevit keeps one interpreter across clicks: reload so edits on disk
# are picked up (same as the Encofrado button).
reload(fw_utils)
reload(fw_spatial)
reload(fw_params)
reload(rs)
reload(rc)
reload(rv)
reload(rf)

from pyrevit import revit, DB, forms, script
from System.Windows import GridLength, TextWrapping, Visibility
from System.Windows.Controls import Dock, DockPanel, Expander, GroupBox
from Autodesk.Revit.Exceptions import OperationCanceledException
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType
from System.Collections.Generic import List
from System.Windows import (FontWeights, HorizontalAlignment, Point, Size, Thickness,
                            VerticalAlignment, Visibility)
from Microsoft.Win32 import OpenFileDialog, SaveFileDialog
from Autodesk.Revit.DB.Structure import RebarShape, RebarStyle
from System.Windows.Input import Key, Keyboard, MouseButton
from System.Windows.Controls import (Button, Canvas, CheckBox, ListBoxItem, Orientation, StackPanel,
                                     TextBlock, TextBox)
from System.Windows.Media import Color, DoubleCollection, PointCollection, SolidColorBrush
from System.Windows.Shapes import Ellipse, Line, Polygon, Polyline

output = script.get_output()
doc = revit.doc

COLUMNS_BIC = DB.BuiltInCategory.OST_Walls  # "Acero Muro": the window of Acero Columna, for walls
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


def group_of(element):
    """Acero Muro: each wall is its own item (its own settings and
    drawing, rc.WALL_PARAM) - not its type, whose walls differ in size."""
    return id_of(element.Id)


class ColumnType(object):
    """One wall in the model (in Acero Columna: a column type)."""

    def __init__(self, type_id, columns):
        self.id = type_id
        self.element = doc.GetElement(DB.ElementId(type_id))
        level = doc.GetElement(self.element.LevelId)
        self.name = u"{} - id {}{}".format(
            rc.element_name(doc.GetElement(self.element.GetTypeId())), type_id,
            u" - {}".format(level.Name) if level is not None else u"")
        self.columns = columns
        self._section = None
        self.section_error = None

    @property
    def section(self):
        if self._section is None and self.section_error is None:
            try:
                self._section = rc.Section(self.columns[0])
            except Exception as e:
                self.section_error = u"{}".format(e)
        return self._section

    def config(self):
        return rc.read_type_config(self.element)

    def configured(self):
        cfg = self.config()
        return bool(cfg["EA_Estribo_Borde_Distribucion"] and cfg["EA_Seccion_Armado"])


class State(object):
    """What survives closing the window to pick columns in the model."""

    def __init__(self):
        self.checked = set()
        self.picked_ids = []
        self.active = None
        self.drafts = {}  # type id -> design being drawn (unsaved)
        self.form = None  # last form field values
        self.scope = "model"  # "pick" when columns were picked in the model


class _ColumnFilter(ISelectionFilter):
    def AllowElement(self, element):
        category = element.Category
        return category is not None and id_of(category.Id) == int(COLUMNS_BIC)

    def AllowReference(self, reference, point):
        return False


def collect_types():
    by_type = {}
    for c in (
        DB.FilteredElementCollector(doc).OfCategory(COLUMNS_BIC).WhereElementIsNotElementType()
    ):
        by_type.setdefault(group_of(c), []).append(c)
    return sorted(
        [ColumnType(t, cols) for t, cols in by_type.items()], key=lambda ct: ct.name
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
        self.cbo_izaje.ItemsSource = List[str](STIRRUP_DIAMETERS)
        self.cbo_dir_bot.ItemsSource = List[str](list(rs.LEG_DIRS))
        self.cbo_dir_top.ItemsSource = List[str](list(rs.LEG_DIRS))
        # "Ø acero" of the sketch: which stirrup family a new stirrup/tie
        # uses. A new longitudinal bar takes the diameter of the "Barras"
        # option chosen.
        self.kind_for = {"stirrup": rs.KIND_EDGE, "tie": rs.KIND_CONFINEMENT}
        # "Barras": four options, each with the diameter chosen in its own
        # list (remembered in the user's pyRevit settings).
        self.bar_tools = [(self.rb_bar_1, self.cbo_bar_1), (self.rb_bar_2, self.cbo_bar_2),
                          (self.rb_bar_3, self.cbo_bar_3), (self.rb_bar_4, self.cbo_bar_4)]
        self._filling_bars = True
        for (rb, cbo), key in zip(self.bar_tools, load_bar_slots()):
            cbo.ItemsSource = List[str](rs.bar_diameter_keys())
            cbo.SelectedItem = key
        self._filling_bars = False
        self.bar_key = self.cbo_bar_2.SelectedItem
        self._fill_splice()
        self._updating_steel = False
        self.selected = None  # index of the stirrup whose measures are shown
        # the wall's cut (CORTE DEL MURO): vertical bars sketched on it
        self.cut_items = []
        self.cut_draft = []
        self.cut_cursor = None
        self.cut_nav = rv.Nav2D()
        self._cut_frame = None
        self._cut_fitted = None
        self._cut_cache = {}
        self.cbo_cut_d.ItemsSource = List[str](rs.bar_diameter_keys())
        self.cbo_cut_d.SelectedItem = u'1/2"'
        self.cbo_cut_m.ItemsSource = List[str](list(rf.DIST_MODES))
        self.cbo_hz_d.ItemsSource = List[str](rs.bar_diameter_keys())
        self.cbo_cut_m.SelectedItem = rf.SPACING
        # Plan / elevation / 3D views: zoom-pan state and what they last showed.
        self.plan_nav = rv.Nav2D()
        self.elev_nav = rv.Nav2D()
        self.scene = rv.Scene3D(self.view3d)
        self.cbo_detail.ItemsSource = List[str](list(rv.DETAIL_LEVELS))
        self.cbo_detail.SelectedItem = u"Medio"
        self._views_sig = None
        self._elev_data = None
        self._clear_cache = {}
        self._neighbor_cache = {}  # column id -> elements touching it
        self._section_cache = {}  # column id -> rc.Section
        self._view_columns = []  # the column(s), stacked, the 3D view and elevation show
        self._filling_columns = False

        self._refresh_steel()
        # The shape clicked in the browser: the stirrups (or, a straight one,
        # the ties) drawn next are created with it. None: Revit picks.
        self.active_shape = None
        self._filling_shapes = False
        self.rebar_shapes = read_rebar_shapes()
        self._fill_shapes()
        picked_types = set(group_of(c) for c in
                           (doc.GetElement(DB.ElementId(i)) for i in state.picked_ids) if c is not None)
        for t in types:
            if picked_types and t.id not in picked_types:
                continue  # with columns picked, only their types are listed
            self.list_types.Items.Add(self._type_item(t))
        if picked_types:
            self._compact_list()
        self._refresh_picked()
        if state.form:
            self._set_form(state.form)
        active = state.active if state.active in self.by_id else (types[0].id if types else None)
        if active is not None:
            self._select_type(active)

    # -- type list ---------------------------------------------------------
    def _compact_list(self):
        """With columns picked, the type box holds just their types: it sits
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

    def _type_item(self, t):
        panel = StackPanel()
        panel.Orientation = Orientation.Horizontal
        box = CheckBox()
        box.IsChecked = t.id in self.state.checked
        box.VerticalAlignment = VerticalAlignment.Center
        box.Margin = Thickness(0, 0, 6, 0)
        self.checkboxes[t.id] = box
        label = TextBlock()
        label.Text = u"{}{}  ({})".format(u"✓ " if t.configured() else u"", t.name, len(t.columns))
        label.TextWrapping = TextWrapping.Wrap
        label.MaxWidth = 300  # a long type name wraps instead of hiding
        panel.Children.Add(box)
        panel.Children.Add(label)
        item = ListBoxItem()
        item.Content = panel
        item.Tag = t.id
        return item

    def _refresh_type_labels(self):
        for item in self.list_types.Items:
            t = self.by_id[item.Tag]
            item.Content.Children[1].Text = u"{}{}  ({})".format(
                u"✓ " if t.configured() else u"", t.name, len(t.columns))

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
            "izaje": cfg["EA_Izaje"],
            "ends": cfg["EA_Barra_Extremos"],
            "conf_type": cfg["EA_Estribo_Conf_Tipo"],
            "edge_type": cfg["EA_Estribo_Borde_Tipo"],
            "conf": cfg["EA_Estribo_Conf_Diametro"] or u'3/8"',
            "conf_dist": cfg["EA_Estribo_Conf_Distribucion"],
            "edge": cfg["EA_Estribo_Borde_Diametro"] or u'3/8"',
            "edge_dist": cfg["EA_Estribo_Borde_Distribucion"],
            "cover": cfg["EA_Recubrimiento_cm"] or str(rc.default_cover_cm(t.name)),
            "nucleo": cfg["EA_Nucleo_cm"],
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
        try:
            self.cut_items = json.loads(cfg.get("EA_Muro_Corte") or u"[]")
        except ValueError:
            self.cut_items = []
        self.cut_draft = []
        hz = rs.read_json_setting(cfg.get("EA_Muro_Horizontal"))
        self.chk_hz.IsChecked = bool(hz.get("on"))
        self.cbo_hz_d.SelectedItem = hz.get("d") or u'3/8"'
        self.txt_hz_dist.Text = hz.get("dist") or u"1@0.05, rto@0.20"
        self.txt_hz_al.Text = u"{:g}".format(float(hz.get("al", 0.30)))
        self.txt_hz_ar.Text = u"{:g}".format(float(hz.get("ar", 0.30)))
        self.txt_hz_hook.Text = u"{:g}".format(float(hz.get("hook", 0.10)))
        self._fill_view_columns(t)
        self.plan_nav.reset()
        self.elev_nav.reset()
        self.scene._extent = None  # refit the 3D camera to the new column
        self._update_measures()
        section = t.section
        if section is None:
            self.txt_section.Text = u"Seccion {}: {}".format(t.name, t.section_error)
        else:
            self.txt_section.Text = u"Seccion {}  ({:.0f} x {:.0f} cm{})".format(
                t.name, section.b * rc.FT * 100, section.h * rc.FT * 100,
                u"" if section.is_rectangle else u", irregular")
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
        izaje = rs.read_json_setting(f.get("izaje"))
        self.cbo_izaje.SelectedItem = izaje.get("d") if izaje.get("d") in STIRRUP_DIAMETERS else u'3/8"'
        names = self._type_names()
        if self.cbo_izaje_type.ItemsSource is None:
            self.cbo_izaje_type.ItemsSource = List[str](names)
        self._filling_types = True
        self.cbo_izaje_type.SelectedItem = izaje.get("type") if izaje.get("type") in names else AUTO_TYPE
        self._filling_types = False
        self.txt_izaje_dist.Text = izaje.get("dist") or u""
        self.chk_izaje.IsChecked = bool(izaje) and izaje.get("on", True)
        self.txt_izaje_h.Text = u"{:g}".format(float(izaje["h"])) if izaje.get("h") else u""
        ends = rs.read_json_setting(f.get("ends"))
        self.legs_bot = dict(ends.get("bot") or {})
        self.legs_top = dict(ends.get("top") or {})
        self.txt_anchor.Text = u"{}".format(ends.get("anchor") or u"")
        self.cbo_dir_bot.SelectedItem = ends.get("dir_bot") if ends.get("dir_bot") in rs.LEG_DIRS else rs.LEG_OUT
        self.cbo_dir_top.SelectedItem = ends.get("dir_top") if ends.get("dir_top") in rs.LEG_DIRS else rs.LEG_IN
        self._fill_slot_types()
        self.txt_conf_dist.Text = f.get("conf_dist") or u""
        self.txt_edge_dist.Text = f.get("edge_dist") or u""
        self.txt_cover.Text = f.get("cover") or u""
        self.chk_nucleo.IsChecked = bool(f.get("nucleo"))
        self.txt_nucleo.Text = f.get("nucleo") or u"10"

    def _get_form(self):
        return {
            "conf": self.cbo_conf.SelectedItem or u'3/8"',
            "bars_type": json.dumps(getattr(self, "bars_types", {}), ensure_ascii=False) if getattr(self, "bars_types", None) else u"",
            "izaje": self._izaje_text(),
            "ends": self._ends_text(),
            "conf_type": self._chosen_type(self.cbo_conf_type),
            "edge_type": self._chosen_type(self.cbo_edge_type),
            "conf_dist": (self.txt_conf_dist.Text or u"").strip(),
            "edge": self.cbo_edge.SelectedItem or u'3/8"',
            "edge_dist": (self.txt_edge_dist.Text or u"").strip(),
            "cover": (self.txt_cover.Text or u"").strip(),
            "nucleo": (self.txt_nucleo.Text or u"").strip() if self.chk_nucleo.IsChecked else u"",
        }

    def _config_from_form(self, with_drawing):
        f = self._get_form()
        config = {
            "EA_Estribo_Conf_Diametro": f["conf"],
            "EA_Estribo_Conf_Distribucion": f["conf_dist"],
            "EA_Estribo_Conf_Tipo": f["conf_type"],
            "EA_Barras_Tipo": f["bars_type"],
            "EA_Izaje": f["izaje"],
            "EA_Barra_Extremos": f["ends"],
            "EA_Estribo_Borde_Tipo": f["edge_type"],
            "EA_Estribo_Borde_Diametro": f["edge"],
            "EA_Estribo_Borde_Distribucion": f["edge_dist"],
            "EA_Recubrimiento_cm": f["cover"],
            "EA_Nucleo_cm": f["nucleo"],
        }
        if with_drawing:
            config["EA_Seccion_Armado"] = rs.design_to_text(self.design)
            config["EA_Muro_Corte"] = json.dumps(self.cut_items) if self.cut_items else u""
            config["EA_Muro_Horizontal"] = json.dumps(self._horizontal_form())
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
        with revit.Transaction("Acero - configuracion de columnas"):
            rc.ensure_parameters(doc)
            for type_id in targets:
                rc.write_type_config(
                    self.by_id[type_id].element,
                    self._config_from_form(type_id in same_section),
                )
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
                u"(diametros, distribucion, recubrimiento y nucleo)?".format(name, b, h, tb, th),
                title="Acero", yes=True, no=True):
                return
            design = None
        self._push_undo()
        self._set_form(form)
        if design is not None:
            self.design = design
        self.dirty = True
        self.draft = []
        self._select(None)
        self._status(u"Cargada la configuracion de {}{}. Pulsa 'Guardar configuracion en los tipos "
                     u"marcados' para guardarla en el tipo.".format(
                         name, u"" if design is not None else u" (solo estribos)"), error=False)
        self.redraw()

    # -- actions -----------------------------------------------------------
    def pick_click(self, sender, args):
        self._leave("pick")

    def run_click(self, sender, args):
        # No "Guardar configuracion" button: generating saves the form and
        # the drawing into the types first (the generation reads them there).
        if not self.save():
            return
        # The columns picked in the model, or else every column of the
        # checked types.
        if not self.state.picked_ids and not self.checked_ids():
            forms.alert(u"Selecciona columnas en el modelo o marca los tipos a generar.", title="Acero")
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
            u"{} muro(s) seleccionado(s) en el modelo.".format(n) if n
            else u"No hay muros seleccionados en el modelo."
        )

    def window_key(self, sender, args):
        """Esc cancels the stroke being drawn instead of closing the window;
        Supr deletes the selected stirrup (unless a box is being typed in)."""
        if args.Key == Key.Escape and getattr(self, "draft", None):
            self.draft = []
            self._status()
            self.redraw()
            args.Handled = True
        elif (args.Key == Key.Delete and getattr(self, "selected", None) is not None
              and not isinstance(Keyboard.FocusedElement, TextBox)):
            self.delete_stirrup_click(sender, args)
            args.Handled = True

    def delete_stirrup_click(self, sender, args):
        """Delete the selected stirrup, to draw it again."""
        if self.selected is None or self.selected >= len(self.design["stirrups"]):
            return
        self._push_undo()
        rs.remove_item(self.design, "stirrups", self.selected)
        self.draft = []
        self._select(None)
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
        if any(rb.IsChecked for rb, _ in self.bar_tools):
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
        for rb, cbo in self.bar_tools:
            if rb.IsChecked and cbo.SelectedItem:
                self.bar_key = cbo.SelectedItem
        if self._tool() not in ("edit",):
            self.selected = None if self._tool() == "erase" else self.selected
        self._refresh_steel()
        self.redraw()

    # -- lap splices of the longitudinal bars ---------------------------------
    def _fill_splice(self):
        """"3. Empalme": the switch, the maximum bar length and, per
        diameter, whether it is spliced and its lap (cm), as last saved."""
        self._filling_splice = True
        try:
            settings = load_splice_settings()
            self.chk_splice.IsChecked = settings["on"]
            self.txt_splice_max.Text = u"{:g}".format(settings["max"])
            self.lap_boxes, self.lap_checks = lap_rows(self.panel_laps, settings, self.splice_changed)
        finally:
            self._filling_splice = False

    def _splice_from_form(self):
        """The splice settings as typed ({"on", "max", "laps", "active"})."""
        laps, active = lap_form(self.lap_boxes, self.lap_checks)
        settings = {"on": bool(self.chk_splice.IsChecked), "max": rs.MAX_BAR_LENGTH, "laps": laps, "active": active}
        try:
            settings["max"] = float((self.txt_splice_max.Text or u"").replace(u",", u".")) or rs.MAX_BAR_LENGTH
        except ValueError:
            pass
        return settings

    def splice_changed(self, sender, args):
        if getattr(self, "_filling_splice", True):
            return
        save_splice_settings(self._splice_from_form())
        self.redraw()  # the stacked views show the laps

    def bar_slot_changed(self, sender, args):
        """A diameter chosen in one of the four "Barras" options: it is
        remembered, and that option becomes the bar tool."""
        if getattr(self, "_filling_bars", True):
            return
        save_bar_slots([cbo.SelectedItem for _, cbo in self.bar_tools])
        self._fill_slot_types()
        for rb, cbo in self.bar_tools:
            if cbo is not sender or not cbo.SelectedItem:
                continue
            if rb.IsChecked:
                self.bar_key = cbo.SelectedItem
                self._refresh_steel()
            else:
                rb.IsChecked = True  # tool_changed takes its diameter

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
        diameter = {id(self.cbo_conf_type): self.cbo_conf, id(self.cbo_edge_type): self.cbo_edge,
                    id(self.cbo_izaje_type): self.cbo_izaje}[id(sender)]
        if key and key in STIRRUP_DIAMETERS and diameter.SelectedItem != key:
            diameter.SelectedItem = key
        self.config_changed(sender, args)

    @staticmethod
    def _number(text):
        try:
            return float((text or u"").strip().replace(u",", u"."))
        except ValueError:
            return None

    def _izaje_text(self):
        """EA_Izaje of the form: JSON, or blank when there is no izaje."""
        if not hasattr(self, "txt_izaje_dist"):
            return u""
        dist, h = (self.txt_izaje_dist.Text or u"").strip(), self._number(self.txt_izaje_h.Text)
        if not dist or not h:
            return u""
        return json.dumps({"on": bool(self.chk_izaje.IsChecked),
                           "d": self.cbo_izaje.SelectedItem or u'3/8"', "dist": dist, "h": h,
                           "type": self._chosen_type(self.cbo_izaje_type)}, ensure_ascii=False)

    def _ends_text(self):
        """EA_Barra_Extremos of the form: anchorage, legs per diameter (cm)
        and their directions, as JSON; blank when nothing is set."""
        if not hasattr(self, "legs_bot"):
            return u""
        anchor = self._number(self.txt_anchor.Text)
        data = {"anchor": anchor if anchor is not None else u"",
                "bot": dict((k, v) for k, v in self.legs_bot.items() if v),
                "top": dict((k, v) for k, v in self.legs_top.items() if v),
                "dir_bot": self.cbo_dir_bot.SelectedItem or rs.LEG_OUT,
                "dir_top": self.cbo_dir_top.SelectedItem or rs.LEG_IN}
        if anchor is None and not data["bot"] and not data["top"]:
            return u""
        return json.dumps(data, ensure_ascii=False)

    def _leg_boxes(self):
        return [(self.txt_leg_bot_1, self.txt_leg_top_1), (self.txt_leg_bot_2, self.txt_leg_top_2),
                (self.txt_leg_bot_3, self.txt_leg_top_3), (self.txt_leg_bot_4, self.txt_leg_top_4)]

    def leg_changed(self, sender, args):
        """A leg (m) typed for one bar option: kept for its diameter."""
        if getattr(self, "_filling_types", True) or not hasattr(self, "legs_bot"):
            return
        for (_, cbo), (bot, top) in zip(self.bar_tools, self._leg_boxes()):
            if sender is bot or sender is top:
                legs = self.legs_bot if sender is bot else self.legs_top
                value = self._number(sender.Text)
                if value:
                    legs[cbo.SelectedItem] = value
                else:
                    legs.pop(cbo.SelectedItem, None)
        self.config_changed(sender, args)

    def _horizontal_form(self):
        """The wall's horizontal bars as typed (EA_Muro_Horizontal)."""
        def num(box, default):
            try:
                return max(0.0, float((box.Text or u"").replace(u",", u".")))
            except ValueError:
                return default
        return {"on": bool(self.chk_hz.IsChecked), "d": self.cbo_hz_d.SelectedItem or u'3/8"',
                "dist": (self.txt_hz_dist.Text or u"").strip(), "al": num(self.txt_hz_al, 0.30),
                "ar": num(self.txt_hz_ar, 0.30), "hook": num(self.txt_hz_hook, 0.10)}

    def elev_edit(self, tag):
        """A click on an editable cota of the elevation: its new value."""
        box = {"izaje_h": self.txt_izaje_h, "anchor": self.txt_anchor,
               "anc_l": self.txt_hz_al, "anc_r": self.txt_hz_ar}.get(tag)
        if box is None:
            return
        label = {"izaje_h": u"Altura de izaje (m), desde la cara de la zapata:",
                 "anchor": u"Anclaje (m) dentro de la cimentacion (vacio: hasta la malla del fondo):",
                 "anc_l": u"Longitud (m) que entra la barra horizontal en la columna izquierda:",
                 "anc_r": u"Longitud (m) que entra la barra horizontal en la columna derecha:"}[tag]
        value = forms.ask_for_string(default=box.Text or u"", prompt=label, title="Acero")
        if value is not None:
            box.Text = value.strip()

    def _slot_type_combos(self):
        return [self.cbo_bar_type_1, self.cbo_bar_type_2, self.cbo_bar_type_3, self.cbo_bar_type_4]

    def _fill_slot_types(self):
        """Each vertical bar option's "Tipo": the type chosen for its
        diameter (self.bars_types), else "(automatico)"."""
        names = self._type_names()
        self._filling_types = True
        for (_, cbo), combo in zip(self.bar_tools, self._slot_type_combos()):
            if combo.ItemsSource is None:
                combo.ItemsSource = List[str](names)
            wanted = getattr(self, "bars_types", {}).get(cbo.SelectedItem)
            combo.SelectedItem = wanted if wanted in names else AUTO_TYPE
        for (_, cbo), (bot, top) in zip(self.bar_tools, self._leg_boxes()):
            for box, legs in ((bot, getattr(self, "legs_bot", {})), (top, getattr(self, "legs_top", {}))):
                value = legs.get(cbo.SelectedItem)
                box.Text = u"{:g}".format(value) if value else u""
        self._filling_types = False

    def bar_type_changed(self, sender, args):
        """A vertical bar option's type chosen: kept for its diameter; a type
        of another diameter moves the option to that diameter."""
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
        self._fill_slot_types()  # options sharing the diameter show it too

    @staticmethod
    def _chosen_type(combo):
        item = combo.SelectedItem
        return u"" if not item or item == AUTO_TYPE else item

    def stirrup_diameter_changed(self, sender, args):
        if not hasattr(self, "kind_for"):
            return
        self._fill_bar_types()
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
            thickness = max(2, rs.BAR_DIAMETERS_MM[key] / 1000.0 * scale)
            self._polyline(frame, line, color(kind), thickness, closed=not is_open)
            if not is_open:
                self._hook(frame, line, key, color(kind), thickness)
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
            thickness = max(2, rs.BAR_DIAMETERS_MM[key] / 1000.0 * scale)
            self._polyline(frame, [a2, b2], color(kind), thickness)
            # C / S crossties: their 180-degree hooks
            for mark in rs.tie_hook_marks(a2, b2, style, 2.5 * rs.BAR_DIAMETERS_MM[key] / 1000.0):
                self._polyline(frame, mark, color(kind), thickness)
        for x, y, key in bars:
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
        signature = (self.state.active, tuple(id_of(c.Id) for c in self._view_columns),
                     repr(sorted(splice_now.items())) if splice_now else None,
                     rs.design_to_text(self.design),
                     tuple(sorted(self._get_form().items())))
        if signature != self._views_sig:
            self._views_sig = signature
            self._refresh_views()

    def _hook(self, frame, line, key, color, thickness):
        """The 135-degree hook of a closed stirrup at its first corner (where
        Revit puts it): a short leg into the core along the bisector."""
        pts = rs.counterclockwise(line)
        if len(pts) < 3:
            return
        c, nxt, prv = pts[0], pts[1], pts[-1]
        u = [nxt[0] - c[0], nxt[1] - c[1]]
        v = [prv[0] - c[0], prv[1] - c[1]]
        lu, lv = (u[0] ** 2 + u[1] ** 2) ** 0.5, (v[0] ** 2 + v[1] ** 2) ** 0.5
        if lu < 1e-9 or lv < 1e-9:
            return
        bx, by = u[0] / lu + v[0] / lv, u[1] / lu + v[1] / lv
        lb = (bx ** 2 + by ** 2) ** 0.5
        if lb < 1e-9:
            return
        length = max(6 * rs.BAR_DIAMETERS_MM[key] / 1000.0, 0.075)
        end = (c[0] + bx / lb * length, c[1] + by / lb * length)
        self._polyline(frame, [c, end], color, thickness)

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
    def _select(self, index):
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
        self.cut_nav.reset()
        self.redraw()
        self._draw_cut()

    # -- the wall's cut: vertical bars by sketch -----------------------------------
    def plan_view_changed(self, sender, args):
        if not hasattr(self, "cut_nav"):
            return  # fired while the XAML loads
        cut = bool(self.rb_view_cut.IsChecked)
        self.canvas_cut.Visibility = Visibility.Visible if cut else Visibility.Collapsed
        self.panel_cut_tools.Visibility = Visibility.Visible if cut else Visibility.Collapsed
        self.canvas.Visibility = Visibility.Collapsed if cut else Visibility.Visible
        self._draw_cut()

    def cut_mode_changed(self, sender, args):
        mode = self.cbo_cut_m.SelectedItem or rf.SPACING
        self.txt_cut_n.IsEnabled = mode != rf.SPACING
        self.txt_cut_s.IsEnabled = mode != rf.QUANTITY

    def _cut_geometry(self):
        """(foundation-like geometry of the wall, its cut outlines with the
        cover line) for the column (wall) shown."""
        t = self.by_id.get(self.state.active)
        if t is None:
            return None, []
        wall = (self._view_columns or t.columns)[0]
        key = id_of(wall.Id)
        if key not in self._cut_cache:
            try:
                geo = rf.Foundation(wall)
                x0, x1 = geo.extent[0], geo.extent[1]
                at = (x0 + x1) / 2.0
                try:
                    others = geo.neighbor_sections(rf.SIDE, at)
                except Exception:
                    others = []
                self._cut_cache[key] = (geo, geo.section(rf.SIDE, at), others)
            except Exception:
                self._cut_cache[key] = (None, [], [])
        geo, outlines, others = self._cut_cache[key]
        self._cut_others = others
        try:
            cover = float((self.txt_cover.Text or u"4").replace(u",", u".")) / 100.0
        except ValueError:
            cover = 0.04
        return geo, [(pts, rf.inner_outline(pts, [cover] * len(pts))) for pts, tags in outlines]

    def cut_resized(self, sender, args):
        self._draw_cut()

    def _draw_cut(self):
        canvas = self.canvas_cut
        canvas.Children.Clear()
        if not self.rb_view_cut.IsChecked or canvas.ActualWidth < 10:
            return
        geo, outlines = self._cut_geometry()
        if not outlines:
            rv._text(canvas, rv.fit_frame(canvas.ActualWidth, canvas.ActualHeight, -1, -1, 1, 1), 0, 0,
                     u"No se pudo cortar este muro", size=11)
            return
        others = getattr(self, "_cut_others", [])
        us = [p[0] for pts, _ in outlines for p in pts] + [p[0] for _, pts in others for p in pts]
        zs = [p[1] for pts, _ in outlines for p in pts] + [p[1] for _, pts in others for p in pts]
        self._cut_fitted = rv.fit_frame(canvas.ActualWidth, canvas.ActualHeight,
                                        min(us) - 0.2, min(zs) - 0.2, max(us) + 0.2, max(zs) + 0.3)
        frame = self.cut_nav.resolve(self._cut_fitted)
        self._cut_frame = frame
        gray = brush(225, 228, 232)
        dark = brush(60, 60, 60)
        labelled = set()
        for label, pts in others:  # the elements touching the wall, behind it
            rv._polygon(canvas, frame, pts, brush(205, 210, 218))
            for i in range(len(pts)):
                rv._line(canvas, frame, pts[i], pts[(i + 1) % len(pts)], brush(140, 145, 155), 1)
            if label not in labelled:
                labelled.add(label)
                cu = sum(q[0] for q in pts) / len(pts)
                cz = sum(q[1] for q in pts) / len(pts)
                rv._text(canvas, frame, cu, cz, label, brush=brush(110, 115, 125), size=10, bold=True)
        for pts, inner in outlines:
            rv._polygon(canvas, frame, pts, gray)
            for i in range(len(pts)):
                rv._line(canvas, frame, pts[i], pts[(i + 1) % len(pts)], dark, 2)
            for i in range(len(inner)):
                rv._line(canvas, frame, inner[i], inner[(i + 1) % len(inner)], brush(90, 90, 90), 1, dash=True)
        rv._text(canvas, frame, (min(us) + max(us)) / 2.0, max(zs) + 0.15,
                 u"Espesor arriba {:.2f} m, abajo {:.2f} m".format(self._width_at(outlines, max([q[1] for pts, _ in outlines for q in pts]) - 0.01), self._width_at(outlines, min([q[1] for pts, _ in outlines for q in pts]) + 0.01)),
                 brush=brush(31, 78, 160), size=10)
        blue = brush(31, 78, 160)
        for item in self.cut_items:
            pts = [tuple(q) for q in item["pts"]]
            for a, b in zip(pts, pts[1:]):
                rv._line(canvas, frame, a, b, blue, 2.5)
            label = u"\u00d8{} ".format(item["d"]) + (
                u"n={}".format(item.get("n")) if item.get("m") == rf.QUANTITY else u"@{:g}".format(float(item["s"])))
            rv._text(canvas, frame, pts[0][0], pts[0][1] - 0.06, label, brush=blue, size=10)
        if self.cut_draft:
            pts = list(self.cut_draft) + ([self.cut_cursor] if self.cut_cursor else [])
            for a, b in zip(pts, pts[1:]):
                rv._line(canvas, frame, a, b, brush(230, 80, 30), 2, dash=True)
        self.txt_cut_count.Text = u"{} barra(s)".format(len(self.cut_items))

    @staticmethod
    def _width_at(outlines, z):
        spans = [s for pts, _ in outlines for s in rf.polygon_spans(pts, z)]
        return float(sum(b - a for a, b in spans))

    def _cut_point(self, args):
        """The clicked point on the cut: snapped to the cover line (its
        corners and the middle of each side), always inside it."""
        if self._cut_frame is None:
            return None
        pt = args.GetPosition(self.canvas_cut)
        scale, ox, oy = self._cut_frame
        p = ((pt.X - ox) / scale, (oy - pt.Y) / scale)
        geo, outlines = self._cut_geometry()
        if not outlines:
            return None
        try:
            cover = float((self.txt_cover.Text or u"4").replace(u",", u".")) / 100.0
        except ValueError:
            cover = 0.04
        # the bars may go on into the touching elements (a footing, a beam):
        # their outlines, with the foundation cover, count too
        for label, pts in getattr(self, "_cut_others", []):
            c = rf.DEFAULT_COVER_CM / 100.0 if label == u"ZAPATA" else cover
            try:
                outlines = outlines + [(pts, rf.inner_outline(pts, [c] * len(pts)))]
            except Exception:
                pass
        tol = 12.0 / scale
        targets = []
        for outer, inner in outlines:
            n = len(inner)
            targets += list(inner)
            targets += [((inner[i][0] + inner[(i + 1) % n][0]) / 2.0, (inner[i][1] + inner[(i + 1) % n][1]) / 2.0)
                        for i in range(n)]
        near = [q for q in targets if ((q[0] - p[0]) ** 2 + (q[1] - p[1]) ** 2) ** 0.5 < tol]
        if near:
            return min(near, key=lambda q: (q[0] - p[0]) ** 2 + (q[1] - p[1]) ** 2)
        for outer, inner in outlines:
            q = rf.snap_to_outline(p, inner, tol)
            if q is not p:
                return q
        p = (round(p[0], 2), round(p[1], 2))
        return min((rf.clamp_inside(p, inner) for outer, inner in outlines),
                   key=lambda q: (q[0] - p[0]) ** 2 + (q[1] - p[1]) ** 2)

    def cut_left(self, sender, args):
        if not self.btn_cut_sketch.IsChecked:
            return
        p = self._cut_point(args)
        if p is not None:
            self.cut_draft.append(p)
            self._draw_cut()

    def cut_right(self, sender, args):
        if len(self.cut_draft) >= 2:
            def num(text, default):
                try:
                    return float((text or u"").replace(u",", u"."))
                except ValueError:
                    return default
            self.cut_items.append({"view": rf.SIDE, "pts": [list(q) for q in self.cut_draft],
                                   "d": self.cbo_cut_d.SelectedItem or u'1/2"',
                                   "m": self.cbo_cut_m.SelectedItem or rf.SPACING,
                                   "n": max(1, int(num(self.txt_cut_n.Text, 5))),
                                   "s": max(0.05, num(self.txt_cut_s.Text, 0.2)), "closed": False})
            self.dirty = True
        self.cut_draft = []
        self.cut_cursor = None
        self._draw_cut()

    def cut_delete(self, sender, args):
        if self.cut_items:
            self.cut_items.pop()
            self.dirty = True
            self._draw_cut()

    def cut_move(self, sender, args):
        if self.cut_nav.pan(args.GetPosition(self.canvas_cut)):
            self._draw_cut()
            return
        if self.cut_draft:
            self.cut_cursor = self._cut_point(args)
            self._draw_cut()

    def cut_wheel(self, sender, args):
        if self._cut_fitted:
            self.cut_nav.wheel(self._cut_fitted, args.GetPosition(self.canvas_cut), args.Delta)
            self._draw_cut()
        args.Handled = True

    def cut_down(self, sender, args):
        if args.ChangedButton == MouseButton.Middle and self._cut_fitted:
            self.cut_nav.start_pan(self._cut_fitted, args.GetPosition(self.canvas_cut))
            self.canvas_cut.CaptureMouse()

    def cut_up(self, sender, args):
        self.cut_nav.end_pan()
        self.canvas_cut.ReleaseMouseCapture()

    # -- elevation and 3D views ----------------------------------------------
    def _column_info(self, column, fallback):
        """(section, clear height m, neighbors) of one column, cached."""
        key = id_of(column.Id)
        if key not in self._section_cache:
            try:
                self._section_cache[key] = rc.Section(column)
            except Exception:
                self._section_cache[key] = fallback
        section = self._section_cache[key]
        if key not in self._clear_cache:
            try:
                top = rc.clear_top(doc, column, section)
            except Exception:
                top = section.z_top
            self._clear_cache[key] = (top - section.z_bottom) * rc.FT
        if key not in self._neighbor_cache:
            try:
                self._neighbor_cache[key] = rc.column_neighbors(doc, column, section)
            except Exception:
                self._neighbor_cache[key] = []
        return section, self._clear_cache[key], self._neighbor_cache[key]

    def _fill_view_columns(self, t):
        """List the type's columns for the 3D view / elevation, each with
        what it touches; with two or more of them picked in the model, first
        an entry showing those stacked. First choice: the picked ones (all
        of them), else the first one a beam frames into, else the first."""
        self._filling_columns = True
        try:
            labels, options = [], []
            beam_at = None
            picked = set(self.state.picked_ids)
            stack = sorted([c for c in t.columns if id_of(c.Id) in picked],
                           key=lambda c: self._column_info(c, t.section)[0].z_bottom
                           if t.section is not None else 0)
            if len(stack) > 1:
                labels.append(u"Las {} seleccionadas, apiladas ({})".format(
                    len(stack), u", ".join(self._level_name(c) for c in stack)))
                options.append(stack)
            for column in t.columns:
                kinds = []
                if t.section is not None:
                    for n in self._column_info(column, t.section)[2]:
                        name = n["label"].lower()
                        if name not in kinds:
                            kinds.append(name)
                labels.append(u"{} - {}{}".format(
                    id_of(column.Id), self._level_name(column),
                    u"  ({})".format(u", ".join(kinds)) if kinds else u""))
                options.append([column])
                if beam_at is None and u"viga" in kinds:
                    beam_at = len(options) - 1
            if stack:
                default = 0 if len(stack) > 1 else options.index([stack[0]])
            else:
                default = beam_at or 0
            self._view_options = options
            self.cbo_view_column.ItemsSource = List[str](labels)
            self.cbo_view_column.SelectedIndex = default
            self._view_columns = options[default]
        finally:
            self._filling_columns = False

    def _level_name(self, column):
        level = doc.GetElement(column.LevelId)
        return level.Name if level else u"sin nivel"

    def view_column_changed(self, sender, args):
        if getattr(self, "_filling_columns", True):
            return
        index = self.cbo_view_column.SelectedIndex
        options = getattr(self, "_view_options", [])
        if index < 0 or index >= len(options):
            return
        self._view_columns = options[index]
        self.elev_nav.reset()
        self.scene._extent = None
        self._views_sig = None  # force the views to redraw for this column
        self.redraw()

    def config_changed(self, sender, args):
        if hasattr(self, "_views_sig"):  # also fired while the XAML loads
            self.redraw()

    def _family_view(self, key_text, dist_text, clear):
        """{"key", "zones", "rest", "tagged"} of a stirrup family for the
        views, or None (message) when its settings don't read."""
        if not (dist_text or u"").strip():
            return None, None
        try:
            key = rs.parse_diameter(key_text)
            zones, rest = rs.parse_distribution(dist_text)
        except rs.SpecError as e:
            return None, u"{}".format(e)
        return {"key": key, "zones": zones, "rest": rest,
                "tagged": rs.stirrup_zone_positions(clear, zones, rest)}, None

    def _views_data(self):
        """(elevation data, 3D data) of the active type as configured and
        drawn right now (unsaved changes included)."""
        t = self.by_id.get(self.state.active)
        if t is None or t.section is None:
            return None, None
        # The column(s) chosen in "Columna:" (each has its own height and
        # its own beams, slab and footing); several are drawn stacked, in
        # the frame of the lowest one.
        columns = [c for c in self._view_columns if c in t.columns] or t.columns[:1]
        f = self._get_form()
        bars = self.design["bars"]
        base = self._column_info(columns[0], t.section)[0]
        to_base = base.transform.Inverse
        segments, neighbors, loops = [], [], []
        messages = []
        for index, column in enumerate(columns):
            section, clear, touching = self._column_info(column, t.section)
            height = (section.z_top - section.z_bottom) * rc.FT
            origin = to_base.OfPoint(section.point_m(0.0, 0.0, section.z_bottom))
            dx = (origin.X - base.center[0]) * rc.FT
            dy = (origin.Y - base.center[1]) * rc.FT
            dz = (section.z_bottom - base.z_bottom) * rc.FT
            # over a footing: izaje up to its height, the column's own
            # distribution from there (rc.generate_column)
            depth = rc.foundation_below(doc, column, section) if index == 0 else None
            izaje = rs.read_json_setting(f["izaje"])
            izaje_h = (float(izaje["h"]) if (depth and izaje.get("on", True) and izaje.get("dist") and izaje.get("h"))
                       else 0.0)
            edge, edge_msg = self._family_view(f["edge"], f["edge_dist"], clear - izaje_h)
            conf, conf_msg = self._family_view(f["conf"], f["conf_dist"], clear - izaje_h)
            for fam in (edge, conf):
                if fam and izaje_h:
                    fam["tagged"] = [(z + izaje_h, zone) for z, zone in fam["tagged"]]
            extra = {}
            if izaje_h:
                try:
                    extra["izaje"] = rs.izaje_positions(izaje_h, izaje["dist"])
                except rs.SpecError as e:
                    messages.append(u"Izaje: {}".format(e))
                extra["izaje_h"] = izaje_h
            ends = rs.read_json_setting(f["ends"])
            if depth:
                anchor = ends.get("anchor")
                extra["anchor"] = (float(anchor) if anchor not in (None, u"") else
                                   depth - rc.FOUNDATION_COVER_M - 0.03)
                extra["leg_bot"] = max([rs.leg_m(v) for v in (ends.get("bot") or {}).values()] or [0.0])
                extra["dir_bot"] = ends.get("dir_bot") or rs.LEG_OUT
            hz = self._horizontal_form()
            if index == 0 and hz["on"] and hz["dist"]:
                try:
                    zones, rest = rs.parse_distribution(hz["dist"])
                    extra["horiz"] = {"levels": rs.stirrup_positions(height, zones, rest),
                                      "al": hz["al"], "ar": hz["ar"]}
                except rs.SpecError as e:
                    messages.append(u"Barras horizontales: {}".format(e))
            if index == len(columns) - 1 and not rc.column_above(doc, column, section):
                extra["leg_top"] = max([rs.leg_m(v) for v in (ends.get("top") or {}).values()] or [0.0])
                extra["dir_top"] = ends.get("dir_top") or rs.LEG_IN
            joint = []
            if f["nucleo"] and height - clear > 0.1:
                try:
                    joint = rs.joint_positions(height - clear, float(f["nucleo"].replace(u",", u".")) / 100.0)
                except ValueError:
                    pass
            messages += [m for m in (edge_msg and u"Borde: " + edge_msg,
                                     conf_msg and u"Confinamiento: " + conf_msg) if m]
            if edge is None and not edge_msg:
                messages.append(u"Falta la distribucion del estribo de borde")
            # confinement stirrups/ties keep their "rto" in the joint (rc.generate_column)
            joint_conf = rs.joint_positions(height - clear, conf["rest"]) if joint and conf else []
            segments.append(dict({"x": dx, "y": dy, "z": dz, "height": height, "clear": clear,
                                  "edge": edge, "conf": conf, "joint": joint, "joint_conf": joint_conf}, **extra))
            for n in touching:
                x0, y0, z0, x1, y1, z1 = n["box"]
                neighbors.append({
                    "label": n["label"], "seg": index,
                    "box": (x0 + dx, y0 + dy, z0 + dz, x1 + dx, y1 + dy, z1 + dz),
                    "triangles": [tuple((p[0] + dx, p[1] + dy, p[2] + dz) for p in tri)
                                  for tri in n["triangles"]],
                })
            families = {rs.KIND_EDGE: edge, rs.KIND_CONFINEMENT: conf}
            drawn = [(kind, poly, wrap, is_open) for kind, poly, wrap, is_open in self.design["stirrups"]]
            drawn += [(kind, [a, b], None, None) for kind, a, b in self.design["ties"]]
            drawn = [item for item in drawn if families.get(item[0]) is not None]
            # stacked like they are generated (see rc.generate_column)
            lifts = rs.stack_lifts([(kind, rs.BAR_DIAMETERS_MM[families[kind]["key"]] / 1000.0, is_open is None)
                                    for kind, _, _, is_open in drawn])
            for (kind, poly, wrap, is_open), lift in zip(drawn, lifts):
                family = families[kind]
                try:
                    if is_open is None:  # a tie
                        line, closed = list(rs.tie_centerline(poly[0], poly[1], bars, family["key"])), False
                    else:
                        line = rs.stirrup_centerline(poly, bars, family["key"], wrap, is_open)
                        closed = not is_open
                except rs.SpecError:
                    continue
                # stacked towards the middle, like rc._runs (down in the top half)
                zs = ([dz + z + (lift if is_open is None else -lift if z > clear / 2.0 + 1e-6 else lift)
                       for z, _ in family["tagged"]]
                      + [dz + lift + clear + j for j in (joint_conf if kind == rs.KIND_CONFINEMENT else joint)])
                loops.append((kind, [(x + dx, y + dy) for x, y in line], closed, zs,
                              rs.BAR_DIAMETERS_MM[family["key"]] / 2000.0))
                if extra.get("izaje") and kind == rs.KIND_EDGE and closed:
                    izaje_key = izaje.get("d") or u'3/8"'
                    loops.append(("izaje", [(x + dx, y + dy) for x, y in line], True,
                                  [dz + z for z in extra["izaje"]],
                                  rs.BAR_DIAMETERS_MM.get(izaje_key, 9.525) / 2000.0))
        top = max(s["z"] + s["height"] for s in segments)
        # Stacked columns with continuous bars: the pieces and laps the
        # generation will make (rc.generate_stack).
        splice = splice_for_generation(self._splice_from_form()) if hasattr(self, "lap_boxes") else None
        bar_paths, laps = None, []
        if splice and len(segments) > 1:
            unspliced = sorted(set(k for _, _, k in bars if k not in splice["laps"]))
            if unspliced and top > splice["max"] + 1e-6:
                messages.append(u"Sin empalme (diametro no marcado): barras de {} de {:.2f} m".format(
                    u", ".join(unspliced), top))
            bar_paths = []
            stories = [(s["z"], s["z"] + s["clear"]) for s in segments]
            for x, y, key in bars:
                lap = splice["laps"].get(key, 0.0)
                pieces = rs.splice_pieces(0.0, top, stories, lap, splice["max"])[0] if lap else [(0.0, top)]
                d = rs.BAR_DIAMETERS_MM[key] / 1000.0
                for i, (a, b) in enumerate(pieces):
                    bar_paths.append((rs.bar_piece_points(x, y, d, a, b, lap, i < len(pieces) - 1), d / 2.0))
                    if i < len(pieces) - 1:
                        laps.append((round(b - lap, 3), round(b, 3), key))
        # the bars into the foundation and their end legs (rc.bar_ends)
        ends = rs.read_json_setting(f["ends"])
        first, last = segments[0], segments[-1]
        if first.get("anchor") is not None or last.get("leg_top"):
            if bar_paths is None:
                bar_paths = [([(x, y, 0.0), (x, y, top)], rs.BAR_DIAMETERS_MM[k] / 2000.0) for x, y, k in bars]
            xs0 = [p[0] for p in base.polygon_m]
            ys0 = [p[1] for p in base.polygon_m]
            half = ((max(xs0) - min(xs0)) / 2.0, (max(ys0) - min(ys0)) / 2.0)
            keys = dict(((round(x, 4), round(y, 4)), k) for x, y, k in bars)
            done = []
            for i, (points, radius) in enumerate(bar_paths):
                x, y = points[0][0], points[0][1]
                key = keys.get((round(x, 4), round(y, 4)))
                kw = {}
                if points[0][2] < 1e-6 and first.get("anchor") is not None:
                    kw["anchor"] = first["anchor"]
                    leg = rs.leg_m((ends.get("bot") or {}).get(key))
                    if leg:
                        kw["leg_bottom"] = leg
                        kw["dir_bottom"] = rs.leg_vector(x, y, half[0], half[1], first.get("dir_bot"))
                leg = rs.leg_m((ends.get("top") or {}).get(key))
                if points[-1][2] > top - 1e-6 and last.get("leg_top") is not None and leg:
                    kw["top_drop"] = 0.05
                    kw["leg_top"] = leg
                    kw["dir_top"] = rs.leg_vector(x, y, half[0], half[1], last.get("dir_top"))
                done.append((rs.bar_with_ends(points, **kw), radius))
            bar_paths = done
        xs = [p[0] for p in base.polygon_m]
        elev = {
            "width": max(xs) - min(xs),
            "height": top,
            "bars_x": sorted(set(round(x, 3) for x, _, _ in bars)),
            "message": messages[0] if messages else None,
            "neighbors": neighbors,
            "segments": segments,
            "laps": sorted(set(laps)),
        }
        if len(segments) == 1:  # the single-column keys too
            elev.update(dict((k, segments[0][k]) for k in ("clear", "edge", "conf", "joint", "joint_conf")))
        scene = {
            "polygon": base.polygon_m,
            "height": top,
            "bars": [(x, y, rs.BAR_DIAMETERS_MM[k] / 2000.0) for x, y, k in bars],
            "loops": loops,
            "neighbors": neighbors,
            "segments": segments,
            "bar_paths": bar_paths,
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
        self._draw_elevation()
        self._build_3d()

    def _elev_fitted(self):
        width, height = self.canvas_elev.ActualWidth, self.canvas_elev.ActualHeight
        if self._elev_data is None or width < 50 or height < 50:
            return None
        return rv.fit_frame(width, height, *rv.elevation_extent(self._elev_data),
                            margins=(20, 16, 20, 16))

    def _draw_elevation(self):
        fitted = self._elev_fitted()
        if fitted is None:
            self.canvas_elev.Children.Clear()
            return
        rv.draw_elevation(self.canvas_elev, self._elev_data, self.elev_nav.resolve(fitted))

    def _build_3d(self):
        self.scene.build(getattr(self, "_scene_data", None), self.cbo_detail.SelectedItem or u"Medio")

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

    def elev_mouse_down(self, sender, args):
        tag = getattr(args.OriginalSource, "Tag", None)
        if args.ChangedButton == MouseButton.Left and tag in ("izaje_h", "anchor", "anc_l", "anc_r"):
            self.elev_edit(tag)  # an editable cota
            args.Handled = True
            return
        fitted = self._elev_fitted()
        if fitted is not None and args.ChangedButton in (MouseButton.Middle, MouseButton.Left):
            self.elev_nav.start_pan(fitted, args.GetPosition(self.canvas_elev))
            self.canvas_elev.CaptureMouse()

    def elev_mouse_move(self, sender, args):
        if self.elev_nav.pan(args.GetPosition(self.canvas_elev)):
            self._draw_elevation()

    def elev_mouse_up(self, sender, args):
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
            self._select(self._stirrup_at(frame, p))
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
        rs.add_item(self.design, "stirrups", (kind, pts, wrap, is_open), shape["name"] if shape else None)
        self.draft = []
        self._select(len(self.design["stirrups"]) - 1)
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
        candidates = [c for c in candidates if c[0] <= tol]
        if not candidates:
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
            self._select(None)

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
            self._select(None)
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
        self._select(None)
        self.redraw()

def pick_columns(state):
    uidoc = revit.uidoc
    try:
        refs = uidoc.Selection.PickObjects(
            ObjectType.Element, _ColumnFilter(),
            "Selecciona los muros y pulsa Finalizar",
        )
    except OperationCanceledException:
        return
    picked = [doc.GetElement(r.ElementId) for r in refs]
    state.picked_ids = [id_of(c.Id) for c in picked if c is not None]
    state.checked |= set(group_of(c) for c in picked if c is not None)
    if state.picked_ids:
        state.scope = "pick"
        # show the type (and, in the views, the column) just picked
        state.active = group_of(doc.GetElement(DB.ElementId(state.picked_ids[0])))


AUTO_TYPE = u"(automatico)"


def cut_bars(wall, item):
    """The vertical bars sketched on the wall's cut (EA_Muro_Corte),
    repeated along the wall as drawn: [(rebar, key, kind)]."""
    cfg = item.config()
    try:
        sketches = json.loads(cfg.get("EA_Muro_Corte") or u"[]")
    except ValueError:
        sketches = []
    if not sketches:
        return []
    geo = rf.Foundation(wall)
    try:
        cover_cm = float((cfg.get("EA_Recubrimiento_cm") or u"4").replace(u",", u"."))
    except ValueError:
        cover_cm = 4.0
    planner = rf.BarPlanner(geo, dict((f.number, cover_cm) for f in geo.faces), rs.BAR_DIAMETERS_MM)
    bars = []
    # each drawn bar repeated along the wall as drawn: it may run on into
    # the footing or beam it was drawn into (no "inside the wall" check)
    for sketch in sketches:
        key = sketch["d"]
        lo, hi = planner._range(rf.SIDE, rs.BAR_DIAMETERS_MM[key] / 1000.0)
        pts = [tuple(q) for q in sketch["pts"]]
        for pos in rf.distribute(lo, hi, sketch.get("m", rf.SPACING), float(sketch["s"]), sketch.get("n", 1)):
            bars.append((rf.SIDE, pos, pts, key, sketch.get("t") or u""))
    made = []
    for view, key, path, first, count, spacing, tname in rf.bar_sets(bars):
        pts = [geo.world(first, -u, z) for u, z in path]
        curves = List[DB.Curve]([DB.Line.CreateBound(pts[k], pts[k + 1]) for k in range(len(pts) - 1)])
        rebar = DB.Structure.Rebar.CreateFromCurves(
            doc, RebarStyle.Standard, bar_types.pick(key, rc.type_mark(item.name)), None, None, wall,
            geo.ux.Negate(), curves, DB.Structure.RebarHookOrientation.Right,
            DB.Structure.RebarHookOrientation.Right, True, True)
        if count > 1:
            rebar.GetShapeDrivenAccessor().SetLayoutAsNumberWithSpacing(count, spacing / rf.FT, False, True, True)
        rc._tag(rebar, wall)
        made.append((rebar, key, rc.LONGITUDINAL))
    return made


def horizontal_bars(wall, item):
    """The wall's horizontal bars (EA_Muro_Horizontal): one per face at each
    level of its distribution, run into the columns at both ends by their
    anchorage, with a leg towards the other face. [(rebar, key, kind)]."""
    cfg = item.config()
    hz = rs.read_json_setting(cfg.get("EA_Muro_Horizontal"))
    if not hz.get("on") or not (hz.get("dist") or u"").strip():
        return []
    section = rc.Section(wall)
    key = hz.get("d") or u'3/8"'
    d = rs.BAR_DIAMETERS_MM[key] / 1000.0
    try:
        cover = float((cfg.get("EA_Recubrimiento_cm") or u"4").replace(u",", u".")) / 100.0
    except ValueError:
        cover = 0.04
    xs = [q[0] for q in section.polygon_m]
    ys = [q[1] for q in section.polygon_m]
    y_face = (max(ys) - min(ys)) / 2.0 - cover - d / 2.0
    height = (section.z_top - section.z_bottom) * rc.FT
    zones, rest = rs.parse_distribution(hz["dist"])
    levels = rs.stirrup_positions(height, zones, rest)
    bar_type = bar_types.pick(key, rc.type_mark(item.name))
    made = []
    al, ar = float(hz.get("al", 0.3)), float(hz.get("ar", 0.3))
    for face, y in enumerate((-y_face, y_face)):
        # both faces' hooks turn towards each other: the second face's bars
        # stop 1.5 diameters short, so their hooks lie beside the first
        # face's ones (touching, as on site), never through them
        back = 1.5 * d if face else 0.0
        path = rs.wall_horizontal_path(min(xs), max(xs), y, max(0.0, al - back), max(0.0, ar - back),
                                       float(hz.get("hook", 0.1)), 2 * y_face - d)
        for start, n, spacing in rs.group_runs(levels):
            z = section.z_bottom + start / rc.FT
            pts = [section.point_m(px, py, z) for px, py in path]
            curves = List[DB.Curve]([DB.Line.CreateBound(pts[k], pts[k + 1]) for k in range(len(pts) - 1)])
            rebar = DB.Structure.Rebar.CreateFromCurves(
                doc, RebarStyle.Standard, bar_type, None, None, wall, DB.XYZ.BasisZ, curves,
                DB.Structure.RebarHookOrientation.Right, DB.Structure.RebarHookOrientation.Right, True, True)
            if n > 1:
                rebar.GetShapeDrivenAccessor().SetLayoutAsNumberWithSpacing(n, spacing / rc.FT, True, True, True)
            rc._tag(rebar, wall)
            made.append((rebar, key, rc.EDGE))
    return made


# --- main -------------------------------------------------------------------
types = collect_types()
if not types:
    forms.alert("El modelo no tiene muros.", title="Acero")
    script.exit()

state = State()
preselected = [doc.GetElement(i) for i in revit.uidoc.Selection.GetElementIds()]
preselected = [e for e in preselected if e is not None and _ColumnFilter().AllowElement(e)]
if preselected:
    state.picked_ids = [id_of(c.Id) for c in preselected]
    state.checked = set(group_of(c) for c in preselected)
    state.scope = "pick"
    state.active = group_of(preselected[0])

xaml = os.path.join(SCRIPT_DIR, "AceroForm.xaml")
while True:
    window = AceroWindow(xaml, types, state)
    window.ShowDialog()
    if window.action == "pick":
        pick_columns(state)
        continue
    if window.action != "run":
        script.exit()
    break

by_id = dict((t.id, t) for t in types)
if state.scope == "pick":
    targets = [doc.GetElement(DB.ElementId(i)) for i in state.picked_ids]
    targets = [c for c in targets if c is not None]
    scope_label = "columnas seleccionadas"
    # The type's configuration fits all its columns: offer them all, on
    # every level, not just the ones picked.
    picked_types = sorted(set(group_of(c) for c in targets))
    same_type = [c for t in picked_types for c in by_id[t].columns]
    if False:  # the window generates the picked columns only
        only_picked = u"Solo las columnas seleccionadas ({})".format(len(targets))
        all_levels = u"Todas las columnas de {} en todos los niveles ({})".format(
            u", ".join(by_id[t].name for t in picked_types), len(same_type))
        choice = forms.CommandSwitchWindow.show(
            [only_picked, all_levels], message=u"Columnas donde generar el acero:")
        if not choice:
            script.exit()
        if choice == all_levels:
            targets = same_type
            scope_label = u"todas las columnas de su tipo, en todos los niveles"
else:
    targets = [c for t in state.checked for c in by_id[t].columns]
    scope_label = "tipos marcados, todo el modelo"

specs = {}
spec_errors = {}
for t in set(group_of(c) for c in targets):
    try:
        specs[t] = rc.ColumnSpec(by_id[t].config())
    except rs.SpecError as e:
        spec_errors[t] = u"{}".format(e)
with_spec = [c for c in targets if group_of(c) in specs]
if not with_spec:
    forms.alert(
        u"Ninguna de las {} columnas del alcance tiene su tipo configurado.".format(len(targets)),
        title="Acero",
    )
    script.exit()

splice = splice_for_generation(load_splice_settings())
if splice is None:
    stacks = [[c] for c in with_spec]
else:
    # Continuous bars run each stack of the columns being built (same type
    # and axis, one on another): only those chosen - picked ones when
    # picking -, from the base of the lowest to the top of the highest.
    stacks = rc.column_stacks(with_spec)
    with_spec = [c for s in stacks for c in s]

dry_run = False  # straight to the model: no mode question

bar_types = rc.BarTypes(doc)
hooks = rc.StirrupHooks(doc)
rebar_shapes = rc.RebarShapes(doc)
warnings = [u"{}: sin generar - {}".format(by_id[t].name, e) for t, e in spec_errors.items()]
stick_out = {}  # type name -> columns where a stirrup/tie hook leaves the section
KINDS = (rc.LONGITUDINAL, rc.EDGE, rc.CONFINEMENT)
by_type = {}  # type name -> {"n": columns, kind: kg}
total_bars = 0

t = DB.Transaction(doc, "Acero")
t.Start()
try:
    rc.ensure_parameters(doc)
    done = []
    with forms.ProgressBar(title="Acero: {value} de {max_value} columnas") as pb:
        count = 0
        for stack in stacks:
            ct = by_id[group_of(stack[0])]
            sub = DB.SubTransaction(doc)
            sub.Start()
            try:
                created, found = rc.generate_stack(
                    doc, stack, specs[ct.id], bar_types, hooks, rc.type_mark(ct.name), rebar_shapes, splice
                )
                for wall in stack:
                    created[id_of(wall.Id)] += cut_bars(wall, ct)
                    created[id_of(wall.Id)] += horizontal_bars(wall, ct)
                sub.Commit()
                done += [(column, ct.name, created[id_of(column.Id)]) for column in stack]
                warnings += [u"{} (columnas {}): {}".format(ct.name, u", ".join(str(id_of(c.Id)) for c in stack), w)
                             for w in found]
            except Exception as e:
                sub.RollBack()
                warnings.append(u"Columna(s) {} ({}): {}".format(
                    u", ".join(str(id_of(c.Id)) for c in stack), ct.name, e))
            count += len(stack)
            pb.update_progress(count, len(with_spec))

    doc.Regenerate()  # bar lengths are only known after a regeneration
    for column, type_name, created in done:
        kg, bars, sticks = rc.record_weight(column, created)
        if sticks:
            stick_out[type_name] = stick_out.get(type_name, 0) + 1
        agg = by_type.setdefault(type_name, dict([("n", 0)] + [(k, 0.0) for k in KINDS]))
        agg["n"] += 1
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
failed = [w for w in warnings if u"sin generar" in w or w.startswith(u"Columna(s)")]
if failed:
    forms.alert(u"No se pudo generar:\n- " + u"\n- ".join(failed[:10]), title="Acero")
