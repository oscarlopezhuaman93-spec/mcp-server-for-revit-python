# -*- coding: utf-8 -*-
"""Acero de losas macizas: malla inferior corrida (empalmada como las vigas),
bastones superiores desde la cara de cada apoyo (o malla superior),
ganchos en los bordes; planta y dos cortes movibles."""

__title__ = "Acero\nLosa"
__author__ = "Revit MCP"

import math
import os
import sys

SCRIPT_DIR = os.path.dirname(__file__)
EXT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", ".."))
REVIT_MCP_DIR = os.path.join(EXT_ROOT, "revit_mcp")
if REVIT_MCP_DIR not in sys.path:
    sys.path.append(REVIT_MCP_DIR)

import formwork_params as fw_params
import rebar_spec as rs
import rebar_columns as rc
import rebar_foundation as rf
import slab_rebar as sr
import rebar_slabs as rsl
import rebar_views as rv
import utils as fw_utils

reload(fw_utils)
reload(fw_params)
reload(rs)
reload(rc)
reload(rf)
reload(sr)
reload(rsl)
reload(rv)

from pyrevit import revit, DB, forms, script
from Autodesk.Revit.Exceptions import OperationCanceledException
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType
from System.Windows import TextWrapping
from System.Windows.Controls import ListBoxItem, TextBlock, Canvas
from System.Windows.Input import MouseButton
from System.Windows.Media import Color, SolidColorBrush
from System.Windows.Shapes import Ellipse

doc = revit.doc
FLOORS_BIC = DB.BuiltInCategory.OST_Floors
CONFIG_SECTION = "OLSTR_Acero"  # the splices set in Acero Viga / Columna
BAR_KEYS = [k for k in rs.bar_diameter_keys() if 6 <= rs.BAR_DIAMETERS_MM[k] <= 25]
MM = rs.BAR_DIAMETERS_MM
PLAN = u"planta"
COLORS = {(sr.INF, sr.X): (29, 158, 117), (sr.INF, sr.Y): (42, 111, 214),
          (sr.SUP, sr.X): (210, 60, 60), (sr.SUP, sr.Y): (240, 140, 20)}
MODES = [(sr.TOP_BASTONES, u"Bastones en apoyos"), (sr.TOP_MESH, u"Malla corrida"), (sr.TOP_NONE, u"Sin acero superior")]
ANCHORS = [(sr.ANCHOR_HOOK, u"Gancho 90"), (sr.ANCHOR_STRAIGHT, u"Recto")]
CUT_NAMES = {sr.X: u"1-1", sr.Y: u"2-2"}


def id_of(element_id):
    return fw_utils.element_id_value(element_id)


def brush(rgb, alpha=255):
    return SolidColorBrush(Color.FromArgb(alpha, rgb[0], rgb[1], rgb[2]))


def load_splice():
    """The splices of the Acero buttons (shared pyRevit settings): None
    when off, else {"max", "laps": {key: m}} of the diameters marked."""
    laps = dict((k, float(rs.e060_lap_cm(k))) for k in rs.bar_diameter_keys())
    on, max_m, active = True, rs.MAX_BAR_LENGTH, set()
    try:
        config = script.get_config(CONFIG_SECTION)
        on = (config.get_option("splice_on", u"1") or u"1") == u"1"
        max_m = float(config.get_option("splice_max", u"") or rs.MAX_BAR_LENGTH)
        for pair in (config.get_option("splice_laps", u"") or u"").split(u"|"):
            if u"=" in pair:
                key, cm = pair.split(u"=", 1)
                laps[rs.parse_diameter(key)] = float(cm)
        for key in (config.get_option("splice_active", u"") or u"").split(u"|"):
            if key.strip():
                active.add(rs.parse_diameter(key))
    except Exception:
        pass
    if not on:
        return None
    return {"max": max_m, "laps": dict((k, laps[k] / 100.0) for k in active if k in laps)}


class _FloorFilter(ISelectionFilter):
    def AllowElement(self, element):
        return isinstance(element, DB.Floor)

    def AllowReference(self, reference, point):
        return False


class State(object):
    def __init__(self):
        self.picked_ids = []
        self.active = None
        self.settings = {}  # floor id -> settings being edited


class LosaWindow(forms.WPFWindow):
    def __init__(self, xaml_file_path, state, models=None):
        forms.WPFWindow.__init__(self, xaml_file_path)
        self.state = state
        self.action = None
        self.model = None
        self._models = models if models is not None else {}
        self.splice = load_splice()
        self.navs = {PLAN: rv.Nav2D(), sr.X: rv.Nav2D(), sr.Y: rv.Nav2D()}
        self._fitted, self._frames = {}, {}
        self._plan, self._warnings = {}, []
        self._drag_cut = None
        self._filling = True
        for name in ("ix", "iy", "sx", "sy"):
            getattr(self, "cbo_{}_d".format(name)).ItemsSource = BAR_KEYS
        self.cbo_mode.ItemsSource = [n for _, n in MODES]
        self.cbo_anchor.ItemsSource = [n for _, n in ANCHORS]
        self._filling = False
        if self.splice is None:
            self.txt_splice.Text = u"Empalmes: desactivados (barras enteras), como en Acero Viga."
        else:
            self.txt_splice.Text = (u"Empalmes (los de Acero Viga): barras de más de {:.2f} m se empalman; "
                                    u"inferiores desde la cara del apoyo en el cuarto extremo (L/4), superiores en el "
                                    u"tercio central. Diámetros: {}.".format(
                                        self.splice["max"],
                                        u", ".join(u"{} {:.2f} m".format(k, v) for k, v in sorted(self.splice["laps"].items()))
                                        or u"ninguno marcado"))
        for i in state.picked_ids:
            e = doc.GetElement(DB.ElementId(i))
            item = ListBoxItem()
            label = TextBlock()
            label.Text = u"{}  (id {})".format(doc.GetElement(e.GetTypeId()).get_Parameter(
                DB.BuiltInParameter.SYMBOL_NAME_PARAM).AsString(), i)
            label.TextWrapping = TextWrapping.Wrap
            label.MaxWidth = 300
            item.Content = label
            item.Tag = i
            self.list_elements.Items.Add(item)
        n = len(state.picked_ids)
        self.txt_picked.Text = u"{} losa(s) seleccionada(s).".format(n) if n else u"No hay losas seleccionadas."
        if n:
            active = state.active if state.active in state.picked_ids else state.picked_ids[0]
            for item in self.list_elements.Items:
                if item.Tag == active:
                    self.list_elements.SelectedItem = item

    # -- slab and form -----------------------------------------------------------
    def element_selected(self, sender, args):
        item = self.list_elements.SelectedItem
        if item is None:
            return
        self.state.active = item.Tag
        if item.Tag not in self._models:
            self.txt_status.Text = u"Leyendo la losa y sus apoyos..."
            self._models[item.Tag] = rsl.SlabModel(doc.GetElement(DB.ElementId(item.Tag)))
        self.model = self._models[item.Tag]
        if item.Tag not in self.state.settings:
            self.state.settings[item.Tag] = rsl.read_settings(self.model.element)
        st = self._settings()
        if not st.get("cuts"):
            st["cuts"] = sr.default_cuts(self.model.slab_tris)
        for nav in self.navs.values():
            nav.reset()
        self._fill_form()
        self.redraw()

    def _settings(self):
        return self.state.settings.get(self.state.active)

    def _fill_form(self):
        self._filling = True
        st = self._settings()
        self.txt_cover.Text = u"{:g}".format(float(st.get("cover") or 2.0))
        self.txt_thick.Text = u"{:.2f} m".format(self.model.thickness)
        self.chk_inf.IsChecked = bool(st[sr.INF].get("on", True))
        self.chk_sup.IsChecked = bool(st[sr.SUP].get("on", True))
        for name, face, axis in (("ix", sr.INF, sr.X), ("iy", sr.INF, sr.Y), ("sx", sr.SUP, sr.X), ("sy", sr.SUP, sr.Y)):
            getattr(self, "cbo_{}_d".format(name)).SelectedItem = st[face][axis]["d"]
            getattr(self, "txt_{}_s".format(name)).Text = u"{:g}".format(float(st[face][axis]["s"]))
        self.cbo_mode.SelectedItem = dict(MODES).get(st[sr.SUP].get("mode"), MODES[0][1])
        self.txt_frac.Text = u"{:g}".format(round(1.0 / float(st[sr.SUP].get("frac") or 1.0 / 3.0), 2))
        self.cbo_anchor.SelectedItem = dict(ANCHORS).get(st.get("anchor"), ANCHORS[0][1])
        self.txt_leg.Text = u"" if st.get("leg") is None else u"{:g}".format(float(st["leg"]))
        self._filling = False

    @staticmethod
    def _num(text, default):
        try:
            return float((text or u"").replace(u",", u"."))
        except ValueError:
            return default

    def form_changed(self, sender, args):
        if self._filling or self._settings() is None:
            return
        st = self._settings()
        st["cover"] = max(1.0, self._num(self.txt_cover.Text, st.get("cover") or 2.0))
        st[sr.INF]["on"] = bool(self.chk_inf.IsChecked)
        st[sr.SUP]["on"] = bool(self.chk_sup.IsChecked)
        for name, face, axis in (("ix", sr.INF, sr.X), ("iy", sr.INF, sr.Y), ("sx", sr.SUP, sr.X), ("sy", sr.SUP, sr.Y)):
            st[face][axis]["d"] = getattr(self, "cbo_{}_d".format(name)).SelectedItem or st[face][axis]["d"]
            st[face][axis]["s"] = max(0.05, self._num(getattr(self, "txt_{}_s".format(name)).Text, st[face][axis]["s"]))
        mode = [k for k, n in MODES if n == self.cbo_mode.SelectedItem]
        st[sr.SUP]["mode"] = mode[0] if mode else sr.TOP_BASTONES
        st[sr.SUP]["frac"] = 1.0 / max(1.5, self._num(self.txt_frac.Text, 3.0))
        anchor = [k for k, n in ANCHORS if n == self.cbo_anchor.SelectedItem]
        st["anchor"] = anchor[0] if anchor else sr.ANCHOR_HOOK
        leg = (self.txt_leg.Text or u"").strip()
        st["leg"] = max(0.0, self._num(leg, 0.15)) if leg else None
        self.redraw()

    # -- drawing -------------------------------------------------------------------
    def redraw(self):
        if self.model is None:
            return
        try:
            self._plan, self._warnings = self.model.plan(self._settings(), MM, self.splice)
            sets = sum(len(sr.bar_sets(self._plan.get(k) or [])) for k in sr.LAYERS)
            self.txt_status.Text = u"{} apoyo(s) leído(s); {} conjunto(s) de barras.{}".format(
                len(self.model.supports), sets,
                (u"  Aviso: " + u"; ".join(self._warnings)) if self._warnings else u"")
        except Exception as e:
            self._plan, self._warnings = {}, []
            self.txt_status.Text = u"No se pudo calcular el acero: {}".format(e)
        self._draw_plan()
        self._draw_cut(sr.X)
        self._draw_cut(sr.Y)

    def views_resized(self, sender, args):
        self._draw_plan()
        self._draw_cut(sr.X)
        self._draw_cut(sr.Y)

    def _draw_plan(self):
        canvas = self.canvas_plan
        canvas.Children.Clear()
        m = self.model
        if m is None or canvas.ActualWidth < 10:
            return
        x0, x1, y0, y1 = sr.extent(m.slab_tris)
        fitted = rv.fit_frame(canvas.ActualWidth, canvas.ActualHeight, x0 - 0.6, y0 - 0.6, x1 + 0.6, y1 + 0.6)
        self._fitted[PLAN] = fitted
        frame = self.navs[PLAN].resolve(fitted)
        self._frames[PLAN] = frame
        for tri in m.slab_tris:
            rv._polygon(canvas, frame, tri, brush((240, 243, 246)))
        for s in m.supports:
            for tri in s["tris"]:
                rv._polygon(canvas, frame, tri, brush((200, 203, 208) if s["label"] != u"COLUMNA" else (90, 90, 90)))
        for (face, axis), lines in sorted(self._plan.items()):
            color = brush(COLORS[(face, axis)], 150 if face == sr.SUP else 110)
            for line in lines:
                p = line["pos"]
                for bar in line["bars"]:
                    a, b = ((bar["s0"], p), (bar["s1"], p)) if axis == sr.X else ((p, bar["s0"]), (p, bar["s1"]))
                    rv._line(canvas, frame, a, b, color, 1.4 if face == sr.SUP else 0.8, dash=face == sr.INF and axis == sr.Y)
                    if bar.get("lap_at"):
                        l0, l1 = bar["lap_at"]
                        a, b = ((l0, p), (l1, p)) if axis == sr.X else ((p, l0), (p, l1))
                        rv._line(canvas, frame, a, b, brush((176, 74, 74), 160), 5)
        st = self._settings()
        cuts = st.get("cuts") or {}
        purple = brush((127, 119, 221))
        for axis in (sr.X, sr.Y):
            c = cuts.get(axis)
            if c is None:
                continue
            a, b = ((x0 - 0.4, c), (x1 + 0.4, c)) if axis == sr.X else ((c, y0 - 0.4), (c, y1 + 0.4))
            rv._line(canvas, frame, a, b, purple, 2, dash=True)
            for q in (a, b):
                rv._text(canvas, frame, q[0], q[1], CUT_NAMES[axis].split(u"-")[0], brush=brush((83, 74, 183)), size=13, bold=True)
        rv._text(canvas, frame, x0 - 0.4, y1 + 0.45, u"X →   Y ↑", brush=brush((40, 40, 40)), size=10, anchor="left")

    def _draw_cut(self, axis):
        canvas = self.canvas_cut_x if axis == sr.X else self.canvas_cut_y
        canvas.Children.Clear()
        m = self.model
        if m is None or canvas.ActualWidth < 10 or not self._plan:
            return
        st = self._settings()
        pos = (st.get("cuts") or {}).get(axis)
        if pos is None:
            return
        other = sr.Y if axis == sr.X else sr.X
        title = self.txt_cut_x if axis == sr.X else self.txt_cut_y
        title.Text = u"CORTE {}  (barras en {}, {} = {:.2f} m)".format(CUT_NAMES[axis], axis.upper(),
                                                                        u"y" if axis == sr.X else u"x", pos)
        chords = sr.line_intervals(m.slab_tris, axis, pos)
        supports = sr.line_intervals(m.support_tris, axis, pos)
        if not chords:
            return
        h = m.thickness
        s_lo, s_hi = min(a for a, _ in chords), max(b for _, b in chords)
        k = max(1.0, (s_hi - s_lo) / (h * 14.0))  # heights drawn k times
        u = (s_hi - s_lo) / 40.0
        fitted = rv.fit_frame(canvas.ActualWidth, canvas.ActualHeight, s_lo - 2 * u, -12 * u, s_hi + 2 * u, h * k + 6 * u)
        self._fitted[axis] = fitted
        frame = self.navs[axis].resolve(fitted)
        self._frames[axis] = frame
        for a, b in supports:
            rv._rect(canvas, frame, a, -4.0 * u, b, 0.0, brush((215, 218, 223)), brush((150, 155, 165)))
        for a, b in chords:
            rv._rect(canvas, frame, a, 0.0, b, h * k, brush((238, 240, 243)), brush((60, 60, 60)))
        # the bars along the cut (the line nearest it), the others as dots
        z = sr.layer_z(st, h, MM)
        for face in (sr.INF, sr.SUP):
            color = brush(COLORS[(face, axis)])
            for line in sr.nearest_line(self._plan.get((face, axis)) or [], pos):
                for bar in line["bars"]:
                    pts = [(s, zz * k) for s, zz in bar["path"]]
                    for p, q in zip(pts, pts[1:]):
                        rv._line(canvas, frame, p, q, color, 2.5)
                    if bar.get("lap_at"):
                        l0, l1 = bar["lap_at"]
                        zz = z[(face, axis)] * k
                        rv._rect(canvas, frame, l0, zz - 0.3 * u, l1, zz + 0.3 * u, brush((176, 74, 74), 90))
                        rv._text(canvas, frame, (l0 + l1) / 2.0, zz + (1.0 if face == sr.INF else -1.0) * u,
                                 u"Emp. {:.2f}".format(l1 - l0), brush=brush((176, 74, 74)), size=9)
                    if face == sr.SUP and bar.get("support"):
                        self._bast_cotas(canvas, frame, line, bar, h * k + 1.4 * u, u)
            dots = brush(COLORS[(face, other)])
            for p in sr.crossing(self._plan.get((face, other)) or [], pos):
                self._dot(canvas, frame, p, z[(face, other)] * k, MM[st[face][other]["d"]] / 2000.0, dots)
        # clear spans under it
        for a, b in sr.clear_and_gaps(chords[0], supports)[0] if len(chords) == 1 else []:
            y = -5.5 * u
            rv._line(canvas, frame, (a, y), (b, y), brush((42, 111, 214)), 1)
            rv._text(canvas, frame, (a + b) / 2.0, y - 0.9 * u, u"Ln {:.2f}".format(b - a), brush=brush((42, 111, 214)), size=9)
        for i, (face, ax) in enumerate(((sr.INF, axis), (sr.INF, other), (sr.SUP, axis), (sr.SUP, other))):
            if not (self._plan.get((face, ax))):
                continue
            cfg = st[face][ax]
            rv._text(canvas, frame, s_lo + (i % 2) * 20 * u, -7.8 * u - (i // 2) * 1.4 * u, u"{} {}: Ø{}@{:g} ({:.2f} cm²/m)".format(
                u"Inf" if face == sr.INF else u"Sup", ax.upper(), cfg["d"], float(cfg["s"]),
                sr.steel_per_m(cfg["d"], float(cfg["s"]), MM)), brush=brush(COLORS[(face, ax)]), size=10, anchor="left")

    def _bast_cotas(self, canvas, frame, line, bar, y, u):
        """A baston's dimensions, from the support faces out to its ends."""
        gap = [g for g in line["gaps"] if sr.support_key(u"x" if bar["support"].startswith(u"x") else u"y", g) == bar["support"]]
        if not gap:
            return
        g0, g1 = gap[0]
        dim = brush((150, 150, 30))
        for side, lo, hi in ((0, bar["s0"], g0), (1, g1, bar["s1"])):
            if hi - lo < 0.05:
                continue
            rv._line(canvas, frame, (lo, y), (hi, y), dim, 1)
            for s in (lo, hi):
                rv._line(canvas, frame, (s, y + 0.4 * u), (s, y - 0.4 * u), dim, 1)
            rv._edit_text(canvas, frame, (lo + hi) / 2.0 - 1.2 * u, y + 0.7 * u, u"{:.2f}".format(hi - lo),
                          u"bast|{}|{}|{:.4f}|{:.4f}".format(bar["support"], side, g0 - bar["s0"], bar["s1"] - g1))

    @staticmethod
    def _dot(canvas, frame, x, y, radius_m, fill):
        r = max(2.5, radius_m * frame[0])
        e = Ellipse()
        e.Width = e.Height = 2 * r
        e.Fill = fill
        e.IsHitTestVisible = False
        px, py = rv._px(frame, x, y)
        Canvas.SetLeft(e, px - r)
        Canvas.SetTop(e, py - r)
        canvas.Children.Add(e)

    # -- mouse -----------------------------------------------------------------------
    def _plan_xy(self, args):
        scale, ox, oy = self._frames[PLAN]
        pt = args.GetPosition(self.canvas_plan)
        return (pt.X - ox) / scale, (oy - pt.Y) / scale, scale

    def plan_wheel(self, sender, args):
        fitted = self._fitted.get(PLAN)
        if fitted:
            self.navs[PLAN].wheel(fitted, args.GetPosition(self.canvas_plan), args.Delta)
            self._draw_plan()
        args.Handled = True

    def plan_down(self, sender, args):
        fitted = self._fitted.get(PLAN)
        if fitted is None:
            return
        if args.ChangedButton == MouseButton.Left and PLAN in self._frames:
            x, y, scale = self._plan_xy(args)
            cuts = self._settings().get("cuts") or {}
            best = None
            for axis, c in cuts.items():
                d = abs((y if axis == sr.X else x) - c) * scale
                if d < 8 and (best is None or d < best[0]):
                    best = (d, axis)
            if best:
                self._drag_cut = best[1]
                self.canvas_plan.CaptureMouse()
                return
        if args.ChangedButton in (MouseButton.Middle, MouseButton.Left):
            self.navs[PLAN].start_pan(fitted, args.GetPosition(self.canvas_plan))
            self.canvas_plan.CaptureMouse()

    def plan_move(self, sender, args):
        if self._drag_cut is not None:
            x, y, _ = self._plan_xy(args)
            x0, x1, y0, y1 = sr.extent(self.model.slab_tris)
            v = (max(y0, min(y1, y)) if self._drag_cut == sr.X else max(x0, min(x1, x)))
            self._settings()["cuts"][self._drag_cut] = round(v, 3)
            self._draw_plan()
            return
        if self.navs[PLAN].pan(args.GetPosition(self.canvas_plan)):
            self._draw_plan()

    def plan_up(self, sender, args):
        if self._drag_cut is not None:
            axis = self._drag_cut
            self._drag_cut = None
            self.canvas_plan.ReleaseMouseCapture()
            self.navs[axis].reset()
            self._draw_cut(axis)
            return
        self.navs[PLAN].end_pan()
        self.canvas_plan.ReleaseMouseCapture()

    def plan_fit_click(self, sender, args):
        self.navs[PLAN].reset()
        self._draw_plan()

    def _cut_canvas(self, sender):
        return sender.Tag

    def cut_wheel(self, sender, args):
        axis = sender.Tag
        fitted = self._fitted.get(axis)
        if fitted:
            self.navs[axis].wheel(fitted, args.GetPosition(sender), args.Delta)
            self._draw_cut(axis)
        args.Handled = True

    def cut_down(self, sender, args):
        axis = sender.Tag
        fitted = self._fitted.get(axis)
        if fitted is None or args.ChangedButton != MouseButton.Middle:
            return
        self.navs[axis].start_pan(fitted, args.GetPosition(sender))
        sender.CaptureMouse()

    def cut_up(self, sender, args):
        self.navs[sender.Tag].end_pan()
        sender.ReleaseMouseCapture()

    def cut_move(self, sender, args):
        if self.navs[sender.Tag].pan(args.GetPosition(sender)):
            self._draw_cut(sender.Tag)

    def cut_fit_click(self, sender, args):
        self.navs[sender.Tag].reset()
        self._draw_cut(sender.Tag)

    def cut_left(self, sender, args):
        tag = getattr(args.OriginalSource, "Tag", None)
        if not (isinstance(tag, basestring) and tag.startswith(u"bast|")):
            return
        _, key, side, left, right = tag.split(u"|")
        side, left, right = int(side), float(left), float(right)
        value = forms.ask_for_string(default=u"{:.2f}".format(left if side == 0 else right), title=u"Acero Losa",
                                     prompt=u"Largo del bastón (m) desde la cara del apoyo, {} (vale para todo ese "
                                            u"apoyo; vacío: Ln/{:g}):".format(
                                                u"izquierda" if side == 0 else u"derecha",
                                                round(1.0 / self._settings()[sr.SUP]["frac"], 2)))
        if value is None:
            return
        bast = self._settings().setdefault("bast", {})
        if not value.strip():
            bast.pop(key, None)
        else:
            v = max(0.0, self._num(value, left if side == 0 else right))
            pair = [left, right]
            pair[side] = v
            bast[key] = [round(pair[0], 3), round(pair[1], 3)]
        args.Handled = True
        self.redraw()

    def pick_click(self, sender, args):
        self.action = "pick"
        self.Close()

    def run_click(self, sender, args):
        if not self.state.picked_ids:
            forms.alert(u"Selecciona primero las losas.", title=u"Acero Losa")
            return
        self.action = "run"
        self.Close()


def pick(state):
    try:
        refs = revit.uidoc.Selection.PickObjects(ObjectType.Element, _FloorFilter(),
                                                 u"Selecciona las losas macizas y pulsa Finalizar")
    except OperationCanceledException:
        return
    state.picked_ids = [id_of(r.ElementId) for r in refs]
    state.active = state.picked_ids[0] if state.picked_ids else None


def generate_all(state, models, splice):
    """Save the settings on each slab and (re)generate its bars."""
    bar_types = rc.BarTypes(doc)
    failed, count, warnings = [], 0, []
    t = DB.Transaction(doc, "Acero losa")
    t.Start()
    try:
        rc.ensure_parameters(doc)
        fw_params.ensure_parameters(doc, "Acero", [(rsl.PARAM, True, [FLOORS_BIC], True)])
        for element_id in state.picked_ids:
            floor = doc.GetElement(DB.ElementId(element_id))
            settings = state.settings.get(element_id) or rsl.read_settings(floor)
            rsl.write_settings(floor, settings)
            try:
                model = models.get(element_id) or rsl.SlabModel(floor)
                rc.delete_generated(doc, floor)
                made, found = rsl.generate(model, settings, MM, lambda key: bar_types.pick(key, u"LOSA"), splice,
                                           tag=lambda r: rc._tag(r, floor))
                count += sum(r.Quantity for r, _, _ in made)
                warnings += found
            except Exception as e:
                failed.append(u"Losa id {}: {}".format(element_id, e))
        t.Commit()
    except Exception:
        t.RollBack()
        raise
    return count, failed, warnings


# --- main -------------------------------------------------------------------
if __name__ == "__main__":
    state = State()
    pre = [doc.GetElement(i) for i in revit.uidoc.Selection.GetElementIds()]
    state.picked_ids = [id_of(e.Id) for e in pre if e is not None and isinstance(e, DB.Floor)]
    xaml = os.path.join(SCRIPT_DIR, "AceroForm.xaml")
    models = {}
    while True:
        window = LosaWindow(xaml, state, models)
        window.ShowDialog()
        if window.action == "pick":
            pick(state)
            continue
        break
    if window.action == "run":
        count, failed, warnings = generate_all(state, models, window.splice)
        msg = u"Acero de losa: {} barra(s) creada(s).".format(count)
        if warnings:
            msg += u"\n\nAvisos:\n- " + u"\n- ".join(sorted(set(warnings)))
        if failed:
            msg += u"\n\nNo se pudo generar:\n- " + u"\n- ".join(failed)
        forms.alert(msg, title=u"Acero Losa")
