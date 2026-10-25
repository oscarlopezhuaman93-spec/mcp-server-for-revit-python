# -*- coding: utf-8 -*-
"""Escalera de concreto por boceto: se dibujan los tramos en la planta del
nivel base (clic en el inicio y el fin de cada uno); cada tramo se crea
como una escalera recta propia del tipo elegido (garganta recta, Acero
Escalera la reconoce) y cada descanso como una losa plana."""

__title__ = "Escalera"
__author__ = "Revit MCP"

import math
import os
import sys

SCRIPT_DIR = os.path.dirname(__file__)
EXT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", ".."))
REVIT_MCP_DIR = os.path.join(EXT_ROOT, "revit_mcp")
if REVIT_MCP_DIR not in sys.path:
    sys.path.append(REVIT_MCP_DIR)

import stair_plan as sp
import stair_create as scr

reload(sp)
reload(scr)

from pyrevit import revit, DB, forms
from Autodesk.Revit.Exceptions import OperationCanceledException
from Autodesk.Revit.UI.Selection import ObjectSnapTypes
from System.Collections.Generic import List
from System.Windows import FontWeights, Point
from System.Windows.Controls import Canvas, TextBlock
from System.Windows.Media import Color, PointCollection, SolidColorBrush
from System.Windows.Shapes import Ellipse, Polygon, Polyline

doc = revit.doc
uidoc = revit.uidoc
FT = 0.3048
SKETCH_RGB = (208, 48, 154)  # the temporary sketch lines in the plan


def brush(r, g, b, a=255):
    return SolidColorBrush(Color.FromArgb(a, r, g, b))


C_RUN, C_LANDING, C_EDGE = brush(207, 213, 220), brush(230, 234, 238), brush(68, 68, 68)
C_SKETCH, C_ARROW = brush(*SKETCH_RGB), brush(45, 140, 240)


def _name(e):
    return DB.Element.Name.GetValue(e)


def levels():
    return sorted(DB.FilteredElementCollector(doc).OfClass(DB.Level), key=lambda l: l.Elevation)


def _num(text, default):
    try:
        return float((text or u"").replace(u",", u"."))
    except ValueError:
        return default


class State(object):
    def __init__(self):
        self.type_name = None
        self.base_id = None
        self.top_id = None
        self.riser = self.tread = self.width = None  # cm, cm, m as typed
        self.sketch = []  # [((x, y), (x, y))] runs drawn, in climbing order (m)


class EscaleraWindow(forms.WPFWindow):
    def __init__(self, xaml, state):
        forms.WPFWindow.__init__(self, xaml)
        self.state = state
        self.action = None
        self.types = scr.stair_types(doc)
        self.levels = levels()
        self._filling = True
        self.cbo_type.ItemsSource = [i.name for i in self.types]
        names = [_name(l) for l in self.levels]
        self.cbo_base.ItemsSource = names
        self.cbo_top.ItemsSource = names
        info = self._info_named(state.type_name) or self.types[0]
        self.cbo_type.SelectedItem = info.name
        base = doc.GetElement(DB.ElementId(state.base_id)) if state.base_id else getattr(doc.ActiveView, "GenLevel", None)
        base = base or self.levels[0]
        self.cbo_base.SelectedItem = _name(base)
        top = doc.GetElement(DB.ElementId(state.top_id)) if state.top_id else \
            next((l for l in self.levels if l.Elevation > base.Elevation + 1.0 / FT), self.levels[-1])
        self.cbo_top.SelectedItem = _name(top)
        self._fill_type(info, keep=state.riser is not None)
        self._filling = False
        self.refresh()

    # -- configuration -------------------------------------------------------
    def _info_named(self, name):
        return next((i for i in self.types if i.name == name), None)

    def _fill_type(self, info, keep=False):
        s = self.state
        self.txt_riser.Text = u"{:g}".format(s.riser if keep else round(info.max_riser * 100, 1))
        self.txt_tread.Text = u"{:g}".format(s.tread if keep else round(info.min_tread * 100, 1))
        self.txt_width.Text = u"{:g}".format(s.width if keep else max(1.20, round(info.min_width, 2)))
        self.txt_waist.Text = u"{:g}".format(round(info.waist * 100, 1)) if info.waist else u"(no monolítico)"
        self.txt_landing.Text = u"{:g}".format(round(info.landing * 100, 1))
        self.txt_type_info.Text = (u"Tramo: {}  ·  monolítico (concreto)".format(_name(doc.GetElement(info.type.RunType)))
                                   if info.monolithic else
                                   u"Este tipo no es monolítico: elige uno de concreto para la garganta.")

    def type_changed(self, sender, args):
        if self._filling:
            return
        info = self._info_named(self.cbo_type.SelectedItem)
        if info:
            self._filling = True
            self._fill_type(info)
            self._filling = False
        self.refresh()

    def config_changed(self, sender, args):
        if not getattr(self, "_filling", True):
            self.refresh()

    def _read(self):
        s = self.state
        s.type_name = self.cbo_type.SelectedItem
        base = next((l for l in self.levels if _name(l) == self.cbo_base.SelectedItem), None)
        top = next((l for l in self.levels if _name(l) == self.cbo_top.SelectedItem), None)
        s.base_id = base.Id.IntegerValue if base else None
        s.top_id = top.Id.IntegerValue if top else None
        s.riser = _num(self.txt_riser.Text, 17.5)
        s.tread = _num(self.txt_tread.Text, 25.0)
        s.width = _num(self.txt_width.Text, 1.20)
        return base, top

    def plan(self):
        base, top = self._read()
        if base is None or top is None or top.Elevation <= base.Elevation:
            return None, u"El nivel superior debe estar más alto que el base."
        s = self.state
        height = (top.Elevation - base.Elevation) * FT
        return sp.plan_stair(s.sketch, height, s.riser / 100.0, s.tread / 100.0, s.width), None

    # -- preview -------------------------------------------------------------
    def resized(self, sender, args):
        self.refresh()

    def refresh(self):
        if getattr(self, "_filling", True):
            return
        plan, error = self.plan()
        self.canvas_plan.Children.Clear()
        self.canvas_cut.Children.Clear()
        s = self.state
        if error:
            self._status(error, True)
            return
        info = self._info_named(s.type_name)
        value, good = sp.comfort(plan["riser"], s.tread / 100.0)
        lines = [u"Altura {:.2f} m → {} contrahuellas de {:.1f} cm".format(
            plan["total"] * plan["riser"], plan["total"], plan["riser"] * 100)]
        if s.sketch:
            used = plan["total"] - plan["remaining"]
            lines.append(u"Tramos: {}  ({})  ·  descansos: {}".format(
                len(plan["runs"]), u" + ".join(str(r["risers"]) for r in plan["runs"]), len(plan["landings"])))
            lines.append(u"{} creadas · {} restantes".format(used, plan["remaining"]))
        lines.append(u"2CH + H = {:.1f} cm {}".format(value * 100, u"✔" if good else u"(usual 60 a 64)"))
        self.txt_summary.Text = u"\n".join(lines)
        if not s.sketch:
            self._status(u"Pulsa DIBUJAR EN PLANTA y marca los tramos en la planta del nivel base.", False)
        elif plan["remaining"]:
            self._status(u"Faltan {} contrahuellas: dibuja otro tramo (o alarga el último).".format(plan["remaining"]), True)
        elif info is not None and not info.monolithic:
            self._status(u"El tipo elegido no es de concreto (monolítico).", True)
        else:
            self._status(u"✔ Listo: {} tramos y {} descansos.".format(len(plan["runs"]), len(plan["landings"])) +
                         (u"  " + u" ".join(plan["warnings"]) if plan["warnings"] else u""), False)
        if s.sketch:
            self._draw_plan(plan)
            self._draw_cut(plan, info)

    def _status(self, text, error):
        self.txt_status.Text = text
        self.txt_status.Foreground = brush(192, 57, 43) if error else brush(31, 157, 85)

    def _fit(self, canvas, pts, margin=30):
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        w, h = canvas.ActualWidth, canvas.ActualHeight
        if w < 50 or h < 50:
            return None
        scale = min((w - 2 * margin) / max(max(xs) - min(xs), 0.5), (h - 2 * margin) / max(max(ys) - min(ys), 0.5))
        cx, cy = (max(xs) + min(xs)) / 2.0, (max(ys) + min(ys)) / 2.0
        return lambda x, y: Point(w / 2.0 + (x - cx) * scale, h / 2.0 - (y - cy) * scale)

    def _poly(self, canvas, to, pts, fill, stroke, width=1.0, closed=True):
        shape = Polygon() if closed else Polyline()
        coll = PointCollection()
        for x, y in pts:
            coll.Add(to(x, y))
        shape.Points = coll
        if closed:
            shape.Fill = fill
        shape.Stroke = stroke
        shape.StrokeThickness = width
        canvas.Children.Add(shape)

    def _text(self, canvas, p, text, color, size=11):
        tb = TextBlock()
        tb.Text = text
        tb.Foreground = color
        tb.FontSize = size
        tb.FontWeight = FontWeights.Bold
        Canvas.SetLeft(tb, p.X + 4)
        Canvas.SetTop(tb, p.Y - 8)
        canvas.Children.Add(tb)

    def _draw_plan(self, plan):
        s = self.state
        width, tread = s.width, s.tread / 100.0
        shapes = []
        for k, r in enumerate(plan["runs"]):
            d = r["dir"]
            n = (-d[1], d[0])
            length = (r["risers"] - (0 if r.get("top_tread") else 1)) * tread
            a = r["start"]
            corners = [(a[0] + n[0] * sgn * width / 2 + d[0] * t, a[1] + n[1] * sgn * width / 2 + d[1] * t)
                       for sgn, t in ((-1, 0), (-1, length), (1, length), (1, 0))]
            shapes.append(("run", corners, r, length))
        pts = [p for _, c, _, _ in shapes for p in c] + [p for l in plan["landings"] for p in l["poly"]]
        to = self._fit(self.canvas_plan, pts)
        if to is None:
            return
        for l in plan["landings"]:
            self._poly(self.canvas_plan, to, l["poly"], C_LANDING, C_EDGE)
        for k, (_, corners, r, length) in enumerate(shapes):
            self._poly(self.canvas_plan, to, corners, C_RUN, C_EDGE, 1.2)
            d = r["dir"]
            n = (-d[1], d[0])
            a = r["start"]
            for i in range(1, int(round(length / tread))):
                p0 = (a[0] + d[0] * i * tread + n[0] * width / 2, a[1] + d[1] * i * tread + n[1] * width / 2)
                p1 = (a[0] + d[0] * i * tread - n[0] * width / 2, a[1] + d[1] * i * tread - n[1] * width / 2)
                self._poly(self.canvas_plan, to, [p0, p1], None, C_EDGE, 0.6, closed=False)
            end = (a[0] + d[0] * length, a[1] + d[1] * length)
            self._poly(self.canvas_plan, to, [a, end], None, C_ARROW, 2.0, closed=False)
            tip = to(end[0], end[1])
            dot = Ellipse()
            dot.Width = dot.Height = 8
            dot.Fill = C_ARROW
            Canvas.SetLeft(dot, tip.X - 4)
            Canvas.SetTop(dot, tip.Y - 4)
            self.canvas_plan.Children.Add(dot)
            mid = to((a[0] + end[0]) / 2.0, (a[1] + end[1]) / 2.0)
            self._text(self.canvas_plan, mid, u"T{} · {} c.h.".format(k + 1, r["risers"]), brush(22, 50, 79))

    def _draw_cut(self, plan, info):
        """Every run unrolled one after another (its landing between), as a
        section along the walking line: steps over a straight waist."""
        s = self.state
        tread, riser = s.tread / 100.0, plan["riser"]
        waist = (info.waist if info and info.waist else 0.15)
        thick = info.landing if info else 0.15
        x = 0.0
        shapes = []
        for k, r in enumerate(plan["runs"]):
            z0 = r["z0"]
            treads = r["risers"] if r.get("top_tread") else r["risers"] - 1
            top = [(x, z0)]
            for i in range(r["risers"]):
                xi = x + i * tread
                top += [(xi, z0 + (i + 1) * riser), (xi + tread if i < treads else xi, z0 + (i + 1) * riser)]
            run_len = treads * tread
            slope = math.atan2(r["risers"] * riser, max(run_len, tread))
            off = waist / math.cos(slope)
            bottom = [(x + run_len, z0 + r["risers"] * riser - off), (x, z0 - off)]
            shapes.append(("run", top + bottom))
            x += run_len
            if k < len(plan["landings"]):
                z = plan["landings"][k]["z"]
                shapes.append(("landing", [(x, z), (x + s.width, z), (x + s.width, z - thick), (x, z - thick)]))
                x += s.width
        pts = [p for _, poly in shapes for p in poly]
        to = self._fit(self.canvas_cut, pts, 20)
        if to is None:
            return
        for kind, poly in shapes:
            self._poly(self.canvas_cut, to, poly, C_RUN if kind == "run" else C_LANDING, C_EDGE, 1.2)

    # -- actions -------------------------------------------------------------
    def draw_click(self, sender, args):
        self._read()
        self.action = "draw"
        self.Close()

    def clear_click(self, sender, args):
        self.state.sketch = []
        self.refresh()

    def cancel_click(self, sender, args):
        self.action = None
        self.Close()

    def create_click(self, sender, args):
        plan, error = self.plan()
        if error or not self.state.sketch:
            forms.alert(error or u"Primero dibuja los tramos en planta.", title="Escalera")
            return
        if plan["remaining"]:
            forms.alert(u"Faltan {} contrahuellas: dibuja otro tramo.".format(plan["remaining"]), title="Escalera")
            return
        self.action = "create"
        self.Close()


# --- drawing in the plan -------------------------------------------------------
def plan_view_of(level):
    """A plan view of the level (one that exists, else a new floor plan)."""
    for v in DB.FilteredElementCollector(doc).OfClass(DB.ViewPlan):
        if not v.IsTemplate and v.GenLevel is not None and v.GenLevel.Id == level.Id \
                and v.ViewType in (DB.ViewType.FloorPlan, DB.ViewType.EngineeringPlan):
            return v
    vft = next(t for t in DB.FilteredElementCollector(doc).OfClass(DB.ViewFamilyType)
               if t.ViewFamily == DB.ViewFamily.FloorPlan)
    with revit.Transaction(u"BOKI Escalera - planta"):
        return DB.ViewPlan.Create(doc, vft.Id, level.Id)


def _temp_line(view, a, b):
    z = view.GenLevel.Elevation
    line = doc.Create.NewDetailCurve(view, DB.Line.CreateBound(
        DB.XYZ(a[0] / FT, a[1] / FT, z), DB.XYZ(b[0] / FT, b[1] / FT, z)))
    ogs = DB.OverrideGraphicSettings()
    ogs.SetProjectionLineColor(DB.Color(*SKETCH_RGB))
    ogs.SetProjectionLineWeight(6)
    view.SetElementOverrides(line.Id, ogs)
    return line.Id


def draw_in_plan(state):
    base = doc.GetElement(DB.ElementId(state.base_id))
    view = plan_view_of(base)
    uidoc.ActiveView = view
    temp = []
    snaps = (ObjectSnapTypes.Endpoints | ObjectSnapTypes.Intersections | ObjectSnapTypes.Midpoints |
             ObjectSnapTypes.Nearest | ObjectSnapTypes.Perpendicular)
    state.sketch = []
    try:
        while True:
            k = len(state.sketch) + 1
            try:
                p = uidoc.Selection.PickPoint(snaps, u"Tramo {}: clic en el INICIO (Esc termina el boceto)".format(k))
                a = (p.X * FT, p.Y * FT)
                p = uidoc.Selection.PickPoint(snaps, u"Tramo {}: clic en el FIN (Esc cancela este tramo)".format(k))
                b = (p.X * FT, p.Y * FT)
            except OperationCanceledException:
                break
            state.sketch.append((a, b))
            with revit.Transaction(u"BOKI Escalera - boceto"):
                temp.append(_temp_line(view, a, b))
    finally:
        if temp:
            with revit.Transaction(u"BOKI Escalera - boceto"):
                for i in temp:
                    if doc.GetElement(i) is not None:
                        doc.Delete(i)


# --- main ----------------------------------------------------------------------
if __name__ == "__main__":
    state = State()
    xaml = os.path.join(SCRIPT_DIR, "EscaleraForm.xaml")
    while True:
        window = EscaleraWindow(xaml, state)
        window.ShowDialog()
        if window.action == "draw":
            draw_in_plan(state)
            continue
        break
    if window.action == "create":
        plan, _ = window.plan()
        info = window._info_named(state.type_name)
        base = doc.GetElement(DB.ElementId(state.base_id))
        top = doc.GetElement(DB.ElementId(state.top_id))
        tg = DB.TransactionGroup(doc, u"BOKI Escalera de concreto")
        tg.Start()
        try:
            runs, floors, messages = scr.create_stair(doc, base, top, info, plan, state.tread / 100.0, state.width)
            tg.Assimilate()
        except Exception as e:
            tg.RollBack()
            forms.alert(u"No se pudo crear la escalera:\n{}".format(e), title="Escalera")
            raise SystemExit
        uidoc.Selection.SetElementIds(List[DB.ElementId]([e.Id for e in runs + floors]))
        forms.alert(u"Escalera creada: {} tramos (escaleras rectas del tipo {}) y {} descansos (losas)."
                    u"\nQuedaron seleccionados.".format(len(runs), info.name, len(floors)), title="Escalera")
