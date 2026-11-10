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
import clr
import json
import math
import re

from pyrevit import DB
from Autodesk.Revit.DB.Structure import (
    MultiplanarOption,
    Rebar,
    RebarBarType,
    RebarFreeFormValidationResult,
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
TOP_MEMBER_REACH_M = 0.10  # a beam / slab whose top is this close to the column's top is over it
COLUMNETA_WORDS = (u"COLUMNETA",)


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
    specs.append((WEIGHT_PARAM, False, columns + [DB.BuiltInCategory.OST_Walls], True))
    specs.append((WALL_PARAM, True, [DB.BuiltInCategory.OST_Walls], True))  # per wall
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


WALL_PARAM = "EA_Muro_Acero"  # a wall's own settings and drawing (JSON of TYPE_PARAMS)
WALL_KEYS = ("EA_Muro_Corte", "EA_Muro_Horizontal", "EA_Muro_Malla")  # walls only: vertical bars sketched on the wall's cut (JSON list)


def read_type_config(column_type):
    """{param name: text} of a column type - or of one wall (Acero Muro:
    each wall keeps its own, see WALL_PARAM); blanks when unset/unbound."""
    if isinstance(column_type, DB.Wall):
        p = column_type.LookupParameter(WALL_PARAM)
        try:
            data = json.loads(p.AsString() or u"{}") if p is not None else {}
        except ValueError:
            data = {}
        return dict((name, data.get(name) or u"") for name in TYPE_PARAMS + WALL_KEYS)
    config = {}
    for name in TYPE_PARAMS:
        p = column_type.LookupParameter(name)
        config[name] = (p.AsString() or u"") if p is not None else u""
    return config


def write_type_config(column_type, config):
    if isinstance(column_type, DB.Wall):
        merged = read_type_config(column_type)
        merged.update(dict((k, v) for k, v in config.items() if k in TYPE_PARAMS + WALL_KEYS))
        p = column_type.LookupParameter(WALL_PARAM)
        if p is not None and not p.IsReadOnly:
            p.Set(json.dumps(merged, ensure_ascii=False))
        return
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
        joint = spec.parse_joint(nucleus)
        if nucleus and joint is None:
            raise spec.SpecError(u"Estribos en nucleo: valor no valido ({})".format(nucleus))
        self.joint_count = joint[1] if joint and joint[0] == "count" else None
        self.joint_spacing_m = (_float_cm(nucleus, u"Espaciamiento en nucleo", 3, 30)
                                if joint and joint[0] == "spacing" else None)
        self.design = spec.design_from_text(config.get("EA_Seccion_Armado"))
        self.bar_type_names = read_bar_type_names(config.get("EA_Barras_Tipo"))
        izaje = spec.read_json_setting(config.get("EA_Izaje"))
        self.izaje, self.izaje_h = None, 0.0
        if izaje.get("on", True) and (izaje.get("dist") or u"").strip() and float(izaje.get("h") or 0) > 0:
            self.izaje = StirrupFamily(izaje.get("d") or u'3/8"', izaje["dist"] + u", rto@1",
                                       u"Estribo de izaje", izaje.get("type"))
            self.izaje_dist = izaje["dist"]
            self.izaje_h = float(izaje["h"])
        ends = spec.read_json_setting(config.get("EA_Barra_Extremos"))
        anchor = u"{}".format(ends.get("anchor") or u"").strip()
        self.anchor_m = float(anchor) if anchor else None  # None: to the footing's bottom
        self.leg_bottom_cm = dict((k, float(v)) for k, v in (ends.get("bot") or {}).items() if v)
        self.leg_top_cm = dict((k, float(v)) for k, v in (ends.get("top") or {}).items() if v)
        # (2026-11-03) the top bars into a sloped beam over the column, bent along
        # it (a ridge column: each bar along the water on its side); off: as before
        self.slope_top = bool(ends.get("slope_top"))
        self.dir_bottom = ends.get("dir_bot") or spec.LEG_OUT
        self.dir_top = ends.get("dir_top") or spec.LEG_IN
        if not require_design:
            return
        if not (self.design and self.design["bars"]):
            raise spec.SpecError(u"dibuja las barras longitudinales de la seccion (o usa Automatico)")
        if not self.design["stirrups"] and not self.design.get("circ"):
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
    """A stirrup as drawn, its cover kept: Revit's shape lays the leg at the
    hooks' corner past the drawn outline (beside the other leg); that side
    is drawn in by as much and the stirrup made again, so no leg is out of
    the cover (`_create_stirrup_once` does the making)."""
    rebar = _create_stirrup_once(doc, shapes, shape_name, column, bar_type, hook, hooks, key, curves,
                                 orient_start, orient_end, normal)
    try:
        fixed = _drawn_in(doc, rebar, curves, normal or DB.XYZ.BasisZ)
    except Exception:
        fixed = None
    if fixed is None:
        return rebar
    doc.Delete(rebar.Id)
    return _create_stirrup_once(doc, shapes, shape_name, column, bar_type, hook, hooks, key, fixed,
                                orient_start, orient_end, normal)


def _drawn_in(doc, rebar, curves, normal, tol=1e-4):
    """The curves with each side the made stirrup runs past moved in by
    that much (a List[Curve]); None when it keeps to them."""
    from Autodesk.Revit.DB.Structure import MultiplanarOption
    doc.Regenerate()
    # the bar as made, hooks and bends included: with the hooks suppressed
    # the side at the hooks reads half a diameter past the drawing even when
    # the stirrup keeps its cover, and it was drawn in for nothing
    made = list(rebar.GetCenterlineCurves(False, False, False, MultiplanarOption.IncludeOnlyPlanarCurves, 0))
    drawn = list(curves)
    if not made or not drawn:
        return None
    longest = max(drawn, key=lambda c: c.Length)
    u = (longest.GetEndPoint(1) - longest.GetEndPoint(0)).Normalize()
    v = normal.CrossProduct(u).Normalize()
    o = drawn[0].GetEndPoint(0)
    pts = [c.GetEndPoint(k) for c in drawn for k in (0, 1)]
    got = [c.GetEndPoint(k) for c in made for k in (0, 1)]
    moves = []
    for axis in (u, v):
        a = [(p - o).DotProduct(axis) for p in pts]
        b = [(p - o).DotProduct(axis) for p in got]
        lo, hi = min(a), max(a)
        if min(b) < lo - tol:
            moves.append((axis, lo, lo - min(b)))  # that side moved in (up the axis)
        if max(b) > hi + tol:
            moves.append((axis, hi, -(max(b) - hi)))
    if not moves:
        return None

    def moved(p):
        q = p
        for axis, side, d in moves:
            if abs((p - o).DotProduct(axis) - side) < tol:
                q = q + axis * d
        return q
    return List[DB.Curve]([DB.Line.CreateBound(moved(c.GetEndPoint(0)), moved(c.GetEndPoint(1))) for c in drawn])


def _create_stirrup_once(doc, shapes, shape_name, column, bar_type, hook, hooks, key, curves,
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
        if isinstance(column, DB.Wall):
            self._wall(column)
            return
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
        curved = [c for c in loops[0] if not isinstance(c, DB.Line)]
        self.is_circle = False
        self.radius_m = None
        if curved:
            # a circular column (its edge one or more arcs of one circle):
            # the outline as a 48-gon, the circle kept for the steel
            arcs = [c for c in curved if isinstance(c, DB.Arc)]
            if len(arcs) != len(list(loops[0])) or any(
                    not a.Center.IsAlmostEqualTo(arcs[0].Center, 1e-4) or abs(a.Radius - arcs[0].Radius) > 1e-4
                    for a in arcs):
                raise spec.SpecError(u"seccion con bordes curvos (no soportada)")
            centre, radius = arcs[0].Center, arcs[0].Radius
            outline = [DB.XYZ(centre.X + radius * math.cos(2 * math.pi * k / 48),
                              centre.Y + radius * math.sin(2 * math.pi * k / 48), centre.Z) for k in range(48)]
            self.is_circle = True
            self.radius_m = radius * FT
        for curve in ([] if curved else loops[0]):
            outline.append(curve.GetEndPoint(0))
        z_bottom = bottom.Origin.Z
        height = z_top - z_bottom
        xs = [p.X for p in outline]
        ys = [p.Y for p in outline]
        cx, cy = (max(xs) + min(xs)) / 2.0, (max(ys) + min(ys)) / 2.0
        polygon_ft = [(p.X - cx, p.Y - cy) for p in outline]
        area = abs(spec.polygon_signed_area(polygon_ft))
        if self.is_circle:
            area = math.pi * (self.radius_m / FT) ** 2
        if height <= 0 or abs(solid.Volume - area * height) > 0.02 * solid.Volume:
            raise spec.SpecError(u"la seccion cambia con la altura (no soportada)")

        self.transform = t
        self.center = (cx, cy)
        self.flip = (1.0, 1.0)
        if getattr(column, "Mirrored", False) and not self.is_circle:
            # a mirrored column: its section is the family's one mirrored. The
            # steel is drawn on the family's: placed with that mirror (x or y)
            sym = _symbol_polygon(column.Symbol)
            if sym is not None:
                for f in ((-1.0, 1.0), (1.0, -1.0), (-1.0, -1.0), (1.0, 1.0)):
                    if _same_polygon([(f[0] * x, f[1] * y) for x, y in sym], polygon_ft):
                        self.flip, polygon_ft = f, sym
                        break
        self.polygon_m = [(x * FT, y * FT) for x, y in polygon_ft]
        self.b = max(xs) - min(xs)
        self.h = max(ys) - min(ys)
        self.is_rectangle = len(polygon_ft) == 4 and abs(area - self.b * self.h) < 1e-4 * self.b * self.h + 1e-6
        self.z_bottom = t.OfPoint(DB.XYZ(cx, cy, z_bottom)).Z
        self.z_top = t.OfPoint(DB.XYZ(cx, cy, z_top)).Z

    def _wall(self, wall):
        self.is_circle, self.radius_m = False, None
        self._wall_outline(wall)

    def _wall_outline(self, wall):
        """A vertical wall of any shape in plan (straight, curved, L, ...):
        its section is the outline of its bottom face (arcs as short
        segments), x along the wall when it is straight. Openings and
        changes of thickness with the height are not modeled."""
        solids = list(_solids(wall.get_Geometry(DB.Options())))
        if not solids:
            raise spec.SpecError(u"muro sin geometria")
        solid = max(solids, key=lambda x: x.Volume)
        bottom, z_top = None, None
        for face in solid.Faces:
            if not isinstance(face, DB.PlanarFace):
                continue
            if face.FaceNormal.Z < -0.999 and (bottom is None or face.Area > bottom.Area):
                bottom = face
            elif face.FaceNormal.Z > 0.999:
                z_top = face.Origin.Z if z_top is None else max(z_top, face.Origin.Z)
        if bottom is None:
            raise spec.SpecError(u"muro sin cara inferior horizontal (no soportado)")
        loops = list(bottom.GetEdgesAsCurveLoops())
        loop = max(loops, key=lambda lp: sum(c.Length for c in lp))  # the outer one
        world = []
        for curve in loop:
            pts = list(curve.Tessellate())
            for q in pts[:-1]:
                if not world or q.DistanceTo(world[-1]) > 1e-4:
                    world.append(q)
        curve = getattr(wall.Location, "Curve", None)
        if isinstance(curve, DB.Line):
            direction = curve.Direction
            direction = DB.XYZ(direction.X, direction.Y, 0.0).Normalize()
        else:
            direction = DB.XYZ.BasisX
        bb = wall.get_BoundingBox(None)
        t = DB.Transform.Identity
        t.Origin = DB.XYZ((bb.Min.X + bb.Max.X) / 2.0, (bb.Min.Y + bb.Max.Y) / 2.0, 0.0)
        t.BasisX = direction
        t.BasisY = DB.XYZ.BasisZ.CrossProduct(direction)
        t.BasisZ = DB.XYZ.BasisZ
        inverse = t.Inverse
        local = [inverse.OfPoint(q) for q in world]
        xs = [q.X for q in local]
        ys = [q.Y for q in local]
        cx, cy = (max(xs) + min(xs)) / 2.0, (max(ys) + min(ys)) / 2.0
        polygon_ft = [(q.X - cx, q.Y - cy) for q in local]
        self.transform = t
        self.center = (cx, cy)
        self.polygon_m = [(x * FT, y * FT) for x, y in polygon_ft]
        self.b = max(xs) - min(xs)
        self.h = max(ys) - min(ys)
        area = abs(spec.polygon_signed_area(polygon_ft))
        self.is_rectangle = len(polygon_ft) == 4 and abs(area - self.b * self.h) < 1e-4 * self.b * self.h + 1e-6
        self.z_bottom = bottom.Origin.Z
        self.z_top = z_top if z_top is not None else bb.Max.Z

    def point_m(self, x_m, y_m, z_world):
        """World point at a local section offset (meters, from the section
        center) and world elevation z (ft)."""
        fx, fy = getattr(self, "flip", (1.0, 1.0))
        p = self.transform.OfPoint(
            DB.XYZ(self.center[0] + fx * x_m / FT, self.center[1] + fy * y_m / FT, 0.0)
        )
        return DB.XYZ(p.X, p.Y, z_world)

    def local_m(self, point):
        """(x, y) meters of a world point in the section's frame - the
        inverse of point_m, mirror included (a mirrored column's steel and
        polygon are the family's)."""
        fx, fy = getattr(self, "flip", (1.0, 1.0))
        q = self.transform.Inverse.OfPoint(point)
        return (fx * (q.X - self.center[0]) * FT, fy * (q.Y - self.center[1]) * FT)


def _symbol_polygon(symbol):
    """The section of a column family type (its bottom face, centered), ft."""
    try:
        solids = list(_solids(symbol.get_Geometry(DB.Options())))
    except Exception:
        return None
    if not solids:
        return None
    solid = max(solids, key=lambda s: s.Volume)
    faces = [f for f in solid.Faces if isinstance(f, DB.PlanarFace) and f.FaceNormal.Z < -0.999]
    if not faces:
        return None
    loop = list(max(faces, key=lambda f: f.Area).GetEdgesAsCurveLoops())[0]
    pts = [c.GetEndPoint(0) for c in loop]
    xs, ys = [p.X for p in pts], [p.Y for p in pts]
    cx, cy = (max(xs) + min(xs)) / 2.0, (max(ys) + min(ys)) / 2.0
    return [(p.X - cx, p.Y - cy) for p in pts]


def _same_polygon(a, b, tol=1e-4):
    """The same vertex set (any start, any direction)."""
    if len(a) != len(b):
        return False
    return all(any(abs(p[0] - q[0]) < tol and abs(p[1] - q[1]) < tol for q in b) for p in a)


def is_columneta(column):
    """A columneta (confining column of a masonry wall): its type or family
    named so. Its stirrups go from the base up only, the last under the
    beam over it, none in the joint (2026-11-03, with the user)."""
    try:
        symbol = column.Document.GetElement(column.GetTypeId())
        names = u"{} {}".format(element_name(symbol), getattr(symbol, "FamilyName", u"") or u"").upper()
    except Exception:
        return False
    return any(w in names for w in COLUMNETA_WORDS)


NUCLEUS_WORDS = (u"NUDO", u"NUCLEO", u"NÚCLEO")
NUCLEUS_MAX_M = 1.0  # a column piece this short standing on a column is its joint


def is_nucleus(doc, column, section=None):
    """True when the element is a column's joint ("nucleo" / "nudo")
    modeled apart, over the column: its ELEMENTO (or Comentarios) parameter
    says so, or - without that - it is a column piece of at most
    NUCLEUS_MAX_M standing on another column. Its whole height is joint:
    no clear height of its own (see clear_top)."""
    if column.Category is None or column.Category.Id.IntegerValue != int(DB.BuiltInCategory.OST_StructuralColumns):
        return False
    # its type or family named as a joint ('SCL_NUDO COLUMNA ...') is one,
    # whatever ELEMENTO says (naming tools fill it with 'Columna de concreto')
    try:
        symbol = column.Document.GetElement(column.GetTypeId())
        names = u"{} {}".format(element_name(symbol), getattr(symbol, "FamilyName", u"") or u"").upper()
        if any(w in names for w in NUCLEUS_WORDS):
            return True
    except Exception:
        pass
    said = None
    for name in (u"ELEMENTO", u"Comentarios", u"Comments"):
        p = column.LookupParameter(name)
        text = (p.AsString() or u"") if p is not None and p.StorageType == DB.StorageType.String else u""
        if text.strip():
            upper = text.upper()
            if any(w in upper for w in NUCLEUS_WORDS):
                return True
            if name == u"ELEMENTO":
                said = text  # it names what it is, and is no joint
    if said is not None:
        return False
    try:
        section = section or Section(column)
    except Exception:
        return False
    if (section.z_top - section.z_bottom) * FT > NUCLEUS_MAX_M + 1e-6:
        return False
    base = section.point_m(0.0, 0.0, section.z_bottom - 0.05 / FT)
    outline = DB.Outline(DB.XYZ(base.X - 0.05, base.Y - 0.05, base.Z - 0.02),
                         DB.XYZ(base.X + 0.05, base.Y + 0.05, base.Z + 0.02))
    for e in (DB.FilteredElementCollector(doc).OfCategoryId(column.Category.Id)
              .WhereElementIsNotElementType().WherePasses(DB.BoundingBoxIntersectsFilter(outline))):
        if e.Id != column.Id:
            return True
    return False


def clear_top(doc, column, section):
    """(Rule agreed with the user, see Acero.pushbutton/REGLAS_ACERO.md.)
    Top of the column's clear height, where its stirrup distribution
    ends: the underside of the deepest beam framing into its top (where
    beams of different depth meet, always the deepest one), else the
    underside of the slab over it, else the column top. From there up to
    the column top is the joint. A joint modeled apart (is_nucleus) is all
    joint: its clear height ends at its base."""
    if is_nucleus(doc, column, section):
        return section.z_bottom
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

    reaches = section.z_top - TOP_MEMBER_REACH_M / FT

    def underside(element):
        """Its underside where it meets the column - only a member reaching
        the column's top (a beam framing in at mid-height, a solera or a
        dintel, is no top: 2026-11-03)."""
        low, high, failed = None, None, False
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
                    high = p.Z if high is None else max(high, p.Z)
        if low is None and failed:
            ebb = element.get_BoundingBox(None)
            low, high = (ebb.Min.Z, ebb.Max.Z) if ebb is not None else (None, None)
        if high is not None and high < reaches:
            return None
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


def set_host_cover(doc, element, cover_m):
    """The element's own rebar cover in Revit (all its faces) set to the
    cover the bars were laid with, so Revit shows the same one (a cover type
    'Recubrimiento X cm', made once). The bars made from curves don't move."""
    from Autodesk.Revit.DB.Structure import RebarHostData, RebarCoverType
    try:
        data = RebarHostData.GetRebarHostData(element)
        if data is None or not cover_m:
            return False
        ft = cover_m / FT
        current = data.GetCommonCoverType()
        if current is not None and abs(current.CoverDistance - ft) < 1e-4:
            return False
        found = None
        for ct in DB.FilteredElementCollector(doc).OfClass(RebarCoverType):
            if abs(ct.CoverDistance - ft) < 1e-4:
                found = ct
                break
        if found is None:
            found = RebarCoverType.Create(doc, u"Recubrimiento {:g} cm".format(round(cover_m * 100.0, 1)), ft)
        data.SetCommonCoverType(found)
        return True
    except Exception:
        return False


def _tag(rebar, column):
    p = rebar.LookupParameter(ORIGIN_PARAM)
    if p is not None and not p.IsReadOnly:
        p.Set(str(element_id_value(column.Id)))


def foundation_below(doc, column, section):
    """Depth (m) of the foundation the column stands on - from the column
    base down to its bottom -, or None when it stands on nothing to anchor
    in: a footing / cimiento / losa de cimentacion, or (2026-11-03) a beam
    or a low wall right under it - a viga de cimentacion, a sobrecimiento,
    a columneta's beam - of at most ANCHOR_HOST_MAX_M."""
    probe = section.point_m(0.0, 0.0, section.z_bottom - 0.05 / FT)
    outline = DB.Outline(DB.XYZ(probe.X - 0.05, probe.Y - 0.05, probe.Z - 0.05),
                         DB.XYZ(probe.X + 0.05, probe.Y + 0.05, probe.Z + 0.05))
    cats = List[DB.BuiltInCategory]([DB.BuiltInCategory.OST_StructuralFoundation,
                                     DB.BuiltInCategory.OST_StructuralFraming, DB.BuiltInCategory.OST_Walls])
    deepest = None
    for element in (DB.FilteredElementCollector(doc).WherePasses(DB.ElementMulticategoryFilter(cats))
                    .WhereElementIsNotElementType()
                    .WherePasses(DB.BoundingBoxIntersectsFilter(outline))):
        bb = element.get_BoundingBox(None)
        if bb is None or bb.Max.Z < section.z_bottom - 0.1 / FT:
            continue
        foundation = element.Category.Id.IntegerValue == int(DB.BuiltInCategory.OST_StructuralFoundation)
        if not foundation and (bb.Max.Z - bb.Min.Z) * FT > ANCHOR_HOST_MAX_M + 1e-6:
            continue  # a tall wall / deep member under it: no foundation to anchor in
        depth = (section.z_bottom - bb.Min.Z) * FT
        deepest = depth if deepest is None else max(deepest, depth)
    return deepest


ANCHOR_HOST_MAX_M = 1.0  # a beam or wall this deep at most under a column is its foundation


def column_above(doc, column, section):
    """True when another element of its category (column on column, wall on
    wall) stands on this one (its bars go on up)."""
    top = section.point_m(0.0, 0.0, section.z_top + 0.05 / FT)
    outline = DB.Outline(DB.XYZ(top.X - 0.05, top.Y - 0.05, top.Z - 0.02),
                         DB.XYZ(top.X + 0.05, top.Y + 0.05, top.Z + 0.02))
    for element in (DB.FilteredElementCollector(doc)
                    .OfCategoryId(column.Category.Id)
                    .WhereElementIsNotElementType()
                    .WherePasses(DB.BoundingBoxIntersectsFilter(outline))):
        if element.Id != column.Id:
            return True
    return False


REVIT_MAX_BAR_M = 10.95  # Revit's longest bar (36 ft), anchorage and legs included
FOUNDATION_COVER_M = 0.075  # on the ground: the bar stops over the bottom mesh


SLOPE_MIN = 0.02  # a beam rising less than this (per unit length) is level


def sloped_beams_over(doc, column, section, reach_m=0.6):
    """The sloped beams over the column's top (a roof's): each
    {"dir": unit plan vector away from the column (world), "rise": its rise
    per unit plan length that way (+ up, - down), "plane": (normal, origin)
    of its top face}. A ridge column: one each side."""
    top = section.point_m(0.0, 0.0, section.z_top)
    r = reach_m / FT
    outline = DB.Outline(DB.XYZ(top.X - r, top.Y - r, top.Z - r), DB.XYZ(top.X + r, top.Y + r, top.Z + 2 * r))
    found = []
    for e in (DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_StructuralFraming)
              .WhereElementIsNotElementType().WherePasses(DB.BoundingBoxIntersectsFilter(outline))):
        curve = getattr(getattr(e, "Location", None), "Curve", None)
        if not isinstance(curve, DB.Line):
            continue
        p0, p1 = curve.GetEndPoint(0), curve.GetEndPoint(1)
        plan = math.hypot(p1.X - p0.X, p1.Y - p0.Y)
        if plan < 1e-6 or abs(p1.Z - p0.Z) / plan < SLOPE_MIN:
            continue  # vertical or level
        near, far = (p0, p1) if math.hypot(p0.X - top.X, p0.Y - top.Y) <= math.hypot(p1.X - top.X, p1.Y - top.Y) \
            else (p1, p0)
        if math.hypot(near.X - top.X, near.Y - top.Y) * FT > reach_m:
            continue  # its end isn't at this column
        direction = ((far.X - near.X) / plan, (far.Y - near.Y) / plan)
        best = None
        for solid in _solids(e.get_Geometry(DB.Options())):
            for face in solid.Faces:
                if isinstance(face, DB.PlanarFace) and face.FaceNormal.Z > 0.3 and (best is None or face.Area > best.Area):
                    best = face
        if best is None:
            continue
        found.append({"dir": direction, "rise": (far.Z - near.Z) / plan, "plane": (best.FaceNormal, best.Origin)})
    return found


def slope_top_end(doc, column, section, x, y, d, cover):
    """(top_drop m, local plan direction, rise per plan length) of a bar's
    top in the sloped beam over the column: the beam on the bar's side (a
    ridge column: each bar the water it stands under), the bar up to under
    its top face less the cover, then along it. None when no sloped beam."""
    beams = sloped_beams_over(doc, column, section)
    if not beams:
        return None
    centre = section.point_m(0.0, 0.0, section.z_top)
    p = section.point_m(x, y, section.z_top)
    off = (p.X - centre.X, p.Y - centre.Y)
    beam = max(beams, key=lambda b: b["dir"][0] * off[0] + b["dir"][1] * off[1])
    n, o = beam["plane"]
    z_face = o.Z - (n.X * (p.X - o.X) + n.Y * (p.Y - o.Y)) / n.Z  # the beam's top over the bar (ft)
    cos = 1.0 / math.sqrt(1.0 + beam["rise"] ** 2)
    z_end = z_face - (cover + d / 2.0) / cos / FT  # the cover square to the beam's face
    fx, fy = getattr(section, "flip", (1.0, 1.0))
    v = section.transform.Inverse.OfVector(DB.XYZ(beam["dir"][0], beam["dir"][1], 0.0))
    lx, ly = fx * v.X, fy * v.Y
    norm = math.hypot(lx, ly) or 1.0
    return (section.z_top - z_end) * FT, (lx / norm, ly / norm), beam["rise"]


def bar_ends(doc, column_spec, key, x, y, section, bottom_column, top_column):
    """`_bar_ends`, its top kept in the concrete over the bar itself: under
    a sloped roof the concrete ends at another height at each bar, and a bar
    taken to the stack's highest point stood out of the roof - it ends
    where the concrete over its own point ends, less the cover, its top leg
    bent where there is concrete at that level."""
    ends = _bar_ends(doc, column_spec, key, x, y, section, bottom_column, top_column)
    if ends.get("rise_top"):
        # bent along the sloped beam over it (the user's option): the bend kept,
        # the bar lowered as much as needed so the bar's top and its whole leg
        # keep the cover under the real concrete top (under a peak the beam's
        # plane, carried past it, stood higher than the concrete)
        try:
            return _lowered_under_top(doc, column_spec, key, x, y, section, top_column, ends)
        except Exception:
            return ends
    try:
        top_section = Section(top_column)
        run_top = _concrete_top(doc, section, x, y, section.z_bottom + 0.1 / FT)
    except Exception:
        return ends
    if run_top is None:
        return ends
    d = spec.BAR_DIAMETERS_MM[key] / 1000.0
    need = (top_section.z_top - run_top) * FT + column_spec.cover_m + d
    if need <= ends.get("top_drop", 0.0) + 0.005:
        return _level_leg_checked(doc, column_spec, key, x, y, section, top_column, ends)
    ends["top_drop"] = need
    if "leg_top" in ends:
        if ends.get("rise_top"):  # the leg along a sloped beam no longer starts there: level
            ends["leg_top"] = spec.leg_m(column_spec.leg_top_cm.get(key)) or ends["leg_top"]
            ends["rise_top"] = 0.0
        xs = [p[0] for p in section.polygon_m]
        ys = [p[1] for p in section.polygon_m]
        half_b, half_h = (max(xs) - min(xs)) / 2.0, (max(ys) - min(ys)) / 2.0
        z_leg = top_section.z_top - need / FT
        ends["dir_top"] = leg_toward_concrete(doc, top_section, top_column, x, y, half_b, half_h,
                                              ends["leg_top"], z_leg, axis=ends.get("dir_bottom"))
    return _level_leg_checked(doc, column_spec, key, x, y, section, top_column, ends)


def _level_leg_checked(doc, column_spec, key, x, y, section, top_column, ends):
    """A level top leg run under a sloped top keeps the cover all along it."""
    if not ends.get("leg_top") or not ends.get("dir_top"):
        return ends
    try:
        return _lowered_under_top(doc, column_spec, key, x, y, section, top_column, ends)
    except Exception:
        return ends


def _lowered_under_top(doc, column_spec, key, x, y, section, top_column, ends):
    """`ends` of a bar bent along a sloped beam, its top_drop grown until
    every point of its top and leg lies the cover (square to the slope)
    under the concrete over it."""
    top_section = Section(top_column)
    d = spec.BAR_DIAMETERS_MM[key] / 1000.0
    leg, rise = ends.get("leg_top", 0.0), ends.get("rise_top", 0.0)
    dx, dy = ends.get("dir_top") or (0.0, 0.0)
    z0 = top_section.z_top - ends.get("top_drop", 0.0) / FT  # the bar's top (world ft)
    near = _solids_near(doc, section.point_m(x, y, z0 - 0.5 / FT), leg + 0.3, 3.0)
    samples = []
    for k in range(6):
        t = k / 5.0
        px, py = x + dx * leg * t, y + dy * leg * t
        # a leg bent up the slope sits higher than the bar's top by its bend
        # (about two diameters, as Revit bends it)
        pz = z0 + (rise * t + (2.0 * d if rise and t > 0 else 0.0)) / FT
        top = _concrete_top(doc, section, px, py, pz - 0.4 / FT, 3.0, near)
        if top is None and rise and t > 0:
            # the leg along the slope runs out of the concrete (a bar beside the
            # sloped beam, not under it): a level leg where there is concrete
            ends["rise_top"] = 0.0
            ends["leg_top"] = spec.leg_m(column_spec.leg_top_cm.get(key)) or leg
            xs = [p[0] for p in section.polygon_m]
            ys = [p[1] for p in section.polygon_m]
            ends["dir_top"] = leg_toward_concrete(
                doc, top_section, top_column, x, y, (max(xs) - min(xs)) / 2.0, (max(ys) - min(ys)) / 2.0,
                ends["leg_top"], z0, axis=ends.get("dir_bottom"))
            return _lowered_under_top(doc, column_spec, key, x, y, section, top_column, ends)
        if top is not None:
            samples.append((leg * t, pz, top))
    # the cover is square to the concrete's top: on a slope it takes more height
    slope = rise / leg if leg else 0.0
    for (s0, _, t0), (s1, _, t1) in zip(samples, samples[1:]):
        if s1 - s0 > 1e-6:
            slope = max(slope, abs(t1 - t0) * FT / (s1 - s0))
    need_v = column_spec.cover_m * math.sqrt(1.0 + slope * slope) + d / 2.0
    worst = 0.0
    for _, pz, top in samples:
        worst = max(worst, (pz - (top - need_v / FT)) * FT)
    if worst > 0.002:
        ends["top_drop"] = ends.get("top_drop", 0.0) + worst
    if not ends.get("rise_top") and leg:
        # a level leg: the way (and, if no way holds it whole, the length)
        # where it stays in concrete with its cover at its height
        fit = _fit_level_leg(doc, column_spec, section, x, y, leg,
                             top_section.z_top - ends["top_drop"] / FT, ends.get("dir_bottom"))
        extra = 0.0
        while fit is None and extra < 0.40:
            # no way holds it there (over a column's top, beside the beam):
            # the bar a little lower, where the column holds the leg
            extra += 0.02
            fit = _fit_level_leg(doc, column_spec, section, x, y, leg,
                                 top_section.z_top - (ends["top_drop"] + extra) / FT, ends.get("dir_bottom"))
        if fit is not None:
            ends["top_drop"] += extra
            ends["dir_top"], ends["leg_top"] = fit
    return ends


def _fit_level_leg(doc, column_spec, section, x, y, leg, z_world, axis, step=0.01, shortest=0.10):
    """(direction, length) of a level top leg at z_world kept in concrete:
    every 2 cm of it and its tip plus the cover under at least the cover of
    concrete; the first way of spec.leg_candidates (along `axis` only - one
    plane with the bottom leg) holding it whole, else the one holding it
    longest (never under `shortest`); None if none holds it."""
    xs = [p[0] for p in section.polygon_m]
    ys = [p[1] for p in section.polygon_m]
    cands = spec.leg_candidates(x, y, (max(xs) - min(xs)) / 2.0, (max(ys) - min(ys)) / 2.0)
    if axis:
        cands = [v for v in cands if _parallel(v, axis)]
    cover = column_spec.cover_m
    near = _solids_near(doc, section.point_m(x, y, z_world - 0.3 / FT), leg + 0.3, 3.0)

    def holds(v, length):
        n = max(2, int(length / 0.02))
        for k in range(1, n + 1):
            for s in ((length * k / n),) + ((length + cover,) if k == n else ()):
                # concrete from the cover under it to the cover over it
                top = _concrete_top(doc, section, x + v[0] * s, y + v[1] * s, z_world - cover / FT, 3.0, near)
                if top is None or (top - z_world) * FT < cover:
                    return False
        return True
    best = None
    for v in cands:
        if holds(v, leg):
            return v, leg
        length = leg - step
        while length >= shortest:
            if holds(v, length):
                if best is None or length > best[1]:
                    best = (v, length)
                break
            length -= step
    return best


_TOP_CATS = (DB.BuiltInCategory.OST_StructuralColumns, DB.BuiltInCategory.OST_StructuralFraming,
             DB.BuiltInCategory.OST_Floors)


def _solids_near(doc, point, reach_m=1.0, up_m=12.0):
    """The concrete solids (columns, beams, slabs) around a world point."""
    r = reach_m / FT
    outline = DB.Outline(DB.XYZ(point.X - r, point.Y - r, point.Z - r),
                         DB.XYZ(point.X + r, point.Y + r, point.Z + up_m / FT))
    cats = List[DB.BuiltInCategory](list(_TOP_CATS))
    out = []
    for e in (DB.FilteredElementCollector(doc).WherePasses(DB.ElementMulticategoryFilter(cats))
              .WhereElementIsNotElementType().WherePasses(DB.BoundingBoxIntersectsFilter(outline))):
        try:
            out += list(_solids(e.get_Geometry(DB.Options())))
        except Exception:
            continue
    return out


def _concrete_top(doc, section, x, y, z_start, up_m=12.0, solids=None):
    """World z (ft) where the concrete over the bar at local (x, y) ends,
    going up from z_start through columns, beams and slabs; None if the
    point isn't in concrete. `solids`: the concrete around, read once."""
    a = section.point_m(x, y, z_start)
    b = DB.XYZ(a.X, a.Y, a.Z + up_m / FT)
    if solids is None:
        solids = _solids_near(doc, a, 0.01, up_m)
    curve = DB.Line.CreateBound(a, b)
    segs = []
    for s in solids:
        try:
            hit = s.IntersectWithCurve(curve, DB.SolidCurveIntersectionOptions())
            for k in range(hit.SegmentCount):
                g = hit.GetCurveSegment(k)
                segs.append(sorted((g.GetEndPoint(0).Z, g.GetEndPoint(1).Z)))
        except Exception:
            continue
    segs.sort()
    top = None
    for z0, z1 in segs:
        if top is None:
            if z0 > a.Z + 0.02:
                return None
            top = z1
        elif z0 <= top + 0.003:
            top = max(top, z1)
    return top


def _bar_ends(doc, column_spec, key, x, y, section, bottom_column, top_column):
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
    leg = spec.leg_m(column_spec.leg_top_cm.get(key))
    top_section = None
    if leg and not column_above(doc, top_column, Section(top_column)):
        ends["top_drop"] = column_spec.cover_m + d
        ends["leg_top"] = leg
        top_section = Section(top_column)
        slope = (slope_top_end(doc, top_column, top_section, x, y, d, column_spec.cover_m)
                 if getattr(column_spec, "slope_top", False) else None)
        if slope is not None:
            # into the sloped beam over it, bent along it (2026-11-03): the bar up
            # to under the beam's top, the leg parallel to the beam - its plan
            # length and rise -, the bottom leg in the same plane
            drop, direction, rise = slope
            cos = 1.0 / math.sqrt(1.0 + rise * rise)
            ends["top_drop"] = drop
            ends["leg_top"] = leg * cos
            ends["rise_top"] = leg * cos * rise
            ends["dir_top"] = direction
            if "leg_bottom" in ends:
                # the bottom leg as on every column (its own best way, 2026-11-03);
                # not along the slope: the top leg in its plane, level under the beam's top
                b = leg_pair(doc, x, y, half_b, half_h,
                             (section, bottom_column, ends["leg_bottom"],
                              section.z_bottom - (ends["anchor"] - d) / FT),
                             (top_section, top_column, leg,
                              top_section.z_top - (column_spec.cover_m + d) / FT))[0]
                ends["dir_bottom"] = b
                if not _parallel(b, direction):
                    ends["leg_top"] = leg
                    ends["rise_top"] = 0.0
                    ends["dir_top"] = leg_toward_concrete(
                        doc, top_section, top_column, x, y, half_b, half_h, leg,
                        top_section.z_top - drop / FT, axis=b)
            return ends
    bot =((section, bottom_column, ends["leg_bottom"], section.z_bottom - (ends["anchor"] - d) / FT)
           if "leg_bottom" in ends else None)
    top = ((top_section, top_column, ends["leg_top"], top_section.z_top - (column_spec.cover_m + d) / FT)
           if "leg_top" in ends else None)
    dir_bottom, dir_top = leg_pair(doc, x, y, half_b, half_h, bot, top)
    if dir_bottom:
        ends["dir_bottom"] = dir_bottom
    if dir_top:
        ends["dir_top"] = dir_top
    return ends


def leg_pair(doc, x, y, half_b, half_h, bot, top):
    """(bottom, top) directions of a bar's end legs; `bot` / `top`
    (section, column, leg m, z world ft) or None. Each leg stays in concrete
    (leg_toward_concrete) and both lie in one plane - bent on site in one
    plane, and Revit makes no bar out of a plane: the first pair, by the
    bottom leg's preference, where both fit; else the bottom's best and the
    top one in its plane."""
    if bot and top:
        # the bottom leg its own best way, as it always bent (out by the nearest
        # face when there is room); the top one in its plane (user, 2026-11-03)
        b = leg_toward_concrete(doc, bot[0], bot[1], x, y, half_b, half_h, bot[2], bot[3])
        return b, leg_toward_concrete(doc, top[0], top[1], x, y, half_b, half_h, top[2], top[3], axis=b)
    if bot:
        return leg_toward_concrete(doc, bot[0], bot[1], x, y, half_b, half_h, bot[2], bot[3]), None
    if top:
        return None, leg_toward_concrete(doc, top[0], top[1], x, y, half_b, half_h, top[2], top[3])
    return None, None


LEG_CONCRETE = (DB.BuiltInCategory.OST_StructuralFoundation, DB.BuiltInCategory.OST_Floors,
                DB.BuiltInCategory.OST_StructuralFraming, DB.BuiltInCategory.OST_Walls)


def leg_toward_concrete(doc, section, column, x, y, half_b, half_h, leg, z_world, axis=None, all_fits=False):
    """The rule agreed with the user (2026-11-03): a bar's end leg never
    leaves the concrete. The whole leg - its middle, its tip and the tip
    plus the cover (spec.leg_samples) - must lie in the column itself or in
    a footing, slab, beam or wall at the leg's level; the first direction
    of spec.leg_candidates that fits is taken (out by the nearest face, out
    by the other one, ..., into the column). None fits: into the column.
    `axis` (the bar's other leg, a unit vector): only along that axis,
    either way - a bar's two legs in one plane, as bent on site (Revit
    makes no bar out of a plane). Returns the unit local vector
    (`all_fits`: the list of every one that fits, best first)."""
    candidates = spec.leg_candidates(x, y, half_b, half_h)
    if axis:
        candidates = [v for v in candidates if _parallel(v, axis)]
    reach = (leg + 0.05) / FT
    centre = section.point_m(x, y, z_world)
    outline = DB.Outline(DB.XYZ(centre.X - reach, centre.Y - reach, centre.Z - 0.05 / FT),
                         DB.XYZ(centre.X + reach, centre.Y + reach, centre.Z + 0.05 / FT))
    cats = List[DB.BuiltInCategory](list(LEG_CONCRETE) + [DB.BuiltInCategory.OST_StructuralColumns])
    solids = []  # the concrete around the bar end, read once
    for e in (DB.FilteredElementCollector(doc).WherePasses(DB.ElementMulticategoryFilter(cats))
              .WhereElementIsNotElementType().WherePasses(DB.BoundingBoxIntersectsFilter(outline))):
        try:  # an element whose geometry Revit can't read is left out, never the whole leg
            solids += [s for s in _solids(e.get_Geometry(DB.Options())) if s.Volume > 1e-9]
        except Exception:
            continue

    def in_concrete(p):
        for s in solids:
            try:
                if _inside(s, p):
                    return True
            except Exception:  # Revit's own errors on odd solids ("Sequence contains no elements")
                continue
        return False

    fits = [v for v in candidates
            if all(in_concrete(section.point_m(x + v[0] * t, y + v[1] * t, z_world)) for t in spec.leg_samples(leg))]
    if all_fits:
        return fits  # every direction that stays in concrete, best first
    return fits[0] if fits else candidates[-1]


def _parallel(a, b):
    return abs(a[0] * b[1] - a[1] * b[0]) < 1e-6


def _inside(solid, point):
    """Point in a closed solid: a ray straight up crosses it an odd number of times."""
    ray = DB.Line.CreateBound(point, point + DB.XYZ(0.0, 0.0, 1000.0))
    hits = solid.IntersectWithCurve(ray, DB.SolidCurveIntersectionOptions())
    return hits.SegmentCount > 0 and any(
        hits.GetCurveSegment(k).GetEndPoint(0).DistanceTo(point) < 1e-6 for k in range(hits.SegmentCount))


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


def _runs(family, joint_spacing_m, section, z_clear_top, z_start=None, joint_count=None, bottom_up=False):
    """(z_start_ft, count, spacing_ft, side) runs of one stirrup family: its
    clear-height distribution, one rebar set per zone and end as written
    (spec.stirrup_sets: '1@.05' a single stirrup, '5@.10' a set of 5 at
    0.10, the rest one set in the middle), plus the joint when EA_Nucleo_cm
    is set. side is -1 for the top end zones: the way stirrups set at one
    height stack (towards the middle, so the first one keeps its distance
    from each end). `bottom_up` (a columneta): the distribution from the
    base only, up to under the beam (spec.cantilever_sets)."""
    z_start = section.z_bottom if z_start is None else z_start
    clear_m = (z_clear_top - z_start) * FT
    sets = (spec.cantilever_sets(clear_m, family.zones, family.rest) if bottom_up
            else spec.stirrup_sets(clear_m, family.zones, family.rest))
    runs = [] if clear_m < 0.05 else [  # a joint modeled apart has no clear height
        (z_start + start / FT, n, spacing / FT, side)
        for start, n, spacing, _, side in sets
    ]
    if (joint_count or joint_spacing_m) and section.z_top - z_clear_top > 0.1 / FT:
        # a count splits the joint in equal parts (EA_Nucleo_cm "3 est")
        joint = (spec.joint_positions_count((section.z_top - z_clear_top) * FT, joint_count) if joint_count
                 else spec.joint_positions((section.z_top - z_clear_top) * FT, joint_spacing_m))
        for start, n, spacing in spec.group_runs(joint):
            runs.append((z_clear_top + start / FT, n, spacing / FT, 1))
    return runs + _lap_extra(runs, getattr(section, "lap_zones", None) or [])


LAP_ZONES = {}  # column id -> the laps of its stack [(z0_ft, z1_ft)], while generate_stack builds it
LAP_STIRRUP_M = 0.10  # E.060 (agreed with the user): stirrups at most this far apart along a lap


def _lap_extra(runs, zones):
    """Stirrups added along the bars' laps (`zones` [(z0_ft, z1_ft)]): one
    halfway between two stirrups farther apart than LAP_STIRRUP_M there -
    the rest at .20 becomes .10 over the lap, the rule unchanged elsewhere."""
    if not zones:
        return []
    zs = sorted(set(round(z0 + k * s, 6) for z0, n, s, _ in runs for k in range(n)))
    extra = []
    for a, b in zip(zs, zs[1:]):
        if (b - a) * FT > LAP_STIRRUP_M + 1e-6:
            mid = (a + b) / 2.0
            if any(lo - 1e-6 <= mid <= hi + 1e-6 for lo, hi in zones):
                extra.append(mid)
    return [(start, n, spacing, 1) for start, n, spacing in spec.group_runs(extra)]  # ft, like the runs


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


def seated_bars(doc, column_spec, bar_types, mark):
    """The drawing's bars with the corner ones seated in the stirrups'
    round bends (the bar type's own bend diameter, else 4 db), touching the
    stirrup's inner face instead of overlapping it - as in the beams
    (rebar_beams.seat_in_bends)."""
    import rebar_beams as rb_
    design = column_spec.design
    bars = design["bars"]
    for kind, poly, wrap, is_open in design.get("stirrups", []):
        if is_open:
            continue
        try:
            _, family = column_spec.family_of(kind)
            key = family.key
            loop = spec.clean_polyline(spec.stirrup_centerline(poly, design["bars"], key, wrap, False), closed=True)
        except Exception:
            continue
        ds = spec.BAR_DIAMETERS_MM[key] / 1000.0
        try:
            st = named_bar_type(doc, family.type_name, key) or _bar_type(bar_types, key, mark)
            bend = st.StirrupTieBendDiameter * FT or 4.0 * ds
        except Exception:
            bend = 4.0 * ds
        bars = rb_.seat_in_bends(bars, [loop], ds, bend)
    return bars


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
    section.lap_zones = LAP_ZONES.get(element_id_value(column.Id))  # its laps: stirrups at .10 there (_lap_extra)
    design = column_spec.design

    def bar_type_for(key):
        return _bar_type(bar_types, key, mark)

    delete_generated(doc, column)
    created = []

    height_m = (section.z_top - section.z_bottom) * FT
    for x, y, key in (seated_bars(doc, column_spec, bar_types, mark) if longitudinal else []):
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
    columneta = is_columneta(column)
    # Izaje: over a footing, from the column base up to izaje_h; the column's
    # own distribution starts there.
    izaje_h = column_spec.izaje_h if (column_spec.izaje and foundation_below(doc, column, section)) else 0.0
    z_start = section.z_bottom + izaje_h / FT
    circ = design.get("circ") if getattr(section, "is_circle", False) else None
    if circ:
        created += _circular_steel(doc, column, column_spec, section, circ, bar_type_for, z_clear_top, z_start)
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
        # (Rule changed with the user 2026-10-15.) The joint always has edge
        # stirrups - at EA_Nucleo_cm, else at their rest spacing ("rto") -,
        # modeled apart or inside the column alike; the confinement ones go
        # on into it at their "rto" when EA_Nucleo_cm is set.
        # (2026-11-03) "N est": the edge stirrups are N, splitting the joint
        # in equal parts; the confinement ones keep their "rto".
        # (2026-11-03) A columneta: its stirrups from the base up only, the
        # last under the beam, none in the joint unless EA_Nucleo_cm says so.
        joint_m, joint_n = column_spec.joint_spacing_m, None
        if kind == CONFINEMENT:
            joint_m = family.rest if (joint_m or column_spec.joint_count) else None
        elif column_spec.joint_count:
            joint_n = column_spec.joint_count
        elif not columneta:
            joint_m = joint_m or family.rest
        for z_set, n, spacing, side in _runs(family, joint_m, section, z_clear_top, z_start, joint_n,
                                             bottom_up=columneta):
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


def _free_form(doc, bar_type, host, points):
    """A free-form bar through world points (a hoop with its hooks, a
    spiral): Revit has no shape for them."""
    loop = DB.CurveLoop()
    for a, b in zip(points, points[1:]):
        if a.DistanceTo(b) > 1e-4:
            loop.Append(DB.Line.CreateBound(a, b))
    loops = List[DB.CurveLoop]()
    loops.Add(loop)
    result = clr.Reference[RebarFreeFormValidationResult]()
    rebar = Rebar.CreateFreeForm(doc, bar_type, host, loops, result)
    if rebar is None:
        raise spec.SpecError(u"Revit no acepto el estribo circular ({})".format(result.Value))
    return rebar


def _circular_steel(doc, column, column_spec, section, circ, bar_type_for, z_clear_top, z_start):
    """A circular column's edge steel at the cover: hoops (spec.hoop_path,
    closed with two 135-degree hooks) at the edge distribution, joint
    included, or one spiral (spec.spiral_path) over the clear height with
    its extra turns at both ends. Returns [(rebar, key, EDGE)]."""
    family = column_spec.edge
    key = family.key
    ds = spec.BAR_DIAMETERS_MM[key] / 1000.0
    radius = section.radius_m - column_spec.cover_m - ds / 2.0
    bar_type = named_bar_type(doc, family.type_name, key) or bar_type_for(key)
    created = []
    if circ.get("t") == spec.CIRC_SPIRAL:
        z0 = (z_start - section.z_bottom) * FT + column_spec.cover_m
        z1 = (section.z_top - section.z_bottom) * FT - column_spec.cover_m
        pts = spec.spiral_path(radius, z0, z1, circ.get("p") or spec.SPIRAL_PITCH_M, ds)
        world = [section.point_m(x, y, section.z_bottom + z / FT) for x, y, z in pts]
        rebar = _free_form(doc, bar_type, column, world)
        _tag(rebar, column)
        return [(rebar, key, EDGE)]
    plan = spec.hoop_path(radius, ds, max(6 * ds, 0.075), **spec.hoop_closing(column_spec.design["bars"], ds))
    joint_m = column_spec.joint_spacing_m or family.rest
    for z_set, n, spacing, side in _runs(family, joint_m, section, z_clear_top, z_start, column_spec.joint_count):
        for i in range(n):
            z = z_set + i * spacing
            rebar = _free_form(doc, bar_type, column, [section.point_m(x, y, z) for x, y in plan])
            _tag(rebar, column)
            created.append((rebar, key, EDGE))
    return created


# --- Stacked columns: continuous longitudinal bars, lap spliced -------------------

def joints_above(doc, columns):
    """The joints modeled apart (is_nucleus) standing on these columns -
    same axis, base on a column's top - not among them: generated with
    their column, its bars run whole through the joint (2026-11-03)."""
    have = set(element_id_value(c.Id) for c in columns)
    found = []
    for column in columns:
        try:
            section = Section(column)
        except Exception:
            continue
        top = section.point_m(0.0, 0.0, section.z_top + 0.05 / FT)
        outline = DB.Outline(DB.XYZ(top.X - 0.05, top.Y - 0.05, top.Z - 0.02),
                             DB.XYZ(top.X + 0.05, top.Y + 0.05, top.Z + 0.02))
        for e in (DB.FilteredElementCollector(doc).OfCategoryId(column.Category.Id)
                  .WhereElementIsNotElementType().WherePasses(DB.BoundingBoxIntersectsFilter(outline))):
            key = element_id_value(e.Id)
            if key not in have and is_nucleus(doc, e):
                have.add(key)
                found.append(e)
    return found


def column_stacks(columns, xy_tol_m=0.02, z_tol_m=0.10, same_type=True):
    """The columns grouped into stacks: same type, same axis and turned the
    same way, each standing on the one below (its base within `z_tol_m` of
    that one's top). A joint modeled apart (is_nucleus) of the same section
    joins the column under it whatever its type: the bars are one through
    the joint, as built. `same_type` False: only joints join (bars not
    continuous between stories). Returns [[column, ...] bottom up]; a lone
    column is a stack of one."""
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
        joint = None  # is_nucleus, asked once and only when needed
        for stack in stacks:
            last_column, last = stack[0][-1], stack[1]
            below = last.point_m(0.0, 0.0, last.z_top)
            if last_column.GetTypeId() == column.GetTypeId():
                kin = same_type
            elif (same_type and stack[0][0].GetTypeId() == column.GetTypeId()
                  and is_nucleus(column.Document, last_column, last)):
                kin = True  # the stack's column goes on over its joint (one bar through all)
            else:
                if joint is None:
                    joint = is_nucleus(column.Document, column, section)
                kin = joint and _same_polygon(section.polygon_m, last.polygon_m, 0.01)
            if (kin
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


def generate_stack(doc, stack, column_spec, bar_types, hooks, mark, shapes=None, splice=None, type_specs=None):
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
    through, with the share of its length there (see `record_weight`).
    `type_specs` {type id: ColumnSpec}: a column of another type in the
    stack (its joint modeled apart) takes its own type's stirrups; the
    bars are the stack's (column_spec)."""
    def own_spec(column):
        return (type_specs or {}).get(element_id_value(column.GetTypeId())) or column_spec

    for c in stack:  # Revit's own cover of each element: the one its bars are laid with
        set_host_cover(doc, c, own_spec(c).cover_m)

    if splice is None:
        if len(stack) == 1:
            return dict((element_id_value(c.Id), generate_column(doc, c, column_spec, bar_types, hooks, mark, shapes))
                        for c in stack), []
        # a column and its joint with no splices: one whole bar through both
        splice = {"max": REVIT_MAX_BAR_M, "laps": {}}
    created = {}
    sections = [Section(c) for c in stack]
    base = sections[0]
    z0_ft = base.z_bottom
    total = (sections[-1].z_top - z0_ft) * FT
    stories = [((s.z_bottom - z0_ft) * FT, (clear_top(doc, c, s) - z0_ft) * FT) for c, s in zip(stack, sections)]
    stories = [st for st in stories if st[1] - st[0] > 0.05]  # joints modeled apart: no lap there
    tops = [(s.z_top - z0_ft) * FT for s in sections]
    warnings = []
    # splice["column"]: the lamina's rule - laps in the central third, the
    # bars in two alternating groups round the section (at most 50% lapped
    # in one section)
    bars = seated_bars(doc, column_spec, bar_types, mark)  # corner bars in the stirrups' bends
    group_of = {}
    if splice.get("column"):
        for rank, k in enumerate(sorted(range(len(bars)), key=lambda k: math.atan2(bars[k][1], bars[k][0]))):
            group_of[k] = rank % 2
    groups_cache = {}
    plans = []
    for index_bar, (x, y, key) in enumerate(bars):
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
            # The bar length counts its anchorage and legs: the cuts are
            # found on the developed bar (from the bottom leg's tip to the
            # top leg's), then put back on the column's axis.
            below = ends.get("anchor", 0.0) + ends.get("leg_bottom", 0.0)
            above = ends.get("leg_top", 0.0) - ends.get("top_drop", 0.0)
            if group_of:
                cache = (key, round(below, 4), round(above, 4))
                if cache not in groups_cache:
                    groups_cache[cache] = spec.column_splice_groups(-below, total + above, stories, lap, splice["max"])
                by_group, lap, found = groups_cache[cache]
                pieces = by_group[group_of[index_bar]]
            else:
                pieces, found = spec.splice_pieces(-below, total + above, stories, lap, splice["max"])
            pieces = [(max(a, 0.0), min(b, total)) for a, b in pieces]
        warnings += [u"Barras de {}: {}".format(key, w) for w in found]
        # with legs, the crank goes square to the same face (one plane per bar)
        leg_dir = ends.get("dir_bottom") or ends.get("dir_top")
        inward = None
        if leg_dir:
            out = spec.leg_vector(x, y, *(_half_sizes(base) + (spec.LEG_OUT,)))
            inward = (-out[0], -out[1])
        plans.append((x, y, key, d, ends, lap, pieces, inward))
    # the stirrups, closer along the laps (_lap_extra): each lap is the top
    # `lap` of a piece that goes on
    zones = []
    for x, y, key, d, ends, lap, pieces, inward in plans:
        for z_start, z_end in pieces[:-1]:
            if lap:
                zones.append((z0_ft + (z_end - lap) / FT, z0_ft + z_end / FT))
    for column in stack:
        LAP_ZONES[element_id_value(column.Id)] = zones
        try:
            created[element_id_value(column.Id)] = generate_column(
                doc, column, own_spec(column), bar_types, hooks, mark, shapes, longitudinal=False)
        finally:
            LAP_ZONES.pop(element_id_value(column.Id), None)
    for x, y, key, d, ends, lap, pieces, inward in plans:
        for index, (z_start, z_end) in enumerate(pieces):
            path = spec.bar_piece_points(x, y, d, z_start, z_end, lap, index < len(pieces) - 1, inward)
            path = spec.bar_with_ends(
                path,
                **dict((k, v) for k, v in ends.items()
                       if (index == 0 and k in ("anchor", "leg_bottom", "dir_bottom"))
                       or (index == len(pieces) - 1 and k in ("top_drop", "leg_top", "dir_top", "rise_top"))))
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
    return points if area > 0 else points[:1] + points[:0:-1]  # the first corner kept first: the hooks


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
            local = section.local_m(point)  # mirror included
            if (not spec.point_in_polygon(local, section.polygon_m)
                    or spec.distance_to_polygon(local, section.polygon_m) < radius - tol):
                stick_out = True
                break
    p = column.LookupParameter(WEIGHT_PARAM)
    if p is not None and not p.IsReadOnly:
        p.Set(round(sum(kg_by_kind.values()), 2))
    return kg_by_kind, bars, stick_out


# --- Elements around the column, for the elevation and 3D views ---------------

# Acero Columna: only what frames into the column - beams, slabs and its
# footing. Each window passes its own list (Acero Muro: WALL_NEIGHBOR_CATEGORIES);
# never widen this one for another window.
NEIGHBOR_CATEGORIES = (
    (DB.BuiltInCategory.OST_StructuralFraming, u"VIGA"),
    (DB.BuiltInCategory.OST_Floors, u"LOSA"),
    (DB.BuiltInCategory.OST_StructuralFoundation, u"ZAPATA"),
)
# Acero Muro: a wall also shows the columns at its ends and the walls it meets.
WALL_NEIGHBOR_CATEGORIES = NEIGHBOR_CATEGORIES + (
    (DB.BuiltInCategory.OST_StructuralColumns, u"COLUMNA"),
    (DB.BuiltInCategory.OST_Walls, u"MURO"),
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


def column_neighbors(doc, column, section, reach_m=0.6, contact_m=0.05, categories=None):
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
    def local(p):
        x, y = section.local_m(p)  # mirror included: the neighbors where they are
        return (x, y, (p.Z - section.z_bottom) * FT)

    result = []
    options = DB.Options()
    for bic, label in (categories or NEIGHBOR_CATEGORIES):
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
