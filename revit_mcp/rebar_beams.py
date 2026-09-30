# -*- coding: UTF-8 -*-
"""
Beam rebar ("Acero Viga"): the counterpart of rebar_columns for
Structural Framing.

- A beam as detailed is a *beam line*: the elements of one type on one
  axis, one after the other (the model splits a beam at its supports).
- Its supports are the columns, walls and deeper beams it crosses; the
  clear spans lie between their faces.
- Stirrups: the type's distribution ('1@.05, 10@.10, rto@.20') in every
  clear span, measured from each support face (spec.stirrup_sets), none
  inside the supports.
- Longitudinal bars (the drawing's top and bottom bars) run the whole
  beam line, anchored at its ends (90-degree hook or straight, per type)
  and spliced beyond the maximum bar length - top bars in the central
  third of a span, bottom bars in its end thirds outside the confinement
  zone (the user's rules).

Local frame of a beam line: x across (horizontal), y up, both from the
section center; s along the axis from the line start. Meters.

Runs inside Revit's IronPython engine - no f-strings.
"""
import math

from pyrevit import DB
from Autodesk.Revit.DB.Structure import Rebar, RebarHookOrientation, RebarStyle
from System.Collections.Generic import List

import formwork_params as fw_params
import rebar_columns as rc
import rebar_spec as spec
from utils import element_id_value

FT = rc.FT
ANCHOR_PARAM = "EA_Anclaje"
ANCHOR_HOOK = u"Gancho 90"
ANCHOR_STRAIGHT = u"Recto"
ANCHORS = (ANCHOR_HOOK, ANCHOR_STRAIGHT)
DEFAULT_COVER_CM = 4.0
LINE_GAP_M = 1.2  # elements of one type and axis this close form one beam
SUPPORT_REACH_M = 1.0  # supports looked for this far beyond the line ends
LINE_TOL_M = 0.02
BEAM_MARK = u"VIGA"  # bar types named for beams are preferred

FRAMING = [DB.BuiltInCategory.OST_StructuralFraming]


def ensure_parameters(doc):
    """The column parameters (bar types, origin tags...) plus the same type
    parameters, the anchorage and the weight on Structural Framing. Inside
    an active Transaction."""
    warnings = rc.ensure_parameters(doc)
    specs = [(name, True, FRAMING, False) for name in rc.TYPE_PARAMS + (ANCHOR_PARAM,)]
    specs.append((rc.WEIGHT_PARAM, False, FRAMING, True))
    return warnings + fw_params.ensure_parameters(doc, rc.GROUP_NAME, specs)


def read_type_config(beam_type):
    config = rc.read_type_config(beam_type)
    p = beam_type.LookupParameter(ANCHOR_PARAM)
    config[ANCHOR_PARAM] = (p.AsString() or u"") if p is not None else u""
    return config


def write_type_config(beam_type, config):
    rc.write_type_config(beam_type, config)
    p = beam_type.LookupParameter(ANCHOR_PARAM)
    if p is not None and not p.IsReadOnly and ANCHOR_PARAM in config:
        p.Set(config[ANCHOR_PARAM] or u"")


def anchor_of(config):
    value = (config.get(ANCHOR_PARAM) or u"").strip()
    return value if value in ANCHORS else ANCHOR_HOOK


class BeamSection(object):
    """Cross-section of a straight beam element, from its end face (the
    element's own geometry). `polygon_m` in the local frame (x across, y
    up, from the section's bounding-box center); b, h in feet like
    rc.Section."""

    def __init__(self, beam):
        curve = beam.Location.Curve
        if not isinstance(curve, DB.Line):
            raise spec.SpecError(u"la viga {} no es recta".format(element_id_value(beam.Id)))
        p0 = curve.GetEndPoint(0)
        d = (curve.GetEndPoint(1) - p0).Normalize()
        if abs(d.Z) > 1e-3:
            raise spec.SpecError(u"la viga {} esta inclinada".format(element_id_value(beam.Id)))
        across = DB.XYZ.BasisZ.CrossProduct(d).Normalize()
        best = None
        for solid in rc._solids(beam.get_Geometry(DB.Options())):
            for face in solid.Faces:
                if isinstance(face, DB.PlanarFace) and abs(abs(face.FaceNormal.DotProduct(d)) - 1.0) < 1e-3:
                    loop = max(face.EdgeLoops, key=lambda lp: lp.Size)
                    pts = [edge.AsCurve().GetEndPoint(0) for edge in loop]
                    poly = [((q - p0).DotProduct(across), q.Z - p0.Z) for q in pts]
                    if best is None or face.Area > best[0]:
                        best = (face.Area, poly)
        if best is None:
            raise spec.SpecError(u"no se encontro la seccion de la viga {}".format(element_id_value(beam.Id)))
        poly = best[1]
        xs = [p[0] for p in poly]
        ys = [p[1] for p in poly]
        cx, cy = (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0
        self.axis_origin = p0
        self.direction = d
        self.across = across
        self.center = (cx, cy)  # ft, from the axis point, across and up
        self.polygon_m = spec.clean_polyline([((x - cx) * FT, (y - cy) * FT) for x, y in poly], closed=True)
        self.b = max(xs) - min(xs)
        self.h = max(ys) - min(ys)
        self.is_rectangle = len(self.polygon_m) == 4 and all(
            abs(a[0] - b[0]) < 1e-4 or abs(a[1] - b[1]) < 1e-4
            for a, b in zip(self.polygon_m, self.polygon_m[1:] + self.polygon_m[:1]))


class BeamLine(object):
    """The elements of one beam type on one axis, one after the other: one
    beam as detailed. s runs from the start of the first element."""

    def __init__(self, elements):
        first = elements[0]
        c = first.Location.Curve
        d = (c.GetEndPoint(1) - c.GetEndPoint(0)).Normalize()
        ranges = []
        for e in elements:
            ec = e.Location.Curve
            ts = sorted(((ec.GetEndPoint(k) - c.GetEndPoint(0)).DotProduct(d) for k in (0, 1)))
            ranges.append((ts[0], ts[1], e))
        ranges.sort(key=lambda r: r[0])
        s0 = ranges[0][0]
        self.elements = [e for _, _, e in ranges]
        self.ranges = [((a - s0) * FT, (b - s0) * FT) for a, b, _ in ranges]  # m, per element
        self.length = self.ranges[-1][1]
        self.section = BeamSection(first)
        sec = self.section
        # the line frame: the first element's axis, moved to the line start
        self.origin = c.GetEndPoint(0) + d * s0
        self.direction = d
        if (sec.direction - d).GetLength() > 1e-6:  # the section read from a reversed curve
            sec.direction = d
            sec.across = DB.XYZ.BasisZ.CrossProduct(d).Normalize()
        ys = [p[1] for p in sec.polygon_m]
        self.ref_bottom, self.ref_top = min(ys), max(ys)  # the reference (deepest end) section
        self._read_profile()

    def _read_profile(self):
        """Bottom and top faces along the line, [(s, y)] each, from the
        vertices of its elements' solids: at every s the lowest and highest
        vertex (a lone vertex goes to the face it is nearer, in the
        reference section). `variable`: a haunch - the depth changes along
        the beam."""
        by_s = {}
        for element in self.elements:
            for solid in rc._solids(element.get_Geometry(DB.Options())):
                for edge in solid.Edges:
                    curve = edge.AsCurve()
                    for k in (0, 1):
                        x, y, s = self.local(curve.GetEndPoint(k))
                        by_s.setdefault(round(s, 3), []).append(y)
        bottom, top = [], []
        for s in sorted(by_s):
            lo, hi = min(by_s[s]), max(by_s[s])
            if hi - lo > 0.02:
                bottom.append((s, lo))
                top.append((s, hi))
            elif abs(lo - self.ref_top) < abs(lo - self.ref_bottom):
                top.append((s, hi))
            else:
                bottom.append((s, lo))
        self.bottom_points = bottom or [(0.0, self.ref_bottom)]
        self.top_points = top or [(0.0, self.ref_top)]
        self.variable = any(abs(y - self.ref_bottom) > 0.01 for _, y in self.bottom_points) or \
            any(abs(y - self.ref_top) > 0.01 for _, y in self.top_points)
        self.breaks = sorted(set(s for s, _ in self.bottom_points + self.top_points))

    def depth_at(self, s):
        """(bottom, top) of the section at s (local y)."""
        return spec.interpolate(self.bottom_points, s), spec.interpolate(self.top_points, s)

    def polygon_at(self, points, s):
        """Points drawn on the reference section, fitted to the section at s."""
        bottom, top = self.depth_at(s)
        return spec.haunch_polyline(points, self.ref_bottom, self.ref_top, bottom, top)

    def shift_at(self, y):
        """The vertical shift along the beam of a bar at reference height y."""
        def shift(s):
            bottom, top = self.depth_at(s)
            return spec.haunch_shift(y, self.ref_bottom, self.ref_top, bottom, top)
        return shift

    @property
    def type_id(self):
        return self.elements[0].GetTypeId()

    def point(self, x, y, s):
        """World point of local (x, y, s) (m)."""
        sec = self.section
        return (self.origin + self.direction * (s / FT)
                + sec.across * (sec.center[0] + x / FT)
                + DB.XYZ.BasisZ * (sec.center[1] + y / FT))

    def local(self, p):
        """(x, y, s) in m of a world point."""
        q = p - self.origin
        sec = self.section
        return ((q.DotProduct(sec.across) - sec.center[0]) * FT,
                (q.Z - sec.center[1]) * FT,
                q.DotProduct(self.direction) * FT)

    def element_at(self, s):
        """The element holding s (a gap between two goes half to each; the
        ends extend beyond)."""
        bounds = []
        for k, (a, b) in enumerate(self.ranges):
            lo = -1e9 if k == 0 else (self.ranges[k - 1][1] + a) / 2.0
            hi = 1e9 if k == len(self.ranges) - 1 else (b + self.ranges[k + 1][0]) / 2.0
            bounds.append((lo, hi))
            if lo <= s < hi:
                return self.elements[k]
        return self.elements[-1]

    def element_bounds(self):
        """[(lo, hi, element)]: the stretch of the line each element owns
        (gaps split in half, the ends open) - to share a bar's weight."""
        out = []
        for k, (a, b) in enumerate(self.ranges):
            lo = -1e9 if k == 0 else (self.ranges[k - 1][1] + a) / 2.0
            hi = 1e9 if k == len(self.ranges) - 1 else (b + self.ranges[k + 1][0]) / 2.0
            out.append((lo, hi, self.elements[k]))
        return out


def beam_lines(beams, gap_m=LINE_GAP_M, tol_m=LINE_TOL_M):
    """Group beam elements into beam lines: same type, straight and level,
    collinear (within tol_m) and one after the other with gaps up to
    gap_m. Elements that aren't straight or level stay out."""
    items = []
    for b in beams:
        loc = getattr(b, "Location", None)
        curve = getattr(loc, "Curve", None)
        if not isinstance(curve, DB.Line):
            continue
        p0, p1 = curve.GetEndPoint(0), curve.GetEndPoint(1)
        if abs(p1.Z - p0.Z) * FT > tol_m:
            continue
        items.append((b, p0, p1))
    lines = []  # [(type id, origin, direction, [(t0, t1, element)])]
    for b, p0, p1 in items:
        d = (p1 - p0).Normalize()
        placed = False
        for line in lines:
            tid, o, ld, members = line
            if tid != b.GetTypeId() or abs(abs(ld.DotProduct(d)) - 1.0) > 1e-4:
                continue
            off = p0 - o
            perp = off - ld * off.DotProduct(ld)
            if perp.GetLength() * FT > tol_m:
                continue
            ts = sorted(((q - o).DotProduct(ld) for q in (p0, p1)))
            members.append((ts[0], ts[1], b))
            placed = True
            break
        if not placed:
            lines.append((b.GetTypeId(), p0, d, [(0.0, (p1 - p0).GetLength(), b)]))
    result = []
    for tid, o, ld, members in lines:
        members.sort(key=lambda m: m[0])
        group = [members[0]]
        for m in members[1:]:
            if (m[0] - group[-1][1]) * FT <= gap_m:
                group.append(m)
            else:
                result.append(group)
                group = [m]
        result.append(group)
    lines_out = []
    for group in result:
        try:
            lines_out.append(BeamLine([e for _, _, e in group]))
        except spec.SpecError:
            continue
    return lines_out


def _prism(line, s0, s1):
    """The beam line's section bounding box swept from s0 to s1 (m)."""
    xs = [p[0] for p in line.section.polygon_m]
    ys = [p[1] for p in line.section.polygon_m]
    corners = [line.point(x, y, s0) for x, y in (
        (min(xs), min(ys)), (max(xs), min(ys)), (max(xs), max(ys)), (min(xs), max(ys)))]
    loop = DB.CurveLoop()
    for k in range(4):
        loop.Append(DB.Line.CreateBound(corners[k], corners[(k + 1) % 4]))
    return DB.GeometryCreationUtilities.CreateExtrusionGeometry(
        List[DB.CurveLoop]([loop]), line.direction, (s1 - s0) / FT)


SUPPORT_CATEGORIES = (
    (DB.BuiltInCategory.OST_StructuralColumns, u"COLUMNA"),
    (DB.BuiltInCategory.OST_Walls, u"MURO"),
    (DB.BuiltInCategory.OST_StructuralFraming, u"VIGA"),
)


def line_supports(doc, line, reach_m=SUPPORT_REACH_M):
    """What the beam line rests on, along it: columns and walls running
    through most of its depth, and crossing beams at least as deep (a
    shallower beam framing into it is carried, not a support). Each
    {"label", "s0", "s1", "triangles"} with the part inside the line's
    section swept from -reach_m to length + reach_m, in the local frame."""
    prism = _prism(line, -reach_m, line.length + reach_m)
    box = prism.GetBoundingBox()
    t = box.Transform
    a, b = t.OfPoint(box.Min), t.OfPoint(box.Max)
    outline = DB.Outline(DB.XYZ(min(a.X, b.X), min(a.Y, b.Y), min(a.Z, b.Z)),
                         DB.XYZ(max(a.X, b.X), max(a.Y, b.Y), max(a.Z, b.Z)))
    own = set(element_id_value(e.Id) for e in line.elements)
    ys = [p[1] for p in line.section.polygon_m]
    depth = max(ys) - min(ys)
    bottom = min(ys)
    options = DB.Options()
    found = []
    for bic, label in SUPPORT_CATEGORIES:
        collector = (DB.FilteredElementCollector(doc).OfCategory(bic).WhereElementIsNotElementType()
                     .WherePasses(DB.BoundingBoxIntersectsFilter(outline)))
        for element in collector:
            if element_id_value(element.Id) in own:
                continue
            if label == u"VIGA":
                curve = getattr(getattr(element, "Location", None), "Curve", None)
                if isinstance(curve, DB.Line):
                    ed = (curve.GetEndPoint(1) - curve.GetEndPoint(0)).Normalize()
                    if abs(abs(ed.DotProduct(line.direction)) - 1.0) < 1e-3:
                        continue  # parallel: a continuation, not a support
            triangles = []
            for solid in rc._solids(element.get_Geometry(options)):
                try:
                    part = DB.BooleanOperationsUtils.ExecuteBooleanOperation(
                        solid, prism, DB.BooleanOperationsType.Intersect)
                except Exception:
                    continue
                if part is None or part.Volume < 1e-6:
                    continue
                for face in part.Faces:
                    mesh = face.Triangulate()
                    for i in range(mesh.NumTriangles):
                        tri = mesh.get_Triangle(i)
                        triangles.append(tuple(line.local(tri.get_Vertex(k)) for k in range(3)))
            if not triangles:
                continue
            pts = [p for tri in triangles for p in tri]
            y0, y1 = min(p[1] for p in pts), max(p[1] for p in pts)
            if y1 - y0 < 0.5 * depth:
                continue  # only touching it (a wall under it, a slab)
            if label == u"VIGA" and y0 > bottom + 0.01:
                continue  # a shallower beam framing into this one
            found.append({"label": label, "s0": min(p[2] for p in pts), "s1": max(p[2] for p in pts),
                          "triangles": triangles, "id": element_id_value(element.Id)})
    found.sort(key=lambda f: f["s0"])
    return found


def spans_and_ends(line, supports):
    """(clear spans [(a, b)], start face, end face): the spans between the
    support faces inside the line; the faces are where the bars anchor -
    the far face of the support at each end, else the line end."""
    spans = spec.clear_spans((0.0, line.length), [(f["s0"], f["s1"]) for f in supports])
    start_face = min([0.0] + [f["s0"] for f in supports if f["s0"] < 0.0 and f["s1"] >= -0.05])
    end_face = max([line.length] + [f["s1"] for f in supports
                                    if f["s1"] > line.length and f["s0"] <= line.length + 0.05])
    return spans, start_face, end_face


def delete_generated(doc, line):
    for element in line.elements:
        rc.delete_generated(doc, element)


def _counterclockwise(points):
    area = 0.0
    for k in range(len(points)):
        (ax, ay), (bx, by) = points[k], points[(k + 1) % len(points)]
        area += ax * by - bx * ay
    return points if area > 0 else list(reversed(points))


def _share(line, created, rebar, key, kind, s_start, s_end):
    """List the bar in each element it runs along, with its share."""
    length = max(s_end - s_start, 1e-9)
    for lo, hi, element in line.element_bounds():
        inside = min(s_end, hi) - max(s_start, lo)
        if inside > 1e-6:
            created[element_id_value(element.Id)].append((rebar, key, kind, inside / length))


def generate_line(doc, line, beam_spec, anchor, bar_types, hooks, shapes=None, splice=None, legs=None,
                  supports=None):
    """Create a beam line's rebar (inside an active Transaction): per clear
    span the stirrups and ties of the drawing with the type's distribution
    from each support face; the longitudinal bars of the drawing along the
    whole line, from face to face of the end supports less the cover,
    with 90-degree hooks (`anchor` ANCHOR_HOOK, leg lengths `legs`
    {key: m}) or straight, cut past splice["max"] with splice["laps"]
    [key] laps (top bars in a span's central third, bottom bars in its
    end thirds outside the confinement zone).
    Returns ({element id: [(rebar, key, kind, share)]}, warnings)."""
    design = beam_spec.design
    supports = line_supports(doc, line) if supports is None else supports
    spans, start_face, end_face = spans_and_ends(line, supports)
    delete_generated(doc, line)
    created = dict((element_id_value(e.Id), []) for e in line.elements)
    warnings = []
    if not spans:
        raise spec.SpecError(u"la viga no tiene luz libre entre apoyos")
    axis = line.direction

    # stirrups and ties, per clear span
    groups = {}
    shapes_s = spec.design_shapes(design, "stirrups")
    for (drawn_kind, poly, wrap, is_open), shape_name in zip(design["stirrups"], shapes_s):
        kind, family = beam_spec.family_of(drawn_kind)
        entry = groups.setdefault(kind, (family, [], []))
        centerline = spec.stirrup_centerline(poly, design["bars"], family.key, wrap, is_open)
        entry[1].append((spec.clean_polyline(centerline, closed=not is_open), is_open, shape_name))
    for (drawn_kind, a, b), shape_name in zip(design["ties"], spec.design_shapes(design, "ties")):
        kind, family = beam_spec.family_of(drawn_kind)
        entry = groups.setdefault(kind, (family, [], []))
        entry[2].append((a, b, shape_name))  # its line with the bar type (rc.tie_ends)
    flat = [(kind, i, is_tie) for kind, (family, loops, ties) in groups.items()
            for is_tie, items in ((False, loops), (True, ties)) for i in range(len(items))]
    lifts = dict(zip(flat, spec.stack_lifts([
        (kind, spec.BAR_DIAMETERS_MM[groups[kind][0].key] / 1000.0, is_tie) for kind, i, is_tie in flat])))

    for kind, (family, loops, ties) in groups.items():
        bar_type = (rc.named_bar_type(doc, family.type_name, family.key)
                    or rc._bar_type(bar_types, family.key, BEAM_MARK))
        hook = hooks.get(family.key)
        for a, b in spans:
            host = line.element_at((a + b) / 2.0)
            for start, n, spacing, _, side in spec.stirrup_sets(b - a, family.zones, family.rest):
                # On a haunch every stirrup has its own height: one by one
                # (a rebar set holds identical bars), each fitted there.
                runs = ([(start + k * spacing, 1) for k in range(n)] if line.variable else [(start, n)])
                for offset, count in runs:
                    for index, (pts, is_open, shape_name) in enumerate(loops):
                        s = a + offset + side * lifts[(kind, index, False)]
                        fitted = line.polygon_at(pts, s) if line.variable else pts
                        if is_open:
                            world = [line.point(x, y, s) for x, y in fitted]
                            curves = [DB.Line.CreateBound(world[k], world[k + 1]) for k in range(len(world) - 1)]
                            rebar = rc._create_stirrup(
                                doc, shapes, shape_name, host, bar_type, None, hooks, family.key,
                                List[DB.Curve](curves), RebarHookOrientation.Left, RebarHookOrientation.Left, axis)
                        else:
                            world = [line.point(x, y, s) for x, y in _counterclockwise(fitted)]
                            curves = [DB.Line.CreateBound(world[k], world[(k + 1) % len(world)])
                                      for k in range(len(world))]
                            rebar = rc._create_stirrup(
                                doc, shapes, shape_name, host, bar_type, hook, hooks, family.key,
                                List[DB.Curve](curves), RebarHookOrientation.Left, RebarHookOrientation.Left, axis)
                        rc._set(rebar, count, spacing / FT)
                        rc._tag(rebar, host)
                        created[element_id_value(host.Id)].append((rebar, family.key, kind, 1.0))
                    for index, (ta, tb, shape_name) in enumerate(ties):
                        s = a + offset + side * lifts[(kind, index, True)]
                        tie_type = rc.tie_bar_type(doc, bar_type, ta, tb, design["bars"], shape_name)
                        ea, eb = rc.tie_ends(ta, tb, design["bars"], family.key, shape_name, tie_type)
                        (pa, pb) = line.polygon_at([ea, eb], s) if line.variable else (ea, eb)
                        curve = DB.Line.CreateBound(line.point(pa[0], pa[1], s), line.point(pb[0], pb[1], s))
                        angle = 180.0 if shape_name in spec.TIE_STYLES else 135.0
                        rebar = rc._create_tie(doc, shapes, shape_name, host, tie_type, hook, hooks,
                                               family.key, curve, axis, leg=rc.tie_leg_m(design, family.key, angle))
                        rc._set(rebar, count, spacing / FT)
                        rc._tag(rebar, host)
                        created[element_id_value(host.Id)].append((rebar, family.key, kind, 1.0))

    # longitudinal bars along the whole line
    cover = beam_spec.cover_m
    bar_start, bar_end = start_face + cover, end_face - cover
    confinement = spec.confinement_length(beam_spec.edge.zones)
    for x, y, key in design["bars"]:
        d = spec.BAR_DIAMETERS_MM[key] / 1000.0
        top = y > 0
        leg = 0.0
        if anchor == ANCHOR_HOOK:
            leg = (legs or {}).get(key)
            if not leg:
                raise spec.SpecError(u"falta la longitud del gancho de 90 de las barras de {}".format(key))
        lap = 0.0
        pieces = [(bar_start, bar_end)]
        # the hook legs count in a bar's length (a 9 m bar holds them)
        if splice is not None and bar_end - bar_start + 2 * leg > splice["max"] + 1e-6:
            lap = splice["laps"].get(key)
            if not lap:  # this diameter isn't spliced: one bar, whatever its length
                warnings.append(u"Barras {} de {}: {:.2f} m sin empalme (diametro no marcado para empalmar)"
                                .format(u"superiores" if top else u"inferiores", key, bar_end - bar_start + 2 * leg))
                lap = 0.0
        if lap:
            zones = spec.beam_lap_zones(spans, top, confinement)
            where = u"un tercio central de tramo" if top else u"un tercio extremo de tramo (fuera del confinamiento)"
            pieces, found = spec.lap_pieces(bar_start, bar_end, zones, lap, splice["max"] - leg, where)
            warnings += [u"Barras {} de {}: {}".format(u"superiores" if top else u"inferiores", key, w)
                         for w in found]
        for index, (s0, s1) in enumerate(pieces):
            last = index == len(pieces) - 1
            path = spec.beam_bar_points(x, y, d, s0, s1, lap, not last,
                                        leg if index == 0 else 0.0, leg if last else 0.0)
            if line.variable:  # along the sloped face of a haunch
                path = spec.follow_profile(path, line.shift_at(y), line.breaks)
            world = [line.point(px, py, ps) for px, py, ps in path]
            curves = [DB.Line.CreateBound(world[k], world[k + 1]) for k in range(len(world) - 1)]
            host = line.element_at((s0 + s1) / 2.0)
            rebar = Rebar.CreateFromCurves(
                doc, RebarStyle.Standard, beam_spec.bar_type(doc, bar_types, key, BEAM_MARK), None, None, host,
                line.section.across, List[DB.Curve](curves),
                RebarHookOrientation.Right, RebarHookOrientation.Right, True, True,
            )
            rc._tag(rebar, host)
            _share(line, created, rebar, key, rc.LONGITUDINAL, s0, s1)
    return created, [w for i, w in enumerate(warnings) if w not in warnings[:i]]  # once each


def record_weight(element, created):
    """Steel weight of the bars listed for this element (after a
    Regenerate; a bar running several elements with its share), written
    to EA_Peso_Acero_kg. Returns ({kind: kg}, number of bars hosted)."""
    kg = {rc.LONGITUDINAL: 0.0, rc.EDGE: 0.0, rc.CONFINEMENT: 0.0}
    bars = 0
    for rebar, key, kind, share in created:
        kg[kind] += rebar.TotalLength * FT * spec.bar_weight_kg_per_m(key) * share
        if rebar.GetHostId() == element.Id:
            bars += rebar.Quantity
    p = element.LookupParameter(rc.WEIGHT_PARAM)
    if p is not None and not p.IsReadOnly:
        p.Set(round(sum(kg.values()), 2))
    return kg, bars
