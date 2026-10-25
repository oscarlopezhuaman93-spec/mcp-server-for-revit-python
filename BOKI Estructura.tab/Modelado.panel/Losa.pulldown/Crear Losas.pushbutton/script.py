# -*- coding: utf-8 -*-
"""Crear losas: en los paños entre vigas y muros (clic en cada uno), en un
rectángulo o en un contorno dibujado; con el tipo de losa del proyecto,
planas o inclinadas (una, dos o cuatro aguas). Las dos y cuatro aguas se
crean con una losa por plano, que se encuentran en la cumbrera."""

__title__ = "Crear\nLosas"
__author__ = "Revit MCP"

import math
import os
import sys

SCRIPT_DIR = os.path.dirname(__file__)
EXT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", "..", ".."))
REVIT_MCP_DIR = os.path.join(EXT_ROOT, "revit_mcp")
if REVIT_MCP_DIR not in sys.path:
    sys.path.append(REVIT_MCP_DIR)

import rebar_views as rv
import slab_create as sc
import slab_regions as sg
import slab_fix as sf
import utils as fw_utils

reload(fw_utils)
reload(rv)
reload(sc)
reload(sg)
reload(sf)

from pyrevit import revit, DB, forms
from Autodesk.Revit.Exceptions import OperationCanceledException
from Autodesk.Revit.UI.Selection import ObjectSnapTypes, ObjectType
from System.Collections.Generic import List
from System.Windows.Controls import ListBoxItem
from System.Windows.Input import MouseButton
from System.Windows.Media import Color, SolidColorBrush

doc = revit.doc
uidoc = revit.uidoc
FT = 0.3048
SHAPE_NAMES = [(sc.FLAT, u"Plana"), (sc.ONE, u"Una agua"), (sc.TWO, u"Dos aguas"), (sc.FOUR, u"Cuatro aguas")]
COLORS = [(240, 140, 20), (42, 150, 214), (29, 158, 117), (200, 80, 160), (160, 120, 40)]


def brush(rgb, alpha=255):
    return SolidColorBrush(Color.FromArgb(alpha, rgb[0], rgb[1], rgb[2]))


class State(object):
    def __init__(self):
        view = doc.ActiveView
        level = getattr(view, "GenLevel", None)
        self.level_id = level.Id.IntegerValue if level is not None else None
        self.type_name = None
        self.top = 0.0
        self.edge = 0.0
        self.join = True
        self.regions = []  # {"poly", "holes", "shape", "slope", "angle", "ridge", "label"}
        self.selected = None
        self.message = u""


class CrearWindow(forms.WPFWindow):
    def __init__(self, xaml, state):
        forms.WPFWindow.__init__(self, xaml)
        self.state = state
        self.action = None
        self.nav = rv.Nav2D()
        self._fitted = None
        self._frame = None
        self._filling = True
        self.types = sg.floor_types(doc)
        self.cbo_type.ItemsSource = sorted(self.types)
        self.cbo_shape.ItemsSource = [n for _, n in SHAPE_NAMES]
        level = self.level()
        self.txt_level.Text = (u"Nivel {} (el de la vista activa) · losas en paños, rectángulo o contorno; planas o inclinadas."
                               .format(level.Name) if level is not None else
                               u"Vista 3D: el nivel se toma de la primera viga, muro o losa que toques al agregar.")
        if state.type_name not in self.types:
            loss = [n for n in sorted(self.types) if u"LOSA" in n.upper()]
            state.type_name = loss[0] if loss else sorted(self.types)[0]
        self.cbo_type.SelectedItem = state.type_name
        self.txt_top.Text = u"{:g}".format(state.top)
        self.txt_edge.Text = u"{:g}".format(state.edge)
        self.chk_join.IsChecked = state.join
        self._filling = False
        self._fill_list()
        self._fill_shape()
        self._type_info()
        self.txt_status.Text = state.message or u"{} losa(s) por crear.".format(len(state.regions))

    # -- form ----------------------------------------------------------------------
    def level(self):
        """The level the slabs go on: the active plan view's, or the one
        taken from the first element touched in a 3D view."""
        return doc.GetElement(DB.ElementId(self.state.level_id)) if self.state.level_id else None

    @staticmethod
    def _num(text, default):
        try:
            return float((text or u"").replace(u",", u"."))
        except ValueError:
            return default

    def _type_info(self):
        t = self.types.get(self.cbo_type.SelectedItem)
        if t is not None:
            self.txt_type.Text = u"Espesor {:.2f} m".format(sg.type_thickness(t))

    def form_changed(self, sender, args):
        if self._filling:
            return
        st = self.state
        st.type_name = self.cbo_type.SelectedItem or st.type_name
        st.top = self._num(self.txt_top.Text, 0.0)
        st.edge = self._num(self.txt_edge.Text, 0.0)
        st.join = bool(self.chk_join.IsChecked)
        self._type_info()
        self.redraw()

    def _fill_list(self):
        self._filling = True
        self.list_regions.Items.Clear()
        for i, r in enumerate(self.state.regions):
            item = ListBoxItem()
            ps = sc.pieces(r["poly"], r["shape"], r["slope"], r.get("angle"), r.get("ridge"))
            item.Content = u"Losa {}: {}  {:.2f} m²{}{}".format(
                i + 1, dict(SHAPE_NAMES)[r["shape"]], abs(sc.area(r["poly"])),
                u"  ({} losas)".format(len(ps)) if len(ps) > 1 else u"",
                u"  {:g} %".format(r["slope"] * 100) if r["shape"] != sc.FLAT else u"")
            item.Tag = i
            self.list_regions.Items.Add(item)
        sel = self.state.selected
        if sel is not None and sel < len(self.state.regions):
            self.list_regions.SelectedIndex = sel
        self._filling = False

    def _fill_shape(self):
        self._filling = True
        r = self._region()
        if r is not None:
            self.cbo_shape.SelectedItem = dict(SHAPE_NAMES)[r["shape"]]
            self.txt_slope.Text = u"{:g}".format(round(r["slope"] * 100, 2))
            self.txt_ridge.Text = u"" if not r.get("ridge") else u"{:g}".format(r["ridge"])
            self.txt_angle.Text = u"" if r.get("angle") is None else u"{:g}".format(r["angle"])
        self._filling = False

    def _region(self):
        sel = self.state.selected
        return self.state.regions[sel] if sel is not None and sel < len(self.state.regions) else None

    def region_selected(self, sender, args):
        if self._filling:
            return
        self.state.selected = self.list_regions.SelectedIndex if self.list_regions.SelectedIndex >= 0 else None
        self._fill_shape()
        self.redraw()

    def shape_changed(self, sender, args):
        if self._filling:
            return
        shape = [k for k, n in SHAPE_NAMES if n == self.cbo_shape.SelectedItem]
        targets = self.state.regions if self.chk_all.IsChecked else ([self._region()] if self._region() else [])
        for r in targets:
            r["shape"] = shape[0] if shape else sc.FLAT
            r["slope"] = max(0.0, self._num(self.txt_slope.Text, 25.0)) / 100.0
            ridge = (self.txt_ridge.Text or u"").strip()
            r["ridge"] = max(0.0, self._num(ridge, 0.0)) if ridge else None
            ang = (self.txt_angle.Text or u"").strip()
            r["angle"] = self._num(ang, 0.0) % 360.0 if ang else None
        self._fill_list()
        self.redraw()

    def turn_click(self, sender, args):
        r = self._region()
        if r is None:
            return
        a = r.get("angle")
        if a is None:
            a = sc.long_side_angle(r["poly"]) + (90.0 if r["shape"] == sc.ONE else 0.0)
        self.txt_angle.Text = u"{:g}".format(round((a + 90.0) % 360.0, 2))  # shape_changed applies it

    def remove_click(self, sender, args):
        r = self._region()
        if r is not None:
            self.state.regions.remove(r)
            self.state.selected = None
            self._fill_list()
            self.redraw()

    def clear_click(self, sender, args):
        self.state.regions = []
        self.state.selected = None
        self._fill_list()
        self.redraw()

    # -- drawing ---------------------------------------------------------------------
    def resized(self, sender, args):
        self.redraw()

    def redraw(self):
        self._draw_plan()

    def _grids(self, box):
        x0, x1, y0, y1 = box
        out = []
        for g in DB.FilteredElementCollector(doc).OfClass(DB.Grid):
            c = g.Curve
            if not isinstance(c, DB.Line):
                continue
            a, b = c.GetEndPoint(0), c.GetEndPoint(1)
            a, b = (a.X * FT, a.Y * FT), (b.X * FT, b.Y * FT)
            if max(a[0], b[0]) < x0 or min(a[0], b[0]) > x1 or max(a[1], b[1]) < y0 or min(a[1], b[1]) > y1:
                continue
            out.append((g.Name, a, b))
        return out

    def _regions_of_level(self):
        """The level's free spaces (cached per level, height and type)."""
        st = self.state
        t = self.types.get(st.type_name)
        key = (st.level_id, round(st.top, 3), round(sg.type_thickness(t), 3) if t else 0.2)
        cache = self.__dict__.setdefault("_level_cache", {})
        if key not in cache:
            cache[key] = sg.LevelRegions(doc, self.level(), st.top, key[2])
        return cache[key]

    def _wide_regions(self):
        """The same, its openings up to 15 cm closed (beams that meet with a
        larger gap): the second try for a click no closed space holds."""
        st = self.state
        t = self.types.get(st.type_name)
        thick = sg.type_thickness(t) if t else 0.2
        return sg.LevelRegions(doc, self.level(), st.top, thick, gap=0.15)

    def _draw_plan(self):
        canvas = self.canvas_plan
        canvas.Children.Clear()
        if canvas.ActualWidth < 10 or self.level() is None:
            return
        try:
            lr = self._regions_of_level()
        except Exception as e:
            self.txt_status.Text = u"No se pudo leer el nivel: {}".format(e)
            return
        pts = [p for r in self.state.regions for p in r["poly"]]
        if pts:  # around the slabs being made
            x0, x1 = min(p[0] for p in pts) - 4, max(p[0] for p in pts) + 4
            y0, y1 = min(p[1] for p in pts) - 4, max(p[1] for p in pts) + 4
        elif getattr(lr, "box", None):
            x0, x1, y0, y1 = lr.box
        elif pts:
            x0, x1 = min(p[0] for p in pts) - 2, max(p[0] for p in pts) + 2
            y0, y1 = min(p[1] for p in pts) - 2, max(p[1] for p in pts) + 2
        else:
            return
        self._fitted = rv.fit_frame(canvas.ActualWidth, canvas.ActualHeight, x0, y0, x1, y1)
        frame = self.nav.resolve(self._fitted)
        self._frame = frame
        for p in lr.pieces:  # the closed spaces: where a click makes a slab
            rv._polygon(canvas, frame, p["outer"], brush((248, 249, 251)), brush((170, 175, 185)))
            for h in p["holes"]:
                rv._polygon(canvas, frame, h, brush((205, 208, 214)))
        for tri in getattr(lr, "footprints", []):  # beams, walls and columns at the slab's height
            rv._polygon(canvas, frame, tri, brush((150, 156, 166)))
        for name, a, b in self._grids((x0, x1, y0, y1)):
            rv._line(canvas, frame, a, b, brush((120, 120, 140)), 1, dash=True)
            for q in (a, b):
                rv._text(canvas, frame, q[0], q[1], name, brush=brush((40, 40, 80)), size=11, bold=True)
        for i, r in enumerate(self.state.regions):
            chosen = i == self.state.selected
            col = COLORS[i % len(COLORS)]
            for piece in sc.pieces(r["poly"], r["shape"], r["slope"], r.get("angle"), r.get("ridge")):
                rv._polygon(canvas, frame, piece["poly"], brush(col, 150 if chosen else 90),
                            brush((200, 40, 20)) if chosen else brush(col))
                if piece["arrow"]:
                    (ax, ay), (bx, by) = piece["arrow"]
                    rv._line(canvas, frame, (ax, ay), (bx, by), brush((180, 30, 30)), 2)
                    L = math.hypot(bx - ax, by - ay) or 1.0
                    ux, uy = (bx - ax) / L, (by - ay) / L
                    for side in (0.35, -0.35):
                        rv._line(canvas, frame, (bx, by), (bx - ux * 0.4 - uy * side, by - uy * 0.4 + ux * side),
                                 brush((180, 30, 30)), 2)
            cx = sum(p[0] for p in r["poly"]) / len(r["poly"])
            cy = sum(p[1] for p in r["poly"]) / len(r["poly"])
            rv._text(canvas, frame, cx, cy, u"{}".format(i + 1), brush=brush((30, 30, 30)), size=14, bold=True)

    # -- mouse ---------------------------------------------------------------------------
    def plan_wheel(self, sender, args):
        if self._fitted:
            self.nav.wheel(self._fitted, args.GetPosition(self.canvas_plan), args.Delta)
            self._draw_plan()
        args.Handled = True

    def plan_down(self, sender, args):
        if self._fitted is None:
            return
        if args.ChangedButton == MouseButton.Left and self._frame:
            scale, ox, oy = self._frame
            pt = args.GetPosition(self.canvas_plan)
            x, y = (pt.X - ox) / scale, (oy - pt.Y) / scale
            for i, r in enumerate(self.state.regions):
                if _inside(r["poly"], x, y):
                    self.state.selected = i
                    self._fill_list()
                    self._fill_shape()
                    self.redraw()
                    return
        if args.ChangedButton in (MouseButton.Middle, MouseButton.Left):
            self.nav.start_pan(self._fitted, args.GetPosition(self.canvas_plan))
            self.canvas_plan.CaptureMouse()

    def plan_move(self, sender, args):
        if self.nav.pan(args.GetPosition(self.canvas_plan)):
            self._draw_plan()

    def plan_up(self, sender, args):
        self.nav.end_pan()
        self.canvas_plan.ReleaseMouseCapture()

    def fit_click(self, sender, args):
        self.nav.reset()
        self._draw_plan()

    # -- actions ---------------------------------------------------------------------------
    def _go(self, action):
        self.form_changed(None, None)
        self.action = action
        self.Close()

    def pick_regions_click(self, sender, args):
        self._go("regions")

    def pick_rect_click(self, sender, args):
        self._go("rect")

    def pick_poly_click(self, sender, args):
        self._go("poly")

    def create_click(self, sender, args):
        if not self.state.regions:
            forms.alert(u"Agrega primero las losas (paños, rectángulo o contorno).", title=u"Crear Losas")
            return
        self._go("create")



def _inside(poly, x, y):
    return sg._inside(poly, x, y)


def new_region(state, poly, holes=None):
    poly = sg.offset_poly(sc.ccw(poly), state.edge)
    state.regions.append({"poly": poly, "holes": holes or [], "shape": sc.FLAT, "slope": 0.25,
                          "angle": None, "ridge": None})
    state.selected = len(state.regions) - 1


def prepare_view(state):
    """Planta: its level. 3D: the level of the first beam, wall or slab
    clicked, and a work plane at the slab's top so the clicks land on it.
    False when cancelled."""
    view = doc.ActiveView
    if isinstance(view, DB.View3D):
        if state.level_id is None:
            try:
                ref = uidoc.Selection.PickObject(ObjectType.Element, u"Vista 3D: clic en una viga, muro o losa del nivel")
            except OperationCanceledException:
                return False
            level = level_of(doc.GetElement(ref.ElementId))
            if level is None:
                forms.alert(u"Ese elemento no tiene nivel: elige una viga, un muro o una losa.", title=u"Crear Losas")
                return False
            state.level_id = level.Id.IntegerValue
        level = doc.GetElement(DB.ElementId(state.level_id))
        with revit.Transaction(u"Crear Losas - plano de trabajo"):
            plane = DB.Plane.CreateByNormalAndOrigin(DB.XYZ.BasisZ, DB.XYZ(0, 0, level.Elevation + state.top / FT))
            view.SketchPlane = DB.SketchPlane.Create(doc, plane)
        return True
    level = getattr(view, "GenLevel", None)
    if level is None:
        forms.alert(u"Abre una vista en planta del nivel (o una vista 3D).", title=u"Crear Losas")
        return False
    state.level_id = level.Id.IntegerValue
    return True


def level_of(e):
    """The level a slab touching this element sits on: a floor's own, a
    beam's reference level, a wall's top."""
    if isinstance(e, DB.Floor):
        return doc.GetElement(e.LevelId)
    for bip in (DB.BuiltInParameter.INSTANCE_REFERENCE_LEVEL_PARAM, DB.BuiltInParameter.WALL_HEIGHT_TYPE):
        p = e.get_Parameter(bip)
        if p is not None and p.AsElementId() != DB.ElementId.InvalidElementId:
            return doc.GetElement(p.AsElementId())
    lid = getattr(e, "LevelId", None)
    return doc.GetElement(lid) if lid and lid != DB.ElementId.InvalidElementId else None


def pick_point(prompt):
    p = uidoc.Selection.PickPoint(ObjectSnapTypes.Endpoints | ObjectSnapTypes.Intersections |
                                  ObjectSnapTypes.Nearest | ObjectSnapTypes.Midpoints, prompt)
    return p.X * FT, p.Y * FT


def pick_regions(state, window):
    found, open_, again = 0, 0, 0
    level_regions = window._regions_of_level()
    wide = None
    while True:
        try:
            x, y = pick_point(u"Clic dentro de cada paño (Esc termina)")
        except OperationCanceledException:
            break
        if any(_inside(r["poly"], x, y) for r in state.regions):
            again += 1  # this paño is already in the list
            continue
        r = level_regions.at(x, y)
        if r is None:  # a gap between beams left it open: closed up to 15 cm
            wide = wide or window._wide_regions()
            r = wide.at(x, y)
        if r is None:
            open_ += 1
            continue
        new_region(state, r["outer"], r["holes"])
        found += 1
    state.message = u"{} paño(s) agregado(s).{}{}".format(
        found, u" {} ya estaba(n) en la lista.".format(again) if again else u"",
        u" {} clic(s) fuera de un paño cerrado (abierto hacia afuera o sin vigas/muros alrededor): "
        u"usa Rectángulo o Contorno.".format(open_) if open_ else u"")


def pick_rect(state):
    try:
        a = pick_point(u"Primera esquina")
        b = pick_point(u"Esquina opuesta")
    except OperationCanceledException:
        return
    x0, x1 = sorted((a[0], b[0]))
    y0, y1 = sorted((a[1], b[1]))
    if x1 - x0 > 0.2 and y1 - y0 > 0.2:
        new_region(state, [(x0, y0), (x1, y0), (x1, y1), (x0, y1)])
        state.message = u"Rectángulo agregado ({:.2f} x {:.2f} m).".format(x1 - x0, y1 - y0)


def pick_poly(state):
    pts = []
    while True:
        try:
            pts.append(pick_point(u"Vértice {} del contorno (Esc cierra)".format(len(pts) + 1)))
        except OperationCanceledException:
            break
    if len(pts) >= 3 and abs(sc.area(pts)) > 0.05:
        new_region(state, pts)
        state.message = u"Contorno de {} vértices agregado.".format(len(pts))


def create_all(state, types):
    level = doc.GetElement(DB.ElementId(state.level_id))
    ftype = types[state.type_name]
    made, joined, failed = [], 0, []
    t = DB.Transaction(doc, u"Crear losas")
    warnings = _Warnings()
    opts = t.GetFailureHandlingOptions()
    opts.SetFailuresPreprocessor(warnings)
    opts.SetForcedModalHandling(False)
    t.SetFailureHandlingOptions(opts)
    t.Start()
    try:
        for i, r in enumerate(state.regions):
            try:
                floors = sg.create_region(doc, level, ftype, r, state.top)
                made += floors
            except Exception as e:
                failed.append(u"Losa {}: {}".format(i + 1, e))
        doc.Regenerate()
        if state.join:
            for f in made:
                joined += sf.join_beams_cutting(doc, f)  # the beams cut it: it stops at their faces
        t.Commit()
    except Exception:
        t.RollBack()
        raise
    return made, joined, failed  # Revit's warnings (overlaps...) dropped: the slabs are made


class _Warnings(DB.IFailuresPreprocessor):
    """Revit's warnings (overlapping floors, a sketch line slightly off
    axis...) dropped: the slabs are made without a dialog."""

    def __init__(self):
        self.messages = []

    def PreprocessFailures(self, accessor):
        for f in accessor.GetFailureMessages():
            if f.GetSeverity() == DB.FailureSeverity.Warning:
                self.messages.append(f.GetDescriptionText())
                accessor.DeleteWarning(f)
        return DB.FailureProcessingResult.Continue


# --- main -------------------------------------------------------------------
if __name__ == "__main__":
    state = State()
    if isinstance(doc.ActiveView, DB.View3D):
        state.level_id = None  # taken from the first element touched
    xaml = os.path.join(SCRIPT_DIR, "CrearForm.xaml")
    while True:
        window = CrearWindow(xaml, state)
        window.ShowDialog()
        if window.action in ("regions", "rect", "poly"):
            if prepare_view(state):
                if window.action == "regions":
                    pick_regions(state, window)
                elif window.action == "rect":
                    pick_rect(state)
                else:
                    pick_poly(state)
            continue
        break
    if window.action == "create":
        made, joined, failed = create_all(state, window.types)
        if made:
            uidoc.Selection.SetElementIds(List[DB.ElementId]([f.Id for f in made]))
        if failed:  # only what really could not be made
            forms.alert(u"Losas creadas: {}.\n\nNo se pudo crear:\n- ".format(len(made)) + u"\n- ".join(failed),
                        title=u"Crear Losas")