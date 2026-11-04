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
import beam_extras as bx
from utils import element_id_value

FT = rc.FT
ANCHOR_PARAM = "EA_Anclaje"
LINE_PARAM = "EA_Viga_Linea"  # per beam line (on its elements): spans' own stirrups, extra bars, cuts (bx)
ANCHOR_HOOK = u"Gancho 90"
ANCHOR_STRAIGHT = u"Recto"
ANCHORS = (ANCHOR_HOOK, ANCHOR_STRAIGHT)
DEFAULT_COVER_CM = 4.0
LINE_GAP_M = 1.2  # elements of one type and axis this close form one beam
SUPPORT_REACH_M = 1.0  # supports looked for this far beyond the line ends
LINE_TOL_M = 0.02
LINE_LEVEL_TOL_M = 0.05  # level beams on one line: their tops this close (else another beam)
BEAM_MARK = u"VIGA"  # bar types named for beams are preferred

FRAMING = [DB.BuiltInCategory.OST_StructuralFraming]


def ensure_parameters(doc):
    """The column parameters (bar types, origin tags...) plus the same type
    parameters, the anchorage and the weight on Structural Framing. Inside
    an active Transaction."""
    warnings = rc.ensure_parameters(doc)
    specs = [(name, True, FRAMING, False) for name in rc.TYPE_PARAMS + (ANCHOR_PARAM,)]
    specs.append((rc.WEIGHT_PARAM, False, FRAMING, True))
    specs.append((LINE_PARAM, True, FRAMING, True))
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
        if abs(d.Z) > 0.97:
            raise spec.SpecError(u"la viga {} esta vertical".format(element_id_value(beam.Id)))
        across = DB.XYZ.BasisZ.CrossProduct(d).Normalize()
        # "up" of the section: vertical for a level beam, square to the
        # axis (in its vertical plane) for a sloped one
        up = d.CrossProduct(across).Normalize()
        best = None
        solids = list(rc._solids(beam.get_Geometry(DB.Options())))
        for solid in solids:
            for face in solid.Faces:
                if isinstance(face, DB.PlanarFace) and abs(abs(face.FaceNormal.DotProduct(d)) - 1.0) < 1e-3:
                    loop = max(face.EdgeLoops, key=lambda lp: lp.Size)
                    pts = [edge.AsCurve().GetEndPoint(0) for edge in loop]
                    poly = [((q - p0).DotProduct(across), (q - p0).DotProduct(up)) for q in pts]
                    if best is None or face.Area > best[0]:
                        best = (face.Area, poly)
        if best is None and abs(d.Z) > 1e-3:
            # a sloped beam cut plumb at its ends: its section's box
            pts = [edge.AsCurve().GetEndPoint(k) for solid in solids for edge in solid.Edges for k in (0, 1)]
            if pts:
                xs = [(q - p0).DotProduct(across) for q in pts]
                ys = [(q - p0).DotProduct(up) for q in pts]
                best = (0.0, [(min(xs), min(ys)), (max(xs), min(ys)), (max(xs), max(ys)), (min(xs), max(ys))])
        if best is None:
            raise spec.SpecError(u"no se encontro la seccion de la viga {}".format(element_id_value(beam.Id)))
        self.up = up
        self.sloped = abs(d.Z) > 1e-3
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
            sec.center = (-sec.center[0], sec.center[1])
            sec.polygon_m = [(-x, y) for x, y in sec.polygon_m]
            sec.up = d.CrossProduct(sec.across).Normalize()
        self.sloped = sec.sloped
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
        ends = (min(by_s), max(by_s)) if by_s else ()
        depth = self.ref_top - self.ref_bottom
        for s in sorted(by_s):
            lo, hi = min(by_s[s]), max(by_s[s])
            if s in ends and 0.02 < hi - lo < 0.5 * depth:
                continue  # a sliver left at the line's end by a join cut, not a haunch
            if s in ends and len(by_s) > 2 and hi - lo <= 0.02 and depth > 0.05:
                continue  # a lone vertex at the very end: same
            if hi - lo > 0.02:
                bottom.append((s, lo))
                top.append((s, hi))
            elif abs(lo - self.ref_top) < abs(lo - self.ref_bottom):
                top.append((s, hi))
            else:
                bottom.append((s, lo))
        self.bottom_points = bottom or [(0.0, self.ref_bottom)]
        self.top_points = top or [(0.0, self.ref_top)]
        # a haunch changes the depth along a stretch (two points or more of a
        # face, by more than 3 cm); one point off, or a notch of a cm or two,
        # is a join's cut - the beam keeps its stirrups in sets
        self.variable = len([1 for _, y in self.bottom_points if abs(y - self.ref_bottom) > 0.03]) >= 2 or \
            len([1 for _, y in self.top_points if abs(y - self.ref_top) > 0.03]) >= 2
        self.breaks = sorted(set(s for s, _ in self.bottom_points + self.top_points))
        if self.sloped:  # its plumb-cut ends aren't a haunch: one section all along
            self.bottom_points, self.top_points = [(0.0, self.ref_bottom)], [(0.0, self.ref_top)]
            self.variable, self.breaks = False, [0.0]

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
                + sec.up * (sec.center[1] + y / FT))

    def local(self, p):
        """(x, y, s) in m of a world point."""
        q = p - self.origin
        sec = self.section
        return ((q.DotProduct(sec.across) - sec.center[0]) * FT,
                (q.DotProduct(sec.up) - sec.center[1]) * FT,
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


def _concrete_top(element):
    """The top (ft) of an element's concrete - its solids', not its box's (a
    beam moved down by its z offset keeps its location line in the box)."""
    top = None
    try:
        for solid in rc._solids(element.get_Geometry(DB.Options())):
            if solid.Volume < 1e-9:
                continue
            z = solid.GetBoundingBox().Transform.OfPoint(solid.GetBoundingBox().Max).Z
            top = z if top is None else max(top, z)
    except Exception:
        pass
    if top is None:
        bb = element.get_BoundingBox(None)
        top = bb.Max.Z if bb is not None else None
    return top


def beam_lines(beams, gap_m=LINE_GAP_M, tol_m=LINE_TOL_M):
    """Group beam elements into beam lines: same type, straight (level or sloped),
    collinear (within tol_m) and one after the other with gaps up to
    gap_m. Elements that aren't straight stay out."""
    items = []
    for b in beams:
        loc = getattr(b, "Location", None)
        curve = getattr(loc, "Curve", None)
        if not isinstance(curve, DB.Line):
            continue
        p0, p1 = curve.GetEndPoint(0), curve.GetEndPoint(1)
        items.append((b, p0, p1, _concrete_top(b)))
    lines = []  # [(type id, origin, direction, [(t0, t1, element)])]
    tops = {}  # line index -> top of its first element (a level line)
    for b, p0, p1, top in items:
        d = (p1 - p0).Normalize()
        placed = False
        for k, line in enumerate(lines):
            tid, o, ld, members = line
            if tid != b.GetTypeId() or abs(abs(ld.DotProduct(d)) - 1.0) > 1e-4:
                continue
            off = p0 - o
            perp = off - ld * off.DotProduct(ld)
            if perp.GetLength() * FT > tol_m:
                continue
            # a level beam moved up / down by its own z offset (its location
            # line on the others', its concrete elsewhere) is another beam
            if (abs(d.Z) < 1e-3 and top is not None and tops.get(k) is not None
                    and abs(top - tops[k]) * FT > LINE_LEVEL_TOL_M):
                continue
            ts = sorted(((q - o).DotProduct(ld) for q in (p0, p1)))
            members.append((ts[0], ts[1], b))
            placed = True
            break
        if not placed:
            tops[len(lines)] = top
            lines.append((b.GetTypeId(), p0, d, [(0.0, (p1 - p0).GetLength(), b)]))
    result = []
    for tid, o, ld, members in lines:
        members.sort(key=lambda m: m[0])
        # a vigueta stops at the columneta beside it: pieces with a column
        # between are separate viguetas (only touching ones go on)
        gap = VIGUETA_GAP_M if _is_vigueta_type(members[0][2]) else gap_m
        group = [members[0]]
        for m in members[1:]:
            if (m[0] - group[-1][1]) * FT <= gap:
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
VIGUETA_WORDS = (u"VIGUETA",)


VIGUETA_GAP_M = 0.02  # vigueta pieces this close are one; farther, a column between: two


def _is_vigueta_type(element):
    try:
        symbol = element.Document.GetElement(element.GetTypeId())
        names = u"{} {}".format(rc.element_name(symbol), getattr(symbol, "FamilyName", u"") or u"").upper()
    except Exception:
        return False
    return any(w in names for w in VIGUETA_WORDS)


def is_vigueta(line):
    """A vigueta (a masonry wall's confining beam): its family or type named
    so. It rests on the columnetas at its sides only - the wall it sits on
    is no support - and stops at them (2026-11-03, with the user)."""
    return _is_vigueta_type(line.elements[0])


def main_bar_run(line, line_settings, design, y, key, cover, start_face, end_face, anchor, legs, splice,
                 continue_top=None):
    """(leg0, leg1, s_start, s_end) of a main bar at height y of the line
    (as generated, and as the windows draw it): its hooks - the beam's own,
    the table's, 12 db; none where it runs on into the next beam line (a
    lap: continue_top) -, cut to the depth there is; a bottom bar's ends set
    in beside the top bars' hooks; the hand edits of its face (longer /
    shorter, its legs), a longer bar stopping at the far face of the
    concrete less the cover; a leg too short to bend raised to the least."""
    d = spec.BAR_DIAMETERS_MM[key] / 1000.0
    top = y > 0
    hooked = anchor == ANCHOR_HOOK
    leg0 = bx.hook_leg(line_settings, 0, key, legs, spec.BAR_DIAMETERS_MM, hooked)
    leg1 = bx.hook_leg(line_settings, 1, key, legs, spec.BAR_DIAMETERS_MM, hooked)
    start_run = end_run = 0.0
    runs = (continue_top or {}).get("top" if top else "bot") or [False, False]
    if any(runs):
        run_on = (splice["laps"].get(key) if splice else None) or spec.e060_lap_cm(key) / 100.0
        reach = continue_top.get("reach") or [run_on, run_on]
        if runs[0]:
            leg0, start_run = 0.0, min(run_on, reach[0])
        if runs[1]:
            leg1, end_run = 0.0, min(run_on, reach[1])
    # a bottom bar's hook goes inside the top bars' one, side by side (not
    # on it, as built): its end set back the two half diameters + 5 mm
    b0, b1 = start_face + cover, end_face - cover
    if not top:
        top_d = max([spec.BAR_DIAMETERS_MM[k] / 1000.0 for _, yy, k in design["bars"] if yy > 0] or [0.0])
        gap = (top_d + d) / 2.0 + 0.005
        b0 += gap if leg0 > 0 else 0.0
        b1 -= gap if leg1 > 0 else 0.0
    s0, s1 = b0 - start_run, b1 + end_run  # run on into the next beam line (a lap)
    # the hand edits of this face (clicked in the window): longer / shorter, its
    # own legs; made longer, a bar stops at the far face of the concrete less
    # the cover: the beam it runs into (continue_top's reach), else its support
    reach = (continue_top or {}).get("reach") or [0.0, 0.0]
    lo = start_face - (reach[0] + 0.05) + cover if reach[0] > 0 else start_face + cover
    hi = end_face + (reach[1] + 0.05) - cover if reach[1] > 0 else end_face - cover
    leg0, leg1, s0, s1 = bx.apply_main_edit(line_settings, bx.TOP if top else bx.BOTTOM, leg0, leg1, s0, s1, lo, hi)
    # an automatic leg never leaves the concrete: in a shallow beam (a chata,
    # a vigueta) it is cut to the depth there is, less the far cover. One typed
    # by hand ("Gancho" of the beam, "Doblez" of the face) is kept as typed -
    # the user's decision (2026-11-03)
    room = (line.ref_top - cover - d / 2.0 - y) if not top else (y - line.ref_bottom - cover - d / 2.0)
    own = ((line_settings or {}).get("hooks") or {})
    edit = bx.main_edit(line_settings, bx.TOP if top else bx.BOTTOM)
    typed = [own.get("0") is not None or edit.get("h0") is not None,
             own.get("1") is not None or edit.get("h1") is not None]
    if room > 0:
        leg0 = min(leg0, max(spec.MIN_LEG_M, room)) if (leg0 > 0 and not typed[0]) else leg0
        leg1 = min(leg1, max(spec.MIN_LEG_M, room)) if (leg1 > 0 and not typed[1]) else leg1
    # a leg too short to bend (a few mm) makes no rebar shape: the least one
    leg0 = max(leg0, spec.MIN_LEG_M) if leg0 > 1e-6 else 0.0
    leg1 = max(leg1, spec.MIN_LEG_M) if leg1 > 1e-6 else 0.0
    return leg0, leg1, s0, s1


def mixed_continuations(lines, gap_m=0.6, tol_m=0.03):
    """Beam lines of different types on one axis meeting at a support (a
    deep beam and a flat one): {id(line): {"top": [start, end], "bot":
    [start, end]}} True where the bars of that face run on into the next
    line - the face the two share (same top, or same bottom: flat beams
    hung level with the deep one's soffit) - lapped past the support
    instead of a hook."""
    out = dict((id(l), {"top": [False, False], "bot": [False, False], "reach": [0.0, 0.0]}) for l in lines)
    for i, a in enumerate(lines):
        for b in lines[i + 1:]:
            if a.type_id == b.type_id or getattr(a, "sloped", False) or getattr(b, "sloped", False):
                continue
            if abs(abs(a.direction.DotProduct(b.direction)) - 1.0) > 1e-3:
                continue
            ca = [a.point(0, 0, 0.0), a.point(0, 0, a.length)]
            cb = [b.point(0, 0, 0.0), b.point(0, 0, b.length)]
            off = cb[0] - ca[0]
            perp = off - a.direction * off.DotProduct(a.direction)
            if DB.XYZ(perp.X, perp.Y, 0).GetLength() * FT > tol_m:
                continue  # not on the same axis
            dist, ka, kb = min(((DB.XYZ(ca[i0].X - cb[j0].X, ca[i0].Y - cb[j0].Y, 0).GetLength() * FT, i0, j0)
                                for i0 in (0, 1) for j0 in (0, 1)), key=lambda r: r[0])
            if dist > gap_m:
                continue
            sa, sb = (0.0 if ka == 0 else a.length), (0.0 if kb == 0 else b.length)
            # how far a bar may run on: to the far end of the next line, less the cover
            out[id(a)]["reach"][ka] = b.length - 0.05  # measured from the far face of the support
            out[id(b)]["reach"][kb] = a.length - 0.05
            for face, ya, yb in (("top", a.ref_top, b.ref_top), ("bot", a.ref_bottom, b.ref_bottom)):
                if abs(a.point(0, ya, sa).Z - b.point(0, yb, sb).Z) * FT <= tol_m:
                    out[id(a)][face][ka] = True
                    out[id(b)][face][kb] = True
    return out

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
    vigueta = is_vigueta(line)
    for bic, label in SUPPORT_CATEGORIES:
        if vigueta and label == u"MURO":
            continue  # a vigueta: the wall under it is no support
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
            if label == u"COLUMNA" and getattr(line, "sloped", False):
                # a sloped beam (a gable's) resting on a column that stops at
                # its bottom face (under the ridge): a support all the same,
                # from where the column meets the beam's bottom
                touch = _column_under(element, line, bottom, options)
                if touch is not None and (not triangles or max(p[1] for tri in triangles for p in tri)
                                          - min(p[1] for tri in triangles for p in tri) < 0.5 * depth):
                    found.append({"label": label, "s0": touch[0], "s1": touch[1], "under": True,
                                  "triangles": triangles, "id": element_id_value(element.Id)})
                    continue
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
    if vigueta:  # only what it touches: the columnetas at its sides, not a column past them
        found = [f for f in found if f["s1"] >= -0.05 and f["s0"] <= line.length + 0.05]
    return found


def _column_under(column, line, bottom, options, tol=0.03):
    """(s0, s1) along `line` of a column whose top meets the line's bottom
    face (its vertices on that face, under the beam's width), from where it
    touches to its far side; None when it does not reach the beam."""
    xs = [p[0] for p in line.section.polygon_m]
    half = (max(xs) - min(xs)) / 2.0 + 0.05
    pts = []
    for solid in rc._solids(column.get_Geometry(options)):
        for edge in solid.Edges:
            curve = edge.AsCurve()
            pts += [line.local(curve.GetEndPoint(0)), line.local(curve.GetEndPoint(1))]
    touching = [p for p in pts if abs(p[1] - bottom) <= tol and abs(p[0]) <= half]
    if not touching:
        return None
    top = max([p[1] for p in pts])
    near = [p for p in pts if top - p[1] <= 0.6 and abs(p[0]) <= half]  # its head, not its foot
    s0 = min([p[2] for p in touching] + [p[2] for p in near])
    s1 = max([p[2] for p in touching] + [p[2] for p in near])
    if s1 - s0 < 0.05:
        return None
    return s0, s1


def spans_and_ends(line, supports):
    """(clear spans [(a, b)], start face, end face): the spans between the
    support faces inside the line; the faces are where the bars anchor -
    the far face of the support at each end, else the line end."""
    spans = spec.clear_spans((0.0, line.length), [(f["s0"], f["s1"]) for f in supports])
    # only where the line has concrete: a gap between its elements (a line
    # running on past a column with no beam there) gets no stirrup
    ranges = getattr(line, "ranges", None)
    if ranges:
        spans = [(max(a, r0), min(b, r1)) for a, b in spans for r0, r1 in ranges
                 if min(b, r1) - max(a, r0) > 0.05]
    start_face = min([0.0] + [f["s0"] for f in supports if f["s0"] < 0.0 and f["s1"] >= -0.05])
    end_face = max([line.length] + [f["s1"] for f in supports
                                    if f["s1"] > line.length and f["s0"] <= line.length + 0.05])
    return spans, start_face, end_face


def read_line_settings(line):
    """The beam line's own settings (bx): from its first element that has them."""
    for e in line.elements:
        p = e.LookupParameter(LINE_PARAM)
        if p is not None and (p.AsString() or u"").strip():
            return bx.read(p.AsString())
    return bx.default_settings()


def write_line_settings(line, settings):
    text = bx.write(settings)
    for e in line.elements:
        p = e.LookupParameter(LINE_PARAM)
        if p is not None and not p.IsReadOnly:
            p.Set(text)


class _SpanFamily(object):
    """A stirrup family with a span's own distribution."""

    def __init__(self, family, text):
        self.key = family.key
        self.type_name = family.type_name
        self.zones, self.rest = spec.parse_distribution(text)


def delete_generated(doc, line):
    for element in line.elements:
        rc.delete_generated(doc, element)


def _counterclockwise(points):
    area = 0.0
    for k in range(len(points)):
        (ax, ay), (bx, by) = points[k], points[(k + 1) % len(points)]
        area += ax * by - bx * ay
    return points if area > 0 else points[:1] + points[:0:-1]  # the first corner kept first: the hooks


def seat_in_bends(bars, loops, ds, bend_d):
    """Corner bars seated in the stirrups' bends: a stirrup is bent round
    (inner diameter bend_d), so a bar drawn in its sharp corner would overlap
    the bend; it moves in along the corner's bisector until it touches the
    bend's inner face (as built). `loops`: the stirrups' centerlines (m);
    returns the bars [(x, y, key)]."""
    import math as _m
    r_in = bend_d / 2.0
    R = r_in + ds / 2.0  # the bend's centerline radius
    out = []
    for x, y, key in bars:
        rb_ = spec.BAR_DIAMETERS_MM[key] / 2000.0
        nx, ny = x, y
        for pts in loops:
            n = len(pts)
            for k in range(n):
                c, a, b = pts[k], pts[k - 1], pts[(k + 1) % n]
                ua = (a[0] - c[0], a[1] - c[1])
                ub = (b[0] - c[0], b[1] - c[1])
                la, lb = _m.hypot(*ua), _m.hypot(*ub)
                if la < 1e-9 or lb < 1e-9:
                    continue
                ua, ub = (ua[0] / la, ua[1] / la), (ub[0] / lb, ub[1] / lb)
                if abs(ua[0] * ub[0] + ua[1] * ub[1]) > 0.2:
                    continue  # a right-angled corner only
                # the bar's offsets from the corner along its two sides
                ox = (x - c[0]) * ua[0] + (y - c[1]) * ua[1]
                oy = (x - c[0]) * ub[0] + (y - c[1]) * ub[1]
                sharp = ds / 2.0 + rb_  # where the sharp corner puts it
                if not (0 < ox < 2.5 * sharp and 0 < oy < 2.5 * sharp):
                    continue
                need = R - (r_in - rb_) / _m.sqrt(2.0)  # tangent to the bend's inner face
                if need > ox + 1e-5 or need > oy + 1e-5:
                    gx, gy = max(ox, need), max(oy, need)
                    nx = c[0] + ua[0] * gx + ub[0] * gy
                    ny = c[1] + ua[1] * gx + ub[1] * gy
        out.append((nx, ny, key))
    return out


def _share(line, created, rebar, key, kind, s_start, s_end):
    """List the bar in each element it runs along, with its share."""
    length = max(s_end - s_start, 1e-9)
    for lo, hi, element in line.element_bounds():
        inside = min(s_end, hi) - max(s_start, lo)
        if inside > 1e-6:
            created[element_id_value(element.Id)].append((rebar, key, kind, inside / length))


def generate_line(doc, line, beam_spec, anchor, bar_types, hooks, shapes=None, splice=None, legs=None,
                  supports=None, line_settings=None, longitudinal=True, continue_top=None):
    # continue_top {"top"/"bot": [start, end]} (mixed_continuations): that face's bars run
    # on there into the next beam line, a lap past the support, no hook
    """Create a beam line's rebar (inside an active Transaction): per clear
    span the stirrups and ties of the drawing with the type's distribution
    from each support face; the longitudinal bars of the drawing along the
    whole line, from face to face of the end supports less the cover,
    with 90-degree hooks (`anchor` ANCHOR_HOOK, leg lengths `legs`
    {key: m}) or straight, cut past splice["max"] with splice["laps"]
    [key] laps (top bars in a span's central third, bottom bars in its
    end thirds outside the confinement zone).
    `line_settings` (bx, optional): each span's own stirrup distribution
    and the extra bars (bastones, balancines); None: the type's only.
    `longitudinal` False: the stirrups only (a sloped frame's bars are
    bent across its ridge, rebar_sloped).
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
    # a cantilever (a span with a free end) has its stirrups from the support only
    free = spec.free_ends(spans, [(f["s0"], f["s1"]) for f in supports])

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
        for span_index, (a, b) in enumerate(spans):
            host = line.element_at((a + b) / 2.0)
            own = bx.span_text(line_settings, span_index, u"edge" if kind == rc.EDGE else u"conf") \
                if line_settings else u""
            span_family = _SpanFamily(family, own) if own else family
            for start, n, spacing, _, side in spec.span_stirrup_sets(b - a, span_family.zones, span_family.rest,
                                                                     free.get(span_index)):
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

    if not longitudinal:
        return created, warnings
    # longitudinal bars along the whole line
    cover = beam_spec.cover_m
    bar_start, bar_end = start_face + cover, end_face - cover
    confinement = spec.confinement_length(beam_spec.edge.zones)
    # the corner bars seated in the stirrups' bends (no overlap with them)
    seated = bx.kept_bars(line_settings, design["bars"])  # less the ones deleted for this beam
    for kind, (family, loops, ties) in groups.items():
        closed = [pts for pts, is_open, _ in loops if not is_open]
        if not closed:
            continue
        st_type = (rc.named_bar_type(doc, family.type_name, family.key)
                   or rc._bar_type(bar_types, family.key, BEAM_MARK))
        ds_m = spec.BAR_DIAMETERS_MM[family.key] / 1000.0
        bend = getattr(st_type, "StirrupTieBendDiameter", 0.0) * FT or 4.0 * ds_m
        seated = seat_in_bends(seated, closed, ds_m, bend)
    for x, y, key in seated:
        d = spec.BAR_DIAMETERS_MM[key] / 1000.0
        top = y > 0
        leg0, leg1, bar_start, bar_end = main_bar_run(line, line_settings, design, y, key, cover, start_face,
                                                      end_face, anchor, legs, splice, continue_top)
        leg = max(leg0, leg1)
        lap = 0.0
        pieces = [(bar_start, bar_end)]
        # the hook legs count in a bar's length (a 9 m bar holds them)
        if splice is not None and bar_end - bar_start + leg0 + leg1 > splice["max"] + 1e-6:
            lap = splice["laps"].get(key)
            if not lap:  # this diameter isn't spliced: one bar, whatever its length
                warnings.append(u"Barras {} de {}: {:.2f} m sin empalme (diametro no marcado para empalmar)"
                                .format(u"superiores" if top else u"inferiores", key, bar_end - bar_start + leg0 + leg1))
                lap = 0.0
        if lap:
            zones = spec.beam_lap_zones(spans, top, confinement, lap)
            where = u"un tercio central de tramo" if top else u"un cuarto extremo de tramo (desde la cara del apoyo)"
            pieces, found = spec.lap_pieces(bar_start, bar_end, zones, lap, splice["max"] - leg, where)
            warnings += [u"Barras {} de {}: {}".format(u"superiores" if top else u"inferiores", key, w)
                         for w in found]
        for index, (s0, s1) in enumerate(pieces):
            last = index == len(pieces) - 1
            path = spec.beam_bar_points(x, y, d, s0, s1, lap, not last,
                                        leg0 if index == 0 else 0.0, leg1 if last else 0.0)
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
    bar_start, bar_end = start_face + cover, end_face - cover
    # extra bars: bastones over the supports (top), balancines in the spans
    # (bottom); where one reaches a beam end it takes the main bars' anchorage
    for extra in (line_settings or {}).get("extras", []):
        key = extra["d"]
        d = spec.BAR_DIAMETERS_MM[key] / 1000.0
        s0, s1, hook0, hook1 = bx.extra_ends(extra, bar_start, bar_end)
        if s1 - s0 < 0.1:
            continue
        leg0, leg1 = bx.extra_legs(extra)  # its own hooks, drawn or typed
        for x, y in bx.placed_positions(extra, design["bars"], spec.BAR_DIAMETERS_MM):
            path = spec.beam_bar_points(x, y, d, s0, s1, 0.0, False, leg0, leg1)
            if line.variable:
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
