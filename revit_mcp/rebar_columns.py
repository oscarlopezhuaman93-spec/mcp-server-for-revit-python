# -*- coding: UTF-8 -*-
"""
Column Rebar Module for Revit MCP ("Acero": columns)

Builds real Revit Rebar in concrete columns from a configuration stored
in the column type's parameters:

- EA_Estribo_Borde_Diametro / _Distribucion   perimeter stirrup, e.g. 3/8"
                                              and 1@.05, 10@.10, rto@.20
- EA_Estribo_Conf_Diametro / _Distribucion    inner (confinement) stirrups
                                              and crossties (grapas)
- EA_Recubrimiento_cm      e.g. 4
- EA_Nucleo_cm             stirrup spacing inside the beam-column joint
                           (blank = no stirrups in the joint)
- EA_Seccion_Armado        hand-drawn section (JSON, see rebar_spec): the
                           longitudinal bars, closed stirrups and grapas

The drawing is used as drawn (any section shape: rectangle, trapezoid,
L, T...). Each drawn stirrup/crosstie carries the family chosen when it
was sketched: edge ("borde") or confinement. A stirrup can also be open
(U-shaped, ends not joined, no hooks), as confinement stirrups often are.

Longitudinal bars run the column's full height (level to level; laps and
anchorages are not modeled). Stirrups and ties are laid out from both ends
over the clear height, up to the underside of the deepest beam/slab
framing into the column top; with EA_Nucleo_cm they also continue through
that joint. Each column gets its steel weight in EA_Peso_Acero_kg; every
bar carries its column's id in EA_Origen_Id so a new run replaces it.

Runs inside Revit's IronPython engine - no f-strings.
"""
import json
import math
import re

from pyrevit import DB
from Autodesk.Revit.DB.Structure import (
    MultiplanarOption,
    Rebar,
    RebarBarType,
    RebarHookOrientation,
    RebarHookType,
    RebarHostData,
    RebarShape,
    RebarStyle,
)
from System.Collections.Generic import List

import formwork_params as fw_params
import rebar_spec as spec
from utils import element_id_value

FT = 0.3048  # m per ft
GROUP_NAME = "Acero"
TYPE_PARAMS = (
    "EA_Estribo_Borde_Diametro",
    "EA_Estribo_Borde_Distribucion",
    "EA_Estribo_Conf_Diametro",
    "EA_Estribo_Conf_Distribucion",
    "EA_Recubrimiento_cm",
    "EA_Nucleo_cm",
    "EA_Seccion_Armado",
    "EA_Estribo_Borde_Tipo",  # Revit bar type name; blank = the usual pick
    "EA_Estribo_Conf_Tipo",
    "EA_Barras_Tipo",  # JSON {diameter: Revit bar type name} of the vertical bars
    "EA_Izaje",  # JSON {"d", "dist", "h" (m), "type"}: izaje stirrups over the footing
    "EA_Barra_Extremos",  # JSON {"anchor" (m, "" = auto), "bot"/"top" {diameter: cm}, "dir_bot"/"dir_top"}
)
# Parameters of the first version: their values move to the new ones
# (None = dropped: longitudinal bars are now only drawn), then they are
# unbound.
OBSOLETE_TYPE_PARAMS = {
    "EA_Estribo_Diametro": "EA_Estribo_Borde_Diametro",
    "EA_Estribo_Distribucion": "EA_Estribo_Borde_Distribucion",
    "EA_Longitudinal": None,
}
EDGE, CONFINEMENT, LONGITUDINAL = "borde", "confinamiento", "longitudinal"
WEIGHT_PARAM = "EA_Peso_Acero_kg"
ORIGIN_PARAM = "EA_Origen_Id"
# Type parameters of the bar types (Structural Rebar), filled from the
# weight table (data/acero_pesos_por_diametro.csv).
BAR_DIAMETER_PARAM = u"DIAMETRO DE BARRA"
BAR_WEIGHT_PARAM = u"PESO NOMINAL (kg/m)"
DEFAULT_COVER_CM = 4.0  # E.060 columns
CONFINEMENT_COVER_CM = 2.5  # columnetas (confinement columns)
# Framing whose underside is at most this far below a column top frames
# into it (limits the stirrup clear height).
TOP_FRAMING_REACH_FT = 5.0


def element_name(element):
    """Name of any element or type. IronPython can't always read `.Name`
    on ElementType subclasses, so fall back to the base-class property."""
    try:
        return element.Name or u""
    except AttributeError:
        pass
    try:
        return DB.Element.Name.__get__(element) or u""
    except Exception:
        return u""


def ensure_parameters(doc):
    """Create/bind the rebar parameters. Inside an active Transaction."""
    columns = [DB.BuiltInCategory.OST_StructuralColumns]
    specs = [(name, True, columns, False) for name in TYPE_PARAMS]
    specs.append((WEIGHT_PARAM, False, columns, True))
    specs.append((ORIGIN_PARAM, True, [DB.BuiltInCategory.OST_Rebar], True))
    specs.append((BAR_DIAMETER_PARAM, True, [DB.BuiltInCategory.OST_Rebar], False))
    specs.append((BAR_WEIGHT_PARAM, False, [DB.BuiltInCategory.OST_Rebar], False))
    warnings = fw_params.ensure_parameters(doc, GROUP_NAME, specs)
    try:
        _migrate_obsolete(doc)
        fw_params.unbind_parameters(doc, list(OBSOLETE_TYPE_PARAMS))
    except Exception as e:
        warnings.append(u"No se pudieron migrar los parametros anteriores: {}".format(e))
    try:
        fill_bar_type_parameters(doc)
    except Exception as e:
        warnings.append(u"No se pudieron llenar el diametro y el peso de los tipos de barra: {}".format(e))
    return warnings


def bar_type_diameter(bar_type):
    """Diameter key of a bar type: the one in its name (Ø5/8"), else the
    one of its nominal diameter; None if neither is a known bar."""
    key = spec.diameter_from_name(element_name(bar_type))
    if key is None:
        d = getattr(bar_type, "BarNominalDiameter", None) or bar_type.BarDiameter
        key = spec.nearest_diameter(d * FT * 1000.0)
    return key


def fill_bar_type_parameters(doc):
    """Write DIAMETRO DE BARRA and PESO NOMINAL (kg/m) on every bar type
    from the weight table. Inside an active Transaction. Returns the names
    of the bar types left blank (a diameter the table doesn't know)."""
    unknown = []
    for bar_type in DB.FilteredElementCollector(doc).OfClass(RebarBarType):
        key = bar_type_diameter(bar_type)
        p_d = bar_type.LookupParameter(BAR_DIAMETER_PARAM)
        p_w = bar_type.LookupParameter(BAR_WEIGHT_PARAM)
        if key is None:
            unknown.append(element_name(bar_type))
            continue
        if p_d is not None and not p_d.IsReadOnly and p_d.AsString() != key:
            p_d.Set(key)
        weight = round(spec.bar_weight_kg_per_m(key), 3)
        if p_w is not None and not p_w.IsReadOnly and abs(p_w.AsDouble() - weight) > 1e-6:
            p_w.Set(weight)
    return unknown


def _migrate_obsolete(doc):
    types = (
        DB.FilteredElementCollector(doc)
        .OfCategory(DB.BuiltInCategory.OST_StructuralColumns)
        .WhereElementIsElementType()
    )
    for column_type in types:
        for old, new in OBSOLETE_TYPE_PARAMS.items():
            if new is None:
                continue
            p_old = column_type.LookupParameter(old)
            p_new = column_type.LookupParameter(new)
            if p_old is None or p_new is None or p_new.IsReadOnly:
                continue
            if (p_old.AsString() or u"") and not (p_new.AsString() or u""):
                p_new.Set(p_old.AsString())


def type_mark(type_name):
    """'C-2' / 'CC-1' out of a column type name ('C-2_0.23x0.60m',
    '..._CC-1_0.14x0.20m'), or None."""
    m = re.search(r"(?<![A-Z0-9])(C{1,2}-\d+)", (type_name or u"").upper())
    return m.group(1) if m else None


def default_cover_cm(type_name):
    """Cover suggested for a column type: 2.5 cm for columnetas, else 4."""
    name = (type_name or u"").upper()
    if u"COLUMNETA" in name or (type_mark(type_name) or u"").startswith(u"CC-"):
        return CONFINEMENT_COVER_CM
    return DEFAULT_COVER_CM


def read_type_config(column_type):
    """{param name: text} of a column type; blanks when unset/unbound."""
    config = {}
    for name in TYPE_PARAMS:
        p = column_type.LookupParameter(name)
        config[name] = (p.AsString() or u"") if p is not None else u""
    return config


def write_type_config(column_type, config):
    for name in TYPE_PARAMS:
        if name not in config:
            continue
        p = column_type.LookupParameter(name)
        if p is not None and not p.IsReadOnly:
            p.Set(config[name] or u"")


def _float_cm(text, label, lo, hi):
    try:
        value = float(u"{}".format(text).replace(u",", u"."))
    except ValueError:
        raise spec.SpecError(u'{} invalido: "{}" (en cm)'.format(label, text))
    if not lo <= value <= hi:
        raise spec.SpecError(u"{} fuera de rango ({} a {} cm)".format(label, lo, hi))
    return value / 100.0


def read_bar_type_names(text):
    """{diameter key: bar type name} stored in EA_Barras_Tipo; {} if blank
    or unreadable."""
    try:
        names = json.loads(text) if (text or u"").strip() else {}
    except ValueError:
        return {}
    return dict((k, v) for k, v in names.items() if v) if isinstance(names, dict) else {}


class StirrupFamily(object):
    """Diameter and distribution of one kind of stirrup."""

    def __init__(self, diameter_text, distribution_text, label, type_name=None):
        self.type_name = (type_name or u"").strip() or None  # a Revit bar type chosen
        try:
            self.key = spec.parse_diameter(diameter_text)
            self.zones, self.rest = spec.parse_distribution(distribution_text)
        except spec.SpecError as e:
            raise spec.SpecError(u"{}: {}".format(label, e))


class ColumnSpec(object):
    """Parsed configuration of one column type (raises spec.SpecError).
    `require_design`: False to check only the stirrup settings (saving a
    configuration before the section is drawn)."""

    def __init__(self, config, require_design=True):
        self.edge = StirrupFamily(
            config.get("EA_Estribo_Borde_Diametro"),
            config.get("EA_Estribo_Borde_Distribucion"),
            u"Estribo de borde",
            config.get("EA_Estribo_Borde_Tipo"),
        )
        conf_d = (config.get("EA_Estribo_Conf_Diametro") or u"").strip()
        conf_dist = (config.get("EA_Estribo_Conf_Distribucion") or u"").strip()
        self.confinement = (
            StirrupFamily(conf_d, conf_dist, u"Estribo de confinamiento", config.get("EA_Estribo_Conf_Tipo"))
            if conf_dist else None
        )
        self.cover_m = _float_cm(config.get("EA_Recubrimiento_cm") or u"", u"Recubrimiento", 1, 10)
        nucleus = (config.get("EA_Nucleo_cm") or u"").strip()
        self.joint_spacing_m = _float_cm(nucleus, u"Espaciamiento en nucleo", 3, 30) if nucleus else None
        self.design = spec.design_from_text(config.get("EA_Seccion_Armado"))
        self.bar_type_names = read_bar_type_names(config.get("EA_Barras_Tipo"))
        izaje = spec.read_json_setting(config.get("EA_Izaje"))
        self.izaje, self.izaje_h = None, 0.0
        if (izaje.get("dist") or u"").strip() and float(izaje.get("h") or 0) > 0:
            self.izaje = StirrupFamily(izaje.get("d") or u'3/8"', izaje["dist"] + u", rto@1",
                                       u"Estribo de izaje", izaje.get("type"))
            self.izaje_dist = izaje["dist"]
            self.izaje_h = float(izaje["h"])
        ends = spec.read_json_setting(config.get("EA_Barra_Extremos"))
        anchor = u"{}".format(ends.get("anchor") or u"").strip()
        self.anchor_m = float(anchor) if anchor else None  # None: to the footing's bottom
        self.leg_bottom_cm = dict((k, float(v)) for k, v in (ends.get("bot") or {}).items() if v)
        self.leg_top_cm = dict((k, float(v)) for k, v in (ends.get("top") or {}).items() if v)
        self.dir_bottom = ends.get("dir_bot") or spec.LEG_OUT
        self.dir_top = ends.get("dir_top") or spec.LEG_IN
        if not require_design:
            return
        if not (self.design and self.design["bars"]):
            raise spec.SpecError(u"dibuja las barras longitudinales de la seccion (o usa Automatico)")
        if not self.design["stirrups"]:
            raise spec.SpecError(u"dibuja al menos el estribo de borde")
        kinds = [st[0] for st in self.design["stirrups"]] + [t[0] for t in self.design["ties"]]
        if spec.KIND_CONFINEMENT in kinds and self.confinement is None:
            raise spec.SpecError(
                u"el dibujo tiene estribos o grapas de confinamiento: falta su diametro y distribucion"
            )

    def bar_type(self, doc, bar_types, key, mark):
        """The vertical bars' type for a diameter: the one chosen in the
        window ("Barras Ø / Tipo"), else the usual pick."""
        return (named_bar_type(doc, self.bar_type_names.get(key), key)
                or _bar_type(bar_types, key, mark))

    def family_of(self, kind):
        """(kind, StirrupFamily) of a drawn stirrup or tie."""
        if kind == spec.KIND_CONFINEMENT:
            return CONFINEMENT, self.confinement
        return EDGE, self.edge


class BarTypes(object):
    """Picks the project's RebarBarType for a bar diameter: same nominal
    diameter, preferring the one named for this column type (…COLUMNA C-2),
    then any column/columneta bar, then the SRB_ family."""

    def __init__(self, doc):
        self.types = []
        for bt in DB.FilteredElementCollector(doc).OfClass(RebarBarType):
            d = getattr(bt, "BarNominalDiameter", None) or bt.BarDiameter
            self.types.append((d * FT * 1000.0, element_name(bt).upper(), bt))

    def pick(self, key, mark):
        d_mm = spec.BAR_DIAMETERS_MM[key]
        best = None
        for diameter, name, bt in self.types:
            if abs(diameter - d_mm) > 0.4 or u" GRAPA " in name:
                continue  # a crosstie's own bend (tie_bar_type)
            score = 0
            if mark and mark in name:
                score += 4
            if u"COLUMN" in name:
                score += 2
            if name.startswith(u"SRB_"):
                score += 1
            if best is None or score > best[0]:
                best = (score, bt)
        return best[1] if best else None


def stirrup_hook(doc, diameter_key, angle_deg=135.0):
    """The stirrup hook (135 degrees unless said otherwise) for a bar
    diameter: one named for it ('Estribo 3/8" - 135'), else the seismic one
    (its length scales with the bar diameter), else any hook of that angle;
    None if there is none. A hook sized for a bigger bar would cross a small
    section and stick out of it (a 3/8" hook on a 1/4" tie in a 14 cm
    columneta)."""
    ranked = []
    for h in DB.FilteredElementCollector(doc).OfClass(RebarHookType):
        if abs(h.HookAngle * 57.29578 - angle_deg) > 1.0:
            continue
        name = element_name(h).lower()
        if diameter_key.lower() in name:
            rank = 0
        elif u"seismic" in name or u"sismic" in name:
            rank = 1
        elif u'"' in name or u"mm" in name:
            rank = 3  # named for another diameter
        else:
            rank = 2
        ranked.append((rank, h))
    ranked.sort(key=lambda r: r[0])
    return ranked[0][1] if ranked else None


class StirrupHooks(object):
    """Per-diameter cache of `stirrup_hook`."""

    def __init__(self, doc):
        self.doc = doc
        self.cache = {}

    def get(self, diameter_key, angle_deg=135.0):
        key = (diameter_key, round(angle_deg))
        if key not in self.cache:
            self.cache[key] = stirrup_hook(self.doc, diameter_key, angle_deg)
        return self.cache[key]

    def tie_hook(self, diameter_key, angle, leg_m):
        """The stirrup/tie hook of `angle` degrees whose straight leg is
        leg_m for this diameter (a hook type's leg is a multiple of the bar
        diameter); made inside the running Transaction when the project
        has none: "Grapa 180 - 6.5 cm (3/8")"."""
        db = spec.BAR_DIAMETERS_MM[diameter_key] / 1000.0
        mult = round(leg_m / db, 3)
        key = ("tie", round(angle), mult)
        if self.cache.get(key) is None:
            hook = None
            for h in DB.FilteredElementCollector(self.doc).OfClass(RebarHookType):
                if (abs(h.HookAngle * 57.29578 - angle) < 1.0 and h.Style == RebarStyle.StirrupTie
                        and abs(h.StraightLineMultiplier - mult) < 0.01):
                    hook = h
                    break
            if hook is None:
                hook = RebarHookType.Create(self.doc, math.radians(angle), mult)
                hook.Style = RebarStyle.StirrupTie
                try:
                    hook.Name = u"Grapa {:.0f} - {:g} cm ({})".format(angle, round(leg_m * 100, 1), diameter_key)
                except Exception:
                    pass
            self.cache[key] = hook
        return self.cache[key]

    def tie_180(self, diameter_key):
        """The 180-degree hook of the C/S crossties; when the project has
        none, one is made (inside the running Transaction): stirrup/tie
        style, straight extension 4 db (E.060 standard 180-degree hook).
        The bend follows each bar type's own diameter."""
        key = ("tie", 180)
        if self.cache.get(key) is None:
            # a tie takes stirrup/tie style hooks only (Revit refuses a
            # "Standard - 180 deg." one on it)
            found = [h for h in DB.FilteredElementCollector(self.doc).OfClass(RebarHookType)
                     if abs(h.HookAngle * 57.29578 - 180.0) < 1.0 and h.Style == RebarStyle.StirrupTie]
            hook = sorted(found, key=lambda h: element_name(h) != u"Grapa 180 (OL-STR)")[0] if found else None
            if hook is None:
                hook = RebarHookType.Create(self.doc, math.pi, 4.0)
                hook.Style = RebarStyle.StirrupTie
                try:
                    hook.Name = u"Grapa 180 (OL-STR)"
                except Exception:
                    pass
            self.cache[key] = hook
        return self.cache[key]


# Hook orientations (start, end) of the C/S crossties along their line
# (checked in Revit: the same orientation at both ends bends the hooks to
# one side, as seen from each end).
TIE_ORIENTATIONS = {
    spec.TIE_C: (RebarHookOrientation.Left, RebarHookOrientation.Left),
    spec.TIE_S: (RebarHookOrientation.Left, RebarHookOrientation.Right),
}


def tie_ends(a, b, bars, key, shape_name, bar_type):
    """Local ends of a crosstie drawn from bar center a to bar center b: a C
    or S one runs so each 180-degree hook's bend (the bar type's stirrup/tie
    bend) is centered on its bar, wrapping it; any other runs through the
    bars to their far sides (spec.tie_centerline)."""
    if shape_name in spec.TIE_STYLES:
        db = getattr(bar_type, "BarNominalDiameter", None) or bar_type.BarDiameter
        radius = (bar_type.StirrupTieBendDiameter + db) / 2.0 * FT
        pa, pb = spec.hooked_tie_line(a, b, radius, shape_name)
        # Revit sets each 180-degree bend radius + db/2 inside the curve's
        # end (measured: 23.8 mm for 3/8"): lengthen the curve by that much
        dx, dy = pb[0] - pa[0], pb[1] - pa[1]
        length = math.hypot(dx, dy) or 1.0
        # ...and the hook's outer face goes no further out than the stirrup's
        # (bar face + one stirrup diameter, the tie's), inside the cover:
        # the bend then moves in along the tie, still around its bar
        def inset(p):
            bar = min(bars, key=lambda q: (q[0] - p[0]) ** 2 + (q[1] - p[1]) ** 2)
            dbl = spec.BAR_DIAMETERS_MM[bar[2]] / 1000.0
            return max(0.0, radius + db / 2.0 * FT - (dbl / 2.0 + db * FT))
        ux, uy = dx / length, dy / length
        ea = radius + db / 2.0 * FT - inset(a)
        eb = radius + db / 2.0 * FT - inset(b)
        return (pa[0] - ux * ea, pa[1] - uy * ea), (pb[0] + ux * eb, pb[1] + uy * eb)
    return spec.tie_centerline(a, b, bars, key)


_TIE_TYPES = {}


def tie_bar_type(doc, bar_type, a, b, bars, shape_name):
    """The bar type of a C/S crosstie hooked on the bars at a and b: a copy
    of `bar_type` ("... GRAPA 1/2"") whose stirrup/tie bend diameter is the
    larger bar's diameter, so each hook hugs its bar; made inside the running
    Transaction when missing. Any other tie keeps `bar_type`."""
    if shape_name not in spec.TIE_STYLES:
        return bar_type
    def key_at(p):
        return min(bars, key=lambda q: (q[0] - p[0]) ** 2 + (q[1] - p[1]) ** 2)[2]
    key = max((key_at(a), key_at(b)), key=lambda k: spec.BAR_DIAMETERS_MM[k])
    base = bar_type.get_Parameter(DB.BuiltInParameter.ALL_MODEL_TYPE_NAME).AsString()
    name = u"{} GRAPA {}".format(base, key)
    cache = (doc.PathName, doc.Title, name)
    found = _TIE_TYPES.get(cache)
    if found is None or not found.IsValidObject:
        found = None
        for t in DB.FilteredElementCollector(doc).OfClass(RebarBarType):
            if t.get_Parameter(DB.BuiltInParameter.ALL_MODEL_TYPE_NAME).AsString() == name:
                found = t
                break
        if found is None:
            found = bar_type.Duplicate(name)
        _TIE_TYPES[cache] = found
    bend = spec.BAR_DIAMETERS_MM[key] / 304.8
    if abs(found.StirrupTieBendDiameter - bend) > 1e-6:
        found.StirrupTieBendDiameter = bend
    return found


def tie_leg_m(design, key, angle):
    """The crosstie hook leg of the drawing ("Pata", cm) or the E.060
    minimum for that diameter and angle; m."""
    leg = design.get("tie_leg")
    return (leg if leg else spec.tie_leg_cm(key, angle)) / 100.0


def _create_tie(doc, shapes, shape_name, host, bar_type, hook, hooks, key, curve, normal=None, leg=None):
    """A crosstie: a C or S one (shape_name in spec.TIE_STYLES) with
    180-degree hooks, a plain one with 135-degree hooks - both with a leg
    of `leg` m when given -, or the Revit shape chosen for it."""
    if shape_name in spec.TIE_STYLES:
        start, end = TIE_ORIENTATIONS[shape_name]
        h180 = hooks.tie_hook(key, 180.0, leg) if leg else hooks.tie_180(key)
        return Rebar.CreateFromCurves(
            doc, RebarStyle.StirrupTie, bar_type, h180, h180, host, normal or DB.XYZ.BasisZ,
            List[DB.Curve]([curve]), start, end, True, True)
    if shape_name is None and leg:
        hook = hooks.tie_hook(key, 135.0, leg)
    return _create_stirrup(doc, shapes, shape_name, host, bar_type, hook, hooks, key,
                           List[DB.Curve]([curve]), RebarHookOrientation.Left, RebarHookOrientation.Right,
                           normal)


class RebarShapes(object):
    """The project's rebar shapes by name, for the stirrups and ties drawn
    with a chosen shape. `mismatched` collects (column id, shape name) where
    Revit refused the drawing for that shape and picked one itself."""

    def __init__(self, doc):
        self.by_name = dict(
            (element_name(s), s) for s in DB.FilteredElementCollector(doc).OfClass(RebarShape)
        )
        self.mismatched = []

    def get(self, name):
        return self.by_name.get(name) if name else None


def _hook_angle(shape, end):
    try:
        return shape.GetDefaultHookAngle(end)
    except Exception:
        return 0


def _create_stirrup(doc, shapes, shape_name, column, bar_type, hook, hooks, key, curves,
                    orient_start, orient_end, normal=None):
    """A stirrup or tie with the shape drawn for it (its hooks those the
    shape asks for); without one, or if Revit refuses the drawing for that
    shape, the shape Revit matches to the curves (with `hook` at both ends).
    `normal`: the normal of its plane (up for a column's, the beam axis for
    a beam's)."""
    normal = normal or DB.XYZ.BasisZ
    shape = shapes.get(shape_name) if shapes is not None else None
    if shape is not None:
        ends = [hooks.get(key, _hook_angle(shape, e)) if _hook_angle(shape, e) else None
                for e in (0, 1)]
        try:
            rebar = Rebar.CreateFromCurvesAndShape(
                doc, shape, bar_type, ends[0], ends[1], column, normal, curves,
                orient_start, orient_end,
            )
        except Exception:
            rebar = None
        if rebar is not None:  # None too when the curves don't fit the shape
            return rebar
        shapes.mismatched.append((element_id_value(column.Id), shape_name))
    return Rebar.CreateFromCurves(
        doc, RebarStyle.StirrupTie, bar_type, hook, hook, column, normal,
        curves, orient_start, orient_end, True, True,
    )


def _solids(geometry):
    for obj in geometry or []:
        if isinstance(obj, DB.Solid) and obj.Volume > 1e-9:
            yield obj
        elif isinstance(obj, DB.GeometryInstance):
            for s in _solids(obj.GetInstanceGeometry()):
                yield s


class Section(object):
    """Cross-section of a vertical prismatic column, from its original
    (uncut) family geometry. Local frame: x/y of the family, origin at the
    section's bounding-box center; `polygon` is the outline in meters.
    Lengths on the object are in feet unless named `_m`."""

    def __init__(self, column):
        t = column.GetTransform()
        if abs(t.BasisZ.Z) < 0.999:
            raise spec.SpecError(u"columna inclinada (no soportada)")
        solids = list(_solids(column.GetOriginalGeometry(DB.Options())))
        if len(solids) != 1:
            raise spec.SpecError(u"geometria de {} solidos (no soportada)".format(len(solids)))
        solid = solids[0]
        bottom = None
        z_top = None
        for face in solid.Faces:
            if not isinstance(face, DB.PlanarFace):
                continue
            if face.FaceNormal.Z < -0.999 and (bottom is None or face.Area > bottom.Area):
                bottom = face
            elif face.FaceNormal.Z > 0.999:
                z_top = face.Origin.Z if z_top is None else max(z_top, face.Origin.Z)
        if bottom is None or z_top is None:
            raise spec.SpecError(u"sin cara inferior/superior horizontal")
        loops = list(bottom.GetEdgesAsCurveLoops())
        if len(loops) != 1:
            raise spec.SpecError(u"seccion con huecos (no soportada)")
        outline = []
        for curve in loops[0]:
            if not isinstance(curve, DB.Line):
                raise spec.SpecError(u"seccion con bordes curvos (no soportada)")
            outline.append(curve.GetEndPoint(0))
        z_bottom = bottom.Origin.Z
        height = z_top - z_bottom
        xs = [p.X for p in outline]
        ys = [p.Y for p in outline]
        cx, cy = (max(xs) + min(xs)) / 2.0, (max(ys) + min(ys)) / 2.0
        polygon_ft = [(p.X - cx, p.Y - cy) for p in outline]
        area = abs(spec.polygon_signed_area(polygon_ft))
        if height <= 0 or abs(solid.Volume - area * height) > 0.02 * solid.Volume:
            raise spec.SpecError(u"la seccion cambia con la altura (no soportada)")

        self.transform = t
        self.center = (cx, cy)
        self.polygon_m = [(x * FT, y * FT) for x, y in polygon_ft]
        self.b = max(xs) - min(xs)
        self.h = max(ys) - min(ys)
        self.is_rectangle = len(polygon_ft) == 4 and abs(area - self.b * self.h) < 1e-4 * self.b * self.h + 1e-6
        self.z_bottom = t.OfPoint(DB.XYZ(cx, cy, z_bottom)).Z
        self.z_top = t.OfPoint(DB.XYZ(cx, cy, z_top)).Z

    def point_m(self, x_m, y_m, z_world):
        """World point at a local section offset (meters, from the section
        center) and world elevation z (ft)."""
        p = self.transform.OfPoint(
            DB.XYZ(self.center[0] + x_m / FT, self.center[1] + y_m / FT, 0.0)
        )
        return DB.XYZ(p.X, p.Y, z_world)


def clear_top(doc, column, section):
    """(Rule agreed with the user, see Acero.pushbutton/REGLAS_ACERO.md.)
    Top of the column's clear height, where its stirrup distribution
    ends: the underside of the deepest beam framing into its top (where
    beams of different depth meet, always the deepest one), else the
    underside of the slab over it, else the column top. From there up to
    the column top is the joint."""
    bb = column.get_BoundingBox(None)
    if bb is None:
        return section.z_top
    reach = 0.05
    outline = DB.Outline(
        DB.XYZ(bb.Min.X - reach, bb.Min.Y - reach, section.z_top - TOP_FRAMING_REACH_FT),
        DB.XYZ(bb.Max.X + reach, bb.Max.Y + reach, section.z_top + 0.01),
    )
    mid = (section.z_bottom + section.z_top) / 2.0
    # Measured where each element meets the column - its solids cut to the
    # column's section grown by 5 cm (beams and slabs are cut back by the
    # column) -, not on its bounding box: a sloped or stepped beam's box
    # reaches far below the column top.
    xs = [p[0] for p in section.polygon_m]
    ys = [p[1] for p in section.polygon_m]
    grow = 0.05
    corners = [section.point_m(x, y, mid) for x, y in (
        (min(xs) - grow, min(ys) - grow), (max(xs) + grow, min(ys) - grow),
        (max(xs) + grow, max(ys) + grow), (min(xs) - grow, max(ys) + grow))]
    loop = DB.CurveLoop()
    for k in range(4):
        loop.Append(DB.Line.CreateBound(corners[k], corners[(k + 1) % 4]))
    around = DB.GeometryCreationUtilities.CreateExtrusionGeometry(
        List[DB.CurveLoop]([loop]), DB.XYZ.BasisZ, section.z_top + 0.01 - mid)
    options = DB.Options()

    def underside(element):
        low, failed = None, False
        for solid in _solids(element.get_Geometry(options)):
            try:
                part = DB.BooleanOperationsUtils.ExecuteBooleanOperation(
                    solid, around, DB.BooleanOperationsType.Intersect)
            except Exception:
                failed = True
                continue
            if part is None or part.Volume < 1e-6:
                continue
            for edge in part.Edges:
                for p in edge.Tessellate():
                    low = p.Z if low is None else min(low, p.Z)
        if low is None and failed:
            ebb = element.get_BoundingBox(None)
            low = ebb.Min.Z if ebb is not None else None
        return low

    for bic in (DB.BuiltInCategory.OST_StructuralFraming, DB.BuiltInCategory.OST_Floors):
        best = section.z_top  # the lowest underside: the deepest member
        found = (
            DB.FilteredElementCollector(doc)
            .OfCategory(bic)
            .WhereElementIsNotElementType()
            .WherePasses(DB.BoundingBoxIntersectsFilter(outline))
        )
        for e in found:
            z = underside(e)
            if z is not None and mid < z < best:
                best = z
        if best < section.z_top - 1e-3:
            return best
    return section.z_top


def delete_generated(doc, column):
    """Delete the bars an earlier run generated for this column."""
    column_id = str(element_id_value(column.Id))
    host_data = RebarHostData.GetRebarHostData(column)
    if host_data is None:
        return 0
    old = []
    for rebar in host_data.GetRebarsInHost():
        p = rebar.LookupParameter(ORIGIN_PARAM)
        if p is not None and p.AsString() == column_id:
            old.append(rebar.Id)
    if old:
        doc.Delete(List[DB.ElementId](old))
    return len(old)


def _tag(rebar, column):
    p = rebar.LookupParameter(ORIGIN_PARAM)
    if p is not None and not p.IsReadOnly:
        p.Set(str(element_id_value(column.Id)))


def foundation_below(doc, column, section):
    """Depth (m) of the foundation (zapata, cimiento, losa de cimentacion)
    the column stands on - from the column base down to its bottom -, or
    None when it stands on something else."""
    cx, cy = section.center
    probe = section.point_m(0.0, 0.0, section.z_bottom - 0.05 / FT)
    outline = DB.Outline(DB.XYZ(probe.X - 0.05, probe.Y - 0.05, probe.Z - 0.05),
                         DB.XYZ(probe.X + 0.05, probe.Y + 0.05, probe.Z + 0.05))
    deepest = None
    for element in (DB.FilteredElementCollector(doc)
                    .OfCategory(DB.BuiltInCategory.OST_StructuralFoundation)
                    .WhereElementIsNotElementType()
                    .WherePasses(DB.BoundingBoxIntersectsFilter(outline))):
        bb = element.get_BoundingBox(None)
        if bb is not None and bb.Max.Z >= section.z_bottom - 0.1 / FT:
            depth = (section.z_bottom - bb.Min.Z) * FT
            deepest = depth if deepest is None else max(deepest, depth)
    return deepest


def column_above(doc, column, section):
    """True when another column stands on this one (its bars go on up)."""
    top = section.point_m(0.0, 0.0, section.z_top + 0.05 / FT)
    outline = DB.Outline(DB.XYZ(top.X - 0.05, top.Y - 0.05, top.Z - 0.02),
                         DB.XYZ(top.X + 0.05, top.Y + 0.05, top.Z + 0.02))
    for element in (DB.FilteredElementCollector(doc)
                    .OfCategory(DB.BuiltInCategory.OST_StructuralColumns)
                    .WhereElementIsNotElementType()
                    .WherePasses(DB.BoundingBoxIntersectsFilter(outline))):
        if element.Id != column.Id:
            return True
    return False


REVIT_MAX_BAR_M = 10.95  # Revit's longest bar (36 ft), anchorage and legs included
FOUNDATION_COVER_M = 0.075  # on the ground: the bar stops over the bottom mesh


def bar_ends(doc, column_spec, key, x, y, section, bottom_column, top_column):
    """kwargs of spec.bar_with_ends for a vertical bar (local x, y): into
    the foundation under `bottom_column` (EA_Barra_Extremos anchor, or its
    depth less the cover and the mesh) with its bottom leg, and - when no
    column stands on `top_column` - ended under the top cover with its top
    leg. Legs in cm per diameter; none when not set."""
    xs = [p[0] for p in section.polygon_m]
    ys = [p[1] for p in section.polygon_m]
    half_b, half_h = (max(xs) - min(xs)) / 2.0, (max(ys) - min(ys)) / 2.0
    d = spec.BAR_DIAMETERS_MM[key] / 1000.0
    ends = {}
    depth = foundation_below(doc, bottom_column, Section(bottom_column))
    if depth:
        anchor = column_spec.anchor_m
        if anchor is None:
            anchor = depth - FOUNDATION_COVER_M - 2 * d  # resting on the bottom mesh
        ends["anchor"] = max(0.0, min(anchor, depth - FOUNDATION_COVER_M))
        leg = spec.leg_m(column_spec.leg_bottom_cm.get(key))
        if leg:
            ends["leg_bottom"] = leg
            ends["dir_bottom"] = spec.leg_vector(x, y, half_b, half_h, column_spec.dir_bottom)
    leg = spec.leg_m(column_spec.leg_top_cm.get(key))
    if leg and not column_above(doc, top_column, Section(top_column)):
        ends["top_drop"] = column_spec.cover_m + d
        ends["leg_top"] = leg
        ends["dir_top"] = spec.leg_vector(x, y, half_b, half_h, column_spec.dir_top)
    return ends


def _half_sizes(section):
    xs = [p[0] for p in section.polygon_m]
    ys = [p[1] for p in section.polygon_m]
    return ((max(xs) - min(xs)) / 2.0, (max(ys) - min(ys)) / 2.0)


def _bar_curves(points_m, section, z0_ft):
    """Revit lines through local (x, y, z m over z0_ft) points."""
    pts = [section.point_m(px, py, z0_ft + pz / FT) for px, py, pz in points_m]
    return List[DB.Curve]([DB.Line.CreateBound(pts[k], pts[k + 1]) for k in range(len(pts) - 1)])


def _bar_normal(section, points_m):
    """Normal of a vertical bar's plane: across its legs / crank, or the
    section's x axis when straight."""
    for a, b in zip(points_m, points_m[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        if math.hypot(dx, dy) > 1e-6:
            v = section.transform.OfVector(DB.XYZ(dx, dy, 0.0)).Normalize()
            return DB.XYZ.BasisZ.CrossProduct(v).Normalize()
    return section.transform.BasisX


def _runs(family, joint_spacing_m, section, z_clear_top, z_start=None):
    """(z_start_ft, count, spacing_ft, side) runs of one stirrup family: its
    clear-height distribution, one rebar set per zone and end as written
    (spec.stirrup_sets: '1@.05' a single stirrup, '5@.10' a set of 5 at
    0.10, the rest one set in the middle), plus the joint when EA_Nucleo_cm
    is set. side is -1 for the top end zones: the way stirrups set at one
    height stack (towards the middle, so the first one keeps its distance
    from each end)."""
    z_start = section.z_bottom if z_start is None else z_start
    clear_m = (z_clear_top - z_start) * FT
    runs = [
        (z_start + start / FT, n, spacing / FT, side)
        for start, n, spacing, _, side in spec.stirrup_sets(clear_m, family.zones, family.rest)
    ]
    if joint_spacing_m and section.z_top - z_clear_top > 0.1 / FT:
        joint = spec.joint_positions((section.z_top - z_clear_top) * FT, joint_spacing_m)
        for start, n, spacing in spec.group_runs(joint):
            runs.append((z_clear_top + start / FT, n, spacing / FT, 1))
    return runs


def _set(rebar, n, spacing_ft):
    if n > 1:
        rebar.GetShapeDrivenAccessor().SetLayoutAsNumberWithSpacing(
            n, spacing_ft, True, True, True
        )


def named_bar_type(doc, name, key):
    """The project's bar type called `name` when it has the diameter `key`
    (the "Tipo" chosen in the window), else None."""
    if not name:
        return None
    d_mm = spec.BAR_DIAMETERS_MM[key]
    for bt in DB.FilteredElementCollector(doc).OfClass(RebarBarType):
        if element_name(bt) == name:
            d = getattr(bt, "BarNominalDiameter", None) or bt.BarDiameter
            return bt if abs(d * FT * 1000.0 - d_mm) <= 0.4 else None
    return None


def bar_type_keys(doc):
    """{name: diameter key} of every bar type of the project with a known
    diameter (not the crosstie copies): the window's "Tipo" lists."""
    keys = {}
    for bt in DB.FilteredElementCollector(doc).OfClass(RebarBarType):
        name = element_name(bt)
        key = bar_type_diameter(bt)
        if key and u" GRAPA " not in name:
            keys[name] = key
    return keys


def bar_type_names(doc, key):
    """Names of the project's bar types of diameter `key` (not the crosstie
    copies), sorted: the "Tipo" list of the window."""
    d_mm = spec.BAR_DIAMETERS_MM[key]
    names = []
    for bt in DB.FilteredElementCollector(doc).OfClass(RebarBarType):
        d = getattr(bt, "BarNominalDiameter", None) or bt.BarDiameter
        name = element_name(bt)
        if abs(d * FT * 1000.0 - d_mm) <= 0.4 and u" GRAPA " not in name:
            names.append(name)
    return sorted(names)


def _bar_type(bar_types, key, mark):
    bar_type = bar_types.pick(key, mark)
    if bar_type is None:
        raise spec.SpecError(u"no hay tipo de barra de {}".format(key))
    return bar_type


def generate_column(doc, column, column_spec, bar_types, hooks, mark, shapes=None, longitudinal=True):
    """Create the column's longitudinal bars, stirrups and ties from its
    drawing (inside an active Transaction; Revit needs a Regenerate before
    their lengths are known, see `record_weight`). Perimeter stirrups use
    the edge ("borde") settings; inner stirrups and ties the confinement
    ones; each takes the rebar shape it was drawn with (`shapes`, a
    RebarShapes) when it has one. `longitudinal`=False leaves the vertical
    bars out (a stack makes them continuous, see `generate_stack`).
    Returns [(rebar, diameter_key, kind)]."""
    section = Section(column)
    design = column_spec.design

    def bar_type_for(key):
        return _bar_type(bar_types, key, mark)

    delete_generated(doc, column)
    created = []

    height_m = (section.z_top - section.z_bottom) * FT
    for x, y, key in (design["bars"] if longitudinal else []):
        path = spec.bar_with_ends([(x, y, 0.0), (x, y, height_m)],
                                  **bar_ends(doc, column_spec, key, x, y, section, column, column))
        rebar = Rebar.CreateFromCurves(
            doc, RebarStyle.Standard, column_spec.bar_type(doc, bar_types, key, mark), None, None, column,
            _bar_normal(section, path), _bar_curves(path, section, section.z_bottom),
            RebarHookOrientation.Right, RebarHookOrientation.Right, True, True,
        )
        _tag(rebar, column)
        created.append((rebar, key, LONGITUDINAL))

    z_clear_top = clear_top(doc, column, section)
    # Izaje: over a footing, from the column base up to izaje_h; the column's
    # own distribution starts there.
    izaje_h = column_spec.izaje_h if (column_spec.izaje and foundation_below(doc, column, section)) else 0.0
    z_start = section.z_bottom + izaje_h / FT
    # (kind, family, [loop centerlines], [tie centerlines]) per family
    groups = {}
    stirrup_shapes = spec.design_shapes(design, "stirrups")
    for (drawn_kind, poly, wrap, is_open), shape_name in zip(design["stirrups"], stirrup_shapes):
        kind, family = column_spec.family_of(drawn_kind)
        entry = groups.setdefault(kind, (family, [], []))
        line = spec.stirrup_centerline(poly, design["bars"], family.key, wrap, is_open)
        entry[1].append((spec.clean_polyline(line, closed=not is_open), is_open, shape_name))
    for (drawn_kind, a, b), shape_name in zip(design["ties"], spec.design_shapes(design, "ties")):
        kind, family = column_spec.family_of(drawn_kind)
        entry = groups.setdefault(kind, (family, [], []))
        entry[2].append((a, b, shape_name))  # its line with the bar type (tie_ends)

    # Stirrups and ties set at the same height lie stacked, one bar
    # diameter apart, like on site: side by side, never through each other.
    flat = [(kind, i, is_tie) for kind, (family, loops, ties) in groups.items()
            for is_tie, items in ((False, loops), (True, ties)) for i in range(len(items))]
    lifts = spec.stack_lifts([
        (kind, spec.BAR_DIAMETERS_MM[groups[kind][0].key] / 1000.0, is_tie) for kind, i, is_tie in flat])
    lift_ft = dict(((kind, i, is_tie), lift / FT) for (kind, i, is_tie), lift in zip(flat, lifts))

    for kind, (family, loops, ties) in groups.items():
        stirrup_type = named_bar_type(doc, family.type_name, family.key) or bar_type_for(family.key)
        hook = hooks.get(family.key)
        # In the joint the edge stirrups take EA_Nucleo_cm; the confinement
        # stirrups and ties keep their own rest spacing ("rto") there too.
        joint_m = column_spec.joint_spacing_m
        if joint_m and kind == CONFINEMENT:
            joint_m = family.rest
        for z_set, n, spacing, side in _runs(family, joint_m, section, z_clear_top, z_start):
            for index, (line, is_open, shape_name) in enumerate(loops):
                z = z_set + side * lift_ft[(kind, index, False)]
                if is_open:
                    # U-shaped stirrup: its drawn segments, ends left
                    # straight (no hooks) unless its shape has them.
                    pts = [section.point_m(x, y, z) for x, y in line]
                    curves = List[DB.Curve](
                        [DB.Line.CreateBound(pts[k], pts[k + 1]) for k in range(len(pts) - 1)]
                    )
                    rebar = _create_stirrup(
                        doc, shapes, shape_name, column, stirrup_type, None, hooks, family.key,
                        curves, RebarHookOrientation.Left, RebarHookOrientation.Left,
                    )
                else:
                    # Counterclockwise seen from above, so Left hooks turn
                    # inward (also on mirrored instances, whose frame flips).
                    pts = _counterclockwise([section.point_m(x, y, z) for x, y in line])
                    loop = List[DB.Curve](
                        [DB.Line.CreateBound(pts[k], pts[(k + 1) % len(pts)]) for k in range(len(pts))]
                    )
                    rebar = _create_stirrup(
                        doc, shapes, shape_name, column, stirrup_type, hook, hooks, family.key,
                        loop, RebarHookOrientation.Left, RebarHookOrientation.Left,
                    )
                _set(rebar, n, spacing)
                _tag(rebar, column)
                created.append((rebar, family.key, kind))
            for index, (ta, tb, shape_name) in enumerate(ties):
                z = z_set + lift_ft[(kind, index, True)]  # a tie: always just below the stirrup
                tie_type = tie_bar_type(doc, stirrup_type, ta, tb, design["bars"], shape_name)
                a, b = tie_ends(ta, tb, design["bars"], family.key, shape_name, tie_type)
                line = DB.Line.CreateBound(
                    section.point_m(a[0], a[1], z), section.point_m(b[0], b[1], z)
                )
                angle = 180.0 if shape_name in spec.TIE_STYLES else 135.0
                rebar = _create_tie(doc, shapes, shape_name, column, tie_type, hook, hooks,
                                    family.key, line, leg=tie_leg_m(design, family.key, angle))
                _set(rebar, n, spacing)
                _tag(rebar, column)
                created.append((rebar, family.key, kind))
    if izaje_h and EDGE in groups:
        izaje = column_spec.izaje
        izaje_type = named_bar_type(doc, izaje.type_name, izaje.key) or bar_type_for(izaje.key)
        positions = spec.izaje_positions(izaje_h, column_spec.izaje_dist)
        for start, n, spacing in spec.group_runs(positions):
            for line, is_open, shape_name in groups[EDGE][1]:
                if is_open:
                    continue
                pts = _counterclockwise([section.point_m(x, y, section.z_bottom + start / FT) for x, y in line])
                loop = List[DB.Curve](
                    [DB.Line.CreateBound(pts[k], pts[(k + 1) % len(pts)]) for k in range(len(pts))])
                rebar = _create_stirrup(
                    doc, shapes, shape_name, column, izaje_type, hooks.get(izaje.key), hooks, izaje.key,
                    loop, RebarHookOrientation.Left, RebarHookOrientation.Left,
                )
                _set(rebar, n, spacing / FT)
                _tag(rebar, column)
                created.append((rebar, izaje.key, EDGE))
    return created


# --- Stacked columns: continuous longitudinal bars, lap spliced -------------------

def column_stacks(columns, xy_tol_m=0.02, z_tol_m=0.10):
    """The columns grouped into stacks: same type, same axis and turned the
    same way, each standing on the one below (its base within `z_tol_m` of
    that one's top). Returns [[column, ...] bottom up]; a lone column is a
    stack of one."""
    info = []
    for column in columns:
        try:
            section = Section(column)
        except Exception:
            continue
        info.append((section.z_bottom, column, section))
    info.sort(key=lambda item: item[0])
    stacks = []  # [(columns, last section)]
    for _, column, section in info:
        axis = section.point_m(0.0, 0.0, section.z_bottom)
        placed = False
        for stack in stacks:
            last_column, last = stack[0][-1], stack[1]
            below = last.point_m(0.0, 0.0, last.z_top)
            if (last_column.GetTypeId() == column.GetTypeId()
                    and abs(section.z_bottom - last.z_top) * FT <= z_tol_m
                    and math.hypot(axis.X - below.X, axis.Y - below.Y) * FT <= xy_tol_m
                    and section.transform.BasisX.IsAlmostEqualTo(last.transform.BasisX, 1e-3)):
                stack[0].append(column)
                stack[1] = section
                placed = True
                break
        if not placed:
            stacks.append([[column], section])
    return [columns_ for columns_, _ in stacks]


def generate_stack(doc, stack, column_spec, bar_types, hooks, mark, shapes=None, splice=None):
    """Create the rebar of a stack of columns (`column_stacks`, bottom up):
    each column's stirrups and ties (`generate_column`), and longitudinal
    bars running the whole stack, cut into bars of at most splice["max"] m
    lapping splice["laps"][diameter key] m, each lap in the central half of
    a story's clear height (spec.splice_pieces). The lower bar of a lap is
    cranked 1:6 one bar diameter towards the section center, so the two
    bars lie side by side, touching. Every piece is hosted by (and tagged
    with) the column holding its middle. `splice` None: per-column bars as
    before. Returns ({column id: [(rebar, key, kind[, share])]},
    warnings): a longitudinal bar is listed in every column it runs
    through, with the share of its length there (see `record_weight`)."""
    if splice is None:
        return dict((element_id_value(c.Id), generate_column(doc, c, column_spec, bar_types, hooks, mark, shapes))
                    for c in stack), []
    created = {}
    for column in stack:
        created[element_id_value(column.Id)] = generate_column(
            doc, column, column_spec, bar_types, hooks, mark, shapes, longitudinal=False)
    sections = [Section(c) for c in stack]
    base = sections[0]
    z0_ft = base.z_bottom
    total = (sections[-1].z_top - z0_ft) * FT
    stories = [((s.z_bottom - z0_ft) * FT, (clear_top(doc, c, s) - z0_ft) * FT) for c, s in zip(stack, sections)]
    tops = [(s.z_top - z0_ft) * FT for s in sections]
    warnings = []
    for x, y, key in column_spec.design["bars"]:
        d = spec.BAR_DIAMETERS_MM[key] / 1000.0
        ends = bar_ends(doc, column_spec, key, x, y, base, stack[0], stack[-1])
        developed = total + ends.get("anchor", 0.0) + ends.get("leg_bottom", 0.0) + ends.get("leg_top", 0.0)
        lap = splice["laps"].get(key)
        if lap is None and developed > REVIT_MAX_BAR_M:
            # Revit won't take a bar this long ("totalmente fuera de su
            # anfitrion"): spliced anyway, with the E.060 lap
            lap = spec.e060_lap_cm(key) / 100.0
            warnings.append(u"Barras de {}: {:.2f} m supera el maximo de Revit ({:.2f} m); se empalmaron con "
                            u"{:.2f} m (E.060). Marca el diametro en '4. Empalme' para elegir el empalme."
                            .format(key, developed, REVIT_MAX_BAR_M, lap))
        if lap is None:  # this diameter isn't spliced: one bar
            if total > splice["max"] + 1e-6:
                warnings.append(u"Barras de {}: {:.2f} m sin empalme (diametro no marcado para empalmar)"
                                .format(key, total))
            pieces, found = [(0.0, total)], []
            lap = 0.0
        else:
            pieces, found = spec.splice_pieces(0.0, total, stories, lap, splice["max"])
        warnings += [u"Barras de {}: {}".format(key, w) for w in found]
        # with legs, the crank goes square to the same face (one plane per bar)
        leg_dir = ends.get("dir_bottom") or ends.get("dir_top")
        inward = None
        if leg_dir:
            out = spec.leg_vector(x, y, *(_half_sizes(base) + (spec.LEG_OUT,)))
            inward = (-out[0], -out[1])
        for index, (z_start, z_end) in enumerate(pieces):
            path = spec.bar_piece_points(x, y, d, z_start, z_end, lap, index < len(pieces) - 1, inward)
            path = spec.bar_with_ends(
                path,
                **dict((k, v) for k, v in ends.items()
                       if (index == 0 and k in ("anchor", "leg_bottom", "dir_bottom"))
                       or (index == len(pieces) - 1 and k in ("top_drop", "leg_top", "dir_top"))))
            middle = (z_start + z_end) / 2.0
            host = stack[next((k for k, top in enumerate(tops) if middle <= top + 1e-6), len(stack) - 1)]
            rebar = Rebar.CreateFromCurves(
                doc, RebarStyle.Standard, column_spec.bar_type(doc, bar_types, key, mark), None, None, host,
                _bar_normal(base, path), _bar_curves(path, base, z0_ft),
                RebarHookOrientation.Right, RebarHookOrientation.Right, True, True,
            )
            _tag(rebar, host)
            bottom = 0.0
            for column, top in zip(stack, tops):
                inside = min(z_end, top) - max(z_start, bottom)
                if inside > 1e-6:
                    created[element_id_value(column.Id)].append(
                        (rebar, key, LONGITUDINAL, inside / (z_end - z_start)))
                bottom = top
    return created, [w for i, w in enumerate(warnings) if w not in warnings[:i]]  # once each


def _counterclockwise(points):
    area = 0.0
    for k in range(len(points)):
        a, b = points[k], points[(k + 1) % len(points)]
        area += a.X * b.Y - b.X * a.Y
    return points if area > 0 else list(reversed(points))


def _centerline_points(rebar):
    """Points along the first bar of a rebar set, hooks included."""
    try:
        curves = rebar.GetCenterlineCurves(
            False, False, False, MultiplanarOption.IncludeOnlyPlanarCurves, 0
        )
    except Exception:
        return []
    points = []
    for curve in curves:
        points.extend(curve.Tessellate())
    return points


def record_weight(column, created):
    """Steel weight of the bars `generate_column` created (after a
    Regenerate), written to EA_Peso_Acero_kg. Returns ({kind: kg},
    number_of_bars, stirrups_stick_out)."""
    kg_by_kind = {LONGITUDINAL: 0.0, EDGE: 0.0, CONFINEMENT: 0.0}
    bars = 0
    stick_out = False
    section = Section(column)
    inverse = section.transform.Inverse
    tol = 0.0015  # m
    for item in created:
        # (rebar, key, kind[, share]): a bar running several stacked columns
        # weighs in each one by the share of its length inside it
        rebar, key, kind = item[:3]
        share = item[3] if len(item) > 3 else 1.0
        kg_by_kind[kind] += rebar.TotalLength * FT * spec.bar_weight_kg_per_m(key) * share
        if rebar.GetHostId() == column.Id:
            bars += rebar.Quantity
        if kind == LONGITUDINAL or stick_out:
            continue
        radius = spec.BAR_DIAMETERS_MM[key] / 2000.0
        for point in _centerline_points(rebar):
            p = inverse.OfPoint(point)
            local = ((p.X - section.center[0]) * FT, (p.Y - section.center[1]) * FT)
            if (not spec.point_in_polygon(local, section.polygon_m)
                    or spec.distance_to_polygon(local, section.polygon_m) < radius - tol):
                stick_out = True
                break
    p = column.LookupParameter(WEIGHT_PARAM)
    if p is not None and not p.IsReadOnly:
        p.Set(round(sum(kg_by_kind.values()), 2))
    return kg_by_kind, bars, stick_out


# --- Elements around the column, for the elevation and 3D views ---------------

# Only what frames into the column: beams, slabs and its footing.
NEIGHBOR_CATEGORIES = (
    (DB.BuiltInCategory.OST_StructuralFraming, u"VIGA"),
    (DB.BuiltInCategory.OST_Floors, u"LOSA"),
    (DB.BuiltInCategory.OST_StructuralFoundation, u"ZAPATA"),
)


def _box_triangles(box):
    """The 12 triangles of an axis-aligned box (x0, y0, z0, x1, y1, z1)."""
    x0, y0, z0, x1, y1, z1 = box
    c = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
         (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    quads = ((0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7))
    tris = []
    for a, b, d, e in quads:
        tris.append((c[a], c[b], c[d]))
        tris.append((c[a], c[d], c[e]))
    return tris


def column_neighbors(doc, column, section, reach_m=0.6, contact_m=0.05):
    """Elements touching the column (within `contact_m`: a beam cut back by
    its join with the column still counts) - beams, slabs and its footing -
    cut to `reach_m` around it,
    in the column's local frame (meters; x/y from the section center, z
    from the column base). Returns [{"label", "triangles": [(p, p, p)],
    "box": (x0, y0, z0, x1, y1, z1)}]."""
    bb = column.get_BoundingBox(None)
    if bb is None:
        return []
    touch = contact_m / FT
    near = DB.Outline(
        DB.XYZ(bb.Min.X - touch, bb.Min.Y - touch, bb.Min.Z - touch),
        DB.XYZ(bb.Max.X + touch, bb.Max.Y + touch, bb.Max.Z + touch),
    )
    # Crop box: the section's bounding box grown by reach_m, from reach_m
    # under the base to reach_m over the top, in the column's own frame.
    xs = [p[0] for p in section.polygon_m]
    ys = [p[1] for p in section.polygon_m]
    x0, x1 = min(xs) - reach_m, max(xs) + reach_m
    y0, y1 = min(ys) - reach_m, max(ys) + reach_m
    z0 = section.z_bottom - reach_m / FT
    corners = [section.point_m(x, y, z0) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
    loop = DB.CurveLoop()
    for k in range(4):
        loop.Append(DB.Line.CreateBound(corners[k], corners[(k + 1) % 4]))
    crop = DB.GeometryCreationUtilities.CreateExtrusionGeometry(
        List[DB.CurveLoop]([loop]), DB.XYZ.BasisZ,
        (section.z_top - section.z_bottom) + 2 * reach_m / FT,
    )
    inverse = section.transform.Inverse

    def local(p):
        q = inverse.OfPoint(p)
        return ((q.X - section.center[0]) * FT, (q.Y - section.center[1]) * FT,
                (p.Z - section.z_bottom) * FT)

    result = []
    options = DB.Options()
    for bic, label in NEIGHBOR_CATEGORIES:
        found = (
            DB.FilteredElementCollector(doc)
            .OfCategory(bic)
            .WhereElementIsNotElementType()
            .WherePasses(DB.BoundingBoxIntersectsFilter(near))
        )
        for element in found:
            if element.Id == column.Id:
                continue
            triangles = []
            failed = False
            for solid in _solids(element.get_Geometry(options)):
                try:
                    part = DB.BooleanOperationsUtils.ExecuteBooleanOperation(
                        solid, crop, DB.BooleanOperationsType.Intersect)
                except Exception:
                    failed = True
                    continue
                if part is None or part.Volume < 1e-6:
                    continue
                for face in part.Faces:
                    mesh = face.Triangulate()
                    for i in range(mesh.NumTriangles):
                        tri = mesh.get_Triangle(i)
                        triangles.append(tuple(local(tri.get_Vertex(k)) for k in range(3)))
            if not triangles and failed:
                # Revit couldn't cut it: show its bounding box, cut to the
                # same zone, rather than leaving it out.
                ebb = element.get_BoundingBox(None)
                if ebb is not None:
                    pts = [local(DB.XYZ(x, y, z)) for x in (ebb.Min.X, ebb.Max.X)
                           for y in (ebb.Min.Y, ebb.Max.Y) for z in (ebb.Min.Z, ebb.Max.Z)]
                    height = (section.z_top - section.z_bottom) * FT
                    box = (max(min(q[0] for q in pts), x0), max(min(q[1] for q in pts), y0),
                           max(min(q[2] for q in pts), -reach_m),
                           min(max(q[0] for q in pts), x1), min(max(q[1] for q in pts), y1),
                           min(max(q[2] for q in pts), height + reach_m))
                    if box[0] < box[3] and box[1] < box[4] and box[2] < box[5]:
                        triangles = _box_triangles(box)
            if not triangles:
                continue
            pts = [p for tri in triangles for p in tri]
            box = (min(p[0] for p in pts), min(p[1] for p in pts), min(p[2] for p in pts),
                   max(p[0] for p in pts), max(p[1] for p in pts), max(p[2] for p in pts))
            result.append({"label": label, "triangles": triangles, "box": box})
    return result
