# -*- coding: utf-8 -*-
"""Ladrillo de techo en losas aligeradas, aparte de Acero Losa (2026-11-26):
elegir losas, tipo de ladrillo y direccion de viguetas, y en la planta las
cotas de los costados (de la viga a la primera / ultima fila) editables.
Reglas propias en revit_mcp/ladrillo_losa.py."""

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
from System.Windows import FontWeights
from System.Windows.Controls import ListBoxItem, TextBlock, TextBox, Canvas
from System.Windows.Input import Key, Cursors
from System.Windows.Media import Color, SolidColorBrush

doc = revit.doc
TITLE = u"Ladrillo losa"
BRICK = (196, 120, 80)
DIM = (216, 90, 48)


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
        self.action = None


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
        self._loading = True
        self.cbo_brick.ItemsSource = sorted(self.types.keys())
        for i in state.ids:
            item = ListBoxItem()
            item.Content = u"Losa {}  ({})".format(i, self._level(i))
            item.Tag = i
            self.list_slabs.Items.Add(item)
        self.rib = None
        self.model = None
        self._editor = None
        self._loading = False
        if state.ids:
            k = state.ids.index(state.active) if state.active in state.ids else 0
            self.list_slabs.SelectedIndex = k
        else:
            self.txt_status.Text = u"Selecciona las losas aligeradas."

    def _level(self, i):
        e = doc.GetElement(DB.ElementId(i))
        lv = doc.GetElement(e.LevelId) if e is not None else None
        return lv.Name if lv is not None else u"-"

    # -- settings ------------------------------------------------------------------
    def _settings(self, i):
        if i not in self.state.settings:
            m = self.layouts.model(i)
            self.state.settings[i] = ll.settings_of(doc, doc.GetElement(DB.ElementId(i)), self.types, m.thickness)
        return self.state.settings[i]

    def slab_changed(self, sender, args):
        item = self.list_slabs.SelectedItem
        if item is None:
            return
        self.state.active = item.Tag
        st = self._settings(item.Tag)
        a = st.get("alig") or {}
        self._loading = True
        self.cbo_brick.SelectedItem = a.get("brick")
        self.chk_cut.IsChecked = bool(a.get("cut", True))
        ang = a.get("angle")
        self.txt_angle.Text = u"" if ang is None else u"{:g}".format(float(ang))
        self._loading = False
        self.redraw()

    def form_changed(self, sender, args):
        if self._loading or self.state.active is None:
            return
        a = self._settings(self.state.active).setdefault("alig", {})
        a["brick"] = self.cbo_brick.SelectedItem
        a["cut"] = bool(self.chk_cut.IsChecked)
        text = (self.txt_angle.Text or u"").strip().replace(u",", u".")
        try:
            a["angle"] = float(text) if text else None
        except ValueError:
            return
        self.redraw()

    def turn_click(self, sender, args):
        m = self.model
        cur = (self._settings(self.state.active).get("alig") or {}).get("angle")
        base = float(cur) if cur is not None else (m.angle if m is not None else 0.0)
        self.txt_angle.Text = u"{:g}".format(round((base + 90.0) % 180.0, 2))

    def auto_click(self, sender, args):
        self.txt_angle.Text = u""

    # -- plan ------------------------------------------------------------------------
    def redraw(self):
        i = self.state.active
        self.rib = None
        if i is None:
            return
        try:
            built, failed = self.layouts.build([i], self.state.settings, self.types)
            hit = [b for b in built if b[0] == i]
            if hit:
                _, self.model, _, _, self.rib = hit[0]
            else:
                self.model = self.layouts.model(i)
            n = len(self.rib["bricks"]) if self.rib else 0
            self.txt_status.Text = (u"{} ladrillo(s), {} vigueta(s), {} apoyo(s) leído(s).".format(
                n, len(self.rib["viguetas"]) if self.rib else 0, len(self.model.supports))
                + (u"\n" + u"\n".join(failed) if failed else u""))
        except Exception as e:
            self.txt_status.Text = u"No se pudo calcular: {}".format(e)
        self._draw()

    def plan_resized(self, sender, args):
        self._draw()

    def _draw(self):
        c = self.canvas_plan
        c.Children.Clear()
        self._editor = None
        m = self.model
        if m is None or c.ActualWidth < 10:
            return
        x0, x1, y0, y1 = sr.extent(m.slab_tris)
        frame = rv.fit_frame(c.ActualWidth, c.ActualHeight, x0 - 0.8, y0 - 0.8, x1 + 0.8, y1 + 0.8)
        self._frame = frame
        for tri in m.slab_tris:  # the concrete (viguetas between the bricks)
            rv._polygon(c, frame, tri, brush((181, 212, 244)))
        for s in m.supports:
            for tri in s["tris"]:
                rv._polygon(c, frame, tri, brush((180, 178, 169) if s["label"] != u"COLUMNA" else (90, 90, 90)))
        rib = self.rib
        if not rib:
            return
        T = m.from_frame
        fill, edge = brush(BRICK, 150), brush((153, 60, 29))
        for bk in rib["bricks"]:
            q = [(bk["s0"], bk["t0"]), (bk["s1"], bk["t0"]), (bk["s1"], bk["t1"]), (bk["s0"], bk["t1"])]
            if rib["axis"] == sr.Y:
                q = [(t, s_) for s_, t in q]
            rv._polygon(c, frame, [T(*p) for p in q], fill, edge)
        # the side gaps, editable
        red = brush(DIM)
        for t_face, t_brick, side, s_mid in ll.side_gaps(m, rib):
            a, b = (s_mid, t_face), (s_mid, t_brick)
            if rib["axis"] == sr.Y:
                a, b = (a[1], a[0]), (b[1], b[0])
            pa, pb = T(*a), T(*b)
            rv._line(c, frame, pa, pb, red, 2)
            value = abs(t_brick - t_face)
            self._dim_label(c, frame, pa, pb, value, side)

    def _dim_label(self, c, frame, pa, pb, value, side):
        tb = TextBlock()
        tb.Text = u"{:.2f}".format(value) + (u" pegado" if value < 0.005 else u"")
        tb.FontSize = 13
        tb.FontWeight = FontWeights.Bold
        tb.Foreground = brush(DIM)
        tb.Background = brush((255, 255, 255), 220)
        tb.Cursor = Cursors.Hand
        tb.ToolTip = u"Clic para cambiar la distancia de la viga a la {} fila".format(
            u"primera" if side == u"start" else u"última")
        scale, ox, oy = frame
        mx, my = (pa[0] + pb[0]) / 2.0, (pa[1] + pb[1]) / 2.0
        px, py = ox + mx * scale, oy - my * scale
        Canvas.SetLeft(tb, px + 4)
        Canvas.SetTop(tb, py - 9)
        tb.MouseLeftButtonDown += lambda s, e, side=side, px=px, py=py, v=value: self._edit(side, px, py, v)
        c.Children.Add(tb)

    def _edit(self, side, px, py, value):
        c = self.canvas_plan
        box = TextBox()
        box.Width = 60
        box.Text = u"{:.2f}".format(value)
        Canvas.SetLeft(box, px + 4)
        Canvas.SetTop(box, py - 11)
        c.Children.Add(box)
        box.Focus()
        box.SelectAll()

        def apply(s, e):
            if hasattr(e, "Key") and e.Key not in (Key.Enter, Key.Return):
                if e.Key == Key.Escape:
                    self._draw()
                return
            try:
                v = max(0.0, float((box.Text or u"0").replace(u",", u".")))
            except ValueError:
                return
            a = self._settings(self.state.active).setdefault("alig", {})
            a["start" if side == u"start" else "end"] = round(v, 3)
            self.redraw()
        box.KeyDown += apply

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
        msg = u"Ladrillos colocados: {} (el acero no se modificó).".format(placed)
        if failed:
            msg += u"\n\nNo se pudo:\n- " + u"\n- ".join(failed)
        forms.alert(msg, title=TITLE)
    elif state.action == "delete":
        n = ll.remove(doc, state.ids)
        forms.alert(u"Ladrillos borrados: {}.".format(n), title=TITLE)
