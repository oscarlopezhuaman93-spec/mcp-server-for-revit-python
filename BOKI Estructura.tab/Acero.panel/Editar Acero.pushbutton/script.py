# -*- coding: utf-8 -*-
"""Editar Acero: a bar or stirrup made by the plugin, its segment lengths
typed in and moved by steps, never out of the concrete less the cover."""
__title__ = "Editar\nAcero"
__author__ = "BOKI"

import os
import sys

SCRIPT_DIR = os.path.dirname(__file__)
REVIT_MCP_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", "..", "revit_mcp"))
if REVIT_MCP_DIR not in sys.path:
    sys.path.append(REVIT_MCP_DIR)

import rebar_spec as rs
import rebar_columns as rc
import rebar_edit as re_
reload(re_)

from pyrevit import revit, DB, forms
from Autodesk.Revit.DB.Structure import Rebar
from Autodesk.Revit.Exceptions import OperationCanceledException
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType
from System.Windows import Point, Thickness, VerticalAlignment
from System.Windows.Controls import Canvas, DockPanel, TextBlock, TextBox
from System.Windows.Media import Color, PointCollection, SolidColorBrush
from System.Windows.Shapes import Ellipse, Polyline

doc, uidoc = revit.doc, revit.uidoc
FT = re_.FT


def brush(r, g, b):
    return SolidColorBrush(Color.FromArgb(255, r, g, b))


class _RebarFilter(ISelectionFilter):
    def AllowElement(self, e):
        return isinstance(e, Rebar)

    def AllowReference(self, ref, point):
        return False


def host_cover(host):
    """The host type's cover (cm) as set in its Acero window, else 4."""
    try:
        cfg = rc.read_type_config(doc.GetElement(host.GetTypeId()))
        v = (cfg.get("EA_Recubrimiento_cm") or u"").strip()
        return float(v) if v else 4.0
    except Exception:
        return 4.0


class EditarWindow(forms.WPFWindow):
    def __init__(self, xaml, edit):
        forms.WPFWindow.__init__(self, xaml)
        self.edit = edit
        self.points = list(edit.points)
        self.action = None
        host = edit.host
        self.txt_info.Text = u"Barra {}  diametro {}  -  anfitrion {} ({})".format(
            edit.rebar.Id.IntegerValue, edit.key or u"?", host.Id.IntegerValue if host else u"-",
            host.Category.Name if host is not None and host.Category else u"-")
        self.txt_cover.Text = u"{:g}".format(host_cover(host) if host is not None else 4.0)
        self.boxes = []
        self._segments()
        self._check()
        # the arrows work at once: the focus on the drawing, not in a box
        self.canvas.Focusable = True
        self.Loaded += lambda s, a: self.canvas.Focus()
        self.canvas.MouseDown += lambda s, a: self.canvas.Focus()

    def _cover(self):
        try:
            return float(self.txt_cover.Text.replace(u",", u".")) / 100.0
        except ValueError:
            return 0.04

    def _status(self, text, ok=True):
        self.txt_status.Text = text
        self.txt_status.Foreground = brush(26, 120, 60) if ok else brush(176, 32, 32)

    def _segments(self):
        self.pnl_segments.Children.Clear()
        self.boxes = []
        lengths = [a.DistanceTo(b) * FT for a, b in zip(self.points, self.points[1:])]
        for k, L in enumerate(lengths):
            row = DockPanel()
            row.Margin = Thickness(0, 0, 0, 3)
            label = TextBlock()
            label.Text = u"Tramo {}:".format(k + 1)
            label.Width = 70
            label.VerticalAlignment = VerticalAlignment.Center
            box = TextBox()
            box.Text = u"{:.3f}".format(L)
            box.IsEnabled = not self.edit.closed
            row.Children.Add(label)
            row.Children.Add(box)
            self.pnl_segments.Children.Add(row)
            self.boxes.append(box)
        total = sum(lengths)
        kg = total * rs.bar_weight_kg_per_m(self.edit.key) if self.edit.key else 0.0
        self.txt_total.Text = u"Total: {:.2f} m  -  {:.2f} kg{}".format(
            total, kg, u"  (estribo: se mueve, su forma no)" if self.edit.closed else u"")
        self.redraw()

    def _check(self):
        ok = self.edit.fits(self.points, self._cover())
        self.txt_cover_state.Text = (u"OK: dentro del recubrimiento" if ok
                                     else u"ATENCION: fuera del recubrimiento en algun punto")
        self.txt_cover_state.Foreground = brush(26, 120, 60) if ok else brush(176, 32, 32)
        return ok

    def lengths_click(self, sender, args):
        try:
            lengths = [float(b.Text.replace(u",", u".")) for b in self.boxes]
        except ValueError:
            self._status(u"Las longitudes deben ser numeros (m).", False)
            return
        new = self.edit.with_lengths(lengths)
        if not self.edit.fits(new, self._cover()):
            self._status(u"Con esas longitudes la barra sale del recubrimiento: no se aplico.", False)
            self._segments()
            return
        self.points = new
        self._segments()
        self._check()
        self._status(u"Longitudes aplicadas (falta 'Aplicar en Revit').")

    KEYS = {"Up": "up", "Down": "down", "Left": "left", "Right": "right", "PageUp": "fwd", "Prior": "fwd",
            "PageDown": "back", "Next": "back"}

    def window_key(self, sender, args):
        """The keyboard's arrows move the bar a step (Re Pag / Av Pag: along);
        a box being typed in keeps its keys."""
        if isinstance(args.OriginalSource, TextBox):
            return
        tag = self.KEYS.get(str(args.Key))
        if tag is None:
            return

        class _Key(object):
            Tag = tag
        self.move_click(_Key(), None)
        args.Handled = True

    def move_click(self, sender, args):
        try:
            step = float(self.txt_step.Text.replace(u",", u".")) / 100.0 / FT
        except ValueError:
            self._status(u"El paso debe ser un numero (cm).", False)
            return
        e = self.edit
        v = {"up": e.up, "down": -e.up, "left": -e.across, "right": e.across,
             "fwd": e.along, "back": -e.along}[sender.Tag]
        self.points, share = e.clamp_move(self.points, v * step, self._cover())
        self.redraw()
        self._check()
        if share >= 0.999:
            self._status(u"Movida {:.1f} cm.".format(step * FT * 100))
        else:
            self._status(u"Solo se pudo mover {:.1f} cm: llego al limite del recubrimiento.".format(
                step * FT * 100 * share), False)

    def resized(self, sender, args):
        self.redraw()

    def redraw(self):
        canvas = self.canvas
        canvas.Children.Clear()
        w, h = canvas.ActualWidth, canvas.ActualHeight
        if w < 10 or h < 10:
            return
        e = self.edit
        o = e.points[0]

        pu, pv = e.plane

        def uv(p):  # the bar's own plane
            q = p - o
            return (q.DotProduct(pu) * FT, q.DotProduct(pv) * FT)
        sets = [[uv(p) for p in e.points], [uv(p) for p in self.points]]
        us = [p[0] for s in sets for p in s]
        vs = [p[1] for s in sets for p in s]
        span = max(max(us) - min(us), max(vs) - min(vs), 0.1)
        k = min((w - 60) / span, (h - 60) / span)
        cu, cv = (max(us) + min(us)) / 2.0, (max(vs) + min(vs)) / 2.0

        def at(p):
            return Point(w / 2.0 + (p[0] - cu) * k, h / 2.0 - (p[1] - cv) * k)
        for pts, color, thick in ((sets[0], brush(170, 170, 170), 2.0), (sets[1], brush(214, 63, 31), 3.5)):
            line = Polyline()
            line.Points = PointCollection([at(p) for p in pts])
            line.Stroke = color
            line.StrokeThickness = thick
            canvas.Children.Add(line)
        for p in sets[1]:
            q = at(p)
            dot = Ellipse()
            dot.Width = dot.Height = 9
            dot.Fill = brush(255, 179, 71)
            Canvas.SetLeft(dot, q.X - 4.5)
            Canvas.SetTop(dot, q.Y - 4.5)
            canvas.Children.Add(dot)

    def apply_click(self, sender, args):
        if not self._check():
            self._status(u"La barra esta fuera del recubrimiento: corrige antes de aplicar.", False)
            return
        self.action = "apply"
        self.Close()

    def cancel_click(self, sender, args):
        self.Close()


if __name__ == "__main__":
    sel = [doc.GetElement(i) for i in uidoc.Selection.GetElementIds()]
    bar = next((e for e in sel if isinstance(e, Rebar)), None)
    if bar is None:
        try:
            bar = doc.GetElement(uidoc.Selection.PickObject(ObjectType.Element, _RebarFilter(),
                                                            u"Clic en la barra o estribo a editar"))
        except OperationCanceledException:
            raise SystemExit
    edit = re_.BarEdit(bar)
    window = EditarWindow(os.path.join(SCRIPT_DIR, "EditarForm.xaml"), edit)
    window.ShowDialog()
    if window.action == "apply":
        unchanged = all(a.IsAlmostEqualTo(b) for a, b in zip(window.points, edit.points))
        if unchanged:
            raise SystemExit  # nothing changed
        with revit.Transaction(u"BOKI Editar acero"):
            new = edit.apply(window.points)
            if edit.host is not None:
                re_.host_weight(doc, edit.host, rc.WEIGHT_PARAM)
        uidoc.Selection.SetElementIds(__import__("System").Collections.Generic.List[DB.ElementId]([new.Id]))
