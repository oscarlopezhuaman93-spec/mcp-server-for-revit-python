# -*- coding: utf-8 -*-
"""Porticos metalicos de alma llena (nave industrial): desde los ejes de
Revit, columnas y vigas W, cartelas, vigas de amarre y placas base.
Reglas propias en revit_mcp/metal_spec.py y metal_porticos.py (no usa nada
de los botones de acero de refuerzo)."""

__title__ = "Pórticos"
__author__ = "BOKI"

import os
import sys

SCRIPT_DIR = os.path.dirname(__file__)
EXT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", ".."))
REVIT_MCP_DIR = os.path.join(EXT_ROOT, "revit_mcp")
if REVIT_MCP_DIR not in sys.path:
    sys.path.append(REVIT_MCP_DIR)

import metal_spec as ms
import metal_porticos as mp

reload(ms)
reload(mp)

from pyrevit import revit, DB, forms
from Autodesk.Revit.Exceptions import OperationCanceledException
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType
from System.Windows import Point
from System.Windows.Controls import Canvas, TextBlock
from System.Windows.Media import Color, PointCollection, SolidColorBrush
from System.Windows.Shapes import Line, Polygon, Rectangle

doc = revit.doc
uidoc = revit.uidoc


def brush(r, g, b, a=255):
    return SolidColorBrush(Color.FromArgb(a, r, g, b))


C_STEEL, C_HAUNCH, C_TEXT = brush(31, 111, 197), brush(127, 179, 230), brush(22, 50, 79)
C_DIM, C_PLATE, C_GROUND = brush(192, 95, 0), brush(22, 50, 79), brush(138, 151, 166)


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


class PorticosWindow(forms.WPFWindow):
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
        self.levels = sorted(DB.FilteredElementCollector(doc).OfClass(DB.Level), key=lambda l: l.Elevation)
        self.cbo_level.ItemsSource = [l.Name for l in self.levels]
        names = [p[0] for p in ms.W_PROFILES]
        for c in (self.cbo_column, self.cbo_rafter, self.cbo_tie):
            c.ItemsSource = names
        cfg = state.form or mp.saved_config(doc) or ms.config()
        self.txt_span.Text = u"{:.2f}".format(cfg["span"])
        self.txt_eave.Text = u"{:.2f}".format(cfg["eave"])
        self.txt_slope.Text = u"{:g}".format(cfg["slope"])
        self.txt_haunch.Text = u"{:.2f}".format(cfg["haunch"])
        self.txt_plate.Text = u"x".join(u"{:g}".format(v) for v in cfg["plate"])
        self.txt_grout.Text = u"{:g}".format(cfg["grout"])
        self.cbo_column.SelectedItem = cfg["column"]
        self.cbo_rafter.SelectedItem = cfg["rafter"]
        self.cbo_tie.SelectedItem = cfg["tie"]
        lv = cfg.get("level")
        self.cbo_level.SelectedIndex = next((i for i, l in enumerate(self.levels) if l.Name == lv), 0)
        self.layout = None
        self.layout_error = None
        if state.grid_ids:
            grids = [doc.GetElement(DB.ElementId(i)) for i in state.grid_ids]
            try:
                self.layout = mp.Layout([g for g in grids if g is not None], _num(self.txt_span.Text, None))
            except ms.SpecError as e:
                self.layout_error = u"{}".format(e)
        self._ready = True
        self.refresh()

    # --- datos ---------------------------------------------------------------
    def form(self):
        plate = [_num(x, 0.0) for x in (self.txt_plate.Text or u"").lower().replace(u" ", u"").split(u"x")]
        return {
            "span": _num(self.txt_span.Text, 0.0), "eave": _num(self.txt_eave.Text, 0.0),
            "slope": _num(self.txt_slope.Text, 0.0), "haunch": _num(self.txt_haunch.Text, 0.0),
            "column": self.cbo_column.SelectedItem, "rafter": self.cbo_rafter.SelectedItem,
            "tie": self.cbo_tie.SelectedItem, "grout": _num(self.txt_grout.Text, 25.0),
            "plate": plate if len(plate) == 3 else ms.DEFAULTS["plate"],
            "level": self.cbo_level.SelectedItem,
        }

    def config(self):
        cfg = ms.config(self.form())
        if self.layout is not None and self.layout.spacings:
            cfg["spacing"] = sum(self.layout.spacings) / len(self.layout.spacings)
        return cfg

    # --- vista previa -------------------------------------------------------
    def refresh(self):
        if not self._ready:
            return
        self.txt_axes.Text = (self.layout.text() if self.layout is not None
                              else self.layout_error or u"No hay ejes seleccionados.")
        try:
            cfg = self.config()
            err = u""
            if self.layout is not None and abs(self.layout.span - cfg["span"]) > 0.01:
                err = u"La luz escrita ({:.2f} m) no es la de los ejes ({:.2f} m): se usa la de los ejes.".format(
                    cfg["span"], self.layout.span)
                cfg["span"] = round(self.layout.span, 3)
        except ms.SpecError as e:
            self.txt_error.Text = u"{}".format(e)
            self.cnv.Children.Clear()
            return
        self.txt_error.Text = err
        frames = len(self.layout.frames) if self.layout is not None else 7
        bays = max(frames - 1, 0)
        s = ms.summary(cfg, frames, bays)
        self.txt_summary.Text = (
            u"{} pórticos - {} columnas {} ({:.2f} m) - {} vigas {} ({:.2f} m) - {} cartelas - "
            u"{} vigas de amarre {} - {} placas base - {} pernos\n"
            u"Peso aprox.: {:.1f} t (columnas {:.1f} t, vigas {:.1f} t, amarres {:.1f} t, cartelas {:.1f} t, placas {:.1f} t)\n"
            u"Nada se crea hasta pulsar Generar; luego se edita con esta misma ventana.").format(
            s["frames"], s["columns"], cfg["column"], s["col_len"], s["rafters"], cfg["rafter"], s["raf_len"],
            s["haunches"], s["ties"], cfg["tie"], s["plates"], s["bolts"], s["kg_total"] / 1000.0,
            s["kg_columns"] / 1000.0, s["kg_rafters"] / 1000.0, s["kg_ties"] / 1000.0,
            s["kg_haunches"] / 1000.0, s["kg_plates"] / 1000.0)
        self.draw(cfg)

    def draw(self, cfg):
        cnv = self.cnv
        cnv.Children.Clear()
        W, H = cnv.ActualWidth, cnv.ActualHeight
        if W < 50 or H < 50:
            return
        span, eave, top = cfg["span"], cfg["eave"], cfg["eave"] + ms.rise(cfg)
        sc = min((W - 160) / span, (H - 120) / top)
        ox, oy = 90 + (W - 160 - span * sc) / 2.0, H - 70

        def P(u, z):
            return Point(ox + u * sc, oy - z * sc)

        def line(a, b, br, w):
            ln = Line()
            ln.X1, ln.Y1, ln.X2, ln.Y2 = a.X, a.Y, b.X, b.Y
            ln.Stroke, ln.StrokeThickness = br, w
            cnv.Children.Add(ln)

        def text(t, x, y, br):
            tb = TextBlock()
            tb.Text, tb.Foreground, tb.FontSize = t, br, 11
            Canvas.SetLeft(tb, x)
            Canvas.SetTop(tb, y)
            cnv.Children.Add(tb)

        line(Point(20, oy + 4), Point(W - 20, oy + 4), C_GROUND, 1)
        for tri in ms.haunch_triangles(cfg):
            pg = Polygon()
            pc = PointCollection()
            for u, z in tri:
                pc.Add(P(u, z))
            pg.Points, pg.Fill = pc, C_HAUNCH
            cnv.Children.Add(pg)
        d_col = max(4.0, ms.depth_m(cfg["column"]) * sc)
        d_raf = max(4.0, ms.depth_m(cfg["rafter"]) * sc)
        for key, w in (("col_left", d_col), ("col_right", d_col), ("rafter_left", d_raf), ("rafter_right", d_raf)):
            (u0, z0), (u1, z1) = ms.frame_points(cfg)[key]
            line(P(u0, z0), P(u1, z1), C_STEEL, min(w, 10))
        for u in (0.0, span):
            r = Rectangle()
            r.Width, r.Height, r.Fill = 30, 6, C_PLATE
            Canvas.SetLeft(r, P(u, 0).X - 15)
            Canvas.SetTop(r, oy - 3)
            cnv.Children.Add(r)
        line(Point(P(0, 0).X, oy + 34), Point(P(span, 0).X, oy + 34), C_DIM, 1)
        text(u"Luz {:.2f} m".format(span), (P(0, 0).X + P(span, 0).X) / 2 - 30, oy + 36, C_DIM)
        line(Point(P(0, 0).X - 45, oy), Point(P(0, 0).X - 45, P(0, eave).Y), C_DIM, 1)
        text(u"{:.2f} m".format(eave), P(0, 0).X - 90, P(0, eave / 2).Y, C_DIM)
        text(u"{:g} %".format(cfg["slope"]), P(span * 0.22, 0).X, P(0, eave + ms.rise(cfg) * 0.6).Y - 22, C_DIM)
        text(cfg["column"], P(0, 0).X + 10, P(0, eave * 0.45).Y, C_STEEL)
        text(cfg["rafter"], P(span * 0.25, 0).X, P(0, eave + ms.rise(cfg) * 0.5).Y + 12, C_STEEL)
        if cfg["haunch"] > 0:
            text(u"cartela {:.2f} m".format(cfg["haunch"]), P(0, 0).X + 10, P(0, eave - 1.6).Y, C_DIM)
        text(u"placa base + {} pernos".format(cfg["bolts"]), P(span, 0).X - 70, oy + 10, C_DIM)

    # --- eventos ------------------------------------------------------------
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
            forms.alert(u"Primero elige los ejes (botón SELECCIONAR EJES).", title=u"Pórticos")
            return
        try:
            cfg = self.config()
        except ms.SpecError as e:
            forms.alert(u"{}".format(e), title=u"Pórticos")
            return
        cfg["span"] = round(self.layout.span, 3)
        self.state.form = self.form()
        self.cfg = cfg
        self.level = self.levels[max(self.cbo_level.SelectedIndex, 0)]
        self.action = "run"
        self.Close()


def pick(state):
    try:
        refs = uidoc.Selection.PickObjects(ObjectType.Element, _GridFilter(),
                                          u"Elige los ejes de los pórticos y los dos de columnas; Finalizar")
        state.grid_ids = [r.ElementId.IntegerValue for r in refs]
    except OperationCanceledException:
        pass


# --- main -------------------------------------------------------------------
if __name__ == "__main__":
    state = State()
    pre = [doc.GetElement(i) for i in uidoc.Selection.GetElementIds()]
    state.grid_ids = [e.Id.IntegerValue for e in pre if isinstance(e, DB.Grid)]
    xaml = os.path.join(SCRIPT_DIR, "PorticosForm.xaml")
    while True:
        window = PorticosWindow(xaml, state)
        window.ShowDialog()
        if window.action == "pick":
            pick(state)
            continue
        break
    if window.action == "run":
        t = DB.Transaction(doc, u"BOKI Pórticos")
        t.Start()
        try:
            made = mp.generate(doc, window.layout, window.level, window.cfg)
            t.Commit()
            forms.alert(u"Pórticos creados: {} columnas, {} vigas, {} vigas de amarre, {} cartelas, {} placas base.".format(
                len(made["columns"]), len(made["rafters"]), len(made["ties"]), len(made["haunches"]),
                len(made["plates"])), title=u"Pórticos")
        except Exception as e:
            t.RollBack()
            forms.alert(u"No se pudo generar:\n{}".format(e), title=u"Pórticos")
