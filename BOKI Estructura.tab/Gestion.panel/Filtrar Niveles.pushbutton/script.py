# -*- coding: utf-8 -*-
"""Filtrar Niveles: muestra u oculta niveles en la vista segun su bloque
(prefijo del nombre, p. ej. "(B-05)") o un texto que contengan."""

__title__ = "Filtrar\nNiveles"
__author__ = "Revit MCP"
__doc__ = ("Muestra u oculta niveles en secciones y elevaciones por bloque "
           "(prefijo) o por un texto del nombre. No borra niveles.")

import os
import re

from pyrevit import revit, DB, forms
from System.Collections.Generic import List
from System.Windows import FontWeights
from System.Windows.Controls import ListBoxItem
from System.Windows.Media import Color, SolidColorBrush

doc = revit.doc
SCRIPT_DIR = os.path.dirname(__file__)
TITLE = u"Filtrar niveles"
ALL = u"Todos los bloques"
PREFIX = re.compile(u"^\\s*(\\([^)]*\\))")
FT = 0.3048
SECTION_TYPES = (DB.ViewType.Section, DB.ViewType.Elevation, DB.ViewType.Detail)
GRAY = SolidColorBrush(Color.FromRgb(160, 164, 170))
GREEN = SolidColorBrush(Color.FromRgb(31, 107, 44))


def prefix_of(name):
    m = PREFIX.match(name)
    return m.group(1) if m else None


def target_views(all_views):
    """The active view, plus every section and elevation when asked."""
    views = [doc.ActiveView]
    if all_views:
        for v in DB.FilteredElementCollector(doc).OfClass(DB.View):
            if not v.IsTemplate and v.ViewType in SECTION_TYPES and v.Id != doc.ActiveView.Id:
                views.append(v)
    return views


def set_levels(views, show, hide):
    """Show the `show` levels and hide the `hide` ones in each view; one undo."""
    with revit.Transaction(u"BOKI Filtrar niveles"):
        for v in views:
            to_show = List[DB.ElementId]([l.Id for l in show if l.IsHidden(v)])
            to_hide = List[DB.ElementId]([l.Id for l in hide if l.CanBeHidden(v) and not l.IsHidden(v)])
            if to_show.Count:
                v.UnhideElements(to_show)
            if to_hide.Count:
                v.HideElements(to_hide)


class FilterWindow(forms.WPFWindow):
    def __init__(self, xaml_file_path):
        self._ready = False
        forms.WPFWindow.__init__(self, xaml_file_path)
        self.levels = sorted(DB.FilteredElementCollector(doc).OfClass(DB.Level),
                             key=lambda l: (-l.Elevation, l.Name))
        prefixes = sorted(set(p for p in (prefix_of(l.Name) for l in self.levels) if p))
        self.cb_prefix.ItemsSource = [ALL] + prefixes
        self.cb_prefix.SelectedIndex = 0
        self._ready = True
        self.refresh(None, None)

    # -- the rule ---------------------------------------------------------
    def matches(self, level):
        prefix = self.cb_prefix.SelectedItem
        text = (self.txt_contains.Text or u"").strip().lower()
        if prefix and prefix != ALL and prefix_of(level.Name) != prefix:
            return False
        return not text or text in level.Name.lower()

    def split(self):
        """Levels to show and to hide, as the window's options say."""
        filtering = self.cb_prefix.SelectedItem not in (None, ALL) or (self.txt_contains.Text or u"").strip()
        show, hide = [], []
        for level in self.levels:
            visible = self.matches(level) == bool(self.rb_show.IsChecked)
            if not filtering:
                visible = True
            if self.chk_general.IsChecked and prefix_of(level.Name) is None:
                visible = True
            (show if visible else hide).append(level)
        return show, hide

    # -- events -----------------------------------------------------------
    def refresh(self, sender, args):
        if not getattr(self, "_ready", False):
            return
        show, hide = self.split()
        shown = set(l.Id for l in show)
        items = []
        for level in self.levels:
            item = ListBoxItem()
            item.Content = u"{}      {:+.2f}".format(level.Name, level.Elevation * FT)
            if level.Id in shown:
                item.FontWeight = FontWeights.Bold
                item.Foreground = GREEN
            else:
                item.Foreground = GRAY
            items.append(item)
        self.lst_levels.ItemsSource = items
        self.txt_count.Text = u"Se verán {} de {} niveles".format(len(show), len(self.levels))

    def apply_click(self, sender, args):
        show, hide = self.split()
        set_levels(target_views(self.chk_all_views.IsChecked), show, hide)
        self.Close()

    def show_all_click(self, sender, args):
        set_levels(target_views(self.chk_all_views.IsChecked), self.levels, [])
        self.Close()

    def close_click(self, sender, args):
        self.Close()


if __name__ == "__main__":
    if doc.ActiveView.ViewType not in SECTION_TYPES:
        forms.alert(u"Abre una sección o una elevación y vuelve a pulsar Filtrar Niveles.\n\n"
                    u"(Los niveles se ven en secciones y elevaciones.)", title=TITLE)
    else:
        FilterWindow(os.path.join(SCRIPT_DIR, "FiltrarForm.xaml")).ShowDialog()
