# -*- coding: utf-8 -*-
"""Ladrillo losa (2026-11-26): the roof bricks of ribbed slabs, apart from
Acero Losa. Its own rules here; the layout is the slab's own (EA_Losa_Acero,
the same viguetas the steel has), so bricks and viguetas always match.

The window edits the brick type, the direction and the two side gaps (by
the first parallel beam: "start"; by the far one: "end")."""
import rebar_slabs as rsl
import slab_rebar as sr
import roof_model as rm

from Autodesk.Revit import DB

MM = None  # set by the button (rebar_spec.BAR_DIAMETERS_MM)


def id_of(element_id):
    v = getattr(element_id, "Value", None)
    return int(v) if v is not None else element_id.IntegerValue


def is_ribbed(doc, floor):
    st = rsl.read_settings(floor)
    if st.get("kind"):
        return st.get("kind") == sr.ALIGERADA
    name = doc.GetElement(floor.GetTypeId()).get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM).AsString()
    return u"ALIGERAD" in (name or u"").upper()


def settings_of(doc, floor, types, thickness):
    """The slab's saved settings (Acero Losa's), else its first ones: ribbed,
    the brick that fits its thickness."""
    st = rsl.read_settings(floor)
    if not st.get("kind"):
        st["kind"] = sr.ALIGERADA
    a = st.setdefault("alig", {})
    if not a.get("brick"):
        a["brick"] = rsl.default_brick(types, thickness)
    return st


class Layouts(object):
    """The bricks of the picked slabs as they will be placed: each slab's
    plan, a roof's two waters lined up (the same logic Acero Losa used)."""

    def __init__(self, doc):
        self.doc = doc
        self.partners = {}
        self.models = {}

    def model(self, element_id):
        if element_id not in self.models:
            self.models[element_id] = rsl.SlabModel(self.doc.GetElement(DB.ElementId(element_id)))
        return self.models[element_id]

    def partner_of(self, element_id):
        if element_id not in self.partners:
            try:
                other = rm.partner(self.doc.GetElement(DB.ElementId(element_id)))
            except Exception:
                other = None
            self.partners[element_id] = id_of(other.Id) if other is not None else None
        return self.partners[element_id]

    def _align(self, element_id, model, settings, done):
        if not model.sloped:
            return
        other = self.partner_of(element_id)
        if other is None or other not in done:
            settings.setdefault("alig", {})["phase"] = None
            return
        pm, prib, pst = done[other]
        a = settings.setdefault("alig", {})
        a["rel"] = (pst.get("alig") or {}).get("rel")
        turn = rsl.slope_turn(settings)
        mirror = None
        if abs(turn - 90.0) < 1e-6:
            roof = rm.RoofModel(pm.element, model.element, {other: pm, element_id: model})
            ft = 0.3048

            def mirror(w):
                s, t = roof.to_frame(w.X * ft, w.Y * ft)
                x, y = roof.to_world(-s, t)
                return DB.XYZ(x / ft, y / ft, w.Z)
        elif turn != 0.0:
            a["start"], a["phase"] = 0.0, None
            return
        a["phase"] = rsl.aligned_phase(pm, prib, model, settings, mirror)
        a["start"] = 0.0

    def laid_out(self, element_id, model, settings, lib, done):
        self._align(element_id, model, settings, done)
        rib = model.plan(settings, MM, None, lib)[1]
        a = settings.setdefault("alig", {})
        if (model.sloped and a.get("phase") is None and self.partner_of(element_id) is not None
                and rib and rib.get("viguetas")):
            a["phase"] = round(rib["viguetas"][0], 4)
            rib = model.plan(settings, MM, None, lib)[1]
        done[element_id] = (model, rib, settings)
        return rib

    def build(self, ids, settings_by_id, types):
        """[(id, model, settings, lib, rib)], failed - the picked slabs and
        the slab across each ridge (only to line the viguetas up)."""
        run = list(ids)
        for i in list(ids):
            j = self.partner_of(i)
            if j is not None and j not in run:
                run.append(j)
        out, failed, done = [], [], {}
        for element_id in run:
            floor = self.doc.GetElement(DB.ElementId(element_id))
            try:
                model = self.model(element_id)
            except Exception as e:
                failed.append(u"Losa id {}: {}".format(element_id, e))
                continue
            settings = settings_by_id.get(element_id)
            if settings is None:
                settings = settings_of(self.doc, floor, types, model.thickness)
                ref = settings_by_id.get(ids[0]) if ids else None
                if ref is not None:  # the partner: the same brick and layout, its own slope
                    settings["alig"] = dict((k, v) for k, v in (ref.get("alig") or {}).items()
                                            if k not in ("dir", "angle", "start", "end"))
                settings_by_id[element_id] = settings
            lib = rsl.brick_library(self.doc, settings, types)
            if not lib:
                failed.append(u"Losa id {}: no hay tipo de ladrillo elegido".format(element_id))
                continue
            try:
                rib = self.laid_out(element_id, model, settings, lib, done)
            except Exception as e:
                failed.append(u"Losa id {}: {}".format(element_id, e))
                continue
            out.append((element_id, model, settings, lib, rib))
        return out, failed


def place(doc, ids, settings_by_id, layouts=None):
    """Replaces the plugin's bricks of the picked slabs with the planned
    ones (the steel left as it is). Returns (placed, failed)."""
    layouts = layouts or Layouts(doc)
    placed = 0
    t = DB.Transaction(doc, u"Ladrillo losa")
    failures = rsl.DropFailing()
    opts = t.GetFailureHandlingOptions()
    opts.SetFailuresPreprocessor(failures)
    opts.SetForcedModalHandling(False)
    t.SetFailureHandlingOptions(opts)
    t.Start()
    try:
        types = rsl.brick_types(doc)
        built, failed = layouts.build(ids, settings_by_id, types)
        for element_id, model, settings, lib, rib in built:
            if element_id not in ids:
                continue  # the slab across the ridge: only to line up
            floor = model.element
            try:
                rsl.write_settings(floor, settings)
                rsl.delete_bricks(doc, floor)
                model.plan(settings, MM, None, lib)  # its frame as laid out
                placed += rsl.place_bricks(model, rib, types)
            except Exception as e:
                failed.append(u"Losa id {}: {}".format(element_id, e))
        t.Commit()
    except Exception:
        t.RollBack()
        raise
    return placed, failed


def remove(doc, ids):
    """Deletes the plugin's bricks of the picked slabs. Returns the count."""
    t = DB.Transaction(doc, u"Borrar ladrillos")
    t.Start()
    n = 0
    try:
        for element_id in ids:
            floor = doc.GetElement(DB.ElementId(element_id))
            n += rsl.delete_bricks(doc, floor) or 0
        t.Commit()
    except Exception:
        t.RollBack()
        raise
    return n


def side_gaps(model, rib):
    """[(t_face, t_brick, side, s_mid)] per bay: the gap by the first
    parallel face ("start") and by the far one ("end"), in the plan's
    frame (axis of the viguetas s, across t)."""
    if not rib or not rib.get("bricks"):
        return []
    axis = rib["axis"]
    slab, sup = model.frame_slab(), model.frame_supports()
    out = []
    for b0, b1 in sr.bays(slab, sup, axis):
        inside = [bk for bk in rib["bricks"] if bk["t0"] >= b0 - 1e-6 and bk["t1"] <= b1 + 1e-6]
        if not inside:
            continue
        s_mid = sorted((bk["s0"] + bk["s1"]) / 2.0 for bk in inside)[len(inside) // 2]
        out.append((b0, min(bk["t0"] for bk in inside), u"start", s_mid))
        out.append((b1, max(bk["t1"] for bk in inside), u"end", s_mid))
    return out
