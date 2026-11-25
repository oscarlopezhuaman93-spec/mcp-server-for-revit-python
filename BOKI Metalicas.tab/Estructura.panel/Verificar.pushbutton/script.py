# -*- coding: utf-8 -*-
"""Verificacion preliminar E.090 / AISC 360 de porticos, cerchas y correas
BOKI. Reglas propias en revit_mcp/metal_verificar.py."""

__title__ = "Verificar"
__author__ = "BOKI"

import codecs
import os
import sys

SCRIPT_DIR = os.path.dirname(__file__)
EXT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", ".."))
REVIT_MCP_DIR = os.path.join(EXT_ROOT, "revit_mcp")
if REVIT_MCP_DIR not in sys.path:
    sys.path.append(REVIT_MCP_DIR)

import metal_verificar as mv

reload(mv)

from pyrevit import revit, DB, forms
from System.Windows import Thickness, FontWeights, TextWrapping
from System.Windows.Controls import Grid, ColumnDefinition, TextBlock, Border
from System.Windows.Media import Color, SolidColorBrush
from System.Windows import GridLength, GridUnitType

doc = revit.doc


def brush(r, g, b, a=255):
    return SolidColorBrush(Color.FromArgb(a, r, g, b))


C_OK, C_WARN, C_BAD, C_HEAD, C_TEXT = brush(31, 157, 85), brush(230, 150, 20), brush(192, 57, 43), brush(22, 50, 79), brush(22, 50, 79)
COLS = [(u"Elemento", 170), (u"Perfil", 165), (u"Combinación", 120), (u"Pu (kN)", 70), (u"Mu (kN-m)", 75),
        (u"Capacidad / límite", 270), (u"D/C", 50), (u"Estado", 85)]


def _num(text, default):
    try:
        return float((text or u"").replace(u",", u"."))
    except ValueError:
        return default


class VerificarWindow(forms.WPFWindow):
    def __init__(self, xaml_file_path):
        forms.WPFWindow.__init__(self, xaml_file_path)
        try:
            import boki_brand  # BOKI ESTRUCTURAS and the seal in the header
            boki_brand.apply(self)
        except Exception:
            pass
        c = mv.config()
        for name, key in (("txt_dead", "dead"), ("txt_live", "live"), ("txt_wind", "wind"), ("txt_fy_w", "fy_w"),
                          ("txt_fy_tube", "fy_tube"), ("txt_fy_c", "fy_c"), ("txt_girt", "girt_step"),
                          ("txt_purlin", "purlin_step")):
            getattr(self, name).Text = u"{:g}".format(c[key])
        self.results = []
        self.run_click(None, None)

    def cfg(self):
        return mv.config(dict((key, _num(getattr(self, name).Text, mv.DEFAULTS[key])) for name, key in (
            ("txt_dead", "dead"), ("txt_live", "live"), ("txt_wind", "wind"), ("txt_fy_w", "fy_w"),
            ("txt_fy_tube", "fy_tube"), ("txt_fy_c", "fy_c"), ("txt_girt", "girt_step"), ("txt_purlin", "purlin_step"))))

    def _row(self, values, head=False, color=None):
        g = Grid()
        for _, w in COLS:
            cd = ColumnDefinition()
            cd.Width = GridLength(w)
            g.ColumnDefinitions.Add(cd)
        for k, v in enumerate(values):
            tb = TextBlock()
            tb.Text = v
            tb.Margin = Thickness(4, 3, 4, 3)
            tb.TextWrapping = TextWrapping.Wrap
            tb.Foreground = color if (color is not None and k >= 6) else C_TEXT
            if head or k >= 6:
                tb.FontWeight = FontWeights.Bold
            Grid.SetColumn(tb, k)
            g.Children.Add(tb)
        b = Border()
        b.BorderBrush = brush(208, 215, 223)
        b.BorderThickness = Thickness(0, 0, 0, 1)
        b.Child = g
        if head:
            b.Background = brush(233, 238, 244)
        return b

    def run_click(self, sender, args):
        pnl = self.pnl_results
        pnl.Children.Clear()
        try:
            self.results = mv.verify(doc, DB, self.cfg())
        except Exception as e:
            tb = TextBlock()
            tb.Text = u"No se pudo verificar: {}".format(e)
            tb.Foreground = C_BAD
            pnl.Children.Add(tb)
            return
        if not self.results:
            tb = TextBlock()
            tb.Text = u"No hay pórticos, cerchas ni correas BOKI en el modelo."
            pnl.Children.Add(tb)
            return
        for title, rows in self.results:
            tb = TextBlock()
            tb.Text = title
            tb.FontWeight = FontWeights.Bold
            tb.FontSize = 14
            tb.Margin = Thickness(0, 10, 0, 4)
            pnl.Children.Add(tb)
            pnl.Children.Add(self._row([c for c, _ in COLS], head=True))
            for r in rows:
                ratio = r["ratio"]
                col = C_OK if ratio <= 0.9 else (C_WARN if ratio <= 1.0 else C_BAD)
                state = u"OK" if ratio <= 1.0 else u"NO CUMPLE"
                pnl.Children.Add(self._row([r["elem"], r["perfil"], r["combo"],
                                            u"{:.1f}".format(r["Pu"]) if r["Pu"] else u"-",
                                            u"{:.1f}".format(r["Mu"]) if r["Mu"] else u"-",
                                            r["cap"], u"{:.2f}".format(ratio), state], color=col))

    def export_click(self, sender, args):
        path = forms.save_file(file_ext="csv", default_name="BOKI_verificacion_metalica")
        if not path:
            return
        with codecs.open(path, "w", "utf-8-sig") as f:
            f.write(u"Grupo;Elemento;Perfil;Combinacion;Pu (kN);Mu (kN-m);Capacidad / limite;D/C;Estado\n")
            for title, rows in self.results:
                for r in rows:
                    f.write(u"{};{};{};{};{:.1f};{:.1f};{};{:.2f};{}\n".format(
                        title, r["elem"], r["perfil"], r["combo"], r["Pu"], r["Mu"], r["cap"], r["ratio"],
                        u"OK" if r["ratio"] <= 1.0 else u"NO CUMPLE"))
        forms.alert(u"Guardado en:\n{}".format(path), title=u"Verificar")

    def close_click(self, sender, args):
        self.Close()


# --- main -------------------------------------------------------------------
if __name__ == "__main__":
    VerificarWindow(os.path.join(SCRIPT_DIR, "VerificarForm.xaml")).ShowDialog()
