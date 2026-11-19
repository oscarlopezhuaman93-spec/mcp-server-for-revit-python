# -*- coding: utf-8 -*-
"""Purgar: borra lo que no se usa en el modelo, por grupos sencillos
(CAD e imagenes, familias, grupos, materiales, anotaciones, acero, otros),
en varias pasadas y con un solo Ctrl+Z."""

__title__ = "Purgar"
__author__ = "Revit MCP"
__doc__ = ("Borra lo que no se usa en el modelo para que pese menos. "
           "Lo recomendado viene marcado; nunca borra lo que empieza con BOKI.")

import os

from pyrevit import revit, DB, forms
from System.Collections.Generic import HashSet, List
from System.Windows import Thickness, FontWeights, TextWrapping
from System.Windows.Controls import CheckBox, StackPanel, TextBlock
from System.Windows.Media import Color, SolidColorBrush

doc = revit.doc
SCRIPT_DIR = os.path.dirname(__file__)
TITLE = u"Purgar"
PROTECTED = u"BOKI"
MAX_PASSES = 5
GRAY = SolidColorBrush(Color.FromRgb(110, 116, 125))

# key, label, help, checked by default -- in the order shown
GROUPS = [
    ("cad", u"Archivos CAD e imágenes sin uso", u"DWG/DXF importados e imágenes. Suelen ser lo que más pesa.", True),
    ("model", u"Familias y tipos sin uso", u"Familias cargadas sin ningún elemento y tipos de muro, losa, viga… sin usar.", True),
    ("groups", u"Grupos sin colocar", u"Grupos de modelo y de detalle que no están en el modelo.", True),
    ("materials", u"Materiales sin uso", u"Materiales y sus apariencias.", True),
    ("annot", u"Anotaciones y estilos sin uso", u"Textos, cotas, etiquetas, rellenos. Revisa: pueden ser de tu plantilla.", False),
    ("rebar", u"Acero: formas y tipos de barra sin uso", u"Revisa: tus botones de Acero pueden necesitarlos.", False),
    ("other", u"Otros", u"Todo lo demás que Revit marca como sin uso.", False),
]
CAD = ("CADLinkType", "ImageType")
MATERIALS = ("Material", "AppearanceAssetElement", "PropertySetElement")
ANNOT = ("TextNoteType", "DimensionType", "SpotDimensionType", "FilledRegionType", "AnnotationSymbolType",
         "ViewFamilyType", "LinePatternElement", "FillPatternElement", "GraphicsStyle")
MODEL_TYPES = ("Family", "FamilySymbol", "WallType", "FloorType", "RoofType", "CeilingType", "ToposolidType",
               "MullionType", "PanelType", "CurtainSystemType", "BuildingPadType", "StairsType", "RailingType")


def _name(e):
    try:
        if isinstance(e, DB.FamilySymbol):
            return u"{} {}".format(e.FamilyName, DB.Element.Name.GetValue(e))
        return DB.Element.Name.GetValue(e) or u""
    except Exception:
        return u""


def _category_type(e):
    cat = e.FamilyCategory if isinstance(e, DB.Family) else e.Category
    return cat.CategoryType if cat is not None else None


def group_of(e):
    cls = e.GetType().Name
    if cls in CAD:
        return "cad"
    if cls == "GroupType":
        return "groups"
    if cls in MATERIALS:
        return "materials"
    if cls.startswith("Rebar"):
        return "rebar"
    if cls in ANNOT or _category_type(e) == DB.CategoryType.Annotation:
        return "annot"
    if cls in MODEL_TYPES:
        return "model"
    return "other"


def unused_by_group():
    """{group: [element ids]} of what Revit can purge now, minus BOKI's own."""
    out = dict((g[0], []) for g in GROUPS)
    for eid in doc.GetUnusedElements(HashSet[DB.ElementId]()):
        e = doc.GetElement(eid)
        if e is None or _name(e).upper().startswith(PROTECTED):
            continue
        out[group_of(e)].append(eid)
    return out


class _QuietWarnings(DB.IFailuresPreprocessor):
    def PreprocessFailures(self, accessor):
        for f in accessor.GetFailureMessages():
            if f.GetSeverity() == DB.FailureSeverity.Warning:
                accessor.DeleteWarning(f)
        return DB.FailureProcessingResult.Continue


def _delete(ids):
    """Delete what can be deleted; returns how many went."""
    alive = [i for i in ids if doc.GetElement(i) is not None]
    if not alive:
        return 0
    try:
        doc.Delete(List[DB.ElementId](alive))
        return len(alive)
    except Exception:
        n = 0
        for i in alive:
            if doc.GetElement(i) is None:
                continue
            try:
                doc.Delete(i)
                n += 1
            except Exception:
                pass
        return n


def purge(keys):
    """Several passes (deleting a family frees its materials...), one undo."""
    deleted, failed = 0, set()
    tg = DB.TransactionGroup(doc, u"BOKI Purgar")
    tg.Start()
    passes = 0
    for passes in range(1, MAX_PASSES + 1):
        found = unused_by_group()
        ids = [i for k in keys for i in found[k] if i not in failed]
        if not ids:
            passes -= 1
            break
        t = DB.Transaction(doc, u"Purgar pasada {}".format(passes))
        opts = t.GetFailureHandlingOptions()
        opts.SetFailuresPreprocessor(_QuietWarnings())
        t.SetFailureHandlingOptions(opts)
        t.Start()
        n = _delete(ids)
        t.Commit()
        failed.update(i for i in ids if doc.GetElement(i) is not None)
        deleted += n
        if n == 0:
            break
    tg.Assimilate()
    return deleted, passes


class PurgeWindow(forms.WPFWindow):
    def __init__(self, xaml_file_path):
        forms.WPFWindow.__init__(self, xaml_file_path)
        self.found = unused_by_group()
        self.boxes = {}
        for key, label, help_text, default in GROUPS:
            n = len(self.found[key])
            row = StackPanel()
            row.Margin = Thickness(0, 6, 0, 6)
            box = CheckBox()
            box.Content = u"{}  ({})".format(label, n)
            box.FontWeight = FontWeights.SemiBold
            box.IsChecked = default and n > 0
            box.IsEnabled = n > 0
            box.Click += self.update_total
            note = TextBlock()
            note.Text = help_text
            note.Foreground = GRAY
            note.TextWrapping = TextWrapping.Wrap
            note.Margin = Thickness(22, 2, 0, 0)
            row.Children.Add(box)
            row.Children.Add(note)
            self.panel_groups.Children.Add(row)
            self.boxes[key] = box
        self.chosen = None
        self.update_total(None, None)

    def selected(self):
        return [k for k, b in self.boxes.items() if b.IsChecked]

    def update_total(self, sender, args):
        n = sum(len(self.found[k]) for k in self.selected())
        self.txt_total.Text = u"Se borrarán {} elementos (y lo que quede libre al borrarlos).".format(n)
        self.button_run.IsEnabled = n > 0

    def run_click(self, sender, args):
        self.chosen = self.selected()
        self.Close()

    def close_click(self, sender, args):
        self.Close()


def _offer_compact_save():
    if not doc.PathName or doc.IsWorkshared:
        return
    if forms.alert(u"Para que el archivo pese menos hay que guardarlo compactado.\n\n¿Guardar y compactar ahora?",
                   title=TITLE, yes=True, no=True):
        opts = DB.SaveOptions()
        opts.Compact = True
        doc.Save(opts)
        size = os.path.getsize(doc.PathName) / (1024.0 * 1024.0)
        forms.alert(u"Guardado y compactado. El archivo pesa ahora {:.0f} MB.".format(size), title=TITLE)


if __name__ == "__main__":
    window = PurgeWindow(os.path.join(SCRIPT_DIR, "PurgarForm.xaml"))
    window.ShowDialog()
    if window.chosen:
        deleted, passes = purge(window.chosen)
        forms.alert(u"Listo: se borraron {} elementos en {} pasada(s).".format(deleted, passes), title=TITLE)
        if deleted:
            _offer_compact_save()
