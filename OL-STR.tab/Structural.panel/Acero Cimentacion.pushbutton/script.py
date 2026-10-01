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
import rebar_foundation as rf
import rebar_views as rv
import utils as fw_utils

reload(fw_utils)
reload(fw_params)
reload(rf)
reload(rv)

from pyrevit import revit, DB, forms, script
from Autodesk.Revit.Exceptions import OperationCanceledException
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType
from Microsoft.Win32 import OpenFileDialog, SaveFileDialog
from System.Windows import Thickness, VerticalAlignment, TextWrapping
from System.Windows.Controls import DockPanel, Dock, ListBoxItem, StackPanel, TextBlock, TextBox, Orientation
from System.Windows.Input import MouseButton
from System.Windows.Media import Color, SolidColorBrush
from System.Windows.Media.Media3D import (AmbientLight, DiffuseMaterial, DirectionalLight, GeometryModel3D,
                                          MeshGeometry3D, Model3DGroup, Point3D, Vector3D)
from System.Windows.Media import Colors
from System.Windows.Shapes import Rectangle

doc = revit.doc
FOUNDATION_BIC = DB.BuiltInCategory.OST_StructuralFoundation
COVERS_PARAM = "EA_Cim_Recubrimientos"  # JSON {face number: cm} on the type
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


class CimentacionWindow(forms.WPFWindow):
    def __init__(self, xaml_file_path, state):
        forms.WPFWindow.__init__(self, xaml_file_path)
        self.state = state
        self.action = None
        self.foundation = None
        self.highlight = None
        self.scene = rv.Scene3D(self.view3d)
        self._cache = {}
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
        if item.Tag not in self.state.covers:
            saved = read_covers(doc.GetElement(element.GetTypeId()))
            self.state.covers[item.Tag] = dict(
                (f.number, saved.get(f.number, default_cover(f))) for f in self.foundation.faces)
        x0, x1, y0, y1, z0, z1 = self.foundation.extent
        self.txt_title.Text = u"VISTA 3D - {}  ({:.2f} x {:.2f} x {:.2f} m)".format(
            element.Name, x1 - x0, y1 - y0, z1 - z0)
        self._fill_covers()
        self.highlight = None
        self.scene._extent = None
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

    def cover_changed(self, sender, args):
        try:
            value = float((sender.Text or u"").replace(u",", u"."))
        except ValueError:
            return
        self.state.covers[self.state.active][sender.Tag] = value
        self._draw_elevations()

    def cover_focus(self, sender, args):
        self.highlight = sender.Tag
        self.redraw()

    # -- views -----------------------------------------------------------
    def redraw(self):
        self._build_3d()
        self._draw_elevations()

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

    def _draw_elevation(self, canvas, view):
        canvas.Children.Clear()
        f = self.foundation
        if f is None or canvas.ActualWidth < 10:
            return
        key = (self.state.active, view)
        if key not in self._cache:
            try:
                self._cache[key] = f.section(view)
            except Exception as e:
                self._cache[key] = []
        outlines = self._cache[key]
        if not outlines:
            rv._text(canvas, rv.fit_frame(canvas.ActualWidth, canvas.ActualHeight, -1, -1, 1, 1),
                     0, 0, u"El corte por el centro no pasa por el elemento", size=11)
            return
        us = [p[0] for pts, _ in outlines for p in pts]
        zs = [p[1] for pts, _ in outlines for p in pts]
        frame = rv.fit_frame(canvas.ActualWidth, canvas.ActualHeight,
                             min(us) - 0.25, min(zs) - 0.25, max(us) + 0.25, max(zs) + 0.25)
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

if window.action == "run":
    t = DB.Transaction(doc, "Acero cimentacion - recubrimientos")
    t.Start()
    try:
        fw_params.ensure_parameters(doc, "Acero", [(COVERS_PARAM, True, [FOUNDATION_BIC], False)])
        for element_id, covers in state.covers.items():
            element_type = doc.GetElement(doc.GetElement(DB.ElementId(element_id)).GetTypeId())
            p = element_type.LookupParameter(COVERS_PARAM)
            if p is not None and not p.IsReadOnly:
                p.Set(json.dumps(dict((str(k), v) for k, v in covers.items())))
        t.Commit()
    except Exception:
        t.RollBack()
        raise
