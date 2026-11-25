# -*- coding: utf-8 -*-
"""Cerchas de tubo rectangular (arco, dos aguas o rectas; seccion plana o
espacial) sobre ejes. Reglas propias en revit_mcp/metal_cerchas.py."""

__title__ = "Cerchas"
__author__ = "BOKI"

import os
import sys

SCRIPT_DIR = os.path.dirname(__file__)
EXT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", ".."))
REVIT_MCP_DIR = os.path.join(EXT_ROOT, "revit_mcp")
if REVIT_MCP_DIR not in sys.path:
    sys.path.append(REVIT_MCP_DIR)

import metal_cerchas as mk

reload(mk)

from pyrevit import revit, DB, forms
from Autodesk.Revit.Exceptions import OperationCanceledException
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType
from System.Windows import Point
from System.Windows.Controls import Canvas, TextBlock
from System.Windows.Media import Color, SolidColorBrush
from System.Windows.Shapes import Line

doc = revit.doc
uidoc = revit.uidoc
SHAPE_TEXT = [(u"arco", u"En arco"), (u"dos_aguas", u"A dos aguas"), (u"recta", u"Recta (cordones paralelos)")]
SECTION_TEXT = [(u"espacial", u"Espacial rectangular"), (u"plana", u"Plana (un plano)")]


def brush(r, g, b, a=255):
    return SolidColorBrush(Color.FromArgb(a, r, g, b))


C_CHORD, C_WEB, C_DIM, C_GROUND = brush(31, 111, 197), brush(208, 48, 43), brush(192, 95, 0), brush(138, 151, 166)


def _num(text, default):
    try:
        return float((text or u"").replace(u",", u"."))
    except ValueError:
        return default


class State(object):
    def __init__(self):
        self.grid_ids = []
        self.form = None


class _GridFilter(ISelectionFilter):
    def AllowElement(self, element):
        return isinstance(element, DB.Grid)

    def AllowReference(self, reference, point):
        return False


class CerchasWindow(forms.WPFWindow):
    def __init__(self, xaml_file_path, state):
        forms.WPFWindow.__init__(self, xaml_file_path)
        try:
            import boki_brand  # BOKI ESTRUCTURAS and the seal in the header
            boki_brand.apply(self)
        except Exception:
            pass
        self.state = state
        self.action = None
        self._ready = False
        self.cbo_shape.ItemsSource = [t for _, t in SHAPE_TEXT]
        self.cbo_section.ItemsSource = [t for _, t in SECTION_TEXT]
        tubes = sorted(mk.TUBES)
        for c in (self.cbo_chord, self.cbo_web, self.cbo_cross):
            c.ItemsSource = tubes
        cfg = state.form or mk.saved_config(doc, DB) or mk.config()
        self.cbo_shape.SelectedItem = dict(SHAPE_TEXT)[cfg["shape"]]
        self.cbo_section.SelectedItem = dict(SECTION_TEXT)[cfg["section"]]
        for name, key in (("txt_rise", "rise"), ("txt_slope", "slope"), ("txt_depth", "depth"), ("txt_width", "width"),
                          ("txt_panel", "panel"), ("txt_support", "support")):
            getattr(self, name).Text = u"{:g}".format(cfg[key])
        self.cbo_chord.SelectedItem, self.cbo_web.SelectedItem, self.cbo_cross.SelectedItem = cfg["chord"], cfg["web"], cfg["cross"]
        self.layout, self.layout_error = None, None
        if state.grid_ids:
            gs = [doc.GetElement(DB.ElementId(i)) for i in state.grid_ids]
            try:
                self.layout = mk.Layout(DB, [g for g in gs if g is not None])
            except mk.SpecError as e:
                self.layout_error = u"{}".format(e)
        self._ready = True
        self.refresh()

    def form(self):
        inv = lambda pairs, text: next((k for k, t in pairs if t == text), pairs[0][0])
        return {
            "shape": inv(SHAPE_TEXT, self.cbo_shape.SelectedItem), "section": inv(SECTION_TEXT, self.cbo_section.SelectedItem),
            "rise": _num(self.txt_rise.Text, 0.0), "slope": _num(self.txt_slope.Text, 0.0),
            "depth": _num(self.txt_depth.Text, 0.0), "width": _num(self.txt_width.Text, 0.0),
            "panel": _num(self.txt_panel.Text, 0.0), "support": _num(self.txt_support.Text, 5.0),
            "chord": self.cbo_chord.SelectedItem, "web": self.cbo_web.SelectedItem, "cross": self.cbo_cross.SelectedItem,
        }

    def refresh(self):
        if not self._ready:
            return
        self.txt_axes.Text = self.layout.text() if self.layout else (self.layout_error or u"No hay ejes seleccionados.")
        try:
            cfg = mk.config(self.form())
        except mk.SpecError as e:
            self.txt_error.Text = u"{}".format(e)
            self.cnv.Children.Clear()
            return
        self.txt_error.Text = u""
        span = self.layout.span if self.layout else 47.4
        n = len(self.layout.frames) if self.layout else 1
        s = mk.summary(cfg, span, n)
        self.txt_summary.Text = (
            u"{} cercha(s) de {:.2f} m - {} paneles - {} montantes, {} diagonales, {} travesaños\n"
            u"Tubo: cordones {:.0f} m ({}), celosía {:.0f} m ({}), travesaños {:.0f} m ({})\n"
            u"Peso aprox.: {:.2f} t (cordones {:.2f} t, celosía {:.2f} t, travesaños {:.2f} t)").format(
            n, span, s["panels"], s["montantes"], s["diagonales"], s["travesanos"],
            s["chord_m"], cfg["chord"], s["web_m"], cfg["web"], s["cross_m"], cfg["cross"],
            s["kg_total"] / 1000, s["kg_chords"] / 1000, s["kg_web"] / 1000, s["kg_cross"] / 1000)
        self.draw(cfg, span)

    def draw(self, cfg, span):
        cnv = self.cnv
        cnv.Children.Clear()
        W, H = cnv.ActualWidth, cnv.ActualHeight
        if W < 50 or H < 50:
            return
        ns = mk.nodes(cfg, span)
        us = [p[0] for b, t in ns for p in (b, t)]
        zs = [p[1] for b, t in ns for p in (b, t)] + [0.0]
        umin, umax, zmax = min(us), max(us), max(zs)
        sc = min((W - 120) / (umax - umin), (H - 110) / max(zmax, 1.0))
        ox, oy = 60 + (W - 120 - (umax - umin) * sc) / 2.0 - umin * sc, H - 60

        def P(p):
            return Point(ox + p[0] * sc, oy - p[1] * sc)

        def line(a, b, br, w):
            ln = Line()
            ln.X1, ln.Y1, ln.X2, ln.Y2 = a.X, a.Y, b.X, b.Y
            ln.Stroke, ln.StrokeThickness = br, w
            cnv.Children.Add(ln)

        line(Point(20, oy + 4), Point(W - 20, oy + 4), C_GROUND, 1)
        for kind, a, b in mk.web(cfg, span):
            line(P(a), P(b), C_WEB if kind == "diagonal" else C_CHORD, 1)
        for i in range(len(ns) - 1):
            for k in (0, 1):
                line(P(ns[i][k]), P(ns[i + 1][k]), C_CHORD, 3)
        tb = TextBlock()
        tb.Text = u"Luz {:.2f} m - cumbre {:.2f} m sobre el apoyo - peralte {:.2f} m".format(
            span, max(t[1] for _, t in ns), cfg["depth"])
        tb.Foreground, tb.FontSize = C_DIM, 11
        Canvas.SetLeft(tb, 20)
        Canvas.SetTop(tb, oy + 12)
        cnv.Children.Add(tb)

    def changed(self, sender, args):
        self.refresh()

    def pick_click(self, sender, args):
        self.state.form = self.form()
        self.action = "pick"
        self.Close()

    def cancel_click(self, sender, args):
        self.Close()

    def run_click(self, sender, args):
        if self.layout is None:
            forms.alert(u"Primero elige los ejes.", title=u"Cerchas")
            return
        try:
            self.cfg = mk.config(self.form())
        except mk.SpecError as e:
            forms.alert(u"{}".format(e), title=u"Cerchas")
            return
        self.state.form = self.form()
        self.action = "run"
        self.Close()


def pick(state):
    try:
        refs = uidoc.Selection.PickObjects(ObjectType.Element, _GridFilter(),
                                          u"Elige los ejes de las cerchas y los dos de apoyo; Finalizar")
        state.grid_ids = [r.ElementId.IntegerValue for r in refs]
    except OperationCanceledException:
        pass


# --- main -------------------------------------------------------------------
if __name__ == "__main__":
    state = State()
    pre = [doc.GetElement(i) for i in uidoc.Selection.GetElementIds()]
    state.grid_ids = [e.Id.IntegerValue for e in pre if isinstance(e, DB.Grid)]
    while True:
        window = CerchasWindow(os.path.join(SCRIPT_DIR, "CerchasForm.xaml"), state)
        window.ShowDialog()
        if window.action == "pick":
            pick(state)
            continue
        break
    if window.action == "run":
        level = sorted(DB.FilteredElementCollector(doc).OfClass(DB.Level), key=lambda l: abs(l.Elevation))[0]
        t = DB.Transaction(doc, u"BOKI Cerchas")
        t.Start()
        try:
            made = mk.generate(doc, DB, window.layout, level, window.cfg)
            t.Commit()
            forms.alert(u"Cerchas creadas: {} cordones, {} montantes, {} diagonales, {} travesaños.".format(
                len(made["chords"]), len(made["montantes"]), len(made["diagonales"]), len(made["travesanos"])),
                title=u"Cerchas")
        except Exception as e:
            t.RollBack()
            forms.alert(u"No se pudo generar:\n{}".format(e), title=u"Cerchas")
