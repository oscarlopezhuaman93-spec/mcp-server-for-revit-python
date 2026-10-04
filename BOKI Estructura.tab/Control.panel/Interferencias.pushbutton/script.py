# -*- coding: utf-8 -*-
"""Interferencias: choques entre barras de distintos elementos y avisos de
Revit sobre el acero, con la opcion de mostrarlos en el modelo."""

__title__ = "Interferencias"
__author__ = "Revit MCP"

import io
import os
import sys

SCRIPT_DIR = os.path.dirname(__file__)
EXT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", ".."))
REVIT_MCP_DIR = os.path.join(EXT_ROOT, "revit_mcp")
if REVIT_MCP_DIR not in sys.path:
    sys.path.append(REVIT_MCP_DIR)

import rebar_spec as rs
import rebar_clash as cl

reload(rs)
reload(cl)

from pyrevit import revit, DB, forms
from Autodesk.Revit.DB.Structure import Rebar, MultiplanarOption
from Microsoft.Win32 import SaveFileDialog
from System.Collections.Generic import List
import clr
clr.AddReference("System.Data")
from System.Data import DataTable
from System.Windows.Media import Color, SolidColorBrush

doc = revit.doc
FT = 0.3048
CLASH = u"Choque entre barras"
REVIT_WARNING = u"Aviso de Revit"
HEADERS = (u"Tipo", u"Elemento A", u"Elemento B", u"Detalle", u"X (m)", u"Y (m)", u"Z (m)")


def _diameter_label(rebar):
    bt = doc.GetElement(rebar.GetTypeId())
    d = (getattr(bt, "BarNominalDiameter", None) or bt.BarDiameter) * FT * 1000.0
    best = min(rs.BAR_DIAMETERS_MM.items(), key=lambda kv: abs(kv[1] - d))
    return best[0] if abs(best[1] - d) < 0.5 else u"{:.0f}mm".format(d)


def describe(rebar):
    host = doc.GetElement(rebar.GetHostId())
    hname = u"?"
    if host is not None:
        cat = host.Category.Name if host.Category else u""
        tname = doc.GetElement(host.GetTypeId())
        tname = tname.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM).AsString() if tname else u""
        hname = u"{} {} ({})".format(cat, tname[:28], host.Id.IntegerValue)
    return u"Ø{} en {}".format(_diameter_label(rebar), hname)


def collect_pieces(rebars):
    """Straight pieces of every bar of every set (arcs tessellated)."""
    pieces = []
    for rebar in rebars:
        bt = doc.GetElement(rebar.GetTypeId())
        radius = (getattr(bt, "BarNominalDiameter", None) or bt.BarDiameter) * FT / 2.0
        rid = rebar.Id.IntegerValue
        for i in range(rebar.NumberOfBarPositions):
            try:
                if not rebar.DoesBarExistAtPosition(i):
                    continue
                curves = rebar.GetTransformedCenterlineCurves(False, False, False,
                                                              MultiplanarOption.IncludeAllMultiplanarCurves, i)
            except Exception:
                continue
            for c in curves:
                pts = [(p.X * FT, p.Y * FT, p.Z * FT) for p in c.Tessellate()]
                for a, b in zip(pts, pts[1:]):
                    pieces.append((rid, i, a, b, radius))
    return pieces


def run_check():
    rebars = list(DB.FilteredElementCollector(doc).OfClass(Rebar))
    by_id = dict((r.Id.IntegerValue, r) for r in rebars)
    rows, ids = [], []
    clashes = cl.find_clashes(collect_pieces(rebars))
    for c in clashes:
        x, y, z = c["point"]
        rows.append((CLASH, describe(by_id[c["a"]]), describe(by_id[c["b"]]),
                     u"se superponen {:.0f} mm".format(c["overlap"] * 1000.0),
                     u"{:.2f}".format(x), u"{:.2f}".format(y), u"{:.2f}".format(z)))
        ids.append([DB.ElementId(c["a"]), DB.ElementId(c["b"])])
    n_warn = 0
    for w in doc.GetWarnings():
        failing = [doc.GetElement(i) for i in w.GetFailingElements()]
        bars = [e for e in failing if isinstance(e, Rebar)]
        if not bars:
            continue
        n_warn += 1
        rows.append((REVIT_WARNING, describe(bars[0]), describe(bars[1]) if len(bars) > 1 else u"-",
                     w.GetDescriptionText(), u"-", u"-", u"-"))
        ids.append([e.Id for e in bars])
    return rows, ids, len(clashes), n_warn, len(rebars)


class ClashWindow(forms.WPFWindow):
    def __init__(self, xaml_file_path, result):
        forms.WPFWindow.__init__(self, xaml_file_path)
        self.rows, self.ids, n_clash, n_warn, n_sets = result
        self.show_ids = None
        self.txt_clash.Text = u"Choques: {}".format(n_clash)
        self.txt_warn.Text = u"Avisos de Revit: {}".format(n_warn)
        self.txt_scope.Text = u"Revisados {} conjuntos de barras del modelo.".format(n_sets)
        table = DataTable()
        for h in HEADERS:
            table.Columns.Add(h)
        for r in self.rows:
            table.Rows.Add(*r)
        self.grid.ItemsSource = table.DefaultView
        self.txt_status.Text = (u"Sin interferencias." if not self.rows
                                else u"{} interferencias. Elige una fila y pulsa Mostrar.".format(len(self.rows)))

    def grid_column(self, sender, args):
        from System.Windows.Controls import DataGridLength
        args.Column.Width = DataGridLength({u"Tipo": 150, u"Elemento A": 330, u"Elemento B": 330, u"Detalle": 240}
                                           .get(args.Column.Header, 70))

    def grid_loading_row(self, sender, args):
        kind = args.Row.Item.Row[0]
        rgb = (252, 235, 235) if kind == CLASH else (250, 238, 218)
        args.Row.Background = SolidColorBrush(Color.FromRgb(*rgb))

    def show_click(self, sender, args):
        k = self.grid.SelectedIndex
        if k < 0:
            forms.alert(u"Elige una fila.", title=u"Interferencias")
            return
        self.show_ids = self.ids[k]
        self.Close()

    def export_click(self, sender, args):
        dialog = SaveFileDialog()
        dialog.Filter = "CSV para Excel (*.csv)|*.csv"
        dialog.FileName = u"Interferencias.csv"
        if dialog.ShowDialog():
            with io.open(dialog.FileName, "w", encoding="utf-8-sig") as fh:
                fh.write(cl.to_csv(self.rows))
            self.txt_status.Text = u"Exportado: {}".format(dialog.FileName)

    def close_click(self, sender, args):
        self.Close()


if __name__ == "__main__":
    window = ClashWindow(os.path.join(SCRIPT_DIR, "ClashForm.xaml"), run_check())
    window.ShowDialog()
    if window.show_ids:
        revit.uidoc.Selection.SetElementIds(List[DB.ElementId](window.show_ids))
        revit.uidoc.ShowElements(List[DB.ElementId](window.show_ids))
