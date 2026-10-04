# -*- coding: utf-8 -*-
"""Acero de escaleras moldeadas in situ: acero inferior y superior
(bastones) a lo largo de cada tramo, temperatura y pasos transversales,
con anclajes en lo que toca (cimiento, muro, viga, losa) editables en el
corte de cada tramo."""

__title__ = "Acero\nEscalera"
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

import formwork_params as fw_params
import rebar_spec as rs
import rebar_columns as rc
import rebar_foundation as rf
import rebar_stairs as rst
import rebar_views as rv
import utils as fw_utils

reload(fw_utils)
reload(fw_params)
reload(rs)
reload(rc)
reload(rf)
reload(rst)
reload(rv)

from pyrevit import revit, DB, forms, script
from Autodesk.Revit.Exceptions import OperationCanceledException
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType
from Autodesk.Revit.DB.Architecture import Stairs
from System.Windows import Thickness, FontWeights, Size, TextWrapping
from System.Windows.Controls import CheckBox, ListBoxItem, TextBlock, Canvas
from System.Windows.Input import MouseButton, Cursors
from System.Windows.Media import Color, Colors, SolidColorBrush
from System.Windows.Media.Media3D import (AmbientLight, DiffuseMaterial, DirectionalLight, GeometryModel3D,
                                          MeshGeometry3D, Model3DGroup, Point3D, Vector3D)

doc = revit.doc
STAIRS_BIC = DB.BuiltInCategory.OST_Stairs
PARAM = "EA_Esc_Acero"  # JSON settings on the stair instance (rst.default_settings)
BAR_KEYS = [k for k in rs.bar_diameter_keys() if 6 <= rs.BAR_DIAMETERS_MM[k] <= 25]
AUTO_TYPE = u"(automatico)"
MM = rs.BAR_DIAMETERS_MM
PLAN, CUT = u"planta", u"corte"
COLORS = {rst.INF: (200, 50, 40), rst.SUP: (31, 78, 160), rst.TEMP: (30, 150, 80), rst.STEP: (230, 130, 20)}


def id_of(element_id):
    return fw_utils.element_id_value(element_id)


def brush(rgb, alpha=255):
    return SolidColorBrush(Color.FromArgb(alpha, rgb[0], rgb[1], rgb[2]))


def read_settings(stairs):
    settings = rst.default_settings()
    p = stairs.LookupParameter(PARAM)
    try:
        saved = json.loads(p.AsString() or u"{}") if p is not None else {}
    except ValueError:
        saved = {}
    for k, v in saved.items():
        if isinstance(v, dict) and isinstance(settings.get(k), dict) and k != "ends":
            settings[k].update(v)
        else:
            settings[k] = v
    return settings


class _StairsFilter(ISelectionFilter):
    def AllowElement(self, element):
        return isinstance(element, Stairs)

    def AllowReference(self, reference, point):
        return False


class State(object):
    def __init__(self):
        self.picked_ids = []
        self.active = None
        self.settings = {}  # stair id -> settings being edited
        self.tramo = 0


class EscaleraWindow(forms.WPFWindow):
    def __init__(self, xaml_file_path, state, models=None):
        forms.WPFWindow.__init__(self, xaml_file_path)
        self.state = state
        self.action = None
        self.model = None
        self._models = models if models is not None else {}  # read stairs, kept across pick rounds
        self.scene = rv.Scene3D(self.view3d)
        self.navs = {PLAN: rv.Nav2D(), CUT: rv.Nav2D()}
        self._fitted = {}
        self._frames = {}
        self._plans = {}
        self._filling = True
        self.type_keys = rc.bar_type_keys(doc)
        names = [AUTO_TYPE] + sorted(self.type_keys)
        for g in ("inf", "sup", "temp", "step"):
            getattr(self, "cbo_{}_d".format(g)).ItemsSource = BAR_KEYS
            getattr(self, "cbo_{}_t".format(g)).ItemsSource = names
        self._filling = False
        for i in state.picked_ids:
            e = doc.GetElement(DB.ElementId(i))
            item = ListBoxItem()
            label = TextBlock()
            label.Text = u"{} - {}  (id {})".format(e.Name, doc.GetElement(e.GetTypeId()).get_Parameter(
                DB.BuiltInParameter.SYMBOL_NAME_PARAM).AsString(), i)
            label.TextWrapping = TextWrapping.Wrap
            label.MaxWidth = 320
            item.Content = label
            item.Tag = i
            self.list_elements.Items.Add(item)
        n = len(state.picked_ids)
        self.txt_picked.Text = (u"{} escalera(s) seleccionada(s).".format(n) if n
                                else u"No hay escaleras seleccionadas.")
        if n:
            active = state.active if state.active in state.picked_ids else state.picked_ids[0]
            for item in self.list_elements.Items:
                if item.Tag == active:
                    self.list_elements.SelectedItem = item

    # -- element ---------------------------------------------------------
    def element_selected(self, sender, args):
        item = self.list_elements.SelectedItem
        if item is None:
            return
        self.state.active = item.Tag
        if item.Tag not in self._models:
            self.txt_status.Text = u"Leyendo la escalera..."
            self._models[item.Tag] = rst.StairModel(doc.GetElement(DB.ElementId(item.Tag)))
        self.model = self._models[item.Tag]
        if item.Tag not in self.state.settings:
            self.state.settings[item.Tag] = read_settings(self.model.element)
        self.state.tramo = min(self.state.tramo, max(0, len(self.model.tramos) - 1))
        self._fill_form()
        self.scene._extent = None
        for nav in self.navs.values():
            nav.reset()
        self._neighbors3d = None
        self.txt_status.Text = u"{} tramo(s); toca: {}.".format(
            len(self.model.tramos), u", ".join(sorted(set(l for l, _ in self.model.touching))) or u"nada")
        self.redraw()

    def _settings(self):
        return self.state.settings.get(self.state.active)

    def _fill_form(self):
        self._filling = True
        st = self._settings()
        self.txt_cover.Text = u"{:g}".format(float(st.get("cover") or 2.5))
        self.txt_waist.Text = u"{:.2f} m".format(self.model.thickness)
        for g, key in (("inf", rst.INF), ("sup", rst.SUP), ("temp", rst.TEMP), ("step", rst.STEP)):
            s = st[key]
            getattr(self, "chk_" + g).IsChecked = bool(s.get("on"))
            getattr(self, "cbo_{}_d".format(g)).SelectedItem = s.get("d")
            name = s.get("t") or u""
            getattr(self, "cbo_{}_t".format(g)).SelectedItem = name if name in self.type_keys else AUTO_TYPE
            box = getattr(self, "txt_{}_s".format(g), None)
            if box is not None:
                box.Text = u"{:g}".format(float(s.get("s") or 0.2))
        neg = st[rst.SUP].get("neg")
        self.txt_sup_neg.Text = u"" if neg in (None, u"") else u"{:g}".format(float(neg))
        self.panel_tramos.Children.Clear()
        off = set(st.get("off") or [])
        for t in self.model.tramos:
            cb = CheckBox()
            cb.Content = u"T{}  ({:.2f} m, ancho {:.2f} m)".format(t.index + 1, t.length, t.t1 - t.t0)
            cb.IsChecked = t.index not in off
            cb.Tag = t.index
            cb.Margin = Thickness(0, 2, 0, 2)
            cb.Click += self.tramo_toggled
            cb.MouseDoubleClick += self.tramo_pick
            cb.PreviewMouseRightButtonDown += self.tramo_pick
            self.panel_tramos.Children.Add(cb)
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
        st["cover"] = max(1.0, self._num(self.txt_cover.Text, st.get("cover") or 2.5))
        for g, key in (("inf", rst.INF), ("sup", rst.SUP), ("temp", rst.TEMP), ("step", rst.STEP)):
            s = st[key]
            s["on"] = bool(getattr(self, "chk_" + g).IsChecked)
            s["d"] = getattr(self, "cbo_{}_d".format(g)).SelectedItem or s["d"]
            box = getattr(self, "txt_{}_s".format(g), None)
            if box is not None:
                s["s"] = max(0.05, self._num(box.Text, s.get("s") or 0.2))
        neg = (self.txt_sup_neg.Text or u"").strip()
        st[rst.SUP]["neg"] = max(0.2, self._num(neg, 0.6)) if neg else None
        self.redraw()

    def type_changed(self, sender, args):
        if self._filling or self._settings() is None:
            return
        st = self._settings()
        for g, key in (("inf", rst.INF), ("sup", rst.SUP), ("temp", rst.TEMP), ("step", rst.STEP)):
            combo = getattr(self, "cbo_{}_t".format(g))
            name = combo.SelectedItem
            st[key]["t"] = u"" if not name or name == AUTO_TYPE else name
            k = self.type_keys.get(st[key]["t"])
            if combo is sender and k in BAR_KEYS:
                getattr(self, "cbo_{}_d".format(g)).SelectedItem = k  # its handler redraws
        self.redraw()

    def tramo_toggled(self, sender, args):
        st = self._settings()
        off = set(st.get("off") or [])
        if sender.IsChecked:
            off.discard(sender.Tag)
        else:
            off.add(sender.Tag)
        st["off"] = sorted(off)
        self.state.tramo = sender.Tag
        self.redraw()

    def tramo_pick(self, sender, args):
        self.state.tramo = sender.Tag
        self.navs[CUT].reset()
        self.redraw()

    # -- drawing -----------------------------------------------------------
    def _plan_all(self):
        st = self._settings()
        return rst.plan_all(self.model, st, MM) if st else {}

    def redraw(self):
        if self.model is None:
            return
        try:
            self._plans = self._plan_all()
        except Exception as e:
            self._plans = {}
            self.txt_status.Text = u"No se pudo calcular el acero: {}".format(e)
        self._draw_plan()
        self._draw_cut()
        self._build_3d()

    def views_resized(self, sender, args):
        self._draw_plan()
        self._draw_cut()

    def _positions(self, t, group):
        st = self._settings()
        cover = float(st.get("cover") or 2.5) / 100.0
        d = MM[st[group]["d"]] / 1000.0
        return rf.bar_positions(t.t0 + cover + d / 2.0, t.t1 - cover - d / 2.0, float(st[group].get("s") or 0.2))

    def _draw_plan(self):
        canvas = self.canvas_plan
        canvas.Children.Clear()
        m = self.model
        if m is None or canvas.ActualWidth < 10:
            return
        x0, x1, y0, y1, _, _ = m.f.extent
        fitted = rv.fit_frame(canvas.ActualWidth, canvas.ActualHeight, x0 - 0.4, y0 - 0.4, x1 + 0.4, y1 + 0.4)
        self._fitted[PLAN] = fitted
        frame = self.navs[PLAN].resolve(fitted)
        self._frames[PLAN] = frame
        gray = brush((225, 228, 232))
        for face in m.f.faces:
            if face.normal[2] > 0.3:
                for tri in face.triangles:
                    rv._polygon(canvas, frame, [(p[0], p[1]) for p in tri], gray)
        edge = brush((150, 155, 165))
        for face in m.f.faces:
            if abs(face.normal[2]) < 0.3:  # risers and sides: their top edge in plan
                for tri in face.triangles:
                    for k in range(3):
                        a, b = tri[k], tri[(k + 1) % 3]
                        if abs(a[2] - b[2]) < 1e-3 and (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 > 1e-4:
                            rv._line(canvas, frame, (a[0], a[1]), (b[0], b[1]), edge, 1)
        st = self._settings()
        off = set(st.get("off") or [])
        for t in m.tramos:
            chosen = t.index == self.state.tramo
            plan = self._plans.get(t.index)
            if chosen and plan:
                # the longitudinal bars of the chosen tramo, seen from above
                for group in (rst.INF, rst.SUP):
                    for bar in plan[group]:
                        ss = [q[0] for q in bar["body"]]
                        for pos in self._positions(t, group):
                            rv._line(canvas, frame, t.plan(min(ss), pos), t.plan(max(ss), pos), brush(COLORS[group], 150), 1)
            color = (200, 50, 40) if chosen else ((160, 160, 160) if t.index in off else (40, 40, 40))
            a, b = t.plan(0.0, 0.0), t.plan(t.length, 0.0)
            rv._line(canvas, frame, a, b, brush(color), 3 if chosen else 2, dash=t.index in off)
            # arrow head (going up)
            hx, hy = t.plan(t.length - 0.15, 0.08), t.plan(t.length - 0.15, -0.08)
            rv._line(canvas, frame, b, hx, brush(color), 2)
            rv._line(canvas, frame, b, hy, brush(color), 2)
            mx, my = t.plan(t.length / 2.0, 0.0)
            rv._text(canvas, frame, mx + 0.12, my + 0.12, u"T{}".format(t.index + 1), brush=brush(color), size=13, bold=True)
        rv._text(canvas, frame, x0 - 0.2, y1 + 0.25, u"X →   Y ↑", brush=brush((40, 40, 40)), size=10, anchor="left")

    def _edit_label(self, canvas, frame, x, y, text, tag, anchor="left"):
        tb = TextBlock()
        tb.Text = text + u"  ✎"
        tb.FontSize = 10
        tb.FontWeight = FontWeights.Bold
        tb.Foreground = brush((31, 78, 160))
        tb.Background = brush((255, 246, 200))
        tb.Tag = tag
        tb.Cursor = Cursors.Hand
        tb.ToolTip = u"Clic para editar"
        tb.Measure(Size(1e4, 1e4))
        px, py = rv._px(frame, x, y)
        if anchor == "right":
            px -= tb.DesiredSize.Width
        Canvas.SetLeft(tb, px)
        Canvas.SetTop(tb, py - tb.DesiredSize.Height / 2.0)
        canvas.Children.Add(tb)

    def _draw_cut(self):
        canvas = self.canvas_cut
        canvas.Children.Clear()
        m = self.model
        if m is None or canvas.ActualWidth < 10 or not m.tramos:
            return
        t = m.tramos[self.state.tramo]
        self.txt_cut_title.Text = u"CORTE DEL TRAMO T{} (por su eje, mirando hacia su izquierda; sube hacia la derecha)".format(
            t.index + 1)
        if not t.profile:
            return
        ss = [p[0] for p in t.profile]
        zs = [p[1] for p in t.profile]
        fitted = rv.fit_frame(canvas.ActualWidth, canvas.ActualHeight, min(ss) - 0.6, min(zs) - 0.6,
                              max(ss) + 0.6, max(zs) + 0.4)
        self._fitted[CUT] = fitted
        frame = self.navs[CUT].resolve(fitted)
        self._frames[CUT] = frame
        labelled = set()
        lo_s, hi_s, lo_z, hi_z = min(ss) - 0.6, max(ss) + 0.6, min(zs) - 0.6, max(zs) + 0.4
        for label, pts in t.neighbors:
            # only what lies around the tramo (a wall may run far)
            clipped = [(max(lo_s, min(hi_s, s)), max(lo_z, min(hi_z, z))) for s, z in pts]
            rv._polygon(canvas, frame, clipped, brush((205, 210, 218)))
            for i in range(len(clipped)):
                rv._line(canvas, frame, clipped[i], clipped[(i + 1) % len(clipped)], brush((140, 145, 155)), 1)
            cs = sum(q[0] for q in clipped) / len(clipped)
            cz = sum(q[1] for q in clipped) / len(clipped)
            if label not in labelled:
                labelled.add(label)
                cz -= 0.16 * (len(labelled) - 1)  # one name under the other where they overlap
                rv._text(canvas, frame, cs, cz, label, brush=brush((110, 115, 125)), size=10, bold=True)
        rv._polygon(canvas, frame, t.profile, brush((228, 231, 235)))
        n = len(t.profile)
        for i in range(n):
            rv._line(canvas, frame, t.profile[i], t.profile[(i + 1) % n], brush((60, 60, 60)), 2)
        plan = self._plans.get(t.index)
        if not plan:
            rv._text(canvas, frame, (min(ss) + max(ss)) / 2.0, max(zs) + 0.2, u"Tramo sin acero", size=12)
            return
        st = self._settings()
        for group in (rst.INF, rst.SUP):
            for bar in plan[group]:
                path = bar["path"]
                for a, b in zip(path, path[1:]):
                    rv._line(canvas, frame, a, b, brush(COLORS[group]), 2.5)
        for group in (rst.TEMP, rst.STEP):
            d = MM[st[group]["d"]] / 1000.0
            for item in plan[group]:
                s, z = item["at"]
                r = max(2.5, d * frame[0] / 2.0)
                from System.Windows.Shapes import Ellipse
                e = Ellipse()
                e.Width = e.Height = 2 * r
                e.Fill = brush(COLORS[group])
                e.IsHitTestVisible = False
                px, py = rv._px(frame, s, z)
                Canvas.SetLeft(e, px - r)
                Canvas.SetTop(e, py - r)
                canvas.Children.Add(e)
        # the ends: what each bar end meets, editable
        shown = set()
        for group in (rst.INF, rst.SUP):
            bars = plan[group]
            for k, bar in enumerate(bars):
                body = bar["body"]
                for which, (pt, _) in zip(("start", "end"), rst.ends_of(body)):
                    tag = u"{}:{}:{}".format(t.index, group, which)
                    # the stair's ends only: the first bar's start, the last one's end
                    real = (which == "start" and k == 0) or (which == "end" and k == len(bars) - 1)
                    if not real or tag not in plan["ends"] or tag in shown:
                        continue
                    shown.add(tag)
                    kind, label, anc, leg = plan["ends"][tag]
                    what = {"down": u"en {}".format(label), "beyond": u"en {}".format(label), "free": u"libre"}[kind]
                    text = u"{} {}: anclaje {:.2f}, pata {:.2f}".format(
                        u"Inf" if group == rst.INF else u"Sup", what, anc, leg)
                    dz = -0.18 if group == rst.INF else 0.18
                    self._edit_label(canvas, frame, pt[0], pt[1] + dz, text, u"end:" + tag,
                                     anchor="left" if which == "start" else "right")
        if plan.get("neg") is not None and plan[rst.SUP]:
            body = plan[rst.SUP][0]["body"]
            self._edit_label(canvas, frame, body[0][0], body[0][1] + 0.4,
                             u"Bastón {:.2f} m".format(plan["neg"]), u"neg")

    def _build_3d(self):
        m = self.model
        group = Model3DGroup()
        group.Children.Add(AmbientLight(Color.FromRgb(110, 110, 110)))
        group.Children.Add(DirectionalLight(Colors.White, Vector3D(-0.6, -0.8, -1.0)))
        group.Children.Add(DirectionalLight(Color.FromRgb(120, 120, 120), Vector3D(0.7, 0.5, 0.3)))
        if m is None:
            self.scene.visual.Content = group
            return

        def add_triangles(triangles, color):
            mesh = MeshGeometry3D()
            for tri in triangles:
                base = mesh.Positions.Count
                for x, y, z in tri:
                    mesh.Positions.Add(Point3D(x, y, z))
                for k in range(3):
                    mesh.TriangleIndices.Add(base + k)
            material = DiffuseMaterial(SolidColorBrush(color))
            model = GeometryModel3D(mesh, material)
            model.BackMaterial = material
            group.Children.Add(model)

        st = self._settings()
        cover = float(st.get("cover") or 2.5) / 100.0
        meshes = {}
        for t in m.tramos:
            plan = self._plans.get(t.index)
            if not plan:
                continue
            for g in (rst.INF, rst.SUP):
                mesh = meshes.setdefault(g, MeshGeometry3D())
                r = MM[st[g]["d"]] / 2000.0
                for bar in plan[g]:
                    for pos in self._positions(t, g):
                        pts = [t.local3d(s, pos, z) for s, z in bar["path"]]
                        for a, b in zip(pts, pts[1:]):
                            rv._add_tube(mesh, a, b, r, 6)
            for g in (rst.TEMP, rst.STEP):
                mesh = meshes.setdefault(g, MeshGeometry3D())
                d = MM[st[g]["d"]] / 1000.0
                r = d / 2.0
                for item in plan[g]:
                    s, z = item["at"]
                    span = m.span_at(t, s, z)
                    if span is None or span[1] - span[0] < 2 * cover + 0.1:
                        continue
                    rv._add_tube(mesh, t.local3d(s, span[0] + cover + r, z), t.local3d(s, span[1] - cover - r, z), r, 6)
        for g, mesh in meshes.items():
            if mesh.Positions.Count:
                c = COLORS[g]
                group.Children.Add(GeometryModel3D(mesh, DiffuseMaterial(SolidColorBrush(Color.FromRgb(*c)))))
        # see-through last: WPF hides what is drawn behind them afterwards
        add_triangles([tri for face in m.f.faces for tri in face.triangles], Color.FromArgb(60, 170, 180, 195))
        if getattr(self, "_neighbors3d", None) is None:
            try:
                self._neighbors3d = rf.neighbors(m.f)
            except Exception:
                self._neighbors3d = []
        for n in self._neighbors3d:
            # the stairwell's walls wrap the stair: barely tinted, the steel shows through
            add_triangles(n["triangles"], Color.FromArgb(25, 150, 155, 165))
        self.scene.visual.Content = group
        x0, x1, y0, y1, z0, z1 = m.f.extent
        extent = (max(x1 - x0, y1 - y0), z1 - z0)
        if self.scene._extent != extent:
            self.scene._extent = extent
            self.scene.fit()

    # -- mouse ---------------------------------------------------------------
    def _wheel(self, key, canvas, args):
        fitted = self._fitted.get(key)
        if fitted:
            self.navs[key].wheel(fitted, args.GetPosition(canvas), args.Delta)
            self._draw_plan() if key == PLAN else self._draw_cut()
        args.Handled = True

    def _down(self, key, canvas, args):
        if args.ChangedButton != MouseButton.Middle:
            return
        if args.ClickCount == 2:
            self.navs[key].reset()
            self._draw_plan() if key == PLAN else self._draw_cut()
            return
        fitted = self._fitted.get(key)
        if fitted:
            self.navs[key].start_pan(fitted, args.GetPosition(canvas))
            canvas.CaptureMouse()

    def _up(self, key, canvas, args):
        self.navs[key].end_pan()
        canvas.ReleaseMouseCapture()

    def _move(self, key, canvas, args):
        if self.navs[key].pan(args.GetPosition(canvas)):
            self._draw_plan() if key == PLAN else self._draw_cut()

    def plan_wheel(self, sender, args):
        self._wheel(PLAN, self.canvas_plan, args)

    def plan_down(self, sender, args):
        self._down(PLAN, self.canvas_plan, args)

    def plan_up(self, sender, args):
        self._up(PLAN, self.canvas_plan, args)

    def plan_move(self, sender, args):
        self._move(PLAN, self.canvas_plan, args)

    def cut_wheel(self, sender, args):
        self._wheel(CUT, self.canvas_cut, args)

    def cut_down(self, sender, args):
        self._down(CUT, self.canvas_cut, args)

    def cut_up(self, sender, args):
        self._up(CUT, self.canvas_cut, args)

    def cut_move(self, sender, args):
        self._move(CUT, self.canvas_cut, args)

    def plan_left(self, sender, args):
        """A click near a tramo's axis shows its cut."""
        frame = self._frames.get(PLAN)
        if frame is None or self.model is None:
            return
        pt = args.GetPosition(self.canvas_plan)
        scale, ox, oy = frame
        x, y = (pt.X - ox) / scale, (oy - pt.Y) / scale
        best = None
        for t in self.model.tramos:
            ds = (x - t.p0[0]) * t.d[0] + (y - t.p0[1]) * t.d[1]
            dt = (x - t.p0[0]) * t.n[0] + (y - t.p0[1]) * t.n[1]
            s = max(0.0, min(t.length, ds))
            dist = math.hypot(ds - s, dt)
            if best is None or dist < best[0]:
                best = (dist, t.index)
        if best and best[0] < 0.6:
            self.state.tramo = best[1]
            self.navs[CUT].reset()
            self.redraw()

    def cut_left(self, sender, args):
        tag = getattr(args.OriginalSource, "Tag", None)
        if not isinstance(tag, basestring):
            return
        st = self._settings()
        t = self.model.tramos[self.state.tramo]
        plan = self._plans.get(t.index) or {}
        if tag == u"neg":
            value = forms.ask_for_string(default=u"{:.2f}".format(plan.get("neg") or 0.6), title="Acero",
                                         prompt=u"Largo del baston (m) a cada lado del apoyo o quiebre (vacio: automatico):")
            if value is None:
                return
            self.txt_sup_neg.Text = value.strip()  # form_changed redraws
            return
        if tag.startswith(u"end:"):
            key = tag[4:]
            kind, label, anc, leg = plan["ends"][key]
            value = forms.ask_for_string(
                default=u"{:.2f}, {:.2f}".format(anc, leg), title="Acero",
                prompt=u"Extremo {} ({}): anclaje y pata en m, separados por coma "
                       u"(anclaje: lo que entra en el apoyo; pata: el gancho final). "
                       u"Escribe 'auto' para volver al valor propuesto:".format(
                           key.split(u":")[-1], label or u"libre"))
            if value is None:
                return
            ends = st.setdefault("ends", {})
            if value.strip().lower() == u"auto":
                ends.pop(key, None)
            else:
                try:
                    parts = [float(v.strip().replace(u",", u".")) for v in value.replace(u";", u" ").replace(u", ", u" ").split()]
                    ends[key] = {"anc": max(0.0, parts[0]), "leg": max(0.0, parts[1] if len(parts) > 1 else leg)}
                except (ValueError, IndexError):
                    forms.alert(u"Escribe dos numeros, por ejemplo: 0.30, 0.15", title="Acero")
                    return
            self.redraw()
        args.Handled = True

    def view3d_wheel(self, sender, args):
        self.scene.wheel(args.Delta)

    def view3d_mouse_down(self, sender, args):
        if args.ChangedButton in (MouseButton.Left, MouseButton.Middle):
            self.scene.start_drag(args.GetPosition(self.view3d), args.ChangedButton == MouseButton.Left)
            self.view3d.CaptureMouse()

    def view3d_mouse_move(self, sender, args):
        self.scene.drag(args.GetPosition(self.view3d))

    def view3d_mouse_up(self, sender, args):
        self.scene.end_drag()
        self.view3d.ReleaseMouseCapture()

    def fit_3d(self, sender, args):
        self.scene.fit()

    def pick_click(self, sender, args):
        self.action = "pick"
        self.Close()

    def run_click(self, sender, args):
        if not self.state.picked_ids:
            forms.alert(u"Selecciona primero la escalera.", title="Acero")
            return
        self.action = "run"
        self.Close()


def pick(state):
    try:
        refs = revit.uidoc.Selection.PickObjects(ObjectType.Element, _StairsFilter(),
                                                 "Selecciona la escalera y pulsa Finalizar")
    except OperationCanceledException:
        return
    state.picked_ids = [id_of(r.ElementId) for r in refs]
    state.active = state.picked_ids[0] if state.picked_ids else None


def generate_all(state, models):
    """Save the settings on each stair and (re)generate its bars."""
    bar_types = rc.BarTypes(doc)
    failed, count = [], 0
    t = DB.Transaction(doc, "Acero escalera")
    t.Start()
    try:
        rc.ensure_parameters(doc)
        fw_params.ensure_parameters(doc, "Acero", [(PARAM, True, [STAIRS_BIC], True)])
        for element_id in state.picked_ids:
            stairs = doc.GetElement(DB.ElementId(element_id))
            settings = state.settings.get(element_id) or read_settings(stairs)
            p = stairs.LookupParameter(PARAM)
            if p is not None and not p.IsReadOnly:
                p.Set(json.dumps(settings, ensure_ascii=False))
            try:
                model = models.get(element_id) or rst.StairModel(stairs)
                rc.delete_generated(doc, stairs)

                def bar_type_for(group, key):
                    return (rc.named_bar_type(doc, settings[group].get("t"), key)
                            or bar_types.pick(key, u"ESCALERA"))
                made = rst.generate(model, settings, MM, bar_type_for, tag=lambda r: rc._tag(r, stairs))
                count += sum(r.Quantity for r, _, _ in made)
            except Exception as e:
                failed.append(u"{} (id {}): {}".format(stairs.Name, element_id, e))
        t.Commit()
    except Exception:
        t.RollBack()
        raise
    return count, failed


# --- main -------------------------------------------------------------------
if __name__ == "__main__":
    state = State()
    pre = [doc.GetElement(i) for i in revit.uidoc.Selection.GetElementIds()]
    state.picked_ids = [id_of(e.Id) for e in pre if e is not None and isinstance(e, Stairs)]
    xaml = os.path.join(SCRIPT_DIR, "AceroForm.xaml")
    models = {}
    while True:
        window = EscaleraWindow(xaml, state, models)
        window.ShowDialog()
        if window.action == "pick":
            pick(state)
            continue
        break
    if window.action == "run":
        count, failed = generate_all(state, models)
        if failed:
            forms.alert(u"No se pudo generar:\n- " + u"\n- ".join(failed), title="Acero")
