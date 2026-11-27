# -*- coding: utf-8 -*-
"""Ladrillo de techo en losas aligeradas, aparte de Acero Losa (2026-11-26):
todas las losas seleccionadas en una planta (filtro por nivel), cada pano
con su boton de giro y sus cotas de costado (de la viga a la primera /
ultima fila) editables. Reglas propias en revit_mcp/ladrillo_losa.py."""

__title__ = "Ladrillo\nLosa"
__author__ = "BOKI"

import os
import sys

SCRIPT_DIR = os.path.dirname(__file__)
EXT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", ".."))
REVIT_MCP_DIR = os.path.join(EXT_ROOT, "revit_mcp")
if REVIT_MCP_DIR not in sys.path:
    sys.path.append(REVIT_MCP_DIR)

import rebar_spec as rs
import slab_rebar as sr
import rebar_slabs as rsl
import rebar_views as rv
import ladrillo_losa as ll
import selection_hosts

reload(sr)
reload(rsl)
reload(rv)
reload(ll)
ll.MM = rs.BAR_DIAMETERS_MM

from pyrevit import revit, DB, forms
from Autodesk.Revit.Exceptions import OperationCanceledException
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType
from System.Windows import FontWeights, Thickness, CornerRadius
from System.Windows.Controls import ListBoxItem, TextBlock, TextBox, Canvas, Border
from System.Windows.Input import Key, Cursors, MouseButton
from System.Windows.Media import Color, SolidColorBrush

doc = revit.doc
TITLE = u"Ladrillo losa"
ALL_LEVELS = u"Todos los niveles"
FT = 0.3048
BRICK = (196, 120, 80)
DIM = (216, 90, 48)
BLUE = (55, 138, 221)


def brush(rgb, a=255):
    return SolidColorBrush(Color.FromArgb(a, rgb[0], rgb[1], rgb[2]))


class _FloorFilter(ISelectionFilter):
    def AllowElement(self, e):
        return isinstance(e, DB.Floor)

    def AllowReference(self, ref, pt):
        return False


class State(object):
    def __init__(self):
        self.ids = []  # the picked ribbed slabs
        self.active = None
        self.settings = {}  # id -> settings as edited here
        self.level = ALL_LEVELS
        self.action = None


def level_name(i):
    e = doc.GetElement(DB.ElementId(i))
    lv = doc.GetElement(e.LevelId) if e is not None else None
    return lv.Name if lv is not None else u"-"


class LadrilloWindow(forms.WPFWindow):
    def __init__(self, xaml, state):
        forms.WPFWindow.__init__(self, xaml)
        try:
            import boki_brand
            boki_brand.apply(self)
        except Exception:
            pass
        self.state = state
        self.layouts = ll.Layouts(doc)
        self.types = rsl.brick_types(doc)
        self.ribs = {}  # id -> (model, rib)
        self._loading = True
        self.cbo_brick.ItemsSource = sorted(self.types.keys())
        for k, i in enumerate(state.ids):
            item = ListBoxItem()
            item.Content = u"{} · Losa {}  ({})".format(self._letter(k), i, level_name(i))
            item.Tag = i
            self.list_slabs.Items.Add(item)
        levels = [ALL_LEVELS] + sorted(set(level_name(i) for i in state.ids))
        self.cbo_level.ItemsSource = levels
        self.cbo_level.SelectedItem = state.level if state.level in levels else ALL_LEVELS
        if state.ids:
            first = self._settings(state.ids[0]).get("alig") or {}
            self.cbo_brick.SelectedItem = first.get("brick")
            self.chk_cut.IsChecked = bool(first.get("cut", True))
        self._loading = False
        if state.ids:
            k = state.ids.index(state.active) if state.active in state.ids else 0
            self.list_slabs.SelectedIndex = k
            self.redraw()
        else:
            self.txt_status.Text = u"Selecciona las losas aligeradas."

    @staticmethod
    def _letter(k):
        return u"ABCDEFGHIJKLMNOPQRSTUVWXYZ"[k] if k < 26 else str(k + 1)

    # -- settings ------------------------------------------------------------------
    def _settings(self, i):
        if i not in self.state.settings:
            m = self.layouts.model(i)
            self.state.settings[i] = ll.settings_of(doc, doc.GetElement(DB.ElementId(i)), self.types, m.thickness)
        return self.state.settings[i]

    def _alig(self, i):
        return self._settings(i).setdefault("alig", {})

    def slab_changed(self, sender, args):
        item = self.list_slabs.SelectedItem
        if item is None:
            return
        self.state.active = item.Tag
        lv = level_name(item.Tag)
        if self.state.level not in (ALL_LEVELS, lv):  # the slab picked in the list: its level shown
            self.cbo_level.SelectedItem = lv
            return
        self._draw()

    def level_changed(self, sender, args):
        self.state.level = self.cbo_level.SelectedItem or ALL_LEVELS
        if not self._loading:
            self._draw()

    def form_changed(self, sender, args):
        """The brick type and the cut: every picked slab."""
        if self._loading:
            return
        for i in self.state.ids:
            a = self._alig(i)
            a["brick"] = self.cbo_brick.SelectedItem
            a["cut"] = bool(self.chk_cut.IsChecked)
        self.redraw()

    def _turn(self, i):
        m = self.ribs.get(i, (None, None))[0]
        cur = self._alig(i).get("angle")
        base = float(cur) if cur is not None else (m.angle if m is not None else 0.0)
        self._alig(i)["angle"] = round((base + 90.0) % 180.0, 4)

    def turn_all_click(self, sender, args):
        for i in self.state.ids:
            self._turn(i)
        self.redraw()

    def auto_all_click(self, sender, args):
        for i in self.state.ids:
            self._alig(i)["angle"] = None
        self.redraw()

    # -- plan ------------------------------------------------------------------------
    def redraw(self):
        self.ribs = {}
        if not self.state.ids:
            return
        ids = [self.state.active] + [i for i in self.state.ids if i != self.state.active] \
            if self.state.active in self.state.ids else list(self.state.ids)
        for i in ids:
            self._settings(i)
        try:
            built, failed = self.layouts.build(ids, self.state.settings, self.types)
        except Exception as e:
            built, failed = [], [u"No se pudo calcular: {}".format(e)]
        for i, model, _, _, rib in built:
            if i in self.state.ids:
                self.ribs[i] = (model, rib)
        n = sum(len(rib["bricks"]) for _, rib in self.ribs.values() if rib)
        self.txt_status.Text = u"{} losa(s), {} ladrillo(s).".format(len(self.ribs), n) + \
            (u"\n" + u"\n".join(failed) if failed else u"")
        self._draw()

    def plan_resized(self, sender, args):
        self._draw()

    def _shown(self):
        lv = self.state.level
        return [i for i in self.state.ids if i in self.ribs and (lv == ALL_LEVELS or level_name(i) == lv)]

    @staticmethod
    def _world(m, p):
        """A point of the slab's plan (local, m) -> the project's plan (m)."""
        w = m.f.world(p[0], p[1], 0.0)
        return (w.X * FT, w.Y * FT)

    def _draw(self):
        c = self.canvas_plan
        c.Children.Clear()
        shown = self._shown()
        if not shown or c.ActualWidth < 10:
            return
        pts = []
        for i in shown:
            m = self.ribs[i][0]
            pts += [self._world(m, p) for t in m.slab_tris for p in t]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        frame = rv.fit_frame(c.ActualWidth, c.ActualHeight, min(xs) - 0.8, min(ys) - 0.8, max(xs) + 0.8, max(ys) + 0.8)
        self._frame = frame
        for i in shown:  # the supports first, under every slab
            m = self.ribs[i][0]
            for s in m.supports:
                for tri in s["tris"]:
                    rv._polygon(c, frame, [self._world(m, p) for p in tri],
                                brush((180, 178, 169) if s["label"] != u"COLUMNA" else (90, 90, 90)))
        for i in shown:
            m, rib = self.ribs[i]
            W = lambda p, m=m: self._world(m, p)
            active = i == self.state.active
            for tri in m.slab_tris:
                rv._polygon(c, frame, [W(p) for p in tri], brush((181, 212, 244)))
            if rib:
                fill, edge = brush(BRICK, 150), brush((153, 60, 29))
                for bk in rib["bricks"]:
                    q = [(bk["s0"], bk["t0"]), (bk["s1"], bk["t0"]), (bk["s1"], bk["t1"]), (bk["s0"], bk["t1"])]
                    if rib["axis"] == sr.Y:
                        q = [(t, s_) for s_, t in q]
                    rv._polygon(c, frame, [W(m.from_frame(*p)) for p in q], fill, edge)
            if active:  # its outline only (the triangles' outer edges)
                for a, b in self._outline(m.slab_tris):
                    rv._line(c, frame, W(a), W(b), brush(BLUE), 3)
        hidden = self._hidden(shown)
        if hidden:
            tb = TextBlock()
            tb.Text = (u"{} losa(s) tapada(s) por otra de otro nivel: elige un nivel arriba "
                       u"para verlas.".format(len(hidden)))
            tb.Foreground = brush((176, 32, 32))
            tb.FontWeight = FontWeights.Bold
            Canvas.SetLeft(tb, 8)
            Canvas.SetTop(tb, 6)
            c.Children.Add(tb)
        for i in shown:
            m, rib = self.ribs[i]
            self._gaps(c, frame, i, m, rib)
            self._turn_badge(c, frame, i, m)

    @staticmethod
    def _outline(tris):
        """The outer edges of a slab's triangles (an edge used once)."""
        count = {}
        for t in tris:
            for a, b in zip(t, t[1:] + t[:1]):
                key = tuple(sorted([(round(a[0], 4), round(a[1], 4)), (round(b[0], 4), round(b[1], 4))]))
                count[key] = count.get(key, 0) + 1
        return [k for k, n in count.items() if n == 1]

    def _hidden(self, shown):
        """The shown slabs under another one of another level (drawn over it)."""
        boxes = {}
        for i in shown:
            m = self.ribs[i][0]
            pts = [self._world(m, p) for t in m.slab_tris for p in t]
            boxes[i] = (min(p[0] for p in pts), max(p[0] for p in pts), min(p[1] for p in pts), max(p[1] for p in pts))
        out = []
        for k, i in enumerate(shown):
            a = boxes[i]
            for j in shown[k + 1:]:
                b = boxes[j]
                ox = min(a[1], b[1]) - max(a[0], b[0])
                oy = min(a[3], b[3]) - max(a[2], b[2])
                if ox > 0.3 and oy > 0.3 and level_name(i) != level_name(j):
                    out.append(i)
                    break
        return out

    def _px(self, frame, p):
        scale, ox, oy = frame
        return ox + p[0] * scale, oy - p[1] * scale

    def _gaps(self, c, frame, i, m, rib):
        """Every bay of the slab: its two side gaps, editable."""
        if not rib:
            return
        red = brush(DIM)
        for t_face, t_brick, side, s_mid in ll.side_gaps(m, rib):
            a, b = (s_mid, t_face), (s_mid, t_brick)
            if rib["axis"] == sr.Y:
                a, b = (a[1], a[0]), (b[1], b[0])
            pa, pb = self._world(m, m.from_frame(*a)), self._world(m, m.from_frame(*b))
            rv._line(c, frame, pa, pb, red, 2)
            value = abs(t_brick - t_face)
            px, py = self._px(frame, ((pa[0] + pb[0]) / 2.0, (pa[1] + pb[1]) / 2.0))
            tb = TextBlock()
            tb.Text = u"{:.2f}".format(value) + (u" pegado" if value < 0.005 else u"")
            tb.FontSize = 12
            tb.FontWeight = FontWeights.Bold
            tb.Foreground = red
            tb.Background = brush((255, 255, 255), 230)
            tb.Cursor = Cursors.Hand
            tb.ToolTip = u"Clic: distancia de la viga a la {} fila de este paño".format(
                u"primera" if side == u"start" else u"última")
            Canvas.SetLeft(tb, px + 3)
            Canvas.SetTop(tb, py - 8)
            tb.MouseLeftButtonDown += lambda s, e, i=i, side=side, px=px, py=py, v=value: self._edit(i, side, px, py, v)
            c.Children.Add(tb)

    def _turn_badge(self, c, frame, i, m):
        """The slab's own turn: clic = 90 deg, right clic = automatic."""
        k = self.state.ids.index(i)
        x0, x1, y0, y1 = sr.extent(m.slab_tris)
        px, py = self._px(frame, self._world(m, ((x0 + x1) / 2.0, (y0 + y1) / 2.0)))
        ang = self._alig(i).get("angle")
        badge = Border()
        badge.Background = brush(BLUE if i == self.state.active else (68, 68, 65), 235)
        badge.CornerRadius = CornerRadius(4)
        badge.Padding = Thickness(6, 2, 6, 2)
        badge.Cursor = Cursors.Hand
        badge.ToolTip = u"Losa {}: clic = girar 90°, clic derecho = automática".format(i)
        tb = TextBlock()
        tb.Text = u"{} ↻ {}".format(self._letter(k), u"auto" if ang is None else u"{:g}°".format(float(ang)))
        tb.Foreground = brush((255, 255, 255))
        tb.FontWeight = FontWeights.Bold
        badge.Child = tb
        Canvas.SetLeft(badge, px - 30)
        Canvas.SetTop(badge, py - 10)

        def click(s, e, i=i):
            if e.ChangedButton == MouseButton.Right:
                self._alig(i)["angle"] = None
            else:
                self._turn(i)
            self.state.active = i
            self.list_slabs.SelectedIndex = self.state.ids.index(i)
            self.redraw()
        badge.MouseUp += click
        c.Children.Add(badge)

    def _edit(self, i, side, px, py, value):
        c = self.canvas_plan
        box = TextBox()
        box.Width = 60
        box.Text = u"{:.2f}".format(value)
        Canvas.SetLeft(box, px + 3)
        Canvas.SetTop(box, py - 10)
        c.Children.Add(box)
        box.Focus()
        box.SelectAll()

        def key(s, e):
            if e.Key == Key.Escape:
                self._draw()
                return
            if e.Key not in (Key.Enter, Key.Return):
                return
            try:
                v = max(0.0, float((box.Text or u"0").replace(u",", u".")))
            except ValueError:
                return
            self._alig(i)["start" if side == u"start" else "end"] = round(v, 3)
            self.redraw()
        box.KeyDown += key

    # -- actions ---------------------------------------------------------------------
    def pick_click(self, sender, args):
        self.state.action = "pick"
        self.Close()

    def place_click(self, sender, args):
        if not self.state.ids:
            forms.alert(u"Selecciona primero las losas.", title=TITLE)
            return
        self.state.action = "place"
        self.Close()

    def delete_click(self, sender, args):
        if not self.state.ids:
            return
        self.state.action = "delete"
        self.Close()

    def close_click(self, sender, args):
        self.state.action = None
        self.Close()


def pick(state):
    try:
        refs = revit.uidoc.Selection.PickObjects(ObjectType.Element, _FloorFilter(),
                                                 u"Selecciona las losas aligeradas y pulsa Finalizar")
    except OperationCanceledException:
        return
    ids = [ll.id_of(r.ElementId) for r in refs]
    ribbed = [i for i in ids if ll.is_ribbed(doc, doc.GetElement(DB.ElementId(i)))]
    if len(ribbed) < len(ids):
        forms.alert(u"{} losa(s) no son aligeradas y se dejaron fuera.".format(len(ids) - len(ribbed)), title=TITLE)
    state.ids = ribbed
    state.active = ribbed[0] if ribbed else None


if __name__ == "__main__":
    state = State()
    pre = selection_hosts.expand(doc, revit.uidoc.Selection.GetElementIds())
    state.ids = [ll.id_of(e.Id) for e in pre if isinstance(e, DB.Floor) and ll.is_ribbed(doc, e)]
    state.active = state.ids[0] if state.ids else None
    xaml = os.path.join(SCRIPT_DIR, "LadrilloForm.xaml")
    while True:
        state.action = None
        LadrilloWindow(xaml, state).ShowDialog()
        if state.action == "pick":
            pick(state)
            continue
        break
    if state.action == "place":
        ids = [state.active] + [i for i in state.ids if i != state.active]
        placed, failed = ll.place(doc, ids, state.settings)
        try:
            revit.uidoc.RefreshActiveView()
        except Exception:
            pass
        msg = u"Ladrillos colocados: {} en {} losa(s) (el acero no se modificó).".format(placed, len(ids))
        if failed:
            msg += u"\n\nNo se pudo:\n- " + u"\n- ".join(failed)
        forms.alert(msg, title=TITLE)
    elif state.action == "delete":
        n = ll.remove(doc, state.ids)
        forms.alert(u"Ladrillos borrados: {}.".format(n), title=TITLE)
