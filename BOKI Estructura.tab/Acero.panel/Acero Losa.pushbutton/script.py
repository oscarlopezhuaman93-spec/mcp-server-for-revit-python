# -*- coding: utf-8 -*-
"""Acero de losas macizas y aligeradas. Maciza: malla inferior corrida
(empalmada como las vigas), bastones superiores desde la cara de cada apoyo
(o malla superior), ganchos en los bordes. Aligerada: viguetas con su
acero inferior corrido y bastones (Ln/4 extremos, Ln/3 interiores),
temperatura y los ladrillos de techo de la familia del proyecto. Planta y
dos cortes movibles."""

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
          (sr.SUP, sr.X): (210, 60, 60), (sr.SUP, sr.Y): (240, 140, 20),
          (u"temp", sr.X): (42, 111, 214), (u"temp", sr.Y): (42, 111, 214)}
KINDS = [(sr.MACIZA, u"Maciza"), (sr.ALIGERADA, u"Aligerada")]
DIRS = [(None, u"Autom\u00e1tico (luz corta)"), (sr.X, u"X"), (sr.Y, u"Y")]
ENS = [(sr.ENS_NONE, u"Ninguno"), (sr.ENS_FULL, u"Corrido"), (sr.ENS_ALT, u"Alternado")]
BRICK = (196, 120, 80)
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
        self.cbo_kind.ItemsSource = [n for _, n in KINDS]
        self.cbo_dir.ItemsSource = [n for _, n in DIRS]
        self.cbo_ens.ItemsSource = [n for _, n in ENS]
        for name in ("vi", "vs", "t"):
            getattr(self, "cbo_{}_d".format(name)).ItemsSource = BAR_KEYS
        self.bricks = rsl.brick_types(doc)  # as the Properties palette lists them
        self.cbo_brick.ItemsSource = sorted(rsl.brick_types(doc, with_cuts=False))
        self._rib = None
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
            st = rsl.read_settings(self.model.element)
            p = self.model.element.LookupParameter(rsl.PARAM)
            if not (p is not None and (p.AsString() or u"").strip()):
                # first time: the kind from its type name, the brick that fits its depth
                name = doc.GetElement(self.model.element.GetTypeId()).get_Parameter(
                    DB.BuiltInParameter.SYMBOL_NAME_PARAM).AsString().upper()
                st["kind"] = sr.ALIGERADA if u"ALIGERAD" in name else sr.MACIZA
                st["alig"] = {"brick": rsl.default_brick(self.bricks, self.model.thickness), "place": True}
            self.state.settings[item.Tag] = st
        st = self._settings()
        if not st.get("cuts"):
            st["cuts"] = sr.default_cuts(self.model.frame_slab())
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
        kind = st.get("kind") or sr.MACIZA
        self.cbo_kind.SelectedItem = dict(KINDS)[kind]
        rb_ = sr.ribbed(st)
        self.cbo_dir.SelectedItem = dict(DIRS).get(rb_["dir"], DIRS[0][1])
        self.txt_vb.Text = u"{:g}".format(rb_["b"])
        self.txt_vs.Text = u"{:g}".format(rb_["s"])
        self.cbo_brick.SelectedItem = rb_.get("brick")
        self.chk_brick.IsChecked = bool(rb_.get("place", True))
        self.chk_cut.IsChecked = bool(rb_.get("cut", True))
        self.txt_angle.Text = u"" if rb_.get("angle") is None else u"{:g}".format(float(rb_["angle"]))
        self.txt_start.Text = u"{:g}".format(float(rb_.get("start") or 0.0))
        self.cbo_ens.SelectedItem = dict(ENS).get(rb_["ens"]["mode"], ENS[0][1])
        self.txt_ens.Text = u"{:g}".format(float(rb_["ens"]["len"]))
        self.cbo_vi_d.SelectedItem = rb_["inf"]["d"]
        self.cbo_vs_d.SelectedItem = rb_["sup"]["d"]
        self.txt_vend.Text = u"{:g}".format(round(1.0 / rb_["sup"]["end_frac"], 2))
        self.txt_vint.Text = u"{:g}".format(round(1.0 / rb_["sup"]["frac"], 2))
        self.chk_temp.IsChecked = bool(rb_["temp"].get("on", True))
        self.cbo_t_d.SelectedItem = rb_["temp"]["d"]
        self.txt_t_s.Text = u"{:g}".format(float(rb_["temp"]["s"]))
        self._show_kind(kind)
        self._filling = False

    def _show_kind(self, kind):
        from System.Windows import Visibility
        self.panel_maciza.Visibility = Visibility.Visible if kind == sr.MACIZA else Visibility.Collapsed
        self.panel_alig.Visibility = Visibility.Visible if kind == sr.ALIGERADA else Visibility.Collapsed

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
        kind = [k for k, n in KINDS if n == self.cbo_kind.SelectedItem]
        st["kind"] = kind[0] if kind else sr.MACIZA
        a = st.setdefault("alig", {})
        d = [k for k, n in DIRS if n == self.cbo_dir.SelectedItem]
        a["dir"] = d[0] if d else None
        a["b"] = max(0.05, self._num(self.txt_vb.Text, 0.10))
        a["s"] = max(a["b"] + 0.10, self._num(self.txt_vs.Text, 0.40))
        a["brick"] = self.cbo_brick.SelectedItem
        a["place"] = bool(self.chk_brick.IsChecked)
        a["cut"] = bool(self.chk_cut.IsChecked)
        a["angle"] = (self._num(self.txt_angle.Text, 0.0) % 180.0) if (self.txt_angle.Text or u"").strip() else None
        a["start"] = max(0.0, self._num(self.txt_start.Text, 0.0))
        e = [k for k, n in ENS if n == self.cbo_ens.SelectedItem]
        a["ens"] = {"mode": e[0] if e else sr.ENS_NONE, "len": max(0.0, self._num(self.txt_ens.Text, 0.25))}
        a["inf"] = {"d": self.cbo_vi_d.SelectedItem or u'1/2"'}
        a["sup"] = {"d": self.cbo_vs_d.SelectedItem or u'1/2"',
                    "end_frac": 1.0 / max(1.5, self._num(self.txt_vend.Text, 4.0)),
                    "frac": 1.0 / max(1.5, self._num(self.txt_vint.Text, 3.0))}
        a["temp"] = {"on": bool(self.chk_temp.IsChecked), "d": self.cbo_t_d.SelectedItem or u'1/4"',
                     "s": max(0.05, self._num(self.txt_t_s.Text, 0.25))}
        self._show_kind(st["kind"])
        self.redraw()

    # -- drawing -------------------------------------------------------------------
    def redraw(self):
        if self.model is None:
            return
        try:
            st = self._settings()
            lib = rsl.brick_library(doc, st, self.bricks)
            self._plan, self._rib, self._warnings = self.model.plan(st, MM, self.splice, lib)
            sets = sum(len(sr.bar_sets(v or [])) for v in self._plan.values())
            extra = u""
            if self._rib is not None:
                extra = u" Aligerado en {}: {} vigueta(s), {} ladrillo(s){}.".format(
                    self._rib["axis"].upper(), len(self._rib["viguetas"]), len(self._rib["bricks"]),
                    u"" if lib else u" (elige el tipo de ladrillo)")
            self.txt_status.Text = u"{} apoyo(s) le\u00eddo(s); {} conjunto(s) de barras.{}{}".format(
                len(self.model.supports), sets, extra,
                (u"  Aviso: " + u"; ".join(self._warnings)) if self._warnings else u"")
        except Exception as e:
            self._plan, self._rib, self._warnings = {}, None, []
            self.txt_status.Text = u"No se pudo calcular el acero: {}".format(e)
        self._draw_plan()
        self._draw_cut(sr.X)
        self._draw_cut(sr.Y)

    def views_resized(self, sender, args):
        self._draw_plan()
        self._draw_cut(sr.X)
        self._draw_cut(sr.Y)

    def _grids(self):
        """[(name, (x, y), (x, y))] of the project's grid lines over the slab
        (local), cached per slab."""
        m = self.model
        cache = self.__dict__.setdefault("_grid_cache", {})
        key = id_of(m.element.Id)
        if key not in cache:
            x0, x1, y0, y1 = sr.extent(m.slab_tris)
            pad = 1.5
            out = []
            for g in DB.FilteredElementCollector(doc).OfClass(DB.Grid):
                c = g.Curve
                if not isinstance(c, DB.Line):
                    continue
                p0, p1 = m.f.local(c.GetEndPoint(0)), m.f.local(c.GetEndPoint(1))
                (ax, ay), (bx, by) = p0[:2], p1[:2]
                # clip the line to the slab's box (+ pad)
                pts = []
                dx, dy = bx - ax, by - ay
                for t in [0.0, 1.0] + [((v - ax) / dx) for v in (x0 - pad, x1 + pad) if abs(dx) > 1e-9] + \
                        [((v - ay) / dy) for v in (y0 - pad, y1 + pad) if abs(dy) > 1e-9]:
                    if 0.0 <= t <= 1.0:
                        x, y = ax + dx * t, ay + dy * t
                        if x0 - pad - 1e-6 <= x <= x1 + pad + 1e-6 and y0 - pad - 1e-6 <= y <= y1 + pad + 1e-6:
                            pts.append((t, x, y))
                if len(pts) >= 2:
                    pts.sort()
                    out.append((g.Name, pts[0][1:], pts[-1][1:]))
            cache[key] = out
        return cache[key]

    def _draw_plan(self):
        canvas = self.canvas_plan
        canvas.Children.Clear()
        m = self.model
        if m is None or canvas.ActualWidth < 10:
            return
        x0, x1, y0, y1 = sr.extent(m.slab_tris)
        fitted = rv.fit_frame(canvas.ActualWidth, canvas.ActualHeight, x0 - 1.4, y0 - 1.4, x1 + 1.4, y1 + 1.4)
        self._fitted[PLAN] = fitted
        frame = self.navs[PLAN].resolve(fitted)
        self._frames[PLAN] = frame
        ang = m.angle

        def T(x, y):  # the plan's frame (turned with the bricks) back to the slab's
            return sr.unrotate(x, y, ang)
        # the grids, behind
        gray = brush((150, 150, 160))
        for name, a, b in self._grids():
            rv._line(canvas, frame, a, b, gray, 1, dash=True)
            for q in (a, b):
                self._dot(canvas, frame, q[0], q[1], 0.0, brush((255, 255, 255)))
                rv._text(canvas, frame, q[0], q[1], name, brush=brush((60, 60, 80)), size=11, bold=True)
        for tri in m.slab_tris:
            rv._polygon(canvas, frame, tri, brush((240, 243, 246)))
        for s in m.supports:
            for tri in s["tris"]:
                rv._polygon(canvas, frame, tri, brush((200, 203, 208) if s["label"] != u"COLUMNA" else (90, 90, 90)))
        rib = getattr(self, "_rib", None)
        if rib is not None:
            fill, edge = brush(BRICK, 70), brush(BRICK, 160)
            for bk in rib["bricks"]:
                corners = [(bk["s0"], bk["t0"]), (bk["s1"], bk["t0"]), (bk["s1"], bk["t1"]), (bk["s0"], bk["t1"])]
                if rib["axis"] == sr.Y:
                    corners = [(t, s_) for s_, t in corners]
                rv._polygon(canvas, frame, [T(*q) for q in corners], fill, edge)
        for (face, axis), lines in sorted(self._plan.items()):
            color = brush(COLORS[(face, axis)], 150 if face == sr.SUP else 110)
            for line in lines:
                p = line["pos"]
                for bar in line["bars"]:
                    a, b = ((bar["s0"], p), (bar["s1"], p)) if axis == sr.X else ((p, bar["s0"]), (p, bar["s1"]))
                    rv._line(canvas, frame, T(*a), T(*b), color, 1.4 if face == sr.SUP else 0.8,
                             dash=face == sr.INF and axis == sr.Y)
                    if bar.get("lap_at"):
                        l0, l1 = bar["lap_at"]
                        a, b = ((l0, p), (l1, p)) if axis == sr.X else ((p, l0), (p, l1))
                        rv._line(canvas, frame, T(*a), T(*b), brush((176, 74, 74), 160), 5)
        st = self._settings()
        fx0, fx1, fy0, fy1 = sr.extent(m.frame_slab())
        cuts = st.get("cuts") or {}
        purple = brush((127, 119, 221))
        for axis in (sr.X, sr.Y):
            c = cuts.get(axis)
            if c is None:
                continue
            a, b = ((fx0 - 0.4, c), (fx1 + 0.4, c)) if axis == sr.X else ((c, fy0 - 0.4), (c, fy1 + 0.4))
            a, b = T(*a), T(*b)
            rv._line(canvas, frame, a, b, purple, 2, dash=True)
            for q in (a, b):
                rv._text(canvas, frame, q[0], q[1], CUT_NAMES[axis].split(u"-")[0], brush=brush((83, 74, 183)), size=13, bold=True)
        if rib is not None:  # the viguetas' direction
            cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
            ux, uy = T(1.0, 0.0) if rib["axis"] == sr.X else T(0.0, 1.0)
            L = 0.22 * min(x1 - x0, y1 - y0)
            a, b = (cx - ux * L, cy - uy * L), (cx + ux * L, cy + uy * L)
            dark = brush((200, 60, 20))
            rv._line(canvas, frame, a, b, dark, 4)
            for end, sgn in ((b, 1.0), (a, -1.0)):
                for side in (0.5, -0.5):
                    hx = end[0] - sgn * ux * 0.25 * L + side * (-uy) * 0.25 * L
                    hy = end[1] - sgn * uy * 0.25 * L + side * ux * 0.25 * L
                    rv._line(canvas, frame, end, (hx, hy), dark, 4)
            rv._text(canvas, frame, cx, cy + 0.15 * L, u"Viguetas  {:.1f}\u00b0".format(
                math.degrees(math.atan2(uy, ux)) % 180.0), brush=dark, size=12, bold=True)
        rv._text(canvas, frame, x0 - 1.2, y1 + 1.2, u"X \u2192   Y \u2191", brush=brush((40, 40, 40)), size=10, anchor="left")

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
        chords = sr.line_intervals(m.frame_slab(), axis, pos)
        supports = sr.line_intervals(m.frame_supports(), axis, pos)
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
        # the bricks cut (a ribbed slab): hanging from the top, 5 cm under it
        rib = getattr(self, "_rib", None)
        if rib is not None:
            top = h - sr.ribbed(st)["top"]
            fill, edge = brush(BRICK, 90), brush(BRICK, 200)
            for bk in rib["bricks"]:
                if rib["axis"] == axis and bk["t0"] <= pos <= bk["t1"]:
                    rv._rect(canvas, frame, bk["s0"], 0.0, bk["s1"], top * k, fill, edge)
                elif rib["axis"] != axis and bk["s0"] <= pos <= bk["s1"]:
                    rv._rect(canvas, frame, bk["t0"], 0.0, bk["t1"], top * k, fill, edge)
        # the bars along the cut (the line nearest it), the others as dots
        for (face, ax), layer in sorted(self._plan.items()):
            if not layer:
                continue
            if ax == axis:
                color = brush(COLORS[(face, ax)])
                for line in sr.nearest_line(layer, pos):
                    for bar in line["bars"]:
                        pts = [(s_, zz * k) for s_, zz in bar["path"]]
                        for p, q in zip(pts, pts[1:]):
                            rv._line(canvas, frame, p, q, color, 2.5)
                        if bar.get("lap_at"):
                            l0, l1 = bar["lap_at"]
                            zz = self._bar_z(bar) * k
                            rv._rect(canvas, frame, l0, zz - 0.3 * u, l1, zz + 0.3 * u, brush((176, 74, 74), 90))
                            rv._text(canvas, frame, (l0 + l1) / 2.0, zz + (1.0 if face != sr.SUP else -1.0) * u,
                                     u"Emp. {:.2f}".format(l1 - l0), brush=brush((176, 74, 74)), size=9)
                        if face == sr.SUP and bar.get("support"):
                            self._bast_cotas(canvas, frame, line, bar, h * k + 1.4 * u, u)
            else:
                dots = brush(COLORS[(face, ax)])
                bar0 = [b_ for l_ in layer for b_ in l_["bars"]]
                if not bar0:
                    continue
                zz = self._bar_z(bar0[0]) * k
                r = MM[bar0[0]["key"]] / 2000.0
                for p in sr.crossing(layer, pos):
                    self._dot(canvas, frame, p, zz, r, dots)
        # clear spans under it
        for a, b in sr.clear_and_gaps(chords[0], supports)[0] if len(chords) == 1 else []:
            y = -5.5 * u
            rv._line(canvas, frame, (a, y), (b, y), brush((42, 111, 214)), 1)
            rv._text(canvas, frame, (a + b) / 2.0, y - 0.9 * u, u"Ln {:.2f}".format(b - a), brush=brush((42, 111, 214)), size=9)
        for i, (text, key) in enumerate(self._legend(st)):
            rv._text(canvas, frame, s_lo + (i % 2) * 20 * u, -7.8 * u - (i // 2) * 1.4 * u, text,
                     brush=brush(COLORS[key]), size=10, anchor="left")

    def _legend(self, st):
        """[(text, color key)] of the steel shown."""
        rib = getattr(self, "_rib", None)
        if rib is not None:
            r = sr.ribbed(st)
            a = rib["axis"]
            other = sr.Y if a == sr.X else sr.X
            out = [(u"Vigueta inf.: 1\u00d8{} corrido".format(r["inf"]["d"]), (sr.INF, a)),
                   (u"Bastones: 1\u00d8{} (Ln/{:g} ext., Ln/{:g} int.)".format(
                       r["sup"]["d"], round(1 / r["sup"]["end_frac"], 2), round(1 / r["sup"]["frac"], 2)), (sr.SUP, a))]
            if r["temp"].get("on", True):
                out.append((u"Temperatura: \u00d8{}@{:g}".format(r["temp"]["d"], float(r["temp"]["s"])), (u"temp", other)))
            return out
        out = []
        for face, ax in sr.LAYERS:
            if not self._plan.get((face, ax)):
                continue
            cfg = st[face][ax]
            out.append((u"{} {}: \u00d8{}@{:g} ({:.2f} cm\u00b2/m)".format(
                u"Inf" if face == sr.INF else u"Sup", ax.upper(), cfg["d"], float(cfg["s"]),
                sr.steel_per_m(cfg["d"], float(cfg["s"]), MM)), (face, ax)))
        return out

    @staticmethod
    def _bar_z(bar):
        """The height of a bar's run (its longest flat stretch)."""
        best = None
        for (s0, z0), (s1, z1) in zip(bar["path"], bar["path"][1:]):
            if abs(z1 - z0) < 1e-6 and (best is None or abs(s1 - s0) > best[0]):
                best = (abs(s1 - s0), z0)
        return best[1] if best else bar["path"][0][1]

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
        if args.ChangedButton == MouseButton.Left and PLAN in self._frames and self.tool_dir.IsChecked:
            x, y, scale = self._plan_xy(args)
            edges = sr.outline_edges(self.model.slab_tris)
            for sp in self.model.supports:
                edges += sr.outline_edges(sp["tris"])
            hit = sr.nearest_edge_angle(edges, x, y)
            if hit and hit[0] * scale < 25:
                # the viguetas rest on what was clicked: perpendicular to it
                a = self._settings().setdefault("alig", {})
                a["angle"] = round((hit[1] + 90.0) % 180.0, 2)
                a["dir"] = sr.X
                self.tool_dir.IsChecked = False
                self._fill_form()
                self.redraw()
            return
        if args.ChangedButton == MouseButton.Left and PLAN in self._frames:
            x, y, scale = self._plan_xy(args)
            x, y = sr.unrotate(x, y, -self.model.angle)  # into the plan's frame
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
            x, y = sr.unrotate(x, y, -self.model.angle)
            x0, x1, y0, y1 = sr.extent(self.model.frame_slab())
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

    def turn90_click(self, sender, args):
        if self._settings() is None:
            return
        a = self._settings().setdefault("alig", {})
        current = a.get("angle")
        if current is None:
            current = sr.dominant_angle(self.model.slab_tris)
        a["angle"] = round((float(current) + 90.0) % 180.0, 2)
        a["dir"] = sr.X
        self._fill_form()
        self.redraw()

    def auto_dir_click(self, sender, args):
        if self._settings() is None:
            return
        a = self._settings().setdefault("alig", {})
        a["angle"] = None
        a["dir"] = None
        self._fill_form()
        self.redraw()

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
                                     prompt=u"Largo del bast\u00f3n (m) desde la cara del apoyo, {} (vale para todo ese "
                                            u"apoyo; vac\u00edo: el autom\u00e1tico):".format(
                                                u"izquierda" if side == 0 else u"derecha"))
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

    def create_slabs_click(self, sender, args):
        self.action = "create_slabs"
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
                                                 u"Selecciona las losas y pulsa Finalizar")
    except OperationCanceledException:
        return
    state.picked_ids = [id_of(r.ElementId) for r in refs]
    state.active = state.picked_ids[0] if state.picked_ids else None


def generate_all(state, models, splice):
    """Save the settings on each slab and (re)generate its bars."""
    bar_types = rc.BarTypes(doc)
    failed, count, warnings, bricks = [], 0, [], 0
    t = DB.Transaction(doc, "Acero losa")
    failures = rsl.DropFailing()  # a brick or bar Revit refuses goes alone, not everything
    opts = t.GetFailureHandlingOptions()
    opts.SetFailuresPreprocessor(failures)
    opts.SetForcedModalHandling(False)
    t.SetFailureHandlingOptions(opts)
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
                if settings.get("kind") == sr.ALIGERADA:
                    rsl.delete_bricks(doc, floor)
                    if sr.ribbed(settings).get("place", True):
                        types = rsl.brick_types(doc)
                        lib = rsl.brick_library(doc, settings, types)
                        if lib:
                            _, rib, _ = model.plan(settings, MM, splice, lib)
                            bricks += rsl.place_bricks(model, rib, types)
                        else:
                            warnings.append(u"no hay tipo de ladrillo elegido: no se colocaron ladrillos")
            except Exception as e:
                failed.append(u"Losa id {}: {}".format(element_id, e))
        t.Commit()
    except Exception:
        t.RollBack()
        raise
    return count, failed, warnings, bricks


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
        if window.action == "create_slabs":
            # the Crear Losas button's own script, then back here with what it made
            crear = os.path.join(EXT_ROOT, u"BOKI Estructura.tab", u"Modelado.panel", u"Crear Losas.pushbutton", u"script.py")
            import io
            code = io.open(crear, encoding="utf-8").read()
            exec(compile(code, crear, "exec"), {"__name__": "__main__", "__file__": crear})
            new = [doc.GetElement(i) for i in revit.uidoc.Selection.GetElementIds()]
            new = [id_of(e.Id) for e in new if e is not None and isinstance(e, DB.Floor)]
            if new:
                state.picked_ids = new
                state.active = new[0]
            continue
        break
    if window.action == "run":
        count, failed, warnings, bricks = generate_all(state, models, window.splice)
        msg = u"Acero de losa: {} barra(s) creada(s).".format(count)
        if bricks:
            msg += u"\nLadrillos de techo colocados: {}.".format(bricks)
        if warnings:
            msg += u"\n\nAvisos:\n- " + u"\n- ".join(sorted(set(warnings)))
        if failed:
            msg += u"\n\nNo se pudo generar:\n- " + u"\n- ".join(failed)
        forms.alert(msg, title=u"Acero Losa")
