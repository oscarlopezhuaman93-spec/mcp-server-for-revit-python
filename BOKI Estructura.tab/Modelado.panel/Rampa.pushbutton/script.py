# -*- coding: utf-8 -*-
"""Rampa vehicular por boceto: el eje se dibuja en la planta del nivel de
inicio (rectas y arcos, cada tramo desde el fin del anterior) y se crea
como losa del tipo elegido: tramos rectos planos con flecha de pendiente,
la curva alabeada siguiendo el eje, transiciones a media pendiente al
inicio y al final, todo unido en una sola losa continua."""

__title__ = "Rampa"
__author__ = "Revit MCP"

import math
import os
import sys

SCRIPT_DIR = os.path.dirname(__file__)
EXT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", ".."))
REVIT_MCP_DIR = os.path.join(EXT_ROOT, "revit_mcp")
if REVIT_MCP_DIR not in sys.path:
    sys.path.append(REVIT_MCP_DIR)

import ramp_plan as rp
import ramp_create as rcr
import slab_regions as sg

reload(rp)
reload(rcr)
reload(sg)

from pyrevit import revit, DB, forms
from Autodesk.Revit.Exceptions import OperationCanceledException
from Autodesk.Revit.UI.Selection import ObjectSnapTypes
from System.Collections.Generic import List
from System.Windows import FontWeights, Point
from System.Windows.Controls import Canvas, TextBlock
from System.Windows.Media import Color, PointCollection, SolidColorBrush
from System.Windows.Shapes import Polygon, Polyline

doc = revit.doc
uidoc = revit.uidoc
FT = 0.3048
SKETCH_RGB = (208, 48, 154)


def brush(r, g, b, a=255):
    return SolidColorBrush(Color.FromArgb(a, r, g, b))


C_SLAB, C_EDGE, C_AXIS, C_TEXT = brush(207, 213, 220), brush(68, 68, 68), brush(208, 48, 154), brush(22, 50, 79)
C_GREEN, C_RED, C_BLUE = brush(31, 157, 85), brush(192, 57, 43), brush(45, 140, 240)


def _name(e):
    return DB.Element.Name.GetValue(e)


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
        self.width, self.transition, self.max_slope = 3.50, 3.60, 15.0
        self.join = True
        self.segments = []
        self.message = u""


class RampaWindow(forms.WPFWindow):
    def __init__(self, xaml, state):
        forms.WPFWindow.__init__(self, xaml)
        self.state = state
        self.action = None
        self._filling = True
        self.types = sg.floor_types(doc)
        names = sorted(self.types)
        self.cbo_type.ItemsSource = names
        if state.type_name not in self.types:
            pick = [n for n in names if u"MACIZA" in n.upper()] or names
            state.type_name = pick[0]
        self.cbo_type.SelectedItem = state.type_name
        self.levels = sorted(DB.FilteredElementCollector(doc).OfClass(DB.Level), key=lambda l: l.Elevation)
        lnames = [_name(l) for l in self.levels]
        self.cbo_base.ItemsSource = lnames
        self.cbo_top.ItemsSource = lnames
        base = doc.GetElement(DB.ElementId(state.base_id)) if state.base_id else getattr(doc.ActiveView, "GenLevel", None)
        base = base or self.levels[0]
        top = doc.GetElement(DB.ElementId(state.top_id)) if state.top_id else \
            next((l for l in self.levels if l.Elevation > base.Elevation + 1.0 / FT), self.levels[-1])
        self.cbo_base.SelectedItem = _name(base)
        self.cbo_top.SelectedItem = _name(top)
        self.txt_width.Text = u"{:g}".format(state.width)
        self.txt_transition.Text = u"{:g}".format(state.transition)
        self.txt_max.Text = u"{:g}".format(state.max_slope)
        self.chk_join.IsChecked = state.join
        self._filling = False
        self.refresh()

    # -- configuration --------------------------------------------------------
    def _read(self):
        s = self.state
        s.type_name = self.cbo_type.SelectedItem or s.type_name
        base = next((l for l in self.levels if _name(l) == self.cbo_base.SelectedItem), None)
        top = next((l for l in self.levels if _name(l) == self.cbo_top.SelectedItem), None)
        s.base_id = base.Id.IntegerValue if base else None
        s.top_id = top.Id.IntegerValue if top else None
        s.width = max(0.5, _num(self.txt_width.Text, 3.5))
        s.transition = max(0.0, _num(self.txt_transition.Text, 3.6))
        s.max_slope = max(1.0, _num(self.txt_max.Text, 15.0))
        s.join = bool(self.chk_join.IsChecked)
        return base, top

    def plan(self):
        base, top = self._read()
        if base is None or top is None or top.Elevation <= base.Elevation:
            return None, u"El nivel de fin debe estar más alto que el de inicio."
        s = self.state
        rise = (top.Elevation - base.Elevation) * FT
        return rp.plan_ramp(s.segments, s.width, rise, s.transition, s.max_slope / 100.0), None

    def config_changed(self, sender, args):
        if not getattr(self, "_filling", True):
            self.refresh()

    def resized(self, sender, args):
        self.refresh()

    def refresh(self):
        if getattr(self, "_filling", True):
            return
        t = self.types.get(self.state.type_name)
        self.txt_type.Text = u"Espesor {:.2f} m (del tipo)".format(sg.type_thickness(t)) if t else u""
        self.canvas_plan.Children.Clear()
        self.canvas_profile.Children.Clear()
        plan, error = self.plan()
        s = self.state
        if error:
            self._status(error, True)
            return
        if not s.segments:
            self.txt_summary.Text = u""
            self._status(s.message or u"Pulsa + Recta y marca el eje en la planta del nivel de inicio.", False)
            return
        lines = [u"Longitud por el eje: {:.2f} m".format(plan["length"]),
                 u"Desnivel: {:.2f} m".format(plan["profile"].rise),
                 u"Pendiente: {:.1f} %  ·  transiciones {:.1f} % ({:.2f} m)".format(
                     plan["slope"] * 100, plan["transition_slope"] * 100, plan["transition"]),
                 u"Tramos: {} recta(s), {} arco(s)  ·  piezas de losa: {}".format(
                     sum(1 for g in s.segments if g["kind"] == "line"), sum(1 for g in s.segments if g["kind"] == "arc"),
                     len(plan["pieces"]))]
        self.txt_summary.Text = u"\n".join(lines)
        if plan["warnings"]:
            self._status(u"  ".join(plan["warnings"]), True)
        else:
            self._status(u"✔ Pendiente {:.1f} % ≤ {:g} % (RNE A.010)".format(plan["slope"] * 100, s.max_slope), False)
        self._draw_plan(plan)
        self._draw_profile(plan)

    def _status(self, text, error):
        self.txt_status.Text = text
        self.txt_status.Foreground = C_RED if error else C_GREEN

    # -- drawing ------------------------------------------------------------------
    def _fit(self, canvas, pts, margin=30, equal=True):
        w, h = canvas.ActualWidth, canvas.ActualHeight
        if w < 50 or h < 50 or not pts:
            return None
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        sx = (w - 2 * margin) / max(max(xs) - min(xs), 0.5)
        sy = (h - 2 * margin) / max(max(ys) - min(ys), 0.5)
        if equal:
            sx = sy = min(sx, sy)
        cx, cy = (max(xs) + min(xs)) / 2.0, (max(ys) + min(ys)) / 2.0
        return lambda x, y: Point(w / 2.0 + (x - cx) * sx, h / 2.0 - (y - cy) * sy)

    def _shape(self, canvas, to, pts, fill, stroke, width, closed=True):
        shape = Polygon() if closed else Polyline()
        coll = PointCollection()
        for p in pts:
            coll.Add(to(p[0], p[1]))
        shape.Points = coll
        if closed and fill is not None:
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
        outlines = []
        for pc in plan["pieces"]:
            if pc["kind"] == "line":
                outlines.append(pc["outline"])
            else:
                outlines.append([(x, y) for x, y, _ in pc["left"]] + [(x, y) for x, y, _ in reversed(pc["right"])])
        axis = []
        for seg in s.segments:
            L = rp.seg_length(seg)
            n = 2 if seg["kind"] == "line" else 24
            axis += [rp.point_at(seg, L * i / float(n))[0] for i in range(n + 1)]
        to = self._fit(self.canvas_plan, [p for o in outlines for p in o] + axis)
        if to is None:
            return
        for k, o in enumerate(outlines):
            self._shape(self.canvas_plan, to, o, C_SLAB, C_EDGE, 1.0)
        self._shape(self.canvas_plan, to, axis, None, C_AXIS, 2.0, closed=False)
        for pc in plan["pieces"]:
            seg = s.segments[pc["seg"]]
            p, _ = rp.point_at(seg, (pc["u0"] + pc["u1"]) / 2.0)
            self._text(self.canvas_plan, to(p[0], p[1]), u"{:+.2f}".format(pc["z1"]), C_TEXT, 10)
        start = axis[0]
        self._text(self.canvas_plan, to(start[0], start[1]), u"INICIO ±0.00", C_BLUE, 11)
        end = axis[-1]
        self._text(self.canvas_plan, to(end[0], end[1]), u"FIN +{:.2f}".format(plan["profile"].rise), C_BLUE, 11)

    def _draw_profile(self, plan):
        prof = plan["profile"]
        t = sg.type_thickness(self.types.get(self.state.type_name)) if self.types.get(self.state.type_name) else 0.2
        n = 80
        top = [(prof.length * i / float(n), prof.z(prof.length * i / float(n))) for i in range(n + 1)]
        band = top + [(x, z - t) for x, z in reversed(top)]
        to = self._fit(self.canvas_profile, band + [(0, prof.rise + 0.5)], 24, equal=False)
        if to is None:
            return
        self._shape(self.canvas_profile, to, band, C_SLAB, C_EDGE, 1.2)
        T = prof.transition
        for (a, b, label) in ((0, T, u"{:.1f} %".format(prof.slope * 50)), (T, prof.length - T, u"{:.1f} %".format(prof.slope * 100)),
                              (prof.length - T, prof.length, u"{:.1f} %".format(prof.slope * 50))):
            if b - a > 0.01:
                m = (a + b) / 2.0
                self._text(self.canvas_profile, to(m, prof.z(m) + 0.25), label, C_BLUE, 11)

    # -- actions ---------------------------------------------------------------------
    def add_line_click(self, sender, args):
        self._read()
        self.action = "line"
        self.Close()

    def add_arc_click(self, sender, args):
        self._read()
        if not self.state.segments:
            self._status(u"El arco sigue al tramo anterior: dibuja primero una recta.", True)
            return
        self.action = "arc"
        self.Close()

    def undo_click(self, sender, args):
        if self.state.segments:
            self.state.segments.pop()
        self.refresh()

    def clear_click(self, sender, args):
        self.state.segments = []
        self.refresh()

    def cancel_click(self, sender, args):
        self.action = None
        self.Close()

    def create_click(self, sender, args):
        plan, error = self.plan()
        if error or not self.state.segments:
            forms.alert(error or u"Primero dibuja el eje de la rampa.", title=u"Rampa")
            return
        self.action = "create"
        self.Close()


# --- drawing in the plan --------------------------------------------------------
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
    with revit.Transaction(u"BOKI Rampa - planta"):
        return DB.ViewPlan.Create(doc, vft.Id, level.Id)


SNAPS = (ObjectSnapTypes.Endpoints | ObjectSnapTypes.Intersections | ObjectSnapTypes.Midpoints |
         ObjectSnapTypes.Nearest | ObjectSnapTypes.Perpendicular | ObjectSnapTypes.Centers)


def _pick(prompt):
    p = uidoc.Selection.PickPoint(SNAPS, prompt)
    return (p.X * FT, p.Y * FT)


def _show_axis(view, segments):
    """The axis drawn so far as temporary lines (deleted at the end)."""
    ids = []
    z = view.GenLevel.Elevation
    with revit.Transaction(u"BOKI Rampa - boceto"):
        for seg in segments:
            L = rp.seg_length(seg)
            n = 1 if seg["kind"] == "line" else 16
            pts = [rp.point_at(seg, L * i / float(n))[0] for i in range(n + 1)]
            for a, b in zip(pts, pts[1:]):
                line = doc.Create.NewDetailCurve(view, DB.Line.CreateBound(
                    DB.XYZ(a[0] / FT, a[1] / FT, z), DB.XYZ(b[0] / FT, b[1] / FT, z)))
                ogs = DB.OverrideGraphicSettings()
                ogs.SetProjectionLineColor(DB.Color(*SKETCH_RGB))
                ogs.SetProjectionLineWeight(6)
                view.SetElementOverrides(line.Id, ogs)
                ids.append(line.Id)
    return ids


def _clear(ids):
    if ids:
        with revit.Transaction(u"BOKI Rampa - boceto"):
            for i in ids:
                if doc.GetElement(i) is not None:
                    doc.Delete(i)


def draw(state, kind):
    base = doc.GetElement(DB.ElementId(state.base_id))
    view = plan_view_of(base)
    uidoc.ActiveView = view
    temp = _show_axis(view, state.segments)
    try:
        if kind == "line":
            if state.segments:
                a = state.segments[-1]["b"]
            else:
                a = _pick(u"Rampa: clic en el INICIO del eje (Esc cancela)")
            b = _pick(u"Rampa: clic en el FIN de la recta (Esc cancela)")
            if math.hypot(b[0] - a[0], b[1] - a[1]) > 0.2:
                state.segments.append({"kind": "line", "a": a, "b": b})
        else:
            a = state.segments[-1]["b"]
            m = _pick(u"Rampa: clic en un PUNTO DEL ARCO (Esc cancela)")
            b = _pick(u"Rampa: clic en el FIN del arco (Esc cancela)")
            if rp.arc_through(a, m, b) is not None:
                state.segments.append({"kind": "arc", "a": a, "m": m, "b": b})
            else:
                state.message = u"Los tres puntos del arco están en línea: no se agregó."
    except OperationCanceledException:
        pass
    finally:
        _clear(temp)


# --- main -------------------------------------------------------------------------
if __name__ == "__main__":
    state = State()
    xaml = os.path.join(SCRIPT_DIR, "RampaForm.xaml")
    while True:
        window = RampaWindow(xaml, state)
        window.ShowDialog()
        if window.action in ("line", "arc"):
            state.message = u""
            draw(state, window.action)
            continue
        break
    if window.action == "create":
        plan, _ = window.plan()
        base = doc.GetElement(DB.ElementId(state.base_id))
        ftype = window.types[state.type_name]
        tg = DB.TransactionGroup(doc, u"BOKI Rampa vehicular")
        tg.Start()
        try:
            floors, messages = rcr.create_ramp(doc, base, ftype, plan, join=state.join)
            tg.Assimilate()
        except Exception as e:
            tg.RollBack()
            forms.alert(u"No se pudo crear la rampa:\n{}".format(e), title=u"Rampa")
            raise SystemExit
        uidoc.Selection.SetElementIds(List[DB.ElementId]([f.Id for f in floors]))
