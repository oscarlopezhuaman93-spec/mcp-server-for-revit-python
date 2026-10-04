# -*- coding: utf-8 -*-
"""Acero de cimentaciones (zapatas, cimientos corridos, losas de
cimentacion). Paso 1: el elemento con sus caras numeradas en 3D, los
alzados frontal y lateral (corte real por su centro, tambien en formas
irregulares) y un recubrimiento por cara, guardado en su tipo."""

__title__ = "Acero\nCimentacion"
__author__ = "Revit MCP"

import io
import json
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
import rebar_views as rv
import rebar_splice_ui as su
import utils as fw_utils

reload(fw_utils)
reload(fw_params)
reload(rs)
reload(rc)
reload(rf)
reload(rv)
reload(su)

from pyrevit import revit, DB, forms, script
from Autodesk.Revit.Exceptions import OperationCanceledException
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType
from Microsoft.Win32 import OpenFileDialog, SaveFileDialog
from System.Windows import Thickness, VerticalAlignment, TextWrapping, Visibility, FontWeights
from System.Windows.Controls import DockPanel, Dock, ListBoxItem, StackPanel, TextBlock, TextBox, Orientation
from System.Windows.Input import MouseButton
from System.Windows.Media import Color, SolidColorBrush
from System.Windows.Media.Media3D import (AmbientLight, DiffuseMaterial, DirectionalLight, GeometryModel3D,
                                          MeshGeometry3D, Model3DGroup, Point3D, Vector3D)
from System.Windows.Media import Colors
from System.Windows.Shapes import Rectangle
from System.Collections.Generic import List
from Autodesk.Revit.DB.Structure import Rebar, RebarHookOrientation, RebarStyle

doc = revit.doc
FOUNDATION_BIC = DB.BuiltInCategory.OST_StructuralFoundation
COVERS_PARAM = "EA_Cim_Recubrimientos"  # JSON {face number: cm} on the type
STEEL_PARAM = "EA_Cim_Acero"  # JSON meshes + sketched bars on the type (rf.default_steel)
BAR_KEYS = [k for k in rs.bar_diameter_keys() if 6 <= rs.BAR_DIAMETERS_MM[k] <= 36]
AUTO_TYPE = u"(automatico)"
PLAN = u"planta"  # the plan view's key (navigation, frames)
PALETTE = [(231, 76, 60), (52, 152, 219), (46, 204, 113), (241, 196, 15), (155, 89, 182),
           (230, 126, 34), (26, 188, 156), (233, 30, 99), (121, 85, 72), (0, 150, 136),
           (63, 81, 181), (205, 220, 57)]


def id_of(element_id):
    return fw_utils.element_id_value(element_id)


def face_color(number, alpha=255):
    r, g, b = PALETTE[(number - 1) % len(PALETTE)]
    return Color.FromArgb(alpha, r, g, b)


def read_covers(element_type):
    p = element_type.LookupParameter(COVERS_PARAM)
    try:
        data = json.loads(p.AsString() or u"{}") if p is not None else {}
    except ValueError:
        data = {}
    return dict((int(k), float(v)) for k, v in data.items())


def read_steel(element_type):
    p = element_type.LookupParameter(STEEL_PARAM)
    steel = rf.default_steel()
    try:
        saved = json.loads(p.AsString() or u"{}") if p is not None else {}
    except ValueError:
        saved = {}
    for k in (rf.BOTTOM, rf.TOP):
        steel[k].update(saved.get(k) or {})
    steel["sketch"] = saved.get("sketch") or []
    return steel


def default_cover(face):
    """E.060: 7.5 cm against the ground; the top face 5 cm."""
    return 5.0 if face.normal[2] > 0.9 else rf.DEFAULT_COVER_CM


class _FoundationFilter(ISelectionFilter):
    def AllowElement(self, element):
        c = element.Category
        return c is not None and id_of(c.Id) == int(FOUNDATION_BIC)

    def AllowReference(self, reference, point):
        return False


class State(object):
    def __init__(self):
        self.picked_ids = []
        self.active = None
        self.covers = {}  # element id -> {face: cm} being edited
        self.steel = {}  # element id -> steel settings being edited


class CimentacionWindow(forms.WPFWindow):
    def __init__(self, xaml_file_path, state):
        forms.WPFWindow.__init__(self, xaml_file_path)
        self.state = state
        self.action = None
        self.foundation = None
        self.highlight = None
        self.scene = rv.Scene3D(self.view3d)
        self.scene_steel = rv.Scene3D(self.view3d_steel)
        self.navs = {rf.FRONT: rv.Nav2D(), rf.SIDE: rv.Nav2D(), PLAN: rv.Nav2D()}
        self.cuts = {}  # element id -> {FRONT: y, SIDE: x} of the elevation cuts
        self._plan_drag = None
        self._planners = {}
        self._steel_sig = None
        self.selected_sketch = None
        self._cache = {}
        self._frames = {}
        self.draft = []  # sketch points (u, z) being drawn
        self.draft_view = None
        self.cursor = None
        self._filling = True
        for combo in (self.cbo_bot_dx, self.cbo_bot_dy, self.cbo_top_dx, self.cbo_top_dy, self.cbo_sketch_d):
            combo.ItemsSource = BAR_KEYS
        for combo in (self.cbo_bot_mx, self.cbo_bot_my, self.cbo_top_mx, self.cbo_top_my, self.cbo_sketch_m):
            combo.ItemsSource = list(rf.DIST_MODES)
        # the Revit bar types: all of the project, "(automatico)" first
        self.type_keys = rc.bar_type_keys(doc)
        names = [AUTO_TYPE] + sorted(self.type_keys)
        for combo in self._type_combos():
            combo.ItemsSource = names
            combo.SelectedItem = AUTO_TYPE
        self.cbo_sketch_m.SelectedItem = rf.SPACING
        self.cbo_plan_layer.ItemsSource = [u"Inferior", u"Superior"]
        self.cbo_plan_layer.SelectedIndex = 0
        self.cbo_plan_dir.ItemsSource = [u"X", u"Y"]
        self.cbo_plan_dir.SelectedIndex = 0
        self.cbo_sketch_d.SelectedItem = u'1/2"'
        self._filling = False
        self._fill_splice()
        for i in state.picked_ids:
            e = doc.GetElement(DB.ElementId(i))
            item = ListBoxItem()
            label = TextBlock()
            label.Text = u"{}  (id {})".format(e.Name, i)
            label.TextWrapping = TextWrapping.Wrap
            label.MaxWidth = 320
            item.Content = label
            item.Tag = i
            self.list_elements.Items.Add(item)
        n = len(state.picked_ids)
        self.txt_picked.Text = (u"{} elemento(s) seleccionado(s).".format(n) if n
                                else u"No hay elementos seleccionados.")
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
        element = doc.GetElement(DB.ElementId(item.Tag))
        if item.Tag not in self._cache:
            self._cache[item.Tag] = rf.Foundation(element)
        self.foundation = self._cache[item.Tag]
        if item.Tag not in self.state.steel:
            self.state.steel[item.Tag] = read_steel(doc.GetElement(element.GetTypeId()))
        self._fill_steel()
        if item.Tag not in self.state.covers:
            saved = read_covers(doc.GetElement(element.GetTypeId()))
            self.state.covers[item.Tag] = dict(
                (f.number, saved.get(f.number, default_cover(f))) for f in self.foundation.faces)
        x0, x1, y0, y1, z0, z1 = self.foundation.extent
        self.txt_title.Text = u"{}  ({:.2f} x {:.2f} x {:.2f} m)".format(
            element.Name, x1 - x0, y1 - y0, z1 - z0)
        self._fill_covers()
        self.highlight = None
        self.scene._extent = None
        self.scene_steel._extent = None
        self._steel_sig = None
        for nav in self.navs.values():
            nav.reset()
        self.redraw()

    def _fill_covers(self):
        panel = self.panel_covers
        panel.Children.Clear()
        covers = self.state.covers[self.state.active]
        for face in self.foundation.faces:
            row = DockPanel()
            row.Margin = Thickness(0, 1, 0, 1)
            swatch = Rectangle()
            swatch.Width, swatch.Height = 14, 14
            swatch.Fill = SolidColorBrush(face_color(face.number))
            swatch.Margin = Thickness(0, 0, 6, 0)
            DockPanel.SetDock(swatch, Dock.Left)
            row.Children.Add(swatch)
            box = TextBox()
            box.Width = 56
            box.Text = u"{:g}".format(covers[face.number])
            box.Tag = face.number
            box.TextChanged += self.cover_changed
            box.GotFocus += self.cover_focus
            DockPanel.SetDock(box, Dock.Right)
            row.Children.Add(box)
            unit = TextBlock()
            unit.Text = u" cm"
            unit.VerticalAlignment = VerticalAlignment.Center
            DockPanel.SetDock(unit, Dock.Right)
            label = TextBlock()
            label.Text = u"{}. {}".format(face.number, face.label)
            label.VerticalAlignment = VerticalAlignment.Center
            row.Children.Add(label)
            panel.Children.Add(row)

    # -- steel form ------------------------------------------------------
    def _type_combos(self):
        return [self.cbo_bot_tx, self.cbo_bot_ty, self.cbo_top_tx, self.cbo_top_ty, self.cbo_sketch_t]

    @staticmethod
    def _chosen_type(combo):
        item = combo.SelectedItem
        return u"" if not item or item == AUTO_TYPE else item

    def type_changed(self, sender, args):
        """A bar type chosen: the diameter beside it follows its type."""
        if self._filling:
            return
        name = self._chosen_type(sender)
        key = self.type_keys.get(name)
        pairs = {id(self.cbo_bot_tx): self.cbo_bot_dx, id(self.cbo_bot_ty): self.cbo_bot_dy,
                 id(self.cbo_top_tx): self.cbo_top_dx, id(self.cbo_top_ty): self.cbo_top_dy,
                 id(self.cbo_sketch_t): self.cbo_sketch_d}
        diameter = pairs[id(sender)]
        if key and key in BAR_KEYS and diameter.SelectedItem != key:
            diameter.SelectedItem = key  # its own handler saves the form
        if sender is self.cbo_sketch_t:
            self.sketch_props_changed(sender, args)
        else:
            self.steel_changed(sender, args)

    def _mesh_controls(self, tag):
        return {"on": getattr(self, "chk_" + tag), "dx": getattr(self, "cbo_{}_dx".format(tag)),
                "dy": getattr(self, "cbo_{}_dy".format(tag)), "sx": getattr(self, "txt_{}_sx".format(tag)),
                "sy": getattr(self, "txt_{}_sy".format(tag)),
                "mx": getattr(self, "cbo_{}_mx".format(tag)), "my": getattr(self, "cbo_{}_my".format(tag)),
                "nx": getattr(self, "txt_{}_nx".format(tag)), "ny": getattr(self, "txt_{}_ny".format(tag)),
                "ha": getattr(self, "txt_{}_ha".format(tag)), "hb": getattr(self, "txt_{}_hb".format(tag)),
                "tx": getattr(self, "cbo_{}_tx".format(tag)), "ty": getattr(self, "cbo_{}_ty".format(tag))}

    def _fill_steel(self):
        self._filling = True
        steel = self.state.steel[self.state.active]
        for tag, layer in (("bot", rf.BOTTOM), ("top", rf.TOP)):
            c, m = self._mesh_controls(tag), steel[layer]
            c["on"].IsChecked = bool(m.get("on"))
            c["dx"].SelectedItem = m.get("dx")
            c["dy"].SelectedItem = m.get("dy")
            c["sx"].Text = u"{:g}".format(float(m.get("sx") or 0.2))
            c["sy"].Text = u"{:g}".format(float(m.get("sy") or 0.2))
            c["ha"].Text = u"{:g}".format(float(m.get("hook_a", m.get("hook")) or 0.0))
            c["hb"].Text = u"{:g}".format(float(m.get("hook_b", m.get("hook")) or 0.0))
            for a in ("x", "y"):
                name = m.get("t" + a) or u""
                c["t" + a].SelectedItem = name if name in self.type_keys else AUTO_TYPE
            for a in ("x", "y"):
                c["m" + a].SelectedItem = m.get("m" + a) or rf.SPACING
                c["n" + a].Text = u"{}".format(int(m.get("n" + a) or 10))
                self._mode_enable(c, a)
        self.chk_sketch.IsChecked = steel.get("sketch_on", True)
        self._filling = False
        self.selected_sketch = None
        self._fill_sketch_list()
        self._sketch_mode_enable()

    @staticmethod
    def _num(text, default):
        try:
            return float((text or u"").replace(u",", u"."))
        except ValueError:
            return default

    def steel_changed(self, sender, args):
        if self._filling or self.state.active not in self.state.steel:
            return
        steel = self.state.steel[self.state.active]
        for tag, layer in (("bot", rf.BOTTOM), ("top", rf.TOP)):
            c, m = self._mesh_controls(tag), steel[layer]
            m["on"] = bool(c["on"].IsChecked)
            m["dx"] = c["dx"].SelectedItem or m["dx"]
            m["dy"] = c["dy"].SelectedItem or m["dy"]
            m["sx"] = max(0.05, self._num(c["sx"].Text, m["sx"]))
            m["sy"] = max(0.05, self._num(c["sy"].Text, m["sy"]))
            m["hook_a"] = max(0.0, self._num(c["ha"].Text, m.get("hook_a", m.get("hook", 0.0))))
            m["hook_b"] = max(0.0, self._num(c["hb"].Text, m.get("hook_b", m.get("hook", 0.0))))
            m["tx"] = self._chosen_type(c["tx"])
            m["ty"] = self._chosen_type(c["ty"])
            for a in ("x", "y"):
                m["m" + a] = c["m" + a].SelectedItem or rf.SPACING
                m["n" + a] = max(1, int(self._num(c["n" + a].Text, m.get("n" + a) or 10)))
                self._mode_enable(c, a)
        steel["sketch_on"] = bool(self.chk_sketch.IsChecked)
        self._draw_elevations()
        self._build_steel_3d()

    @staticmethod
    def _mode_enable(c, a):
        """Quantity box only for Cantidad / Ambos, spacing box only for
        Espaciado / Ambos."""
        mode = c["m" + a].SelectedItem or rf.SPACING
        c["n" + a].IsEnabled = mode != rf.SPACING
        c["s" + a].IsEnabled = mode != rf.QUANTITY

    # -- sketch ----------------------------------------------------------
    def sketch_toggle(self, sender, args):
        if self.btn_sketch.IsChecked:
            self.btn_rect.IsChecked = False
        self.draft, self.draft_view = [], None
        self._draw_elevations()

    def rect_toggle(self, sender, args):
        if self.btn_rect.IsChecked:
            self.btn_sketch.IsChecked = False
        self.draft, self.draft_view = [], None
        self._draw_elevations()

    def _drawing(self):
        return bool(self.btn_sketch.IsChecked or self.btn_rect.IsChecked)

    def _to_model(self, view, canvas, args):
        """The clicked point in the section: snapped to the cover line (or
        a corner of it) when close, else to 1 cm - always inside the cover
        (a point outside goes to the nearest point of the cover line)."""
        frame = self._frames.get(view)
        if frame is None:
            return None
        pt = args.GetPosition(canvas)
        scale, ox, oy = frame
        p = ((pt.X - ox) / scale, (oy - pt.Y) / scale)
        outlines = self._center_outlines(view)
        if not outlines:
            return None
        tol = 12.0 / scale
        targets = [(u, z) for u, z, _ in getattr(self, "_dots", {}).get(view, [])]
        for outer, inner in outlines:
            n = len(inner)
            targets += list(inner)  # corners
            targets += [((inner[i][0] + inner[(i + 1) % n][0]) / 2.0, (inner[i][1] + inner[(i + 1) % n][1]) / 2.0)
                        for i in range(n)]  # middle of each side
        best = None
        for q in targets:
            d = ((q[0] - p[0]) ** 2 + (q[1] - p[1]) ** 2) ** 0.5
            if d < tol and (best is None or d < best[0]):
                best = (d, q)
        if best:
            return best[1]
        for outer, inner in outlines:
            q = rf.snap_to_outline(p, inner, tol)
            if q is not p:
                return q
        p = (round(p[0], 2), round(p[1], 2))
        best = None
        for outer, inner in outlines:
            q = rf.clamp_inside(p, inner)
            d = (q[0] - p[0]) ** 2 + (q[1] - p[1]) ** 2
            if best is None or d < best[0]:
                best = (d, q)
        return best[1]

    def _sketch_click(self, view, canvas, args):
        if not self._drawing():
            return
        p = self._to_model(view, canvas, args)
        if p is None:
            return
        if self.draft_view not in (None, view):
            self.draft = []
        self.draft_view = view
        if self.btn_rect.IsChecked:
            # two opposite corners -> a closed rectangular stirrup
            if not self.draft:
                self.draft = [p]
                self._draw_elevations()
                return
            a = self.draft[0]
            u0, u1 = min(a[0], p[0]), max(a[0], p[0])
            z0, z1 = min(a[1], p[1]), max(a[1], p[1])
            if u1 - u0 < 0.03 or z1 - z0 < 0.03:
                return
            self.draft = [(u0, z0), (u1, z0), (u1, z1), (u0, z1)]
            self._sketch_finish(view, closed=True)
            return
        # a click on the first point closes the stirrup
        scale = self._frames[view][0]
        if len(self.draft) >= 3 and ((p[0] - self.draft[0][0]) ** 2 + (p[1] - self.draft[0][1]) ** 2) ** 0.5 \
                < 12.0 / scale:
            self._sketch_finish(view, closed=True)
            return
        self.draft.append(p)
        self._draw_elevations()

    def _sketch_finish(self, view, closed=False):
        if not self._drawing() or len(self.draft) < 2:
            self.draft = []
            self._draw_elevations()
            return
        steel = self.state.steel[self.state.active]
        pts = list(self.draft)
        if closed:
            # a stirrup wraps the bars its corners are on (like in Acero
            # Columna): its centerline goes around them, half its own
            # diameter further out
            key = self.cbo_sketch_d.SelectedItem or u'1/2"'
            bars = [(u, z, k) for u, z, k in getattr(self, "_dots", {}).get(view, [])]
            try:
                if rs.wrap_radius(pts, bars) > 0:
                    pts = rs.stirrup_centerline(pts, bars, key)
                else:
                    # corners on the cover line: the stirrup inside it
                    pts = rs.offset_polygon_outward(pts, -rs.BAR_DIAMETERS_MM[key] / 2000.0)
            except Exception:
                pass
        steel["sketch"].append({"view": view, "pts": [list(p) for p in pts],
                                "d": self.cbo_sketch_d.SelectedItem or u'1/2"',
                                "s": max(0.05, self._num(self.txt_sketch_s.Text, 0.2)),
                                "m": self.cbo_sketch_m.SelectedItem or rf.SPACING,
                                "n": max(1, int(self._num(self.txt_sketch_n.Text, 5))),
                                "closed": bool(closed),
                                "t": self._chosen_type(self.cbo_sketch_t)})
        self.draft, self.draft_view = [], None
        self.selected_sketch = len(steel["sketch"]) - 1
        self._fill_sketch_list()
        self._draw_elevations()
        self._build_steel_3d()

    def _fill_sketch_list(self):
        steel = self.state.steel.get(self.state.active) or {"sketch": []}
        self._filling_list = True
        self.list_sketch.Items.Clear()
        for i, item in enumerate(steel["sketch"]):
            self.list_sketch.Items.Add(u"{}. {} \u00d8{} {} {}".format(
                i + 1, u"Estribo" if item.get("closed") else u"Barra", item["d"],
                u"n={}".format(item.get("n")) if item.get("m") == rf.QUANTITY else u"@{:g}".format(float(item["s"])),
                u"(frontal)" if item["view"] == rf.FRONT else u"(lateral)"))
        self.txt_sketch_count.Text = u"{} barra(s) dibujada(s)".format(len(steel["sketch"]))
        if self.selected_sketch is not None and self.selected_sketch < len(steel["sketch"]):
            self.list_sketch.SelectedIndex = self.selected_sketch
        self._filling_list = False
        self._fill_segments()

    def sketch_selected(self, sender, args):
        if getattr(self, "_filling_list", False):
            return
        i = self.list_sketch.SelectedIndex
        self.selected_sketch = i if i >= 0 else None
        steel = self.state.steel.get(self.state.active)
        if self.selected_sketch is not None and steel:
            item = steel["sketch"][self.selected_sketch]
            self._filling = True
            self.cbo_sketch_d.SelectedItem = item["d"]
            self.cbo_sketch_m.SelectedItem = item.get("m") or rf.SPACING
            self.txt_sketch_n.Text = u"{}".format(item.get("n") or 5)
            self.txt_sketch_s.Text = u"{:g}".format(float(item["s"]))
            name = item.get("t") or u""
            self.cbo_sketch_t.SelectedItem = name if name in self.type_keys else AUTO_TYPE
            self._filling = False
        self._sketch_mode_enable()
        self._fill_segments()
        self._draw_elevations()

    def _fill_segments(self):
        """One box per segment of the chosen sketched bar: its length (m)."""
        panel = self.panel_segments
        panel.Children.Clear()
        steel = self.state.steel.get(self.state.active)
        if self.selected_sketch is None or not steel or self.selected_sketch >= len(steel["sketch"]):
            return
        pts = steel["sketch"][self.selected_sketch]["pts"]
        for k in range(len(pts) - 1):
            cell = StackPanel()
            cell.Orientation = Orientation.Horizontal
            cell.Margin = Thickness(0, 0, 6, 2)
            label = TextBlock()
            label.Text = u"T{} ".format(k + 1)
            label.VerticalAlignment = VerticalAlignment.Center
            box = TextBox()
            box.Width = 44
            a, b = pts[k], pts[k + 1]
            box.Text = u"{:.2f}".format(((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5)
            box.Tag = k
            box.LostFocus += self.segment_changed
            box.KeyDown += self.segment_key
            cell.Children.Add(label)
            cell.Children.Add(box)
            panel.Children.Add(cell)

    def segment_key(self, sender, args):
        from System.Windows.Input import Key
        if args.Key == Key.Enter:
            self.segment_changed(sender, args)

    def segment_changed(self, sender, args):
        steel = self.state.steel.get(self.state.active)
        if self.selected_sketch is None or not steel:
            return
        item = steel["sketch"][self.selected_sketch]
        length = self._num(sender.Text, None)
        if not length or length <= 0:
            return
        pts = rf.set_segment_length([tuple(q) for q in item["pts"]], sender.Tag, length)
        outlines = self._center_outlines(item["view"])
        if outlines:
            inner = outlines[0][1]
            pts = [rf.clamp_inside(q, inner) for q in pts]  # never out of the cover
        item["pts"] = [list(q) for q in pts]
        self._fill_segments()
        self._draw_elevations()
        self._build_steel_3d()

    def sketch_props_changed(self, sender, args):
        """Ø / mode / n / @ of the sketch tool - and of the chosen sketched
        bar, when one is chosen."""
        self._sketch_mode_enable()
        if self._filling:
            return
        steel = self.state.steel.get(self.state.active)
        if self.selected_sketch is None or not steel or self.selected_sketch >= len(steel["sketch"]):
            return
        item = steel["sketch"][self.selected_sketch]
        item["d"] = self.cbo_sketch_d.SelectedItem or item["d"]
        item["m"] = self.cbo_sketch_m.SelectedItem or rf.SPACING
        item["n"] = max(1, int(self._num(self.txt_sketch_n.Text, item.get("n") or 5)))
        item["s"] = max(0.05, self._num(self.txt_sketch_s.Text, item["s"]))
        item["t"] = self._chosen_type(self.cbo_sketch_t)
        i = self.selected_sketch
        self._fill_sketch_list()
        self.selected_sketch = i
        self._draw_elevations()
        self._build_steel_3d()

    def _sketch_mode_enable(self):
        mode = self.cbo_sketch_m.SelectedItem or rf.SPACING
        self.txt_sketch_n.IsEnabled = mode != rf.SPACING
        self.txt_sketch_s.IsEnabled = mode != rf.QUANTITY

    def sketch_delete(self, sender, args):
        """Delete the chosen sketched bar (or the last one)."""
        steel = self.state.steel.get(self.state.active)
        if not steel or not steel["sketch"]:
            return
        i = self.selected_sketch if self.selected_sketch is not None else len(steel["sketch"]) - 1
        steel["sketch"].pop(i)
        self.selected_sketch = None
        self._fill_sketch_list()
        self._draw_elevations()
        self._build_steel_3d()

    def sketch_undo(self, sender, args):
        steel = self.state.steel.get(self.state.active)
        if steel and steel["sketch"]:
            steel["sketch"].pop()
            self.txt_sketch_count.Text = u"{} barra(s) dibujada(s)".format(len(steel["sketch"]))
            self._draw_elevations()

    def front_left(self, sender, args):
        self._sketch_click(rf.FRONT, self.canvas_front, args)

    def side_left(self, sender, args):
        self._sketch_click(rf.SIDE, self.canvas_side, args)

    def front_right(self, sender, args):
        self._sketch_finish(rf.FRONT)

    def side_right(self, sender, args):
        self._sketch_finish(rf.SIDE)

    def front_move(self, sender, args):
        self._move(rf.FRONT, self.canvas_front, args)

    def side_move(self, sender, args):
        self._move(rf.SIDE, self.canvas_side, args)

    def _move(self, view, canvas, args):
        if self.navs[view].pan(args.GetPosition(canvas)):
            self._draw_elevation(canvas, view)
            return
        if self._drawing() and self.draft and self.draft_view == view:
            self.cursor = self._to_model(view, canvas, args)
            self._draw_elevation(canvas, view)

    def window_key(self, sender, args):
        from System.Windows.Input import Key
        if args.Key == Key.Escape and self.draft:
            self.draft, self.draft_view = [], None
            self._draw_elevations()
            args.Handled = True

    # -- splices ---------------------------------------------------------
    def _fill_splice(self):
        self._filling_splice = True
        try:
            settings = su.load_splice_settings()
            self.chk_splice.IsChecked = settings["on"]
            self.txt_splice_max.Text = u"{:g}".format(settings["max"])
            self.lap_boxes, self.lap_checks = su.lap_rows(self.panel_laps, settings, self.splice_changed)
        finally:
            self._filling_splice = False

    def _splice_from_form(self):
        laps, active = su.lap_form(self.lap_boxes, self.lap_checks)
        return {"on": bool(self.chk_splice.IsChecked), "laps": laps, "active": active,
                "max": max(1.0, self._num(self.txt_splice_max.Text, rs.MAX_BAR_LENGTH))}

    def splice_changed(self, sender, args):
        if getattr(self, "_filling_splice", True):
            return
        su.save_splice_settings(self._splice_from_form())
        self._build_steel_3d()

    def cover_changed(self, sender, args):
        try:
            value = float((sender.Text or u"").replace(u",", u"."))
        except ValueError:
            return
        self.state.covers[self.state.active][sender.Tag] = value
        self._draw_elevations()
        self._build_steel_3d()

    def cover_focus(self, sender, args):
        self.highlight = sender.Tag
        self._build_3d()
        self._draw_elevations()

    # -- views -----------------------------------------------------------
    def redraw(self):
        self._build_3d()
        self._draw_elevations()
        self._build_steel_3d()

    def _neighbors(self):
        key = ("neighbors", self.state.active)
        if key not in self._cache:
            try:
                self._cache[key] = rf.neighbors(self.foundation)
            except Exception:
                self._cache[key] = []
        return self._cache[key]

    def _build_steel_3d(self):
        """The steel as generated (sketches, splices included) inside the
        see-through foundation, with the elements touching it."""
        f = self.foundation
        if f is None or self.state.active not in self.state.steel:
            return
        steel = self.state.steel[self.state.active]
        covers = self.state.covers.get(self.state.active, {})
        splice = su.splice_for_generation(self._splice_from_form()) if hasattr(self, "lap_boxes") else None
        sig = json.dumps([steel, sorted(covers.items()), splice], sort_keys=True, default=str)
        if sig == self._steel_sig:
            return
        self._steel_sig = sig
        group = Model3DGroup()
        group.Children.Add(AmbientLight(Color.FromRgb(110, 110, 110)))
        group.Children.Add(DirectionalLight(Colors.White, Vector3D(-0.6, -0.8, -1.0)))
        group.Children.Add(DirectionalLight(Color.FromRgb(120, 120, 120), Vector3D(0.7, 0.5, 0.3)))

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

        for n in self._neighbors():
            add_triangles(n["triangles"], Color.FromArgb(90, 150, 155, 165))
        try:
            bars = rf.BarPlanner(f, covers, rs.BAR_DIAMETERS_MM).all_bars(steel, splice)
        except Exception:
            bars = []
        meshes = {}
        for view, pos, path, bar_key, _ in bars:
            sketched = False
            mesh = meshes.setdefault(view, MeshGeometry3D())
            pts = rf.bar_points_3d(view, pos, path)
            radius = rs.BAR_DIAMETERS_MM[bar_key] / 2000.0
            for a, b in zip(pts, pts[1:]):
                rv._add_tube(mesh, a, b, radius, 8)
        colors = {rf.FRONT: Color.FromRgb(200, 70, 40), rf.SIDE: Color.FromRgb(40, 90, 200)}
        for view, mesh in meshes.items():
            group.Children.Add(GeometryModel3D(mesh, DiffuseMaterial(SolidColorBrush(colors[view]))))
        add_triangles([t for face in f.faces for t in face.triangles], Color.FromArgb(55, 170, 180, 195))
        self.scene_steel.visual.Content = group
        x0, x1, y0, y1, z0, z1 = f.extent
        extent = (max(x1 - x0, y1 - y0), max(z1 - z0, 0.3))
        if self.scene_steel._extent != extent:
            self.scene_steel._extent = extent
            self.scene_steel.fit()

    def _build_3d(self):
        group = Model3DGroup()
        group.Children.Add(AmbientLight(Color.FromRgb(110, 110, 110)))
        group.Children.Add(DirectionalLight(Colors.White, Vector3D(-0.6, -0.8, -1.0)))
        group.Children.Add(DirectionalLight(Color.FromRgb(120, 120, 120), Vector3D(0.7, 0.5, 0.3)))
        f = self.foundation
        if f is not None:
            for face in f.faces:
                mesh = MeshGeometry3D()
                for tri in face.triangles:
                    base = mesh.Positions.Count
                    for x, y, z in tri:
                        mesh.Positions.Add(Point3D(x, y, z))
                    for k in range(3):
                        mesh.TriangleIndices.Add(base + k)
                alpha = 255 if self.highlight in (None, face.number) else 70
                material = DiffuseMaterial(SolidColorBrush(face_color(face.number, alpha)))
                model = GeometryModel3D(mesh, material)
                model.BackMaterial = material
                group.Children.Add(model)
            for n in self._neighbors():
                mesh = MeshGeometry3D()
                for tri in n["triangles"]:
                    base = mesh.Positions.Count
                    for x, y, z in tri:
                        mesh.Positions.Add(Point3D(x, y, z))
                    for k in range(3):
                        mesh.TriangleIndices.Add(base + k)
                gray = DiffuseMaterial(SolidColorBrush(Color.FromArgb(90, 150, 155, 165)))
                model = GeometryModel3D(mesh, gray)
                model.BackMaterial = gray
                group.Children.Add(model)
            x0, x1, y0, y1, z0, z1 = f.extent
            extent = (max(x1 - x0, y1 - y0), max(z1 - z0, 0.3))
            if self.scene._extent != extent:
                self.scene._extent = extent
                self.scene.fit()
        self.scene.visual.Content = group

    def views_resized(self, sender, args):
        self._draw_elevations()

    def _draw_elevations(self):
        for canvas, view in ((self.canvas_front, rf.FRONT), (self.canvas_side, rf.SIDE)):
            self._draw_elevation(canvas, view)
        self._draw_plan()

    def lower_view_changed(self, sender, args):
        if hasattr(self, "navs"):
            self.navs[rf.SIDE].reset()
            self._draw_elevation(self.canvas_side, rf.SIDE)

    # -- plan: zones and cuts ----------------------------------------------
    def top_view_changed(self, sender, args):
        if not hasattr(self, "border_plan"):
            return
        plan = bool(self.rb_top_plan.IsChecked)
        self.border_plan.Visibility = Visibility.Visible if plan else Visibility.Collapsed
        self.txt_plan_hint.Visibility = self.border_plan.Visibility
        self.panel_plan_tools.Visibility = self.border_plan.Visibility
        self.border_3d.Visibility = Visibility.Collapsed if plan else Visibility.Visible
        if plan:
            self.UpdateLayout()
            self._draw_plan()
        else:
            self.UpdateLayout()
            self.scene._extent = None
            self._build_3d()

    def plan_tools_changed(self, sender, args):
        if hasattr(self, "canvas_plan"):
            self._draw_plan()

    def _plan_sel(self):
        """(layer, settings, axis, lo, hi) of the mesh direction edited in
        plan: axis "x" = the X bars, spread along y (lo..hi of the
        element), "y" = the Y bars, spread along x."""
        layer = rf.BOTTOM if self.cbo_plan_layer.SelectedIndex != 1 else rf.TOP
        axis = u"x" if self.cbo_plan_dir.SelectedIndex != 1 else u"y"
        x0, x1, y0, y1, _, _ = self.foundation.extent
        lo, hi = (y0, y1) if axis == u"x" else (x0, x1)
        return layer, self.state.steel[self.state.active][layer], axis, lo, hi

    def _planner(self):
        covers = self.state.covers.get(self.state.active, {})
        key = (self.state.active, json.dumps(sorted(covers.items())))
        if key not in self._planners:
            self._planners[key] = rf.BarPlanner(self.foundation, covers, rs.BAR_DIAMETERS_MM)
        return self._planners[key]

    def _steel_refresh(self):
        self._draw_elevations()
        self._build_steel_3d()

    def _plan_point(self, args):
        frame = self._frames.get(PLAN)
        if frame is None:
            return None
        pt = args.GetPosition(self.canvas_plan)
        scale, ox, oy = frame
        return ((pt.X - ox) / scale, (oy - pt.Y) / scale), scale

    def _plan_hit(self, p, scale):
        """What is under the cursor in plan: ("limit", k), ("cut", view) or None."""
        tol = 8.0 / scale
        layer, m, axis, lo, hi = self._plan_sel()
        zones = m.get("z" + axis) or []
        coord = p[1] if axis == u"x" else p[0]
        found = [(abs(coord - zones[k]["b"]), ("limit", k)) for k in range(len(zones) - 1)]
        found += [(abs(p[1] - self._center(rf.FRONT)), ("cut", rf.FRONT)),
                  (abs(p[0] - self._center(rf.SIDE)), ("cut", rf.SIDE))]
        found = [f for f in found if f[0] < tol]
        return min(found)[1] if found else None  # the nearest line

    def plan_left(self, sender, args):
        if self.foundation is None:
            return
        tag = getattr(args.OriginalSource, "Tag", None)
        if isinstance(tag, basestring) and tag.startswith(u"zone:"):
            self._zone_edit(int(tag[5:]))
            args.Handled = True
            return
        found = self._plan_point(args)
        if found is None:
            return
        p, scale = found
        layer, m, axis, lo, hi = self._plan_sel()
        if self.btn_zone_split.IsChecked:
            coord = round(p[1] if axis == u"x" else p[0], 2)
            m["z" + axis] = rf.split_zone(m.get("z" + axis), coord, m, axis, lo, hi)
            self._steel_refresh()
            return
        hit = self._plan_hit(p, scale)
        if hit:
            self._plan_drag = hit
            self.canvas_plan.CaptureMouse()

    def plan_right(self, sender, args):
        found = self._plan_point(args)
        if found is None:
            return
        hit = self._plan_hit(*found)
        if hit and hit[0] == "limit":
            layer, m, axis, lo, hi = self._plan_sel()
            m["z" + axis] = rf.remove_limit(m["z" + axis], hit[1])
            self._steel_refresh()

    def plan_move(self, sender, args):
        from System.Windows.Input import Cursors
        if self.navs[PLAN].pan(args.GetPosition(self.canvas_plan)):
            self._draw_plan()
            return
        found = self._plan_point(args)
        if found is None:
            return
        p, scale = found
        x0, x1, y0, y1, _, _ = self.foundation.extent
        drag = self._plan_drag
        if drag is None:
            hit = self._plan_hit(p, scale)
            horizontal = hit and ((hit[0] == "cut" and hit[1] == rf.FRONT) or
                                  (hit[0] == "limit" and self._plan_sel()[2] == u"x"))
            self.canvas_plan.Cursor = (Cursors.Cross if self.btn_zone_split.IsChecked else
                                       Cursors.SizeNS if horizontal else Cursors.SizeWE if hit else Cursors.Arrow)
            return
        if drag[0] == "cut":
            view = drag[1]
            v = round(p[1], 2) if view == rf.FRONT else round(p[0], 2)
            lo, hi = (y0, y1) if view == rf.FRONT else (x0, x1)
            self.cuts.setdefault(self.state.active, {})[view] = max(lo + 0.01, min(hi - 0.01, v))
            self._draw_plan()
            canvas = self.canvas_front if view == rf.FRONT else self.canvas_side
            self._draw_elevation(canvas, view)
        else:
            layer, m, axis, lo, hi = self._plan_sel()
            v = round(p[1] if axis == u"x" else p[0], 2)
            m["z" + axis] = rf.move_limit(m["z" + axis], drag[1], v)
            self._draw_plan()

    def plan_up(self, sender, args):
        if args.ChangedButton == MouseButton.Middle:
            self.navs[PLAN].end_pan()
            self.canvas_plan.ReleaseMouseCapture()
            return
        if args.ChangedButton == MouseButton.Left and self._plan_drag:
            drag, self._plan_drag = self._plan_drag, None
            self.canvas_plan.ReleaseMouseCapture()
            if drag[0] == "limit":
                self._steel_refresh()
            else:
                self._build_steel_3d()

    def plan_wheel(self, sender, args):
        self._wheel(PLAN, self.canvas_plan, args)

    def plan_down(self, sender, args):
        self._down(PLAN, self.canvas_plan, args)

    def zones_auto(self, sender, args):
        if self.foundation is None:
            return
        layer, m, axis, lo, hi = self._plan_sel()
        k = 1 if axis == u"x" else 0
        breaks = [q[k] for e in rf.plan_outline(self.foundation) for q in e]
        m["z" + axis] = rf.auto_zones(breaks, lo, hi, m, axis)
        if len(m["z" + axis]) < 2:
            m["z" + axis] = []
            forms.alert(u"La planta no cambia de forma en esa direccion: basta una sola distribucion.",
                        title="Acero")
        self._steel_refresh()

    def zones_clear(self, sender, args):
        if self.foundation is None:
            return
        layer, m, axis, lo, hi = self._plan_sel()
        m["z" + axis] = []
        self._steel_refresh()

    def _zone_edit(self, k):
        """A click on a zone's text: its new quantity / spacing (k = -1: the
        single distribution, written back to the configuration boxes)."""
        layer, m, axis, lo, hi = self._plan_sel()
        zones = m.get("z" + axis) or []
        if k < 0:
            current = rf.zone_text(m.get("m" + axis, rf.SPACING), m.get("n" + axis, 1), float(m.get("s" + axis) or 0.2))
            where = u"toda la cimentacion"
        else:
            z = zones[k]
            current = rf.zone_text(z["m"], z["n"], z["s"])
            where = u"la zona {} ({:.2f} a {:.2f} m)".format(k + 1, z["a"], z["b"])
        value = forms.ask_for_string(
            default=current, title="Acero",
            prompt=u"Barras {} de {}: escribe la cantidad (15), el espaciado (@0.20) o ambos (15@0.20):".format(
                axis.upper(), where))
        if value is None:
            return
        try:
            mode, count, spacing = rf.parse_zone_text(value)
        except ValueError:
            forms.alert(u"No entiendo '{}'. Ejemplos: 15, @0.20, 15@0.20.".format(value), title="Acero")
            return
        if k < 0:
            c = self._mesh_controls(u"bot" if layer == rf.BOTTOM else u"top")
            self._filling = True
            c["m" + axis].SelectedItem = mode
            c["n" + axis].Text = u"{}".format(count)
            c["s" + axis].Text = u"{:g}".format(spacing)
            self._filling = False
            self.steel_changed(None, None)
            return
        zones[k].update({"m": mode, "n": count, "s": spacing})
        self._steel_refresh()

    def _plan_label(self, canvas, frame, x, y, text, tag, anchor="left"):
        """A zone's clickable text."""
        from System.Windows.Input import Cursors
        from System.Windows.Controls import Canvas
        from System.Windows import Size
        tb = TextBlock()
        tb.Text = text + u"  \u270e"
        tb.FontSize = 10
        tb.FontWeight = FontWeights.Bold
        tb.Foreground = SolidColorBrush(Color.FromRgb(20, 110, 60))
        tb.Background = SolidColorBrush(Color.FromRgb(255, 246, 200))
        tb.Tag = tag
        tb.Cursor = Cursors.Hand
        tb.ToolTip = u"Clic para cambiar la cantidad / el espaciado"
        tb.Measure(Size(1e4, 1e4))
        px, py = rv._px(frame, x, y)
        if anchor == "center":
            px -= tb.DesiredSize.Width / 2.0
        Canvas.SetLeft(tb, px)
        Canvas.SetTop(tb, py - tb.DesiredSize.Height / 2.0)
        canvas.Children.Add(tb)

    def _draw_plan(self, canvas=None):
        """The foundation in plan: its outline with each edge's length, the
        overall sizes, the bars of the chosen mesh with its zones, and the
        two cuts (A, B) the elevations show, which can be dragged."""
        canvas = self.canvas_plan
        canvas.Children.Clear()
        f = self.foundation
        if f is None or canvas.ActualWidth < 10 or not self.rb_top_plan.IsChecked:
            return
        edges = rf.plan_outline(f)
        if not edges:
            return
        xs = [p[0] for e in edges for p in e]
        ys = [p[1] for e in edges for p in e]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        layer, m, axis, zlo, zhi = self._plan_sel()
        right = 1.7 if axis == u"x" else 2.3
        bottom = 0.8 if axis == u"y" else 0.5
        fitted = rv.fit_frame(canvas.ActualWidth, canvas.ActualHeight, x0 - 0.5, y0 - bottom, x1 + right, y1 + 0.5)
        self._fitted = getattr(self, "_fitted", {})
        self._fitted[PLAN] = fitted
        frame = self.navs[PLAN].resolve(fitted)
        self._frames[PLAN] = frame
        dark = SolidColorBrush(Color.FromRgb(40, 40, 40))
        dim = SolidColorBrush(Color.FromRgb(31, 78, 160))
        red = SolidColorBrush(Color.FromRgb(200, 50, 40))
        for face in f.faces:
            if face.normal[2] < -0.9:
                for tri in face.triangles:
                    rv._polygon(canvas, frame, [(p[0], p[1]) for p in tri], SolidColorBrush(Color.FromRgb(225, 228, 232)))
        cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        irregular = len(edges) > 4  # a rectangle: its two overall sizes say it all
        for a, b in edges:
            rv._line(canvas, frame, a, b, dark, 2.5)
            length = ((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5
            if irregular and length > 0.05:
                mx, my = (a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0
                # the length written just outside the edge
                ox, oy = mx - cx, my - cy
                norm = (ox * ox + oy * oy) ** 0.5 or 1.0
                rv._text(canvas, frame, mx + ox / norm * 0.12, my + oy / norm * 0.12, u"{:.2f}".format(length),
                         brush=dim, size=11)
        # overall sizes
        rv._line(canvas, frame, (x0, y0 - 0.3), (x1, y0 - 0.3), dim, 1)
        rv._text(canvas, frame, cx, y0 - 0.38, u"{:.2f} m".format(x1 - x0), brush=dim, size=11, bold=True)
        rv._line(canvas, frame, (x0 - 0.3, y0), (x0 - 0.3, y1), dim, 1)
        rv._text(canvas, frame, x0 - 0.42, cy, u"{:.2f}".format(y1 - y0), brush=dim, size=11, bold=True)
        # the bars of the chosen mesh (as generated: cut to the shape)
        try:
            bars = self._planner().mesh(layer, dict(m, on=True))
        except Exception:
            bars = []
        for view, pos, path, key, _ in bars:
            us = [q[0] for q in path]
            if view == rf.FRONT:
                a, b = (min(us), pos), (max(us), pos)
            else:
                a, b = (pos, -max(us)), (pos, -min(us))
            mine = (view == rf.FRONT) == (axis == u"x")
            color = (Color.FromArgb(255 if mine else 70, 200, 70, 40) if view == rf.FRONT
                     else Color.FromArgb(255 if mine else 70, 40, 90, 200))
            rv._line(canvas, frame, a, b, SolidColorBrush(color), 1.6 if mine else 1)
        lo_c, hi_c = self._planner()._range(rf.FRONT if axis == u"x" else rf.SIDE,
                                            rs.BAR_DIAMETERS_MM[m["d" + axis]] / 1000.0)
        per_zone = {}
        for _, k in rf.zone_positions(lo_c, hi_c, m, axis):
            per_zone[k] = per_zone.get(k, 0) + 1
        # the zones of the chosen direction, their limits and clickable texts
        green = SolidColorBrush(Color.FromRgb(20, 140, 70))
        zones = m.get("z" + axis) or []
        shown = zones or [rf.zone_from(m, axis, zlo, zhi)]
        for k, z in enumerate(shown):
            n = per_zone.get(k, 0)
            text = u"{}{}  ({} barras)".format(u"Z{}: ".format(k + 1) if zones else u"Toda: ",
                                               rf.zone_text(z["m"], z["n"], z["s"]), n)
            tag = u"zone:{}".format(k if zones else -1)
            mid = (z["a"] + z["b"]) / 2.0
            if axis == u"x":
                rv._line(canvas, frame, (x1 + 0.1, z["a"] + 0.02), (x1 + 0.1, z["b"] - 0.02), green, 3)
                self._plan_label(canvas, frame, x1 + 0.16, mid, text, tag)
            else:
                rv._line(canvas, frame, (z["a"] + 0.02, y0 - 0.55), (z["b"] - 0.02, y0 - 0.55), green, 3)
                # the texts in a column on the right, each with its x range
                rv._text(canvas, frame, mid, y0 - 0.67, u"Z{}".format(k + 1) if zones else u"", brush=green,
                         size=10, bold=True)
                self._plan_label(canvas, frame, x1 + 0.16, y1 - 0.1 - k * 20.0 / frame[0],
                                 text + (u"  x {:.2f} a {:.2f}".format(z["a"], z["b"]) if zones else u""), tag)
        for k in range(len(zones) - 1):
            v = zones[k]["b"]
            if axis == u"x":
                rv._line(canvas, frame, (x0 - 0.15, v), (x1 + 0.15, v), green, 1.5, dash=True)
                rv._text(canvas, frame, x1 + 0.16, v, u"{:.2f}".format(v), brush=green, size=10, bold=True, anchor="left")
            else:
                rv._line(canvas, frame, (v, y0 - 0.15), (v, y1 + 0.15), green, 1.5, dash=True)
                rv._text(canvas, frame, v, y1 + 0.24, u"{:.2f}".format(v), brush=green, size=10, bold=True)
        # the cuts of the elevations (drag them)
        yc = self._center(rf.FRONT)
        xc = self._center(rf.SIDE)
        rv._line(canvas, frame, (x0 - 0.25, yc), (x1 + 0.25, yc), red, 2, dash=True)
        rv._text(canvas, frame, x0 - 0.14, yc + 0.13, u"A", brush=red, size=13, bold=True)
        rv._line(canvas, frame, (xc, y0 - 0.25), (xc, y1 + 0.25), red, 2, dash=True)
        rv._text(canvas, frame, xc, y1 + 0.38, u"B", brush=red, size=13, bold=True)
        rv._text(canvas, frame, x1 + 0.1, y1 + 0.38, u"X \u2192   Y \u2191", brush=dark, size=10, anchor="left")

    def _center(self, view):
        """Where the elevation's cut is: y of A (FRONT), x of B (SIDE) -
        the middle until it is dragged in plan."""
        x0, x1, y0, y1, _, _ = self.foundation.extent
        mid = (y0 + y1) / 2.0 if view == rf.FRONT else (x0 + x1) / 2.0
        return self.cuts.get(self.state.active, {}).get(view, mid)

    def _section(self, view):
        key = (self.state.active, view, round(self._center(view), 3))
        if key not in self._cache:
            try:
                self._cache[key] = self.foundation.section(view, self._center(view))
            except Exception:
                self._cache[key] = []
        return self._cache[key]

    def _center_outlines(self, view):
        covers = self.state.covers.get(self.state.active, {})
        return [(pts, rf.inner_outline(pts, [covers.get(t, rf.DEFAULT_COVER_CM) / 100.0 if t else 0.0
                                             for t in tags])) for pts, tags in self._section(view)]

    @staticmethod
    def _dot(canvas, frame, u, z, radius, brush):
        from System.Windows.Shapes import Ellipse
        from System.Windows.Controls import Canvas
        e = Ellipse()
        e.Width = e.Height = 2 * radius
        e.Fill = brush
        e.IsHitTestVisible = False
        px, py = rv._px(frame, u, z)
        Canvas.SetLeft(e, px - radius)
        Canvas.SetTop(e, py - radius)
        canvas.Children.Add(e)

    def _draw_bars(self, canvas, frame, view):
        """The bars in this cut: the ones lying in it as lines, the other
        direction's as dots, the sketched ones, the sketch being drawn."""
        steel = self.state.steel.get(self.state.active)
        if not steel:
            return
        self._dots = getattr(self, "_dots", {})
        self._dots[view] = []
        mm = rs.BAR_DIAMETERS_MM
        planner = self._planner()
        blue = SolidColorBrush(Color.FromRgb(31, 78, 160))
        dark = SolidColorBrush(Color.FromRgb(40, 40, 40))
        other = rf.SIDE if view == rf.FRONT else rf.FRONT
        for layer in (rf.BOTTOM, rf.TOP):
            m = steel[layer]
            if not m.get("on"):
                continue
            d_in, d_out = (m["dx"], m["dy"]) if view == rf.FRONT else (m["dy"], m["dx"])
            level = 0 if view == rf.FRONT else 1
            d_first = mm[m["dx"]] / 1000.0
            for outer, inner in self._center_outlines(view):
                for path in rf.mesh_layer_paths(inner, layer, mm[d_in] / 1000.0,
                                                float(m.get("hook_a", m.get("hook")) or 0.0),
                                                hook_b=float(m.get("hook_b", m.get("hook")) or 0.0)):
                    if level:
                        shift = d_first if layer == rf.BOTTOM else -d_first
                        path = [(u, z + shift) for u, z in path]
                    for a, b in zip(path, path[1:]):
                        rv._line(canvas, frame, a, b, dark, 2.5)
                # the other direction cut square: dots on their level
                d = mm[d_out] / 1000.0
                zs = [q[1] for q in inner]
                lvl = (1 - level)
                z = (min(zs) + d / 2.0 + lvl * d_first) if layer == rf.BOTTOM else (max(zs) - d / 2.0 - lvl * d_first)
                lo, hi = planner._range(other, d)
                a = "y" if view == rf.FRONT else "x"
                for pos in rf.mesh_positions(lo, hi, m, a):
                    u = -pos if view == rf.SIDE else pos
                    if rf.point_inside(inner, (u, z)):
                        self._dot(canvas, frame, u, z, max(2.5, d * frame[0] / 2.0), dark)
                        self._dots[view].append((u, z, d_out))
        on = steel.get("sketch_on", True)
        for i, item in enumerate(steel.get("sketch", [])):
            if item["view"] != view:
                continue
            pts = [tuple(q) for q in item["pts"]]
            if item.get("closed"):
                pts = pts + [pts[0]]
            color = (SolidColorBrush(Color.FromRgb(230, 120, 20)) if i == self.selected_sketch
                     else blue if on else SolidColorBrush(Color.FromArgb(70, 31, 78, 160)))
            for a, b in zip(pts, pts[1:]):
                rv._line(canvas, frame, a, b, color, 3 if i == self.selected_sketch else 2.5)
            if i == self.selected_sketch:
                for k, (a, b) in enumerate(zip(pts, pts[1:])):
                    rv._text(canvas, frame, (a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0, u"T{}".format(k + 1),
                             brush=color, size=10, bold=True)
            rv._text(canvas, frame, pts[0][0], pts[0][1] + 0.04, u"\u00d8{} @{:g}".format(item["d"], float(item["s"])),
                     brush=blue, size=10)
        if self.draft and self.draft_view == view:
            pts = list(self.draft) + ([self.cursor] if self.cursor else [])
            if self.btn_rect.IsChecked and len(pts) == 2:
                (a0, b0), (a1, b1) = pts
                pts = [(a0, b0), (a1, b0), (a1, b1), (a0, b1), (a0, b0)]
            for a, b in zip(pts, pts[1:]):
                rv._line(canvas, frame, a, b, SolidColorBrush(Color.FromRgb(230, 80, 30)), 2, dash=True)

    def _draw_elevation(self, canvas, view):
        if view == PLAN:
            self._draw_plan()
            return
        canvas.Children.Clear()
        f = self.foundation
        if f is None or canvas.ActualWidth < 10:
            return
        at = self._center(view)
        if view == rf.FRONT:
            self.txt_front_title.Text = u"ALZADO FRONTAL - corte A en y = {:.2f} m (mirando hacia +Y)".format(at)
        else:
            self.txt_side_title.Text = u"ALZADO LATERAL - corte B en x = {:.2f} m (mirando hacia -X)".format(at)
        outlines = self._section(view)
        if not outlines:
            rv._text(canvas, rv.fit_frame(canvas.ActualWidth, canvas.ActualHeight, -1, -1, 1, 1),
                     0, 0, u"El corte {} no pasa por el elemento: muevelo en la planta".format(
                         u"A" if view == rf.FRONT else u"B"), size=11)
            return
        us = [p[0] for pts, _ in outlines for p in pts]
        zs = [p[1] for pts, _ in outlines for p in pts]
        fitted = rv.fit_frame(canvas.ActualWidth, canvas.ActualHeight,
                              min(us) - 0.25, min(zs) - 0.25, max(us) + 0.25, max(zs) + 0.25)
        self._fitted = getattr(self, "_fitted", {})
        self._fitted[view] = fitted
        frame = self.navs[view].resolve(fitted)
        self._frames[view] = frame
        covers = self.state.covers.get(self.state.active, {})
        gray = SolidColorBrush(Color.FromRgb(225, 228, 232))
        for pts, tags in outlines:
            rv._polygon(canvas, frame, pts, gray)
            n = len(pts)
            for i in range(n):
                a, b = pts[i], pts[(i + 1) % n]
                number = tags[i]
                color = face_color(number) if number else Color.FromRgb(80, 80, 80)
                bold = number is not None and number == self.highlight
                rv._line(canvas, frame, a, b, SolidColorBrush(color), 5 if bold else 2.5)
                if number and ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) > 0.01:
                    rv._text(canvas, frame, (a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0, u"{}".format(number),
                             brush=SolidColorBrush(color), size=12, bold=True)
            inner = rf.inner_outline(pts, [covers.get(t, rf.DEFAULT_COVER_CM) / 100.0 if t else 0.0
                                           for t in tags])
            for i in range(len(inner)):
                rv._line(canvas, frame, inner[i], inner[(i + 1) % len(inner)],
                         SolidColorBrush(Color.FromRgb(90, 90, 90)), 1, dash=True)
        self._draw_bars(canvas, frame, view)

    # 2D zoom / pan: wheel zooms, middle button drags, middle double click fits
    def _wheel(self, view, canvas, args):
        fitted = getattr(self, "_fitted", {}).get(view)
        if fitted:
            self.navs[view].wheel(fitted, args.GetPosition(canvas), args.Delta)
            self._draw_elevation(canvas, view)
        args.Handled = True

    def _down(self, view, canvas, args):
        if args.ChangedButton != MouseButton.Middle:
            return
        if args.ClickCount == 2:
            self.navs[view].reset()
            self._draw_elevation(canvas, view)
            return
        fitted = getattr(self, "_fitted", {}).get(view)
        if fitted:
            self.navs[view].start_pan(fitted, args.GetPosition(canvas))
            canvas.CaptureMouse()

    def _up(self, view, canvas, args):
        self.navs[view].end_pan()
        canvas.ReleaseMouseCapture()

    def front_wheel(self, sender, args):
        self._wheel(rf.FRONT, self.canvas_front, args)

    def side_wheel(self, sender, args):
        self._wheel(rf.SIDE, self.canvas_side, args)

    def front_down(self, sender, args):
        self._down(rf.FRONT, self.canvas_front, args)

    def side_down(self, sender, args):
        self._down(rf.SIDE, self.canvas_side, args)

    def front_up(self, sender, args):
        self._up(rf.FRONT, self.canvas_front, args)

    def side_up(self, sender, args):
        self._up(rf.SIDE, self.canvas_side, args)

    # steel 3D mouse
    def steel3d_wheel(self, sender, args):
        self.scene_steel.wheel(args.Delta)

    def steel3d_mouse_down(self, sender, args):
        if args.ChangedButton in (MouseButton.Left, MouseButton.Middle):
            self.scene_steel.start_drag(args.GetPosition(self.view3d_steel), args.ChangedButton == MouseButton.Left)
            self.view3d_steel.CaptureMouse()

    def steel3d_mouse_move(self, sender, args):
        self.scene_steel.drag(args.GetPosition(self.view3d_steel))

    def steel3d_mouse_up(self, sender, args):
        self.scene_steel.end_drag()
        self.view3d_steel.ReleaseMouseCapture()

    # 3D mouse
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

    # -- actions ---------------------------------------------------------
    def pick_click(self, sender, args):
        self.action = "pick"
        self.Close()

    def run_click(self, sender, args):
        if not self.state.picked_ids:
            forms.alert(u"Selecciona primero la cimentacion.", title="Acero")
            return
        self.action = "run"
        self.Close()

    def file_save_click(self, sender, args):
        covers = self.state.covers.get(self.state.active)
        if not covers:
            return
        dialog = SaveFileDialog()
        dialog.Filter = "Recubrimientos (*.json)|*.json"
        dialog.FileName = u"Recubrimientos_cimentacion.json"
        if dialog.ShowDialog():
            with io.open(dialog.FileName, "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"recubrimientos": covers}, ensure_ascii=False, indent=2))

    def file_open_click(self, sender, args):
        if self.foundation is None:
            return
        dialog = OpenFileDialog()
        dialog.Filter = "Recubrimientos (*.json)|*.json"
        if dialog.ShowDialog():
            with io.open(dialog.FileName, encoding="utf-8") as fh:
                data = json.loads(fh.read()).get("recubrimientos", {})
            covers = self.state.covers[self.state.active]
            for k, v in data.items():
                if int(k) in covers:
                    covers[int(k)] = float(v)
            self._fill_covers()
            self._draw_elevations()


def pick(state):
    try:
        refs = revit.uidoc.Selection.PickObjects(
            ObjectType.Element, _FoundationFilter(), "Selecciona la cimentacion y pulsa Finalizar")
    except OperationCanceledException:
        return
    state.picked_ids = [id_of(r.ElementId) for r in refs]
    state.active = state.picked_ids[0] if state.picked_ids else None


# --- main -------------------------------------------------------------------
state = State()
pre = [doc.GetElement(i) for i in revit.uidoc.Selection.GetElementIds()]
state.picked_ids = [id_of(e.Id) for e in pre if e is not None and _FoundationFilter().AllowElement(e)]
xaml = os.path.join(SCRIPT_DIR, "AceroForm.xaml")
while True:
    window = CimentacionWindow(xaml, state)
    window.ShowDialog()
    if window.action == "pick":
        pick(state)
        continue
    break

def _hook_tips_inside(rebar):
    """True when both hook tips of a closed stirrup lie inside it."""
    from Autodesk.Revit.DB.Structure import MultiplanarOption
    body = list(rebar.GetCenterlineCurves(True, True, True, MultiplanarOption.IncludeOnlyPlanarCurves, 0))
    full = list(rebar.GetCenterlineCurves(False, False, False, MultiplanarOption.IncludeAllMultiplanarCurves, 0))
    pts = [c.GetEndPoint(0) for c in body]
    if len(pts) < 3:
        return True
    e1 = (pts[1] - pts[0]).Normalize()
    e2 = e1.CrossProduct(pts[2] - pts[0]).Normalize().CrossProduct(e1)
    flat = lambda q: ((q - pts[0]).DotProduct(e1), (q - pts[0]).DotProduct(e2))
    poly = [flat(q) for q in pts]
    return all(rf.point_inside(poly, flat(t)) for t in (full[0].GetEndPoint(0), full[-1].GetEndPoint(1)))


def create_closed_stirrup(pts, bar_type, hook, host, normal):
    """A sketched closed stirrup with its hooks turned inward: made one way
    round and, if Revit turns its hooks out, remade the other way. The
    hooks go at its widest corner (a sharp corner leaves no room for a
    135-degree hook, as on site)."""
    import math

    def angle(k):
        a, b, c = pts[k - 1], pts[k], pts[(k + 1) % len(pts)]
        u, v = a - b, c - b
        return math.acos(max(-1.0, min(1.0, u.Normalize().DotProduct(v.Normalize()))))
    start = max(range(len(pts)), key=angle)
    pts = pts[start:] + pts[:start]
    for points in (pts, [pts[0]] + list(reversed(pts[1:]))):
        curves = List[DB.Curve]([DB.Line.CreateBound(points[k], points[(k + 1) % len(points)])
                                 for k in range(len(points))])
        rebar = Rebar.CreateFromCurves(
            doc, RebarStyle.StirrupTie, bar_type, hook, hook, host, normal,
            curves, RebarHookOrientation.Left, RebarHookOrientation.Left, True, True)
        if _hook_tips_inside(rebar):
            return rebar
        doc.Delete(rebar.Id)
    curves = List[DB.Curve]([DB.Line.CreateBound(pts[k], pts[(k + 1) % len(pts)]) for k in range(len(pts))])
    return Rebar.CreateFromCurves(doc, RebarStyle.StirrupTie, bar_type, hook, hook, host, normal,
                                  curves, RebarHookOrientation.Right, RebarHookOrientation.Right, True, True)


if window.action == "run":
    bar_types = rc.BarTypes(doc)
    hooks = rc.StirrupHooks(doc)
    splice = su.splice_for_generation(su.load_splice_settings())
    failed = []
    t = DB.Transaction(doc, "Acero cimentacion")
    t.Start()
    try:
        rc.ensure_parameters(doc)
        fw_params.ensure_parameters(doc, "Acero", [(COVERS_PARAM, True, [FOUNDATION_BIC], False),
                                                    (STEEL_PARAM, True, [FOUNDATION_BIC], False)])
        for element_id in state.picked_ids:
            element = doc.GetElement(DB.ElementId(element_id))
            element_type = doc.GetElement(element.GetTypeId())
            covers = state.covers.get(element_id) or read_covers(element_type)
            steel = state.steel.get(element_id) or read_steel(element_type)
            for name, value in ((COVERS_PARAM, json.dumps(dict((str(k), v) for k, v in covers.items()))),
                                (STEEL_PARAM, json.dumps(steel, ensure_ascii=False))):
                p = element_type.LookupParameter(name)
                if p is not None and not p.IsReadOnly:
                    p.Set(value)
            try:
                foundation = rf.Foundation(element)
                rc.delete_generated(doc, element)
                planner = rf.BarPlanner(foundation, covers, rs.BAR_DIAMETERS_MM)
                for view, key, path, first, count, spacing, tname in rf.bar_sets(planner.all_bars(steel, splice)):
                    bar_type = rc.named_bar_type(doc, tname, key) or bar_types.pick(key, u"ZAPATA")  # automatic: prefer a footing type
                    closed = len(path) > 3 and path[0] == path[-1]
                    if closed:
                        path = path[:-1]
                    if view == rf.FRONT:
                        pts = [foundation.world(u, first, z) for u, z in path]
                        normal = foundation.uy
                    else:
                        pts = [foundation.world(first, -u, z) for u, z in path]
                        normal = foundation.ux.Negate()
                    if closed:
                        # a sketched closed stirrup, with the stirrup hooks:
                        # counterclockwise around its normal, so the Left
                        # hooks turn inward (as in Acero Columna)
                        area = DB.XYZ(0, 0, 0)
                        for k in range(len(pts)):
                            area = area + pts[k].CrossProduct(pts[(k + 1) % len(pts)])
                        if area.DotProduct(normal) < 0:
                            pts = list(reversed(pts))
                        rebar = create_closed_stirrup(pts, bar_type, hooks.get(key), element, normal)
                    else:
                        curves = List[DB.Curve]([DB.Line.CreateBound(pts[k], pts[k + 1]) for k in range(len(pts) - 1)])
                        rebar = Rebar.CreateFromCurves(
                            doc, RebarStyle.Standard, bar_type, None, None, element, normal, curves,
                            RebarHookOrientation.Right, RebarHookOrientation.Right, True, True)
                    if count > 1:
                        # sets run along the normal; the side ones go towards +x
                        rebar.GetShapeDrivenAccessor().SetLayoutAsNumberWithSpacing(
                            count, spacing / rf.FT, view == rf.FRONT, True, True)
                    rc._tag(rebar, element)
            except Exception as e:
                failed.append(u"{} (id {}): {}".format(element.Name, element_id, e))
        t.Commit()
    except Exception:
        t.RollBack()
        raise
    if failed:
        forms.alert(u"No se pudo generar:\n- " + u"\n- ".join(failed), title="Acero")
