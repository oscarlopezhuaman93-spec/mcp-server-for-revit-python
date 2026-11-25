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


def _clean2(pts, eps=1e-6):
    """A closed polygon without repeated or straight-through points."""
    out = []
    for p in pts:
        if not out or abs(p[0] - out[-1][0]) > eps or abs(p[1] - out[-1][1]) > eps:
            out.append(p)
    if len(out) > 1 and abs(out[0][0] - out[-1][0]) <= eps and abs(out[0][1] - out[-1][1]) <= eps:
        out.pop()
    keep = []
    for k, p in enumerate(out):
        a, b = out[k - 1], out[(k + 1) % len(out)]
        if abs((p[0] - a[0]) * (b[1] - a[1]) - (p[1] - a[1]) * (b[0] - a[0])) > 1e-9:
            keep.append(p)
    return keep


def _ear_clip(poly):
    """Triangles (index triples) of a simple polygon (either winding)."""
    n = len(poly)
    if n < 3:
        return []
    area = sum(poly[i][0] * poly[(i + 1) % n][1] - poly[(i + 1) % n][0] * poly[i][1] for i in range(n))
    idx = list(range(n)) if area > 0 else list(reversed(range(n)))
    tris = []

    def cross(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    def inside(p, a, b, c):
        return cross(a, b, p) >= 0 and cross(b, c, p) >= 0 and cross(c, a, p) >= 0
    guard = 0
    while len(idx) > 3 and guard < 10000:
        guard += 1
        for k in range(len(idx)):
            i0, i1, i2 = idx[k - 1], idx[k], idx[(k + 1) % len(idx)]
            a, b, c = poly[i0], poly[i1], poly[i2]
            if cross(a, b, c) <= 1e-12:
                continue
            if any(inside(poly[j], a, b, c) for j in idx if j not in (i0, i1, i2)):
                continue
            tris.append((i0, i1, i2))
            idx.pop(k)
            break
        else:
            break
    if len(idx) == 3:
        tris.append(tuple(idx))
    return tris


class State(object):
    def __init__(self):
        self.type_name = None
        self.base_id = None
        self.top_id = None
        self.riser = self.tread = self.width = None  # cm, cm, m as typed
        self.waist = self.landing = None  # cm as typed (the type's by default)
        self.sketch = []  # [((x, y), (x, y))] runs drawn, in climbing order (m)


class EscaleraWindow(forms.WPFWindow):
    def __init__(self, xaml, state):
        forms.WPFWindow.__init__(self, xaml)
        try:
            import boki_brand  # BOKI ESTRUCTURAS and the seal in the header
            boki_brand.apply(self)
        except Exception:
            pass
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
        waist = s.waist if (keep and s.waist) else (round(info.waist * 100, 1) if info.waist else None)
        landing = s.landing if (keep and s.landing) else round(info.landing * 100, 1)
        self.txt_waist.Text = u"{:g}".format(waist) if waist else u"(no monolítico)"
        self.txt_landing.Text = u"{:g}".format(landing)
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
        # (2026-11-21) waist and landing typed here (cm), not only the type's
        info = self._info_named(s.type_name)
        s.waist = _num(self.txt_waist.Text, round(info.waist * 100, 1) if info and info.waist else 0.0) or None
        s.landing = _num(self.txt_landing.Text, round(info.landing * 100, 1) if info else 15.0)
        return base, top

    def info_used(self):
        """The chosen type's info with the waist and landing typed here."""
        info = self._info_named(self.state.type_name)
        if info is None:
            return None
        return scr.with_thickness(info, (self.state.waist or 0) / 100.0 or None, (self.state.landing or 0) / 100.0 or None)

    def plan(self):
        base, top = self._read()
        if base is None or top is None or top.Elevation <= base.Elevation:
            return None, u"El nivel superior debe estar más alto que el base."
        s = self.state
        height = (top.Elevation - base.Elevation) * FT
        if getattr(s, "steps", False) and getattr(s, "exact", None):
            return step_plan(s, base.Elevation * FT, height), None
        return sp.plan_stair(s.sketch, height, s.riser / 100.0, s.tread / 100.0, s.width), None

    def steps_changed(self, sender, args):
        self.state.steps = bool(self.chk_steps.IsChecked)
        try:
            from System.Windows import Visibility as _V
            self.panel_soffit.Visibility = _V.Visible if self.state.steps else _V.Collapsed
        except Exception:
            pass
        self.refresh()

    def soffit_changed(self, sender, args):
        self.state.soffit = "sloped" if self.rad_sloped.IsChecked else "stepped"
        if not getattr(self, "_filling", True):
            self.refresh()

    # -- 3D view (2026-11-22) -------------------------------------------------
    def view3d_down(self, sender, args):
        self._drag = args.GetPosition(self.border_3d)
        self.border_3d.CaptureMouse()

    def view3d_move(self, sender, args):
        if getattr(self, "_drag", None) is None:
            return
        p = args.GetPosition(self.border_3d)
        self._yaw = getattr(self, "_yaw", -50.0) - (p.X - self._drag.X) * 0.5
        self._pitch = max(-80.0, min(80.0, getattr(self, "_pitch", 30.0) + (p.Y - self._drag.Y) * 0.4))
        self._drag = p
        self._place_camera()

    def view3d_up(self, sender, args):
        self._drag = None
        self.border_3d.ReleaseMouseCapture()

    def _place_camera(self):
        from System.Windows.Media.Media3D import PerspectiveCamera, Point3D, Vector3D
        c, r = getattr(self, "_center", (0, 0, 0)), getattr(self, "_radius", 5.0)
        yaw, pitch = math.radians(getattr(self, "_yaw", -50.0)), math.radians(getattr(self, "_pitch", 30.0))
        eye = (c[0] + r * math.cos(pitch) * math.cos(yaw), c[1] + r * math.cos(pitch) * math.sin(yaw),
               c[2] + r * math.sin(pitch))
        cam = PerspectiveCamera()
        cam.Position = Point3D(*eye)
        cam.LookDirection = Vector3D(c[0] - eye[0], c[1] - eye[1], c[2] - eye[2])
        cam.UpDirection = Vector3D(0, 0, 1)
        cam.FieldOfView = 40
        self.view_3d.Camera = cam

    def _draw_3d(self, plan, info):
        """The stair as it will be built: runs (steps over a straight waist),
        landings, the stepped / sloped soffit of a stepped landing."""
        from System.Windows.Media.Media3D import (Model3DGroup, GeometryModel3D, MeshGeometry3D, Point3D,
                                                  DiffuseMaterial, ModelVisual3D, AmbientLight, DirectionalLight,
                                                  Vector3D)
        from System.Windows.Media import Colors
        s = self.state
        tread = s.tread / 100.0
        waist = (info.waist if info and info.waist else 0.15)
        thick = info.landing if info else 0.15
        group = Model3DGroup()
        group.Children.Add(AmbientLight(Color.FromRgb(110, 110, 110)))
        group.Children.Add(DirectionalLight(Colors.White, Vector3D(-0.4, 0.6, -1.0)))
        pts_all = []

        def add_prism(profile, frame, length, color):
            """profile [(u, z)] in the plane through origin o (direction e for
            u), extruded `length` along w: frame = (o, e, w)."""
            o, e, w = frame
            mesh = MeshGeometry3D()

            def P(u, z, k):
                return (o[0] + e[0] * u + w[0] * k, o[1] + e[1] * u + w[1] * k, z)
            n = len(profile)
            for k in (0.0, length):
                for u, z in profile:
                    q = P(u, z, k)
                    mesh.Positions.Add(Point3D(*q))
                    pts_all.append(q)
            for a, b, c in _ear_clip(profile):
                mesh.TriangleIndices.Add(a); mesh.TriangleIndices.Add(c); mesh.TriangleIndices.Add(b)
                mesh.TriangleIndices.Add(a + n); mesh.TriangleIndices.Add(b + n); mesh.TriangleIndices.Add(c + n)
            for i in range(n):
                j = (i + 1) % n
                for t in ((i, j, j + n), (i, j + n, i + n)):
                    for v in t:
                        mesh.TriangleIndices.Add(v)
                    for v in reversed(t):
                        mesh.TriangleIndices.Add(v)
            m = GeometryModel3D(mesh, DiffuseMaterial(SolidColorBrush(color)))
            m.BackMaterial = DiffuseMaterial(SolidColorBrush(color))
            group.Children.Add(m)

        def add_slab(poly, z_top, h, color):
            mesh = MeshGeometry3D()
            n = len(poly)
            for z in (z_top - h, z_top):
                for x, y in poly:
                    mesh.Positions.Add(Point3D(x, y, z))
                    pts_all.append((x, y, z))
            for a, b, c in _ear_clip(poly):
                for v in (a, b, c, a + n, b + n, c + n):
                    mesh.TriangleIndices.Add(v)
            for i in range(n):
                j = (i + 1) % n
                for v in (i, j, j + n, i, j + n, i + n):
                    mesh.TriangleIndices.Add(v)
            m = GeometryModel3D(mesh, DiffuseMaterial(SolidColorBrush(color)))
            m.BackMaterial = DiffuseMaterial(SolidColorBrush(color))
            group.Children.Add(m)
        c_run, c_land = Color.FromRgb(190, 196, 204), Color.FromRgb(220, 205, 170)
        for k, r in enumerate(plan["runs"]):
            d = r["dir"]
            nrm = (-d[1], d[0])
            treads = r["risers"] if r.get("top_tread") else r["risers"] - 1
            riser = plan["riser"]
            z0 = r["z0"]
            top = [(0.0, z0)]
            for i in range(r["risers"]):
                top += [(i * tread, z0 + (i + 1) * riser), (min(i + 1, treads) * tread, z0 + (i + 1) * riser)]
            run_len = treads * tread
            slope = math.atan2(r["risers"] * riser, max(run_len, tread))
            off = waist / math.cos(slope)
            down = r.get("from_landing", thick if k else 0.0)
            prof = _clean2(top + [(run_len, z0 + r["risers"] * riser - off), (0.0, z0 - off - down)])
            a = r["start"]
            o = (a[0] - nrm[0] * s.width / 2.0, a[1] - nrm[1] * s.width / 2.0)
            add_prism(prof, (o, d, nrm), s.width, c_run)
        for l in plan["landings"]:
            add_slab(l["poly"], l["z"], thick, c_land)
        # (2026-11-22) a stepped landing's soffit as it will be built: under
        # each step the full-thickness block (Escalonado) or the sloped piece
        # (Inclinado) - the 3D changes with the option
        if plan.get("steps"):
            c_join = Color.FromRgb(225, 150, 80)
            lands = plan["landings"]
            for lo in lands:
                for hi in lands:
                    dz = hi["z"] - lo["z"]
                    if dz <= 0.01 or dz > 0.5:
                        continue
                    edge = scr._shared_edge(lo["poly"], hi["poly"])
                    if edge is None:
                        continue
                    p, q, L = edge
                    ex, ey = (q[0] - p[0]) / L, (q[1] - p[1]) / L
                    nn = (-ey, ex)
                    cx = sum(v[0] for v in hi["poly"]) / len(hi["poly"])
                    cy = sum(v[1] for v in hi["poly"]) / len(hi["poly"])
                    if (cx - p[0]) * nn[0] + (cy - p[1]) * nn[1] < 0:
                        nn = (-nn[0], -nn[1])
                    z0, z1 = lo["z"] - thick, hi["z"] - thick
                    if plan.get("soffit") == "sloped":
                        run = (z1 - z0) / max(plan["riser"] / tread, 0.05)
                        faces = scr.sloped_piece_faces(hi["poly"], p, nn, run, z0, z1)  # inside its outline
                        if faces:
                            mesh = MeshGeometry3D()
                            for f in faces:
                                i0 = mesh.Positions.Count
                                for q in f:
                                    mesh.Positions.Add(Point3D(*q))
                                    pts_all.append(q)
                                for k in range(1, len(f) - 1):
                                    for v in (i0, i0 + k, i0 + k + 1):
                                        mesh.TriangleIndices.Add(v)
                            m = GeometryModel3D(mesh, DiffuseMaterial(SolidColorBrush(c_join)))
                            m.BackMaterial = DiffuseMaterial(SolidColorBrush(c_join))
                            group.Children.Add(m)
                    else:
                        strip = scr._clip_half(hi["poly"], p, nn, 0.0, 1)
                        strip = scr._clip_half(strip, p, nn, thick, -1)
                        if len(strip) >= 3:
                            add_slab(strip, z1, z1 - z0, c_join)
        if not pts_all:
            return
        model = ModelVisual3D()
        model.Content = group
        self.view_3d.Children.Clear()
        self.view_3d.Children.Add(model)
        xs, ys, zs = [p[0] for p in pts_all], [p[1] for p in pts_all], [p[2] for p in pts_all]
        self._center = ((max(xs) + min(xs)) / 2.0, (max(ys) + min(ys)) / 2.0, (max(zs) + min(zs)) / 2.0)
        self._radius = 2.2 * max(max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs), 1.0)
        self._place_camera()

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
        info = self.info_used()
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
            try:
                self._draw_3d(plan, info)
            except Exception:
                pass

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

    def revit_sketch_click(self, sender, args):
        self._read()
        self.action = "revit"
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
    floors = [e for e in pieces if isinstance(e, (DB.Floor, DB.DirectShape))]  # landings and waist wedges
    return stairs, floors


def _top_faces(element):
    """[(z, xmin, xmax, ymin, ymax)] (m) of the upward flat faces of an
    element's solids: its treads and landings."""
    out = []

    def walk(g):
        for x in g:
            if isinstance(x, DB.Solid) and x.Volume > 0:
                for f in x.Faces:
                    if isinstance(f, DB.PlanarFace) and f.FaceNormal.Z > 0.99:
                        pts = [ed.AsCurve().GetEndPoint(0) for lp in f.EdgeLoops for ed in lp]
                        xs = [p.X * FT for p in pts]
                        ys = [p.Y * FT for p in pts]
                        out.append((pts[0].Z * FT, min(xs), max(xs), min(ys), max(ys)))
            elif isinstance(x, DB.GeometryInstance):
                walk(x.GetInstanceGeometry())
    walk(element.get_Geometry(DB.Options()))
    return out


def _landing_faces(element):
    """[(polygon [(x, y)] m, z m)] of the upward flat faces of an element,
    their real outline (a landing drawn with risers across it: its pieces)."""
    out = []

    def walk(g):
        for x in g:
            if isinstance(x, DB.Solid) and x.Volume > 0:
                for f in x.Faces:
                    if isinstance(f, DB.PlanarFace) and f.FaceNormal.Z > 0.99:
                        lp = max(f.EdgeLoops, key=lambda l: l.Size)
                        pts = []
                        for ed in lp:
                            q = ed.AsCurve().GetEndPoint(0)
                            p = (q.X * FT, q.Y * FT)
                            if not pts or math.hypot(p[0] - pts[-1][0], p[1] - pts[-1][1]) > 0.005:
                                pts.append(p)
                        if len(pts) > 1 and math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1]) < 0.005:
                            pts.pop()
                        clean = []
                        for k, p in enumerate(pts):  # drop the points in a straight side
                            a, b = pts[k - 1], pts[(k + 1) % len(pts)]
                            if abs((p[0] - a[0]) * (b[1] - a[1]) - (p[1] - a[1]) * (b[0] - a[0])) > 1e-4:
                                clean.append(p)
                        if len(clean) >= 3:
                            out.append((clean, lp[0].AsCurve().GetEndPoint(0).Z * FT))
            elif isinstance(x, DB.GeometryInstance):
                walk(x.GetInstanceGeometry())
    walk(element.get_Geometry(DB.Options()))
    return out


def step_plan(state, base_z, height):
    """(2026-11-22) The plan of a sketched stair kept as drawn: its runs
    with their own risers and heights, every landing piece at its own
    height (stair_create joins consecutive ones with a connecting slab)."""
    exact, steps, drawn_riser = state.exact
    steps = sorted(steps, key=lambda s: s[1])
    # (2026-11-22) the riser and tread typed in the window, as everywhere:
    # the whole height shared in equal risers no higher than the one typed;
    # the drawing keeps its runs and its landing pieces (one riser between
    # each), the riser count of the runs fitted to that total
    total = sp.riser_count(height, (state.riser or 17.5) / 100.0)
    riser = height / total if total else drawn_riser
    tread = (state.tread or 25.0) / 100.0
    on_landing = max(0, len(steps) - 1)
    counts = [r["risers"] for r in exact]
    extra = total - on_landing - sum(counts)
    if counts:
        counts[-1] = max(1, counts[-1] + extra)  # the last run takes what is left / over
    runs = []
    z = 0.0
    for k, r in enumerate(exact):
        n = counts[k]
        d = r["dir"]
        start = r["start"]
        end = (start[0] + d[0] * (n - 1) * tread, start[1] + d[1] * (n - 1) * tread)
        runs.append(dict(r, risers=n, z0=z, z1=z + n * riser, end=end, top_tread=False))
        z += n * riser
        if k == 0:
            z += on_landing * riser  # the landing's own steps
    z_land = runs[0]["z1"] if runs else 0.0
    landings = [{"poly": poly, "z": z_land + j * riser} for j, (poly, _) in enumerate(steps)]
    landings = scr.snap_pieces(landings)  # the slits Revit's sketch leaves between them closed
    return {"total": total, "riser": riser, "runs": runs, "landings": landings, "remaining": 0,
            "warnings": [], "steps": True, "soffit": getattr(state, "soffit", "stepped")}


def sketched_runs(stairs, run, tread):
    """(2026-11-22) A run drawn with Revit's own sketch (Contorno /
    Contrahuella / Camino) is one run over the whole stair, its landings in
    it - often stepped (risers drawn across the landing: two landings at two
    heights, no slab to reinforce). Read back as straight runs: the path's
    legs that hold treads, each from its first riser; the risers left on
    the landings shared out to the runs on each side of it (one flat
    landing between them, as built). Returns ([(start, end)], width, notes)."""
    path = list(run.GetStairsPath())
    legs = []
    for c in path:
        a, b = c.GetEndPoint(0), c.GetEndPoint(1)
        a, b = (a.X * FT, a.Y * FT), (b.X * FT, b.Y * FT)
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        if L > 1e-6:
            legs.append((a, ((b[0] - a[0]) / L, (b[1] - a[1]) / L), L))
    faces = _top_faces(stairs)
    found = []  # per leg: [t of each tread's start along the leg]
    widths = []
    for a, d, L in legs:
        n = (-d[1], d[0])
        ts = []
        for z, x0, x1, y0, y1 in faces:
            cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
            depth = abs(d[0]) * (x1 - x0) + abs(d[1]) * (y1 - y0)
            across = abs(n[0]) * (x1 - x0) + abs(n[1]) * (y1 - y0)
            t = (cx - a[0]) * d[0] + (cy - a[1]) * d[1]
            v = (cx - a[0]) * n[0] + (cy - a[1]) * n[1]
            if abs(depth - tread) < 0.03 and -0.01 < t < L + 0.01 and abs(v) < across / 2.0 + 0.05:
                ts.append(t - depth / 2.0)
                widths.append(across)
        found.append(sorted(ts))
    runs = [(leg, ts) for leg, ts in zip(legs, found) if len(ts) >= 1]
    total = stairs.ActualRisersNumber
    # (2026-11-22) as drawn, too: each run from its first riser with its own
    # treads and height, and every landing piece at its own height
    riser = stairs.ActualRiserHeight * FT
    exact = []
    for (a, d, L), ts in runs:
        z_first = None
        nn = (-d[1], d[0])
        for z, x0, x1, y0, y1 in faces:
            cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
            t = (cx - a[0]) * d[0] + (cy - a[1]) * d[1]
            v = (cx - a[0]) * nn[0] + (cy - a[1]) * nn[1]
            across = abs(nn[0]) * (x1 - x0) + abs(nn[1]) * (y1 - y0)
            depth = abs(d[0]) * (x1 - x0) + abs(d[1]) * (y1 - y0)
            if abs(depth - tread) < 0.03 and abs(t - depth / 2.0 - ts[0]) < 0.01 and abs(v) < across / 2.0 + 0.05:
                z_first = z if z_first is None else min(z_first, z)
        start = (a[0] + d[0] * ts[0], a[1] + d[1] * ts[0])
        end = (start[0] + d[0] * len(ts) * tread, start[1] + d[1] * len(ts) * tread)
        exact.append({"start": start, "end": end, "dir": d, "risers": len(ts) + 1,
                      "z_abs": (z_first or 0.0) - riser})
    tread_like = set()
    for (a, d, L), ts in runs:
        for f in faces:
            z, x0, x1, y0, y1 = f
            depth = abs(d[0]) * (x1 - x0) + abs(d[1]) * (y1 - y0)
            if abs(depth - tread) < 0.03:
                cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
                t = (cx - a[0]) * d[0] + (cy - a[1]) * d[1]
                if -0.01 < t < L + 0.01:
                    tread_like.add(f)
    steps = [(poly, z) for poly, z in _landing_faces(stairs)
             if not any(abs(z - f[0]) < 1e-3 for f in tread_like)]
    sketched_runs.exact = (exact, steps, riser)
    in_runs = sum(len(ts) + 1 for _, ts in runs)
    extra = max(0, total - in_runs)  # risers drawn on the landings
    notes = []
    if extra and len(runs) > 1:
        notes.append(u"{} contrahuellas dibujadas sobre el descanso pasan a los tramos: "
                     u"un solo descanso plano.".format(extra))
    out = []
    gaps = len(runs) - 1
    for k, ((a, d, L), ts) in enumerate(runs):
        # the extra risers of the landing before / after this run
        before = (extra // gaps) // 2 if (k > 0 and gaps) else 0
        after = (extra // gaps) - (extra // gaps) // 2 if (k < gaps and gaps) else 0
        if gaps and k == gaps - 1 and extra % gaps:
            after += extra % gaps
        t0 = ts[0] - before * tread
        count = len(ts) + 1 + before + after
        start = (a[0] + d[0] * t0, a[1] + d[1] * t0)
        end = (start[0] + d[0] * count * tread, start[1] + d[1] * count * tread)
        out.append((start, end))
    width = round(min(widths), 2) if widths else None
    return out, width, notes


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
            if str(r.StairsRunStyle) == "Sketched":
                legs, width, notes = sketched_runs(s, r, s.ActualTreadDepth * FT)
                z = base.Elevation + off + getattr(r, "BaseElevation", 0.0)
                for k, (a, b) in enumerate(legs):
                    runs.append((z + k * 1e-3, s, r, a, b))
                state.sketch_width = width
                state.notes = notes
                state.exact = getattr(sketched_runs, "exact", None)
                state.steps = bool(state.exact and len(state.exact[1]) > 1)  # landings at two heights drawn
                continue
            curves = list(r.GetStairsPath())
            if not curves:
                continue
            a, b = curves[0].GetEndPoint(0), curves[-1].GetEndPoint(1)
            # (2026-11-21) one Revit stair of several runs (drawn with Revit's
            # own sketch: boundary, risers, path): each run by its own height
            z = base.Elevation + off + getattr(r, "BaseElevation", 0.0)
            runs.append((z, s, r, (a.X * FT, a.Y * FT), (b.X * FT, b.Y * FT)))
    if not runs:
        return False
    runs.sort(key=lambda x: x[0])
    first = runs[0][1]
    base = doc.GetElement(first.get_Parameter(DB.BuiltInParameter.STAIRS_BASE_LEVEL_PARAM).AsElementId())
    top_z = max(doc.GetElement(s.get_Parameter(DB.BuiltInParameter.STAIRS_BASE_LEVEL_PARAM).AsElementId()).Elevation
                + s.get_Parameter(DB.BuiltInParameter.STAIRS_BASE_OFFSET).AsDouble()
                + s.get_Parameter(DB.BuiltInParameter.STAIRS_STAIRS_HEIGHT).AsDouble() for s in stairs)
    top = min(levels(), key=lambda l: abs(l.Elevation - top_z))
    state.type_name = _name(doc.GetElement(first.GetTypeId()))
    state.base_id = base.Id.IntegerValue
    state.top_id = top.Id.IntegerValue
    state.riser = math.ceil(first.ActualRiserHeight * FT * 1000 - 1e-6) / 10.0  # the max: its own riser
    state.tread = round(first.ActualTreadDepth * FT * 100, 1)
    state.width = round(runs[0][2].ActualRunWidth * FT, 2) or getattr(state, "sketch_width", None) or 1.20
    state.sketch = [(a, b) for _, _, _, a, b in runs]
    rt = doc.GetElement(runs[0][2].GetTypeId())
    if rt is not None and getattr(rt, "IsMonolithic", False):
        state.waist = round(rt.StructuralDepth * FT * 100, 1)
    slabs = [f for f in floors if isinstance(f, DB.Floor)]
    if slabs:
        p = slabs[0].get_Parameter(DB.BuiltInParameter.FLOOR_ATTR_THICKNESS_PARAM)
        if p is not None:
            state.landing = round(p.AsDouble() * FT * 100, 1)
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
        if getattr(state, "exact", None) and len(state.exact[1]) > 1:
            from System.Windows import Visibility as _Vis
            window._filling = True
            window.chk_steps.Visibility = _Vis.Visible
            window.chk_steps.IsChecked = bool(state.steps)
            window.panel_soffit.Visibility = _Vis.Visible if state.steps else _Vis.Collapsed
            window.rad_sloped.IsChecked = getattr(state, "soffit", "stepped") == "sloped"
            window.rad_stepped.IsChecked = not window.rad_sloped.IsChecked
            window._filling = False
            window.refresh()
        if state.edit_ids:
            window.Title = u"Escalera - EDITANDO la escalera seleccionada ({} piezas)".format(len(state.edit_ids))
            window.button_create.Content = u"ACTUALIZAR ESCALERA"
        window.ShowDialog()
        if window.action == "draw":
            draw_in_plan(state)
            continue
        break
    if window.action == "revit":
        # (2026-11-21) Revit's own stair tool, its sketch (Contorno,
        # Contrahuella, Camino de escalera); the stair drawn is then picked
        # and opened here to be made a concrete one
        try:
            base = doc.GetElement(DB.ElementId(state.base_id))
            uidoc.ActiveView = plan_view_of(base)
        except Exception:
            pass
        forms.alert(u"Se abre la herramienta Escalera de Revit.\n\n"
                    u"1. En la cinta, pulsa 'Crear boceto' (lápiz) y dibuja con Contorno, "
                    u"Contrahuella y Camino de escalera.\n"
                    u"2. Pulsa Finalizar (✔) dos veces.\n"
                    u"3. Selecciona la escalera dibujada y abre BOKI Escalera: "
                    u"pulsa ACTUALIZAR ESCALERA y se hace de concreto (garganta, descansos).",
                    title="Escalera")
        from Autodesk.Revit.UI import RevitCommandId, PostableCommand
        revit.uidoc.Application.PostCommand(RevitCommandId.LookupPostableCommandId(PostableCommand.Stair))
    if window.action == "create":
        plan, _ = window.plan()
        info = window.info_used()  # the type with the waist / landing typed
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
