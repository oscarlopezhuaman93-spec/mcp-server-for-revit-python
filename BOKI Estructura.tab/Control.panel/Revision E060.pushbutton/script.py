# -*- coding: utf-8 -*-
"""Revision E.060: verifica el acero configurado (Acero Columna, Muro,
Cimentacion, Escalera) contra los limites prescriptivos de la Norma E.060
y lista lo que cumple, los avisos y lo que no cumple."""

__title__ = u"Revisión\nE.060"
__author__ = "Revit MCP"

import io
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(__file__)
EXT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", ".."))
REVIT_MCP_DIR = os.path.join(EXT_ROOT, "revit_mcp")
if REVIT_MCP_DIR not in sys.path:
    sys.path.append(REVIT_MCP_DIR)

import rebar_spec as rs
import rebar_columns as rc
import rebar_foundation as rf
import e060_review as ev
import footing_table as ft  # only its pure footing_mark (Z-1, CC 1-1)

reload(rs)
reload(rc)
reload(rf)
reload(ev)
reload(ft)

from pyrevit import revit, DB, forms
from Autodesk.Revit.DB.Architecture import Stairs
from Microsoft.Win32 import SaveFileDialog
from System.Collections.Generic import List
import clr
clr.AddReference("System.Data")
from System.Data import DataTable
from System.Windows.Media import Color, SolidColorBrush

doc = revit.doc
MM = rs.BAR_DIAMETERS_MM
FT = 0.3048
COLUMNS = ((u"Estado", "status"), (u"Categoria", "category"), (u"Elemento", "element"),
           (u"Verificacion", "rule"), (u"Valor", "value"), (u"Limite", "limit"), (u"Norma", "ref"))
LEVELS = sorted(l.Elevation * 0.3048 for l in DB.FilteredElementCollector(doc).OfClass(DB.Level))
ROW_COLORS = {ev.FAIL: (252, 235, 235), ev.WARN: (250, 238, 218), ev.OK: (255, 255, 255)}


def _name(element):
    p = element.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM)
    return p.AsString() if p else element.Name


def _json(text):
    try:
        return json.loads(text or u"{}")
    except ValueError:
        return {}


def _num(text, default=None):
    try:
        return float((u"{}".format(text) or u"").replace(u",", u"."))
    except ValueError:
        return default


def _distribution(text):
    try:
        return rs.parse_distribution(text)
    except Exception:
        return None, None


def _polygon_area(poly):
    n = len(poly)
    return abs(sum(poly[i][0] * poly[(i + 1) % n][1] - poly[(i + 1) % n][0] * poly[i][1] for i in range(n))) / 2.0


def review_columns(out, ids):
    by_type = {}
    for c in (DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_StructuralColumns)
              .WhereElementIsNotElementType()):
        by_type.setdefault(c.GetTypeId().IntegerValue, []).append(c)
    pending = 0
    for tid, cols in sorted(by_type.items()):
        ctype = doc.GetElement(DB.ElementId(tid))
        cfg = rc.read_type_config(ctype)
        try:
            design = rs.design_from_text(cfg.get("EA_Seccion_Armado"))
        except rs.SpecError:
            design = None
        if not design or not design["bars"]:
            pending += 1
            continue
        name = rc.type_mark(_name(ctype)) or _name(ctype)
        section = None
        for c in cols:
            try:
                section = rc.Section(c)
                break
            except Exception:
                continue
        if section is None:
            continue
        poly = section.polygon_m
        xs = [p[0] for p in poly]
        ys = [p[1] for p in poly]
        # ln: the lowest storey the type runs through (a column modeled over
        # several floors is checked floor by floor)
        heights = []
        for c in cols:
            bb = c.get_BoundingBox(None)
            if bb is None:
                continue
            z0, z1 = bb.Min.Z * FT, bb.Max.Z * FT
            inside = [z for z in LEVELS if z0 - 0.05 <= z <= z1 + 0.05]
            storeys = [b - a for a, b in zip(inside, inside[1:]) if b - a > 1.5]
            heights.append(min(storeys) if storeys else z1 - z0)
        zones, rest = _distribution(cfg.get("EA_Estribo_Borde_Distribucion"))
        stirrup = MM.get((cfg.get("EA_Estribo_Borde_Diametro") or u"").strip())
        out += ev.check_column(name, max(xs) - min(xs), max(ys) - min(ys), _polygon_area(poly),
                               [MM[k] for _, _, k in design["bars"]], False,
                               _num(cfg.get("EA_Recubrimiento_cm"), 4.0), stirrup,
                               zones, rest, min(heights) if heights else 3.0)
        ids[(ev.C_COLUMN, name)] = [c.Id for c in cols]
    if pending:
        out.append(ev.finding(ev.WARN, ev.C_COLUMN, u"({} tipos)".format(pending), u"Acero sin configurar",
                              u"sin armado", u"Acero Columna", u"-"))


def review_walls(out, ids):
    for w in DB.FilteredElementCollector(doc).OfClass(DB.Wall):
        p = w.LookupParameter(rc.WALL_PARAM)
        if p is None or not (p.AsString() or u"").strip():
            continue
        cfg = rc.read_type_config(w)
        name = u"Muro {}".format(w.Id.IntegerValue)
        vertical = []
        for sk in _json(cfg.get("EA_Muro_Corte")) or []:
            d = MM.get(sk.get("d"))
            s = _num(sk.get("s"))
            if d and s:
                vertical.append((d, s))
        hz = _json(cfg.get("EA_Muro_Horizontal"))
        horizontal = None
        if hz.get("on") and hz.get("dist"):
            zones, rest = _distribution(hz["dist"])
            if rest:
                horizontal = (MM.get(hz.get("d"), 9.525), rest, 2)
        out += ev.check_wall(name, w.Width * FT, vertical, horizontal, _num(cfg.get("EA_Recubrimiento_cm")))
        ids[(ev.C_WALL, name)] = [w.Id]


def review_footings(out, ids):
    by_type = {}
    for e in (DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_StructuralFoundation)
              .WhereElementIsNotElementType()):
        by_type.setdefault(e.GetTypeId().IntegerValue, []).append(e)
    for tid, els in sorted(by_type.items()):
        ftype = doc.GetElement(DB.ElementId(tid))
        p = ftype.LookupParameter("EA_Cim_Acero")
        saved = _json(p.AsString() if p else u"")
        if not saved:
            continue
        name = ft.footing_mark(_name(ftype))
        try:
            f = rf.Foundation(els[0])
            h = f.extent[5] - f.extent[4]
        except Exception:
            continue
        meshes = []
        for layer, label in ((rf.BOTTOM, u"inferior"), (rf.TOP, u"superior")):
            m = rf.default_steel()[layer]
            m.update(saved.get(layer) or {})
            if not m.get("on"):
                continue
            for axis in (u"x", u"y"):
                meshes.append((label, axis.upper(), MM[m["d" + axis]], float(m.get("s" + axis) or 0.2)))
        covers = _json((ftype.LookupParameter("EA_Cim_Recubrimientos").AsString()
                        if ftype.LookupParameter("EA_Cim_Recubrimientos") else u""))
        bottom = _num(covers.get("1"), rf.DEFAULT_COVER_CM)
        out += ev.check_footing(name, h, meshes, bottom)
        ids[(ev.C_FOOTING, name)] = [e.Id for e in els]


def review_stairs(out, ids):
    for s in DB.FilteredElementCollector(doc).OfClass(Stairs):
        p = s.LookupParameter("EA_Esc_Acero")
        st = _json(p.AsString() if p else u"")
        if not st:
            continue
        t = 0.15
        for rid in s.GetStairsRuns():
            rt = doc.GetElement(doc.GetElement(rid).GetTypeId())
            q = rt.get_Parameter(DB.BuiltInParameter.STAIRS_RUNTYPE_STRUCTURAL_DEPTH) if rt else None
            if q is not None and q.AsDouble() > 0:
                t = q.AsDouble() * FT
        inf, temp = st.get(u"inferior") or {}, st.get(u"temperatura") or {}
        main = (MM.get(inf.get("d"), 12.7), float(inf.get("s") or 0.2)) if inf.get("on") else None
        tmp = (MM.get(temp.get("d"), 9.525), float(temp.get("s") or 0.25)) if temp.get("on") else None
        name = u"Escalera {}".format(s.Id.IntegerValue)
        out += ev.check_stair(name, t, _num(st.get("cover"), 2.5), main, tmp)
        ids[(ev.C_STAIR, name)] = [s.Id]


def review_beams(out):
    beams = list(DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_StructuralFraming)
                 .WhereElementIsNotElementType())
    if beams:
        out.append(ev.finding(ev.WARN, ev.C_BEAM, u"({} vigas)".format(len(beams)), u"Revision de vigas",
                              u"no disponible aun", u"Acero Viga", u"-"))


def run_review():
    findings, ids = [], {}
    for step in (review_columns, review_walls, review_footings, review_stairs):
        try:
            step(findings, ids)
        except Exception as e:
            findings.append(ev.finding(ev.WARN, u"-", step.__name__, u"No se pudo revisar", u"{}".format(e),
                                       u"-", u"-"))
    review_beams(findings)
    return ev.sort_findings(findings), ids


class ReviewWindow(forms.WPFWindow):
    def __init__(self, xaml_file_path, findings, ids):
        forms.WPFWindow.__init__(self, xaml_file_path)
        self.findings = findings
        self.ids = ids
        self.selected_ids = None
        counts = ev.summary(findings)
        self.txt_fail.Text = u"NO CUMPLE: {}".format(counts[ev.FAIL])
        self.txt_warn.Text = u"AVISO: {}".format(counts[ev.WARN])
        self.txt_ok.Text = u"CUMPLE: {}".format(counts[ev.OK])
        self.cbo_status.ItemsSource = [u"Todos", ev.FAIL, ev.WARN, ev.OK]
        self.cbo_status.SelectedIndex = 0
        self.cbo_category.ItemsSource = [u"Todas"] + sorted(set(f["category"] for f in findings))
        self.cbo_category.SelectedIndex = 0
        self.fill()

    def visible(self):
        status = self.cbo_status.SelectedItem
        category = self.cbo_category.SelectedItem
        return [f for f in self.findings
                if (status in (None, u"Todos") or f["status"] == status)
                and (category in (None, u"Todas") or f["category"] == category)]

    def fill(self):
        table = DataTable()
        for title, _ in COLUMNS:
            table.Columns.Add(title)
        for f in self.visible():
            table.Rows.Add(*[f[key] for _, key in COLUMNS])
        self.grid.ItemsSource = table.DefaultView
        self.txt_status.Text = u"{} verificaciones mostradas de {}".format(table.Rows.Count, len(self.findings))

    def filter_changed(self, sender, args):
        if hasattr(self, "findings"):
            self.fill()

    def grid_column(self, sender, args):
        from System.Windows.Controls import DataGridLength
        args.Column.Width = DataGridLength({u"Estado": 100, u"Categoria": 100, u"Elemento": 230, u"Verificacion": 280,
                                            u"Valor": 100, u"Limite": 290, u"Norma": 150}.get(args.Column.Header, 120))

    def grid_loading_row(self, sender, args):
        status = args.Row.Item.Row[0]
        rgb = ROW_COLORS.get(status, (255, 255, 255))
        args.Row.Background = SolidColorBrush(Color.FromRgb(*rgb))

    def select_click(self, sender, args):
        item = self.grid.SelectedItem
        if item is None:
            forms.alert(u"Elige una fila.", title=u"Revision E.060")
            return
        key = (item.Row[1], item.Row[2])
        found = self.ids.get(key)
        if not found:
            forms.alert(u"Esa fila no tiene elementos para seleccionar.", title=u"Revision E.060")
            return
        self.selected_ids = found
        self.Close()

    def export_click(self, sender, args):
        dialog = SaveFileDialog()
        dialog.Filter = "CSV para Excel (*.csv)|*.csv"
        dialog.FileName = u"Revision_E060.csv"
        if dialog.ShowDialog():
            with io.open(dialog.FileName, "w", encoding="utf-8-sig") as fh:
                fh.write(ev.to_csv(self.findings))
            self.txt_status.Text = u"Exportado: {}".format(dialog.FileName)

    def close_click(self, sender, args):
        self.Close()


if __name__ == "__main__":
    findings, ids = run_review()
    window = ReviewWindow(os.path.join(SCRIPT_DIR, "ReviewForm.xaml"), findings, ids)
    window.ShowDialog()
    if window.selected_ids:
        revit.uidoc.Selection.SetElementIds(List[DB.ElementId](window.selected_ids))
        revit.uidoc.ShowElements(List[DB.ElementId](window.selected_ids))
