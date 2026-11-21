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
        # (2026-11-21) as Revit's stair sketch: green boundaries, yellow
        # risers, orange landings, a purple path with its arrow up
        green, yellow, orange, purple = (brush(*RGB_BOUNDARY), brush(*RGB_RISER),
                                         brush(*RGB_LANDING), brush(*RGB_PATH))
        for l in plan["landings"]:
            self._poly(self.canvas_plan, to, l["poly"], C_LANDING, orange, 3.0)
        for k, (_, corners, r, length) in enumerate(shapes):
            self._poly(self.canvas_plan, to, corners, C_RUN, None, 0.0)
            bounds, risers, path, arrow = run_outline(r, width, tread)
            for p0, p1 in risers:
                self._poly(self.canvas_plan, to, [p0, p1], None, yellow, 2.0, closed=False)
            for p0, p1 in bounds:
                self._poly(self.canvas_plan, to, [p0, p1], None, green, 3.0, closed=False)
            self._poly(self.canvas_plan, to, list(path), None, purple, 2.0, closed=False)
            for p0, p1 in arrow:
                self._poly(self.canvas_plan, to, [p0, p1], None, purple, 2.0, closed=False)
            a, end = path
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
    """The plan to draw in: the active view when it is a plan of this level;
    else a plan of the level showing a CAD (linked or imported) - the
    drawing to trace over -; else any plan of it; else a new floor plan."""
    active = doc.ActiveView
    plans = [v for v in DB.FilteredElementCollector(doc).OfClass(DB.ViewPlan)
             if not v.IsTemplate and v.GenLevel is not None and v.GenLevel.Id == level.Id
             and v.ViewType in (DB.ViewType.FloorPlan, DB.ViewType.EngineeringPlan)]
    if any(v.Id == active.Id for v in plans):
        return active
    for v in plans:
        try:
            if DB.FilteredElementCollector(doc, v.Id).OfClass(DB.ImportInstance).GetElementCount():
                return v
        except Exception:
            pass
    if plans:
        return plans[0]
    vft = next(t for t in DB.FilteredElementCollector(doc).OfClass(DB.ViewFamilyType)
               if t.ViewFamily == DB.ViewFamily.FloorPlan)
    with revit.Transaction(u"BOKI Escalera - planta"):
        return DB.ViewPlan.Create(doc, vft.Id, level.Id)


def _temp_line(view, a, b, rgb=SKETCH_RGB, weight=6):
    z = view.GenLevel.Elevation
    line = doc.Create.NewDetailCurve(view, DB.Line.CreateBound(
        DB.XYZ(a[0] / FT, a[1] / FT, z), DB.XYZ(b[0] / FT, b[1] / FT, z)))
    ogs = DB.OverrideGraphicSettings()
    ogs.SetProjectionLineColor(DB.Color(*rgb))
    ogs.SetProjectionLineWeight(weight)
    view.SetElementOverrides(line.Id, ogs)
    return line.Id


# (2026-11-21) the sketch shown as Revit's own stair sketch
RGB_BOUNDARY, RGB_RISER, RGB_LANDING, RGB_PATH = (0, 128, 80), (240, 200, 0), (255, 90, 0), (80, 20, 200)


def run_outline(r, width, tread):
    """(boundary lines, riser lines, path, arrow) of a planned run, in plan (m)."""
    d = r["dir"]
    n = (-d[1], d[0])
    a = r["start"]
    treads = r["risers"] if r.get("top_tread") else r["risers"] - 1
    length = treads * tread
    h = width / 2.0

    def P(t, v):
        return (a[0] + d[0] * t + n[0] * v, a[1] + d[1] * t + n[1] * v)
    bounds = [(P(0, -h), P(length, -h)), (P(0, h), P(length, h))]
    risers = [(P(i * tread, -h), P(i * tread, h)) for i in range(treads + 1)]
    end = P(length, 0)
    head = 0.12
    arrow = [(end, P(length - head, -head * 0.6)), (end, P(length - head, head * 0.6))]
    return bounds, risers, (P(0, 0), end), arrow


def draw_sketch(view, plan, width, tread):
    """Every run and landing of `plan` drawn in the view as Revit's stair
    sketch: green boundaries, yellow risers, orange landings, a purple
    path with its arrow up. Returns the ids (temporary)."""
    ids = []
    for r in plan["runs"]:
        bounds, risers, path, arrow = run_outline(r, width, tread)
        for a, b in bounds:
            ids.append(_temp_line(view, a, b, RGB_BOUNDARY, 7))
        for a, b in risers:
            ids.append(_temp_line(view, a, b, RGB_RISER, 4))
        ids.append(_temp_line(view, path[0], path[1], RGB_PATH, 5))
        for a, b in arrow:
            ids.append(_temp_line(view, a, b, RGB_PATH, 5))
    for l in plan["landings"]:
        poly = l["poly"]
        for k in range(len(poly)):
            ids.append(_temp_line(view, poly[k], poly[(k + 1) % len(poly)], RGB_LANDING, 7))
    return ids


def _start_mark(view, a, size=0.15):
    """(2026-11-21) A cross at the clicked start of a run, shown while the
    end is picked: where the run leaves from is seen at once."""
    ids = []
    for dx, dy in ((size, size), (size, -size)):
        ids.append(_temp_line(view, (a[0] - dx, a[1] - dy), (a[0] + dx, a[1] + dy)))
    return ids


def selected_lines_sketch():
    """(2026-11-21) Runs from lines drawn beforehand with Revit's own Detail
    / Model Line (live line while drawing, its length shown) and selected
    before opening Escalera: each line a run, in the order drawn, from its
    start (the first click) to its end. [] when none is selected."""
    out = []
    for i in sorted(uidoc.Selection.GetElementIds(), key=lambda x: x.IntegerValue):
        e = doc.GetElement(i)
        c = getattr(e, "GeometryCurve", None)
        if isinstance(e, DB.CurveElement) and isinstance(c, DB.Line):
            a, b = c.GetEndPoint(0), c.GetEndPoint(1)
            out.append(((a.X * FT, a.Y * FT), (b.X * FT, b.Y * FT)))
    return out


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
            mark = []
            try:
                p = uidoc.Selection.PickPoint(snaps, u"Tramo {}: clic en el INICIO (Esc termina el boceto)".format(k))
                a = (p.X * FT, p.Y * FT)
                with revit.Transaction(u"BOKI Escalera - boceto"):
                    mark = [_temp_line(view, p0, p1, RGB_PATH, 6) for p0, p1 in
                            (((a[0] - 0.15, a[1] - 0.15), (a[0] + 0.15, a[1] + 0.15)),
                             ((a[0] - 0.15, a[1] + 0.15), (a[0] + 0.15, a[1] - 0.15)))]
                temp += mark
                p = uidoc.Selection.PickPoint(snaps, u"Tramo {}: clic en el FIN (Esc cancela este tramo)".format(k))
                b = (p.X * FT, p.Y * FT)
            except OperationCanceledException:
                break
            state.sketch.append((a, b))
            # the whole stair sketched again as it will be built (runs fitted
            # to their risers, landings between, none over another)
            top = doc.GetElement(DB.ElementId(state.top_id)) if state.top_id else None
            with revit.Transaction(u"BOKI Escalera - boceto"):
                for i in temp:
                    if doc.GetElement(i) is not None:
                        doc.Delete(i)
                temp = []
                if top is not None and top.Elevation > base.Elevation:
                    plan = sp.plan_stair(state.sketch, (top.Elevation - base.Elevation) * FT,
                                         (state.riser or 17.5) / 100.0, (state.tread or 25.0) / 100.0,
                                         state.width or 1.20)
                    temp = draw_sketch(view, plan, state.width or 1.20, (state.tread or 25.0) / 100.0)
                else:
                    temp.append(_temp_line(view, a, b))
    finally:
        if temp:
            with revit.Transaction(u"BOKI Escalera - boceto"):
                for i in temp:
                    if doc.GetElement(i) is not None:
                        doc.Delete(i)


# --- editing a stair made here (2026-11-21) -------------------------------------
TAG_PREFIX = u"BOKI Escalera "


def _comment(e):
    p = e.get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
    return p.AsString() if p is not None else None


def _set_comment(e, text):
    p = e.get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
    if p is not None and not p.IsReadOnly:
        p.Set(text)


def stair_pieces(selected):
    """The runs (Stairs) and landings (floors) of the stair(s) picked: a
    piece made by Escalera brings every piece of its stair (its tag);
    otherwise only what was picked. ([Stairs], [Floor])."""
    from Autodesk.Revit.DB.Architecture import Stairs
    tags = set(c for c in (_comment(e) for e in selected) if c and c.startswith(TAG_PREFIX))
    pieces = list(selected)
    if tags:
        for cat in (DB.BuiltInCategory.OST_Stairs, DB.BuiltInCategory.OST_Floors):
            for e in DB.FilteredElementCollector(doc).OfCategory(cat).WhereElementIsNotElementType():
                if _comment(e) in tags and all(e.Id != x.Id for x in pieces):
                    pieces.append(e)
    stairs = [e for e in pieces if isinstance(e, Stairs)]
    floors = [e for e in pieces if isinstance(e, DB.Floor)]
    return stairs, floors


def read_existing(state):
    """A stair picked before opening: its runs back as the sketch, its
    type, levels, riser, tread and width in the window; its pieces noted
    (state.edit_ids) to be replaced when it is created again. False when
    no stair is picked."""
    sel = [doc.GetElement(i) for i in uidoc.Selection.GetElementIds()]
    stairs, floors = stair_pieces([e for e in sel if e is not None])
    if not stairs:
        return False
    runs = []
    for s in stairs:
        base = doc.GetElement(s.get_Parameter(DB.BuiltInParameter.STAIRS_BASE_LEVEL_PARAM).AsElementId())
        off = s.get_Parameter(DB.BuiltInParameter.STAIRS_BASE_OFFSET).AsDouble()
        for rid in s.GetStairsRuns():
            r = doc.GetElement(rid)
            curves = list(r.GetStairsPath())
            if not curves:
                continue
            a, b = curves[0].GetEndPoint(0), curves[-1].GetEndPoint(1)
            runs.append((base.Elevation + off, s, r, (a.X * FT, a.Y * FT), (b.X * FT, b.Y * FT)))
    if not runs:
        return False
    runs.sort(key=lambda x: x[0])
    first = runs[0][1]
    base = doc.GetElement(first.get_Parameter(DB.BuiltInParameter.STAIRS_BASE_LEVEL_PARAM).AsElementId())
    top_z = max(z + s.get_Parameter(DB.BuiltInParameter.STAIRS_STAIRS_HEIGHT).AsDouble() for z, s, _, _, _ in runs)
    top = min(levels(), key=lambda l: abs(l.Elevation - top_z))
    state.type_name = _name(doc.GetElement(first.GetTypeId()))
    state.base_id = base.Id.IntegerValue
    state.top_id = top.Id.IntegerValue
    state.riser = math.ceil(first.ActualRiserHeight * FT * 1000 - 1e-6) / 10.0  # the max: its own riser
    state.tread = round(first.ActualTreadDepth * FT * 100, 1)
    state.width = round(runs[0][2].ActualRunWidth * FT, 2)
    state.sketch = [(a, b) for _, _, _, a, b in runs]
    state.edit_ids = [e.Id.IntegerValue for e in stairs + floors]
    return True


# --- main ----------------------------------------------------------------------
if __name__ == "__main__":
    state = State()
    state.edit_ids = []
    if not read_existing(state):  # a stair picked: edit it
        state.sketch = selected_lines_sketch()  # lines drawn with Revit's tool, picked before: the runs
    xaml = os.path.join(SCRIPT_DIR, "EscaleraForm.xaml")
    while True:
        window = EscaleraWindow(xaml, state)
        if state.edit_ids:
            window.Title = u"Escalera - EDITANDO la escalera seleccionada ({} piezas)".format(len(state.edit_ids))
            window.button_create.Content = u"ACTUALIZAR ESCALERA"
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
            if state.edit_ids:  # editing: the old pieces replaced by the new ones
                with revit.Transaction(u"BOKI Escalera - reemplazar"):
                    for i in state.edit_ids:
                        if doc.GetElement(DB.ElementId(i)) is not None:
                            doc.Delete(DB.ElementId(i))
            runs, floors, messages = scr.create_stair(doc, base, top, info, plan, state.tread / 100.0, state.width)
            import uuid
            tag = TAG_PREFIX + str(uuid.uuid4())[:8]  # its pieces found together when edited again
            with revit.Transaction(u"BOKI Escalera - marca"):
                for e in runs + floors:
                    _set_comment(e, tag)
            tg.Assimilate()
        except Exception as e:
            tg.RollBack()
            forms.alert(u"No se pudo crear la escalera:\n{}".format(e), title="Escalera")
            raise SystemExit
        uidoc.Selection.SetElementIds(List[DB.ElementId]([e.Id for e in runs + floors]))
        forms.alert((u"Escalera actualizada" if state.edit_ids else u"Escalera creada") + u": {} tramos (escaleras rectas del tipo {}) y {} descansos (losas)."
                    u"\nQuedaron seleccionados.".format(len(runs), info.name, len(floors)), title="Escalera")
