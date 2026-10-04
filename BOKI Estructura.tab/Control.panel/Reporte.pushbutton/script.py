# -*- coding: utf-8 -*-
"""Reporte: cantidades del acero del modelo por categoria y diametro,
concreto y cuantias, con el resumen de la Revision E.060 y de las
Interferencias; exporta a Excel (CSV) y a un reporte HTML para imprimir."""

__title__ = "Reporte"
__author__ = "Revit MCP"

import datetime
import io
import os
import sys

SCRIPT_DIR = os.path.dirname(__file__)
EXT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", ".."))
REVIT_MCP_DIR = os.path.join(EXT_ROOT, "revit_mcp")
if REVIT_MCP_DIR not in sys.path:
    sys.path.append(REVIT_MCP_DIR)

import rebar_spec as rs
import steel_report as sr

reload(rs)
reload(sr)

from pyrevit import revit, DB, forms
from Autodesk.Revit.DB.Structure import Rebar
from Microsoft.Win32 import SaveFileDialog
import clr
clr.AddReference("System.Data")
from System.Data import DataTable

doc = revit.doc
FT = 0.3048
CATEGORY_NAMES = {
    int(DB.BuiltInCategory.OST_StructuralColumns): u"Columnas",
    int(DB.BuiltInCategory.OST_StructuralFraming): u"Vigas",
    int(DB.BuiltInCategory.OST_Walls): u"Muros",
    int(DB.BuiltInCategory.OST_StructuralFoundation): u"Cimentaciones",
    int(DB.BuiltInCategory.OST_Stairs): u"Escaleras",
    int(DB.BuiltInCategory.OST_Floors): u"Losas",
}
VIEWS = (u"Por categoria", u"Por categoria y diametro")


def _key(diameter_mm):
    best = min(rs.BAR_DIAMETERS_MM.items(), key=lambda kv: abs(kv[1] - diameter_mm))
    return best[0] if abs(best[1] - diameter_mm) < 0.6 else u"{:.0f}mm".format(diameter_mm)


def collect():
    """bars [(category, key, count, length m)] and concrete volumes of the
    elements holding steel {category: m3}."""
    bars, hosts = [], {}
    for r in DB.FilteredElementCollector(doc).OfClass(Rebar):
        host = doc.GetElement(r.GetHostId())
        cat = CATEGORY_NAMES.get(host.Category.Id.IntegerValue, host.Category.Name) if host and host.Category \
            else u"Otros"
        bt = doc.GetElement(r.GetTypeId())
        d = (getattr(bt, "BarNominalDiameter", None) or bt.BarDiameter) * FT * 1000.0
        bars.append((cat, _key(d), r.Quantity, r.TotalLength * FT))
        if host is not None:
            hosts[host.Id.IntegerValue] = (cat, host)
    volumes = {}
    for cat, host in hosts.values():
        volumes[cat] = volumes.get(cat, 0.0) + _volume(host)
    return bars, volumes


def _volume(element):
    """Concrete volume (m3): the element's own parameter, else its solids
    (stairs have no volume parameter)."""
    p = element.get_Parameter(DB.BuiltInParameter.HOST_VOLUME_COMPUTED)
    if p is not None and p.HasValue and p.AsDouble() > 1e-9:
        return p.AsDouble() * FT ** 3
    total = 0.0
    stack = list(element.get_Geometry(DB.Options()) or [])
    while stack:
        g = stack.pop()
        if isinstance(g, DB.Solid):
            total += g.Volume
        elif isinstance(g, DB.GeometryInstance):
            stack.extend(list(g.GetInstanceGeometry()))
    return total * FT ** 3


def _load(button, function):
    """A function of another Control button's script (its own window stays
    its own: only its checking routine is run here)."""
    folder = os.path.join(os.path.dirname(SCRIPT_DIR), button + ".pushbutton")
    path = os.path.join(folder, "script.py")
    src = io.open(path, encoding="utf-8").read()
    g = {"__name__": "reporte", "__file__": path}
    exec(compile(src, path, "exec"), g)
    return g[function], g


def checks():
    review, clashes = None, None
    try:
        run_review, g = _load(u"Revision E060", "run_review")
        findings, _ = run_review()
        review = g["ev"].summary(findings)
    except Exception:
        pass
    try:
        run_check, _ = _load(u"Interferencias", "run_check")
        result = run_check()
        clashes = (result[2], result[3])
    except Exception:
        pass
    return review, clashes


class ReportWindow(forms.WPFWindow):
    def __init__(self, xaml_file_path, bars, volumes):
        forms.WPFWindow.__init__(self, xaml_file_path)
        self.rows, self.totals = sr.aggregate(bars, rs.bar_weight_kg_per_m, rs.BAR_DIAMETERS_MM)
        self.volumes = volumes
        count, length, weight = sr.grand_total(self.totals)
        vol = sum(volumes.get(c, 0.0) for c in self.totals)
        self.txt_project.Text = u"{}  -  {}".format(doc.Title, datetime.date.today().isoformat())
        self.txt_weight.Text = u"{:,.1f} kg".format(weight)
        self.txt_length.Text = u"{:,.1f} m".format(length)
        self.txt_count.Text = u"{}".format(count)
        self.txt_ratio.Text = u"{:,.1f}".format(weight / vol) if vol > 1e-6 else u"-"
        self.cbo_view.ItemsSource = list(VIEWS)
        self.cbo_view.SelectedIndex = 0
        if not self.rows:
            self.txt_status.Text = u"El modelo no tiene acero (Rebar) todavia."

    def view_changed(self, sender, args):
        table = DataTable()
        if self.cbo_view.SelectedIndex == 1:
            for h in (u"Categoria", u"Diametro", u"Barras", u"Longitud (m)", u"Peso (kg)"):
                table.Columns.Add(h)
            for cat, key, c, l, w in self.rows:
                table.Rows.Add(cat, key, u"{}".format(c), u"{:,.2f}".format(l), u"{:,.2f}".format(w))
        else:
            rat = sr.ratios(self.totals, self.volumes)
            for h in (u"Categoria", u"Barras", u"Longitud (m)", u"Peso (kg)", u"Concreto (m3)", u"Cuantia (kg/m3)"):
                table.Columns.Add(h)
            for cat in sorted(self.totals, key=lambda k: -self.totals[k][2]):
                c, l, w = self.totals[cat]
                table.Rows.Add(cat, u"{}".format(c), u"{:,.2f}".format(l), u"{:,.2f}".format(w),
                               u"{:,.2f}".format(self.volumes.get(cat, 0.0)),
                               u"{:,.1f}".format(rat[cat]) if cat in rat else u"-")
        self.grid.ItemsSource = table.DefaultView

    def grid_column(self, sender, args):
        from System.Windows.Controls import DataGridLength
        args.Column.Width = DataGridLength(170)

    def _save(self, filter_text, name):
        dialog = SaveFileDialog()
        dialog.Filter = filter_text
        dialog.FileName = name
        return dialog.FileName if dialog.ShowDialog() else None

    def csv_click(self, sender, args):
        path = self._save("CSV para Excel (*.csv)|*.csv", u"Reporte_acero.csv")
        if path:
            with io.open(path, "w", encoding="utf-8-sig") as fh:
                fh.write(sr.to_csv(self.rows, self.totals, self.volumes))
            self.txt_status.Text = u"Exportado: {}".format(path)

    def html_text(self):
        review, clashes = checks() if self.chk_checks.IsChecked else (None, None)
        return sr.to_html(u"Reporte del acero", doc.Title, datetime.date.today().isoformat(),
                          self.rows, self.totals, self.volumes, review, clashes)

    def html_click(self, sender, args):
        path = self._save("Reporte (*.html)|*.html", u"Reporte_acero.html")
        if path:
            with io.open(path, "w", encoding="utf-8") as fh:
                fh.write(self.html_text())
            self.txt_status.Text = u"Guardado: {}".format(path)
            try:
                os.startfile(path)
            except Exception:
                pass

    def close_click(self, sender, args):
        self.Close()


if __name__ == "__main__":
    bars, volumes = collect()
    ReportWindow(os.path.join(SCRIPT_DIR, "ReportForm.xaml"), bars, volumes).ShowDialog()
