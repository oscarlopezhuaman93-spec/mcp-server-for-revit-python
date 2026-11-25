# -*- coding: utf-8 -*-
"""Correas y arriostres sobre los porticos BOKI ya generados: correas C de
techo, templadores, largueros laterales y cruces de arriostre. Reglas propias
en revit_mcp/metal_correas.py (no usa las de Porticos ni las de acero de
refuerzo; lee la nave del modelo)."""

__title__ = "Correas y\narriostres"
__author__ = "BOKI"

import os
import sys

SCRIPT_DIR = os.path.dirname(__file__)
EXT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", ".."))
REVIT_MCP_DIR = os.path.join(EXT_ROOT, "revit_mcp")
if REVIT_MCP_DIR not in sys.path:
    sys.path.append(REVIT_MCP_DIR)

import metal_correas as mc

reload(mc)

from pyrevit import revit, DB, forms
from System.Windows import Point
from System.Windows.Controls import Canvas, TextBlock
from System.Windows.Media import Color, SolidColorBrush
from System.Windows.Shapes import Line

doc = revit.doc


def brush(r, g, b, a=255):
    return SolidColorBrush(Color.FromArgb(a, r, g, b))


C_RAFTER, C_PURLIN, C_SAG = brush(31, 111, 197), brush(46, 158, 91), brush(138, 151, 166)
C_BRACE, C_TEXT, C_RIDGE = brush(208, 48, 43), brush(22, 50, 79), brush(255, 179, 71)


def _num(text, default):
    try:
        return float((text or u"").replace(u",", u"."))
    except ValueError:
        return default


class CorreasWindow(forms.WPFWindow):
    def __init__(self, xaml_file_path):
        forms.WPFWindow.__init__(self, xaml_file_path)
        try:
            import boki_brand  # BOKI ESTRUCTURAS and the seal in the header
            boki_brand.apply(self)
        except Exception:
            pass
        self.action = None
        self._ready = False
        try:
            self.shed = mc.Shed(doc, DB)
            self.txt_shed.Text = self.shed.text()
        except mc.SpecError as e:
            self.shed = None
            self.txt_shed.Text = u"{}".format(e)
        names = mc.profiles()  # C con labios y tubos rectangulares
        self.cbo_purlin.ItemsSource = names
        self.cbo_girt.ItemsSource = names
        rods = [mc.RODS[k] for k in sorted(mc.RODS)]
        self.cbo_sag.ItemsSource = rods
        self.cbo_brace.ItemsSource = rods
        cfg = mc.saved_config(doc, DB) or mc.config()
        self.chk_roof.IsChecked = cfg["roof"]
        self.chk_sag.IsChecked = cfg["sag"]
        self.chk_girts.IsChecked = cfg["girts"]
        self.chk_brace_roof.IsChecked = cfg["brace_roof"]
        self.chk_brace_walls.IsChecked = cfg["brace_walls"]
        self.cbo_purlin.SelectedItem = cfg["purlin"]
        self.cbo_girt.SelectedItem = cfg["girt"]
        self.cbo_sag.SelectedItem = mc.RODS.get(cfg["sag_rod"])
        self.cbo_brace.SelectedItem = mc.RODS.get(cfg["brace_rod"])
        self.txt_spacing.Text = u"{:.2f}".format(cfg["spacing"])
        self.txt_first.Text = u"{:.2f}".format(cfg["first"])
        self.txt_girt_step.Text = u"{:.2f}".format(cfg["girt_step"])
        self._ready = True
        self.refresh()

    def _rod(self, combo, default):
        for k, v in mc.RODS.items():
            if v == combo.SelectedItem:
                return k
        return default

    def form(self):
        return {
            "roof": bool(self.chk_roof.IsChecked), "purlin": self.cbo_purlin.SelectedItem,
            "spacing": _num(self.txt_spacing.Text, 0.0), "first": _num(self.txt_first.Text, 0.15),
            "sag": bool(self.chk_sag.IsChecked), "sag_rod": self._rod(self.cbo_sag, 12.7),
            "girts": bool(self.chk_girts.IsChecked), "girt": self.cbo_girt.SelectedItem,
            "girt_step": _num(self.txt_girt_step.Text, 0.0),
            "brace_roof": bool(self.chk_brace_roof.IsChecked), "brace_walls": bool(self.chk_brace_walls.IsChecked),
            "brace_rod": self._rod(self.cbo_brace, 19.05),
        }

    def geometry(self):
        """(porticos, luz, alero, pendiente %, separacion) de la nave leida."""
        if self.shed is None:
            return 7, 20.0, 7.0, 10.0, 6.0, None
        s = self.shed
        eave = (s.eave_z - s.base_z) * mc.FT
        rise = (s.frames[0][4].Z - s.eave_z) * mc.FT
        bay = s.frames[0][1].DistanceTo(s.frames[1][1]) * mc.FT
        self.waters = s.water_lengths()
        return len(s.frames), s.span, eave, rise / (s.span / 2.0) * 100.0, bay, self.waters[0]

    def refresh(self):
        if not self._ready:
            return
        try:
            cfg = mc.config(self.form())
        except mc.SpecError as e:
            self.txt_error.Text = u"{}".format(e)
            return
        self.txt_error.Text = u""
        frames, span, eave, slope, bay, water = self.geometry()
        cfg["bay"] = bay
        s = mc.summary(cfg, frames, span, eave, slope, water,
                       getattr(self, "waters", None), self.shed.walls if self.shed else True)
        self.txt_summary.Text = (
            u"Techo: {} líneas de correas x {} paños = {} correas {} (separación real {:.2f} m) - {} templadores\n"
            u"Paredes: {} líneas = {} largueros {} - Arriostres: {} cruces de techo + {} cruces de pared\n"
            u"Peso aprox.: correas {:.1f} t + largueros {:.1f} t + templadores {:.1f} t + arriostres {:.1f} t = {:.1f} t").format(
            s["purlin_lines"], frames - 1, s["purlins"], cfg["purlin"], s["spacing_real"], s["sag"],
            s["girt_lines"], s["girts"], cfg["girt"], s["brace_roof"], s["brace_walls"],
            s["kg_purlins"] / 1000, s["kg_girts"] / 1000, s["kg_sag"] / 1000, s["kg_brace"] / 1000, s["kg_total"] / 1000)
        self.draw(cfg, frames, span, slope, bay, water)

    def draw(self, cfg, frames, span, slope, bay, water_len=None):
        cnv = self.cnv
        cnv.Children.Clear()
        W, H = cnv.ActualWidth, cnv.ActualHeight
        if W < 50 or H < 50:
            return
        length = bay * (frames - 1)
        sc = min((W - 140) / length, (H - 90) / span)
        ox, oy = 70 + (W - 140 - length * sc) / 2.0, 45

        def P(x, y):
            return Point(ox + x * sc, oy + y * sc)

        def line(a, b, br, w):
            ln = Line()
            ln.X1, ln.Y1, ln.X2, ln.Y2 = a.X, a.Y, b.X, b.Y
            ln.Stroke, ln.StrokeThickness = br, w
            cnv.Children.Add(ln)

        water = water_len or (span / 2.0) * (1 + (slope / 100.0) ** 2) ** 0.5
        st = mc.purlin_stations(water, cfg["spacing"], cfg["first"]) if cfg["roof"] else []
        k = (span / 2.0) / ((span / 2.0) * (1 + (slope / 100.0) ** 2) ** 0.5)
        two = len(getattr(self, "waters", [0, 0])) > 1
        for s in st:
            for y in ((s * k, span - s * k) if two else (s / water * span,)):
                line(P(0, y), P(length, y), C_PURLIN, 2)
        for i in range(frames - 1):
            if cfg["roof"] and cfg["sag"] and len(st) > 1:
                for f in (1 / 3.0, 2 / 3.0):
                    x = (i + f) * bay
                    line(P(x, st[0] * k), P(x, span - st[0] * k), C_SAG, 1)
        bays = [0, frames - 2] if frames > 2 else [0]
        if cfg["brace_roof"]:
            for i in bays:
                x0, x1 = i * bay, (i + 1) * bay
                for y0, y1 in ((0, span / 4), (span / 4, span / 2), (span / 2, 3 * span / 4), (3 * span / 4, span)):
                    line(P(x0, y0), P(x1, y1), C_BRACE, 2)
                    line(P(x1, y0), P(x0, y1), C_BRACE, 2)
        for i in range(frames):
            line(P(i * bay, 0), P(i * bay, span), C_RAFTER, 5)
        line(P(-0.5, span / 2), P(length + 0.5, span / 2), C_RIDGE, 1)
        if self.shed is not None:
            for i, f in enumerate(self.shed.frames):
                tb = TextBlock()
                tb.Text, tb.Foreground, tb.FontSize = f[0], C_TEXT, 11
                Canvas.SetLeft(tb, P(i * bay, 0).X - 8)
                Canvas.SetTop(tb, P(0, span).Y + 6)
                cnv.Children.Add(tb)

    def changed(self, sender, args):
        self.refresh()

    def cancel_click(self, sender, args):
        self.Close()

    def run_click(self, sender, args):
        if self.shed is None:
            forms.alert(u"Primero genera los Pórticos (botón Pórticos).", title=u"Correas y arriostres")
            return
        try:
            self.cfg = mc.config(self.form())
        except mc.SpecError as e:
            forms.alert(u"{}".format(e), title=u"Correas y arriostres")
            return
        self.action = "run"
        self.Close()


# --- main -------------------------------------------------------------------
if __name__ == "__main__":
    window = CorreasWindow(os.path.join(SCRIPT_DIR, "CorreasForm.xaml"))
    window.ShowDialog()
    if window.action == "run":
        level = sorted(DB.FilteredElementCollector(doc).OfClass(DB.Level),
                       key=lambda l: abs(l.Elevation - window.shed.base_z))[0]
        t = DB.Transaction(doc, u"BOKI Correas y arriostres")
        t.Start()
        try:
            made = mc.generate(doc, DB, level, window.cfg)
            t.Commit()
            forms.alert(u"Creados: {} correas, {} templadores, {} largueros, {} barras de arriostre de techo y {} de pared.".format(
                len(made["purlins"]), len(made["sag"]), len(made["girts"]), len(made["brace_roof"]),
                len(made["brace_walls"])), title=u"Correas y arriostres")
        except Exception as e:
            t.RollBack()
            forms.alert(u"No se pudo generar:\n{}".format(e), title=u"Correas y arriostres")
