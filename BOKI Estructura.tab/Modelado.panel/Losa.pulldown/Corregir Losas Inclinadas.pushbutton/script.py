# -*- coding: utf-8 -*-
"""Corrige las losas inclinadas: las hechas con edición de forma (espesor
del tipo en vertical) se rehacen con flecha de pendiente, y las que llegan
a una viga cumbrera se prolongan hasta su eje para que la viga las corte
(sin la cuña que deja su borde vertical contra la cara inclinada de la
viga). Todas las del modelo o las elegidas con clic; vista previa del
corte antes de corregir."""

__title__ = "Corregir\nLosas Inclinadas"
__author__ = "Revit MCP"

import math
import os
import sys

SCRIPT_DIR = os.path.dirname(__file__)
REVIT_MCP_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", "..", "..", "revit_mcp"))
if REVIT_MCP_DIR not in sys.path:
    sys.path.append(REVIT_MCP_DIR)

import slab_create as sc
import slab_regions as sg
import slab_fix as sf
import rebar_slabs as rsl

reload(sc)
reload(sg)
reload(sf)
reload(rsl)

from pyrevit import revit, DB, forms
from Autodesk.Revit.Exceptions import OperationCanceledException
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType
from System.Collections.Generic import List
from System.Windows import Point
from System.Windows.Controls import ListBoxItem
from System.Windows.Media import Color, DoubleCollection, PointCollection, SolidColorBrush
from System.Windows.Shapes import Line, Polygon

doc = revit.doc
uidoc = revit.uidoc
FT = 0.3048


def brush(r, g, b, a=255):
    return SolidColorBrush(Color.FromArgb(a, r, g, b))


C_SLAB, C_SLAB_EDGE = brush(143, 211, 214), brush(26, 154, 160)
C_BEAM, C_EDGE, C_RED, C_GREEN, C_BLUE = brush(201, 206, 213), brush(68, 68, 68), brush(192, 57, 43), \
    brush(31, 157, 85), brush(45, 140, 240)


def sloped(floor):
    tops = sf._top_faces(floor)
    return bool(tops) and any(abs(f.FaceNormal.Z) < 0.999 for f in tops)


def analyze(floor):
    """What a sloped floor needs: {"floor", "shape": shape-edited, "ridge":
    (beam, m to its axis) or None}."""
    try:
        shape = sf.needs_fix(floor)
    except Exception:
        shape = False
    try:
        ridge = sf.ridge_beam(doc, floor)
    except Exception:
        ridge = None
    try:
        thick = sf.thickness_need(floor)  # Revit's plumb thickness: thinner than nominal square to the slope
    except Exception:
        thick = None
    return {"floor": floor, "shape": shape, "ridge": ridge, "thick": thick}


def all_sloped():
    return [f for f in DB.FilteredElementCollector(doc).OfClass(DB.Floor).WhereElementIsNotElementType()
            if _safe(sloped, f)]


def _safe(fn, *args):
    try:
        return fn(*args)
    except Exception:
        return False


class State(object):
    def __init__(self):
        self.scope = "all"
        self.picked = []  # floor ids picked
        self.items = None  # analyzed floors of the scope
        self.ridge = self.join = True
        self.shape = False  # remaking a floor gives a new one (its shape points lost): only when asked
        self.selected = 0


class CorregirWindow(forms.WPFWindow):
    def __init__(self, xaml, state):
        forms.WPFWindow.__init__(self, xaml)
        self.state = state
        self.action = None
        self._filling = True
        self.rb_all.IsChecked = state.scope == "all"
        self.rb_pick.IsChecked = state.scope == "pick"
        self.chk_shape.IsChecked, self.chk_ridge.IsChecked, self.chk_join.IsChecked = state.shape, state.ridge, state.join
        self._filling = False
        self.refresh()

    # -- scope / list ------------------------------------------------------------
    def _items(self):
        s = self.state
        if s.items is None:
            if s.scope == "all":
                floors = all_sloped()
            else:
                floors = [doc.GetElement(DB.ElementId(i)) for i in s.picked]
                floors = [f for f in floors if f is not None and _safe(sloped, f)]
            s.items = [analyze(f) for f in floors]
            s.found = len(floors)
        return s.items

    def _todo(self, item):
        return (self.chk_shape.IsChecked and item["shape"]) or \
            (self.chk_ridge.IsChecked and (item["ridge"] or item["thick"]))

    def refresh(self):
        s = self.state
        items = self._items()
        todo = [i for i in items if self._todo(i)]
        n_found = s.found
        self.txt_all.Text = (u"{} losas inclinadas encontradas · {} necesitan corrección".format(n_found, len(todo))
                             if s.scope == "all" else u"")
        self.txt_pick.Text = (u"{} losa(s) elegidas · {} necesitan corrección".format(len(items), len(todo))
                              if s.scope == "pick" and s.picked else u"Esc termina la selección")
        self._filling = True
        self.list_floors.Items.Clear()
        for k, it in enumerate(todo):
            f = it["floor"]
            what = []
            if self.chk_shape.IsChecked and it["shape"]:
                what.append(u"forma")
            if self.chk_ridge.IsChecked and it["ridge"]:
                what.append(u"ajustar a la cumbrera {}".format(it["ridge"][0].Id.IntegerValue))
            if self.chk_ridge.IsChecked and it["thick"]:
                what.append(u"espesor {:.0f} cm real (hoy {:.1f})".format(
                    it["thick"][0] * 100, it["thick"][0] ** 2 / it["thick"][1] * 100))
            row = ListBoxItem()
            row.Content = u"Losa {}  ·  {}".format(f.Id.IntegerValue, u" + ".join(what))
            row.Tag = k
            self.list_floors.Items.Add(row)
        s.todo = todo
        if todo:
            s.selected = min(s.selected, len(todo) - 1)
            self.list_floors.SelectedIndex = s.selected
        self._filling = False
        self.button_run.Content = u"CORREGIR ({} losa{})".format(len(todo), u"" if len(todo) == 1 else u"s")
        self.button_run.IsEnabled = bool(todo)
        if not todo:
            self._status(u"✔ Nada que corregir: las losas ya tienen su espesor real y llegan a sus cumbreras.", False)
        else:
            self._status(u"", False)
        self.draw()

    def _status(self, text, error):
        self.txt_status.Text = text
        self.txt_status.Foreground = C_RED if error else C_GREEN

    def scope_changed(self, sender, args):
        if self._filling:
            return
        self.state.scope = "pick" if self.rb_pick.IsChecked else "all"
        self.state.items = None
        self.state.selected = 0
        self.refresh()

    def options_changed(self, sender, args):
        if self._filling:
            return
        s = self.state
        s.shape, s.ridge, s.join = bool(self.chk_shape.IsChecked), bool(self.chk_ridge.IsChecked), bool(self.chk_join.IsChecked)
        self.refresh()

    def floor_selected(self, sender, args):
        if self._filling or self.list_floors.SelectedIndex < 0:
            return
        self.state.selected = self.list_floors.SelectedIndex
        self.draw()

    def resized(self, sender, args):
        self.draw()

    # -- preview ------------------------------------------------------------------
    def draw(self):
        canvas = self.canvas
        canvas.Children.Clear()
        todo = getattr(self.state, "todo", [])
        if not todo or canvas.ActualWidth < 50:
            self.txt_note.Text = u""
            return
        item = todo[self.state.selected]
        f = item["floor"]
        self.box_preview.Header = u"Vista previa · losa {}".format(f.Id.IntegerValue)
        info = sf._single_slope(f)
        if info is None:
            self.txt_preview.Text = u"Losa con varios planos: se rehace con un plano por cada cara"
            self.txt_note.Text = u""
            return
        face, outer, holes, arrow, slope, z_tail, h, hi = info
        t = sg.type_thickness(doc.GetElement(f.GetTypeId()))
        vertical = t * math.sqrt(1 + slope * slope)
        s_tail = arrow[0][0] * h[0] + arrow[0][1] * h[1]
        ztop = lambda s: z_tail + slope * (s - s_tail)
        ridge = item["ridge"] if self.chk_ridge.IsChecked else None
        now_vertical = sf.thickness_across(f)[1] if item["shape"] else vertical
        new_vertical = vertical if self.chk_shape.IsChecked else now_vertical
        # the section across the high edge, in (s along the climb, z)
        lo = hi - 1.2
        end = hi + (ridge[1] if ridge else 0.0)
        before = [(lo, ztop(lo)), (hi, ztop(hi)), (hi, ztop(hi) - now_vertical), (lo, ztop(lo) - now_vertical)]
        after = [(lo, ztop(lo)), (end, ztop(end)), (end, ztop(end) - new_vertical), (lo, ztop(lo) - new_vertical)]
        beam = self._beam_section(ridge[0], h) if ridge else []
        pts = before + after + beam
        frame = self._frame(pts)
        if frame is None:
            return
        self._poly(after, frame, C_SLAB, C_SLAB_EDGE, 1.5)
        if beam:
            self._poly(beam, frame, C_BEAM, C_EDGE, 2.0)  # over the slab: the beam cuts it
            axis = hi + ridge[1]
            self._line((axis, ztop(axis) + 0.15), (axis, ztop(axis) - vertical - 0.35), frame, C_BLUE, 1, dash=True)
        self._poly(before, frame, None, C_RED, 1.5, dash=True)
        self.txt_preview.Text = (u"Corte por la cumbrera: así quedará" if ridge else u"Corte de la losa: así quedará")
        notes = []
        if ridge:
            notes.append(u"Antes (rojo discontinuo): termina {:.0f} cm antes del eje de la viga {}, con borde vertical. "
                         u"Después: llega hasta el eje y la viga la corta sobre su cara inclinada."
                         .format(ridge[1] * 100, ridge[0].Id.IntegerValue))
        if item["shape"] and self.chk_shape.IsChecked:
            notes.append(u"Espesor perpendicular: {:.3f} m → {:.3f} m (el del tipo).".format(
                sf.thickness_across(f)[0], t))
        self.txt_note.Text = u"  ".join(notes)

    def _beam_section(self, beam, h):
        """The beam's section in (s, z): the corners of one end face."""
        c = beam.Location.Curve
        d = c.Direction
        pts = []
        for s in _solids(beam):
            for e in s.Edges:
                for p in (e.AsCurve().GetEndPoint(0), e.AsCurve().GetEndPoint(1)):
                    pts.append(p)
        if not pts:
            return []
        along = [p.X * d.X + p.Y * d.Y for p in pts]
        a0 = min(along)
        face = [p for p, a in zip(pts, along) if a - a0 < 0.01]
        sec = []
        for p in face:
            q = (round((p.X * h[0] + p.Y * h[1]) * FT, 4), round(p.Z * FT, 4))
            if q not in sec:
                sec.append(q)
        cx = sum(q[0] for q in sec) / len(sec)
        cz = sum(q[1] for q in sec) / len(sec)
        return sorted(sec, key=lambda q: math.atan2(q[1] - cz, q[0] - cx))

    def _frame(self, pts, margin=40):
        w, hgt = self.canvas.ActualWidth, self.canvas.ActualHeight
        xs = [p[0] for p in pts]
        zs = [p[1] for p in pts]
        sx = max(max(xs) - min(xs), 0.2)
        sz = max(max(zs) - min(zs), 0.2)
        scale = min((w - 2 * margin) / sx, (hgt - 2 * margin) / sz)
        cx, cz = (max(xs) + min(xs)) / 2.0, (max(zs) + min(zs)) / 2.0
        return lambda x, z: Point(w / 2.0 + (x - cx) * scale, hgt / 2.0 - (z - cz) * scale)

    def _poly(self, pts, to, fill, stroke, width, dash=False):
        shape = Polygon()
        coll = PointCollection()
        for x, z in pts:
            coll.Add(to(x, z))
        shape.Points = coll
        if fill is not None:
            shape.Fill = fill
        shape.Stroke = stroke
        shape.StrokeThickness = width
        if dash:
            shape.StrokeDashArray = _dashes(5, 3)
        self.canvas.Children.Add(shape)

    def _line(self, a, b, to, stroke, width, dash=False):
        p, q = to(*a), to(*b)
        ln = Line()
        ln.X1, ln.Y1, ln.X2, ln.Y2 = p.X, p.Y, q.X, q.Y
        ln.Stroke = stroke
        ln.StrokeThickness = width
        if dash:
            ln.StrokeDashArray = _dashes(6, 4)
        self.canvas.Children.Add(ln)

    # -- actions ------------------------------------------------------------------
    def pick_click(self, sender, args):
        self.state.scope = "pick"
        self.action = "pick"
        self.Close()

    def cancel_click(self, sender, args):
        self.action = None
        self.Close()

    def run_click(self, sender, args):
        self.action = "run"
        self.Close()


def _dashes(a, b):
    dc = DoubleCollection()
    dc.Add(a)
    dc.Add(b)
    return dc


def _solids(e):
    def walk(g):
        for o in g or []:
            if isinstance(o, DB.Solid) and o.Volume > 1e-9:
                yield o
            elif isinstance(o, DB.GeometryInstance):
                for s in walk(o.GetInstanceGeometry()):
                    yield s
    return list(walk(e.get_Geometry(DB.Options())))


class _FloorFilter(ISelectionFilter):
    def AllowElement(self, e):
        return isinstance(e, DB.Floor)

    def AllowReference(self, ref, point):
        return False


def pick_floors(state):
    try:
        refs = uidoc.Selection.PickObjects(ObjectType.Element, _FloorFilter(),
                                           u"Clic en cada losa inclinada a corregir (Finalizar o Esc)")
    except OperationCanceledException:
        return
    state.picked = [r.ElementId.IntegerValue for r in refs]
    state.items = None
    state.selected = 0


def correct(state):
    """Every floor to do: remade with a slope arrow only when asked (a new
    floor), then fitted to its ridge beam in place (the same floor, its
    shape points kept). Returns (the floors corrected, failures)."""
    done, failed = [], []
    tg = DB.TransactionGroup(doc, u"Corregir losas inclinadas")
    tg.Start()
    try:
        for it in state.todo:
            f = it["floor"]
            fid = f.Id.IntegerValue
            try:
                floors = [f]
                if state.shape and it["shape"]:
                    t = DB.Transaction(doc, u"Rehacer losa con flecha")
                    opts = t.GetFailureHandlingOptions()
                    opts.SetFailuresPreprocessor(rsl.DropFailing())
                    opts.SetForcedModalHandling(False)
                    t.SetFailureHandlingOptions(opts)
                    t.Start()
                    rsl.delete_bricks(doc, f)
                    floors = sf.fix_floor(doc, f, join=state.join)
                    t.Commit()
                if state.ridge:
                    for g in floors:
                        # its nominal thickness square to the slope (flush with
                        # a ridge beam of the same depth), then onto the beam
                        t = DB.Transaction(doc, u"Espesor real de la losa")
                        t.Start()
                        sf.apply_true_thickness(doc, g)
                        t.Commit()
                        sf.fit_to_ridge_in_place(doc, g, join=state.join)
                done += floors
            except Exception as e:
                failed.append(u"id {}: {}".format(fid, e))
        tg.Assimilate()
    except Exception:
        tg.RollBack()
        raise
    return done, failed


if __name__ == "__main__":
    state = State()
    sel = [doc.GetElement(i) for i in uidoc.Selection.GetElementIds()]
    floors = [e for e in sel if isinstance(e, DB.Floor)]
    if floors:  # floors already selected: start with them
        state.scope = "pick"
        state.picked = [f.Id.IntegerValue for f in floors]
    xaml = os.path.join(SCRIPT_DIR, "CorregirForm.xaml")
    while True:
        window = CorregirWindow(xaml, state)
        window.ShowDialog()
        if window.action == "pick":
            pick_floors(state)
            continue
        break
    if window.action == "run":
        made, failed = correct(state)
        if made:
            uidoc.Selection.SetElementIds(List[DB.ElementId]([m.Id for m in made]))
        if failed:
            forms.alert(u"Corregidas: {}.\n\nNo se pudo:\n- ".format(len(state.todo) - len(failed)) + u"\n- ".join(failed),
                        title=u"Corregir Losas Inclinadas")
