# -*- coding: utf-8 -*-
"""Metrado del acero estructural generado por BOKI Metalicas. Reglas propias
en revit_mcp/metal_metrado.py."""

__title__ = "Metrado\nmetálico"
__author__ = "BOKI"

import os
import sys

SCRIPT_DIR = os.path.dirname(__file__)
EXT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", ".."))
REVIT_MCP_DIR = os.path.join(EXT_ROOT, "revit_mcp")
if REVIT_MCP_DIR not in sys.path:
    sys.path.append(REVIT_MCP_DIR)

import metal_metrado as mm

reload(mm)

from pyrevit import revit, DB, forms
from System.Windows import Thickness, FontWeights, GridLength, TextAlignment
from System.Windows.Controls import Grid, ColumnDefinition, TextBlock, Border
from System.Windows.Media import Color, SolidColorBrush

doc = revit.doc
COLS = [(u"Grupo", 220), (u"Perfil", 220), (u"Cantidad", 90), (u"Longitud (m)", 110), (u"Peso (kg)", 110),
        (u"Pintura (m²)", 110)]


def brush(r, g, b, a=255):
    return SolidColorBrush(Color.FromArgb(a, r, g, b))


class MetradoWindow(forms.WPFWindow):
    def __init__(self, xaml_file_path):
        forms.WPFWindow.__init__(self, xaml_file_path)
        try:
            import boki_brand  # BOKI ESTRUCTURAS and the seal in the header
            boki_brand.apply(self)
        except Exception:
            pass
        self.rows, self.total = mm.measure(doc, DB)
        self.pnl_rows.Children.Add(self._row([c for c, _ in COLS], head=True))
        for r in self.rows:
            self.pnl_rows.Children.Add(self._row([r["grupo"], r["perfil"], u"{}".format(r["cant"]),
                                                  u"{:.2f}".format(r["largo"]) if r["largo"] else u"-",
                                                  u"{:,.1f}".format(r["kg"]), u"{:.2f}".format(r["m2"]) if r["m2"] else u"-"]))
        t = self.total
        self.txt_total.Text = (u"TOTAL: {:,.1f} kg ({:.2f} t) - {:.2f} m de perfiles - {:.2f} m² de pintura".format(
            t["kg"], t["kg"] / 1000.0, t["largo"], t["m2"]) if self.rows else
            u"No hay elementos de BOKI Metálicas en el modelo.")

    def _row(self, values, head=False):
        g = Grid()
        for _, w in COLS:
            cd = ColumnDefinition()
            cd.Width = GridLength(w)
            g.ColumnDefinitions.Add(cd)
        for k, v in enumerate(values):
            tb = TextBlock()
            tb.Text = v
            tb.Margin = Thickness(4, 3, 4, 3)
            tb.Foreground = brush(22, 50, 79)
            if head:
                tb.FontWeight = FontWeights.Bold
            if k >= 2:
                tb.TextAlignment = TextAlignment.Right
            Grid.SetColumn(tb, k)
            g.Children.Add(tb)
        b = Border()
        b.BorderBrush = brush(208, 215, 223)
        b.BorderThickness = Thickness(0, 0, 0, 1)
        b.Child = g
        if head:
            b.Background = brush(233, 238, 244)
        return b

    def export_click(self, sender, args):
        path = forms.save_file(file_ext="csv", default_name="BOKI_metrado_metalico")
        if path:
            mm.to_csv(path, self.rows, self.total)
            forms.alert(u"Guardado en:\n{}".format(path), title=u"Metrado")

    def close_click(self, sender, args):
        self.Close()


# --- main -------------------------------------------------------------------
if __name__ == "__main__":
    MetradoWindow(os.path.join(SCRIPT_DIR, "MetradoForm.xaml")).ShowDialog()
