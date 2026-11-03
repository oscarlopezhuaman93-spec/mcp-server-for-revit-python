# -*- coding: utf-8 -*-
"""Cuadro de viguetas (dibujo joist_table): las viguetas de las losas
aligeradas configuradas en Acero Losa, una columna por vigueta distinta
(TIPO, DIMENSIONES, ACERO INFERIOR, ACERO SUPERIOR, TEMPERATURA y la
seccion en T con sus ladrillos), con vista previa antes de crear la
leyenda. El dibujo en Revit y la vista previa son los del Cuadro de Vigas
(se cargan de su script, sin cambiarlo)."""

__title__ = "Cuadro\nViguetas"
__author__ = "Revit MCP"

import imp
import os
import sys

SCRIPT_DIR = os.path.dirname(__file__)
EXT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", ".."))
REVIT_MCP_DIR = os.path.join(EXT_ROOT, "revit_mcp")
if REVIT_MCP_DIR not in sys.path:
    sys.path.append(REVIT_MCP_DIR)

import rebar_spec as rs
import column_table as ct
import joist_table as jt
import rebar_slabs as rsb
import slab_rebar as sr

reload(jt)

from pyrevit import revit, DB, forms
from System.Windows import Thickness
from System.Windows.Controls import CheckBox

# the legend drawer, the preview drawer and the table styles: the beams' table ones
lib = imp.load_source("boki_cuadro_vigas", os.path.join(SCRIPT_DIR, "..", "Cuadro Vigas.pushbutton", "script.py"))

doc = revit.doc
MM = rs.BAR_DIAMETERS_MM
TITLE = u"CUADRO DE VIGUETAS"


def collect_joists():
    """The distinct viguetas of the ribbed slabs set up in Acero Losa."""
    found = []
    for floor in DB.FilteredElementCollector(doc).OfClass(DB.Floor):
        p = floor.LookupParameter(rsb.PARAM)
        if p is None or not p.AsString():
            continue
        settings = sr.read(p.AsString())
        if settings.get("kind") != sr.ALIGERADA:
            continue
        ftype = floor.FloorType
        thick = ftype.get_Parameter(DB.BuiltInParameter.FLOOR_ATTR_DEFAULT_THICKNESS_PARAM)
        h = jt.nominal_thickness(lib._name(ftype), thick.AsDouble() * 0.3048 if thick else 0.20)
        try:
            found.append(jt.Joist(h, sr.ribbed(settings), settings.get("cover"), MM, slabs=[floor.Id]))
        except Exception:
            continue  # a slab whose settings can't be read: left out
    return jt.group(found)


def make(items, title, scale, detail, transparency=20, dim_name=None, label_name=None):
    t = DB.Transaction(doc, "Cuadro de viguetas")
    t.Start()
    try:
        view = lib.new_legend(title, scale)
        if view is None:
            t.RollBack()
            forms.alert(u"El proyecto no tiene ninguna leyenda: crea una (Vista > Leyendas) y vuelve a intentar.",
                        title="Cuadro de viguetas")
            return None
        lib.ensure_styles()
        dims = dict((lib._name(x), x) for x in DB.FilteredElementCollector(doc).OfClass(DB.DimensionType)
                    if x.StyleType == DB.DimensionStyleType.Linear)
        texts = dict((lib._name(x), x) for x in DB.FilteredElementCollector(doc).OfClass(DB.TextNoteType))
        jt.draw_table(lib.RevitDrawer(view, transparency, dims.get(dim_name), texts.get(label_name)),
                      items, title, scale, detail, MM)
        t.Commit()
    except Exception:
        t.RollBack()
        raise
    return view


class ViguetasWindow(forms.WPFWindow):
    def __init__(self, xaml_file_path, joists):
        forms.WPFWindow.__init__(self, xaml_file_path)
        self.joists = joists
        self.result = None
        self._ready = False
        self.txt_title.Text = TITLE
        self.cbo_scale.ItemsSource = [str(s) for s in ct.SCALES]
        self.cbo_scale.SelectedItem = u"10"
        self.cbo_detail.ItemsSource = list(ct.DETAILS)
        self.cbo_detail.SelectedItem = ct.DETAIL_HIGH
        self.cbo_alpha.ItemsSource = [str(v) for v in lib.TRANSPARENCIES]
        self.cbo_alpha.SelectedItem = u"20"
        dims = sorted(lib._name(x) for x in DB.FilteredElementCollector(doc).OfClass(DB.DimensionType)
                      if x.StyleType == DB.DimensionStyleType.Linear)
        self.cbo_dim.ItemsSource = [lib.NO_DIMS] + dims
        linear = [n for n in dims if u"arc" not in n.lower() and u"angul" not in n.lower()
                  and u"radi" not in n.lower()] or dims
        self.cbo_dim.SelectedItem = next((n for n in linear if u"2.5" in n), linear[0] if linear else lib.NO_DIMS)
        texts = sorted(lib._name(x) for x in DB.FilteredElementCollector(doc).OfClass(DB.TextNoteType))
        self.cbo_label.ItemsSource = texts
        self.cbo_label.SelectedItem = next((n for n in texts if u"2.5" in n),
                                           u"BOKI Cuadro 2mm" if u"BOKI Cuadro 2mm" in texts
                                           else (texts[0] if texts else None))
        self.boxes = []
        for j in joists:
            cb = CheckBox()
            cb.Content = u"{}  - {}  ({} losa(s))".format(j.mark, j.rows()[0], len(j.slabs))
            cb.IsChecked = True
            cb.Margin = Thickness(0, 2, 0, 2)
            cb.Click += self.options_changed
            self.boxes.append((cb, j))
            self.panel_types.Children.Add(cb)
        self._ready = True
        self.SizeChanged += self.options_changed
        self.draw()

    def options(self):
        return ([j for cb, j in self.boxes if cb.IsChecked], (self.txt_title.Text or u"").strip() or TITLE,
                int(self.cbo_scale.SelectedItem or 10), self.cbo_detail.SelectedItem or ct.DETAIL_HIGH,
                int(self.cbo_alpha.SelectedItem or 20))

    def draw(self):
        canvas = self.canvas_preview
        canvas.Children.Clear()
        items, title, scale, detail, alpha = self.options()
        if not items:
            self.txt_status.Text = u"Marca al menos una vigueta."
            return

        class _Size(object):  # a throwaway pass: the size first
            def __getattr__(self, name):
                return lambda *a, **k: None
        w, h = jt.draw_table(_Size(), items, title, scale, detail, MM)
        avail = max(300.0, (self.scroll_preview.ActualWidth or 1000.0) - 30.0)
        zoom = max(2.0, min(6.0, avail / w))
        jt.draw_table(lib.CanvasDrawer(canvas, zoom, alpha), items, title, scale, detail, MM)
        canvas.Width, canvas.Height = w * zoom + 2, h * zoom + 2
        self.txt_status.Text = u"{} vigueta(s) - escala 1:{} - detalle {} - {:.0f} x {:.0f} mm en papel".format(
            len(items), scale, detail.lower(), w, h)

    def options_changed(self, sender, args):
        if self._ready:
            self.draw()

    def _set_all(self, value):
        for cb, _ in self.boxes:
            cb.IsChecked = value
        self.draw()

    def check_all(self, sender, args):
        self._set_all(True)

    def check_none(self, sender, args):
        self._set_all(False)

    def cancel_click(self, sender, args):
        self.Close()

    def create_click(self, sender, args):
        items, title, scale, detail, alpha = self.options()
        if not items:
            forms.alert(u"Marca al menos una vigueta.", title="Cuadro de viguetas")
            return
        dim = self.cbo_dim.SelectedItem
        self.result = (items, title, scale, detail, alpha, None if dim == lib.NO_DIMS else dim,
                       self.cbo_label.SelectedItem)
        self.Close()


if __name__ == "__main__":
    joists = collect_joists()
    if not joists:
        forms.alert(u"No hay losas aligeradas configuradas: configuralas primero en Acero Losa.",
                    title="Cuadro de viguetas")
    else:
        window = ViguetasWindow(os.path.join(SCRIPT_DIR, "CuadroForm.xaml"), joists)
        window.ShowDialog()
        if window.result:
            view = make(*window.result)
            if view is not None:
                revit.uidoc.ActiveView = view
