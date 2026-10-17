# -*- coding: utf-8 -*-
"""Steel of a solid slab ("losa maciza") for the "Acero Losa" window.

Everything in the slab's local frame, meters: x / y in plan, z up from
the slab's bottom. The slab is given by the triangles of its top face in
plan and its supports (beams, walls, columns under it) by the triangles of
their footprint, so any outline works (an L, a hole...).

Per direction (x: bars along x, laid across y; y: the other way):
- bottom mesh ("inf"): each bar line runs the concrete it crosses, end to
  end less the cover, hooked at the edges (anchor "gancho"); past the
  maximum bar length it is spliced like the beams' bottom bars - from a
  support face, in the end quarter of a clear span (rs.beam_lap_zones);
- top steel ("sup"): bastones over each support, by default Ln/3 of the
  clear span on each side measured from the support face (editable per
  support), hooked down at an edge support; or a full top mesh spliced in
  the central third.
Pure (tests/unit/test_slab_rebar.py)."""
import json
import math

import rebar_spec as rs

INF, SUP = u"inf", u"sup"
X, Y = u"x", u"y"
LAYERS = ((INF, X), (INF, Y), (SUP, X), (SUP, Y))
ANCHOR_HOOK, ANCHOR_STRAIGHT = u"gancho", u"recto"
TOP_BASTONES, TOP_MESH, TOP_NONE = u"bastones", u"malla", u"ninguno"


def default_settings():
    return {
        "cover": 2.0,  # cm (E.060 7.7.1: slabs not exposed, 2 cm)
        INF: {"on": True, X: {"d": u'3/8"', "s": 0.20}, Y: {"d": u'3/8"', "s": 0.20}},
        SUP: {"on": True, "mode": TOP_BASTONES, "frac": 1.0 / 3.0,
              X: {"d": u'3/8"', "s": 0.20}, Y: {"d": u'3/8"', "s": 0.20}},
        "anchor": ANCHOR_HOOK,
        "leg": None,  # m, None: 12 db
        "bast": {},  # "x@4.50": [left, right] m from the support faces
        "cuts": {},  # "x": y of cut 1-1 (bars along x), "y": x of cut 2-2
    }


def read(text):
    out = default_settings()
    try:
        saved = json.loads(text or u"{}")
    except ValueError:
        saved = {}
    for k, v in saved.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k not in ("bast", "cuts"):
            for kk, vv in v.items():
                if isinstance(vv, dict) and isinstance(out[k].get(kk), dict):
                    out[k][kk].update(vv)
                else:
                    out[k][kk] = vv
        else:
            out[k] = v
    return out


def write(settings):
    return json.dumps(settings, ensure_ascii=False, sort_keys=True)


# --- geometry along a line -------------------------------------------------------
def _merge(intervals, tol=1e-4):
    out = []
    for a, b in sorted(intervals):
        if out and a <= out[-1][1] + tol:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def line_intervals(triangles, axis, pos):
    """The stretches [(s0, s1)] (s along `axis`) where the line at `pos`
    across crosses the triangles (plan points (x, y))."""
    found = []
    for tri in triangles:
        pts = [((p[0], p[1]) if axis == X else (p[1], p[0])) for p in tri]  # (s, t)
        hits = []
        for k in range(3):
            (s0, t0), (s1, t1) = pts[k], pts[(k + 1) % 3]
            if (t0 - pos) * (t1 - pos) < 0 or (abs(t0 - pos) < 1e-9 and abs(t1 - pos) > 1e-9):
                hits.append(s0 + (s1 - s0) * (pos - t0) / (t1 - t0))
        if len(hits) >= 2:
            found.append((min(hits), max(hits)))
    return [(a, b) for a, b in _merge(found) if b - a > 1e-3]


def extent(triangles):
    xs = [p[0] for t in triangles for p in t]
    ys = [p[1] for t in triangles for p in t]
    return min(xs), max(xs), min(ys), max(ys)


def positions(lo, hi, spacing):
    """Bar positions from lo to hi every `spacing` at most, evenly spread
    (both ends included)."""
    if hi - lo < 1e-6:
        return [lo]
    n = int(math.ceil((hi - lo) / spacing - 1e-6))
    return [lo + (hi - lo) * k / n for k in range(n + 1)]


def clear_and_gaps(chord, supports):
    """(clear spans, supports) of a chord: the supports crossing it, clipped,
    and the stretches between them."""
    a, b = chord
    gaps = [(max(a, s0), min(b, s1)) for s0, s1 in supports if s1 > a + 1e-3 and s0 < b - 1e-3]
    spans, pos = [], a
    for g0, g1 in gaps:
        if g0 > pos + 0.05:
            spans.append((pos, g0))
        pos = max(pos, g1)
    if b > pos + 0.05:
        spans.append((pos, b))
    return spans, gaps


def support_key(axis, gap):
    return u"{}@{:.2f}".format(axis, (gap[0] + gap[1]) / 2.0)


def leg_length(settings, key, mm, room):
    """The hook leg (m): the one typed, else 12 db up to 5 cm, never more
    than the room in the slab."""
    leg = settings.get("leg")
    if leg is None:
        leg = math.ceil(12.0 * mm[key] / 1000.0 / 0.05 - 1e-6) * 0.05
    return max(0.0, min(float(leg), room))


def bastones(chord, spans, gaps, frac, edits, axis, tol=0.02, end_frac=None):
    """[(s0, s1, at_start_edge, at_end_edge, key)] of the bastones of a line:
    over each support, `frac` of the clear span on each side from its faces
    (or the lengths typed for that support); at an edge support from the
    edge into the span."""
    a, b = chord
    out = []
    for g in gaps:
        g0, g1 = g
        key = support_key(axis, g)
        left = [s for s in spans if abs(s[1] - g0) < tol]
        right = [s for s in spans if abs(s[0] - g1) < tol]
        if not left and not right:
            continue  # the line runs along a support (over a parallel beam): nothing to hang over
        own = edits.get(key)
        edge = g0 <= a + tol or g1 >= b - tol or not left or not right
        f = end_frac if (edge and end_frac is not None) else frac  # Ln/4 at an end support
        l_len = own[0] if own else (f * (left[0][1] - left[0][0]) if left else 0.0)
        r_len = own[1] if own else (f * (right[0][1] - right[0][0]) if right else 0.0)
        s0 = a if g0 <= a + tol else g0 - l_len
        s1 = b if g1 >= b - tol else g1 + r_len
        if s1 - s0 > 0.1:
            out.append((s0, s1, g0 <= a + tol, g1 >= b - tol, key))
    return out


def _path(s0, s1, z, h, d, lap, cranked, leg0, leg1):
    """(s, z) points of a bar from s0 to s1 at height z, hooked at either
    end towards the other face, cranked before a lap."""
    pts = rs.beam_bar_points(0.0, z - h / 2.0, d, s0, s1, lap, cranked, leg0, leg1)
    return [(p[2], p[1] + h / 2.0) for p in pts]


def layer_z(settings, h, mm):
    """{(face, axis): z} of the four layers: bottom x lowest, bottom y on it;
    top x highest, top y under it."""
    c = float(settings.get("cover") or 2.0) / 100.0
    dix = mm[settings[INF][X]["d"]] / 1000.0
    diy = mm[settings[INF][Y]["d"]] / 1000.0
    dsx = mm[settings[SUP][X]["d"]] / 1000.0
    dsy = mm[settings[SUP][Y]["d"]] / 1000.0
    return {(INF, X): c + dix / 2.0, (INF, Y): c + dix + diy / 2.0,
            (SUP, X): h - c - dsx / 2.0, (SUP, Y): h - c - dsx - dsy / 2.0}


def plan_line(chord, supports, face, axis, settings, h, mm, splice=None):
    """The bars of one line on one chord: [{"s0", "s1", "path", "lap",
    "lap_at" (s0, s1) or None, "hooks" (bool, bool)}] plus warnings."""
    c = float(settings.get("cover") or 2.0) / 100.0
    key = settings[face][axis]["d"]
    d = mm[key] / 1000.0
    z = (settings.get("_z") or {}).get((face, axis)) or layer_z(settings, h, mm)[(face, axis)]
    room = h - 2 * c - 2 * d
    hooked = settings.get("anchor", ANCHOR_HOOK) == ANCHOR_HOOK
    leg = leg_length(settings, key, mm, room) if hooked else 0.0
    spans, gaps = clear_and_gaps(chord, supports)
    a, b = chord
    bar_start, bar_end = a + c, b - c
    bars, warnings = [], []
    mode = settings[SUP].get("mode", TOP_BASTONES) if face == SUP else None
    if face == INF or mode == TOP_MESH:
        top = face == SUP
        pieces, lap = [(bar_start, bar_end)], 0.0
        if splice and bar_end - bar_start + 2 * leg > splice["max"] + 1e-6:
            lap = (splice.get("laps") or {}).get(key, 0.0)
            if lap:
                # a line over a parallel beam has no clear span: its laps go anywhere
                zones = rs.beam_lap_zones(spans, top, 0.0, lap) if spans else [(bar_start, bar_end)]
                where = u"un tercio central de paño" if top else u"un cuarto extremo de paño (desde la cara)"
                pieces, found = rs.lap_pieces(bar_start, bar_end, zones, lap, splice["max"] - leg, where)
                warnings += found
            else:
                warnings.append(u"barras de {} de {:.2f} m sin empalme (diámetro no marcado)".format(
                    key, bar_end - bar_start))
        for i, (s0, s1) in enumerate(pieces):
            last = i == len(pieces) - 1
            l0 = leg if i == 0 else 0.0
            l1 = leg if last else 0.0
            bars.append({"s0": s0, "s1": s1, "path": _path(s0, s1, z, h, d, lap, not last, l0, l1),
                         "lap_at": None if last else (round(s1 - lap, 3), round(s1, 3)), "key": key})
    elif mode == TOP_BASTONES:
        frac = float(settings[SUP].get("frac") or 1.0 / 3.0)
        for s0, s1, e0, e1, sk in bastones(chord, spans, gaps, frac, settings.get("bast") or {}, axis,
                                            end_frac=settings[SUP].get("end_frac")):
            s0, s1 = max(s0, bar_start), min(s1, bar_end)
            bars.append({"s0": s0, "s1": s1, "support": sk, "lap_at": None, "key": key,
                         "path": _path(s0, s1, z, h, d, 0.0, False, leg if e0 else 0.0, leg if e1 else 0.0)})
    return bars, spans, gaps, warnings


def plan_slab(slab_tris, support_tris, h, settings, mm, splice=None):
    """Every line of every layer: {(face, axis): [{"pos", "chord", "bars",
    "spans", "gaps"}]}, and the warnings (once each)."""
    x0, x1, y0, y1 = extent(slab_tris)
    c = float(settings.get("cover") or 2.0) / 100.0
    out, warnings = {}, []
    for face, axis in LAYERS:
        if not settings[face].get("on", True):
            out[(face, axis)] = []
            continue
        if face == SUP and settings[SUP].get("mode") == TOP_NONE:
            out[(face, axis)] = []
            continue
        cfg = settings[face][axis]
        d = mm[cfg["d"]] / 1000.0
        lo, hi = (y0, y1) if axis == X else (x0, x1)
        lines = []
        for pos in positions(lo + c + d / 2.0, hi - c - d / 2.0, float(cfg["s"])):
            chords = line_intervals(slab_tris, axis, pos)
            sup = line_intervals(support_tris, axis, pos)
            for chord in chords:
                bars, spans, gaps, found = plan_line(chord, sup, face, axis, settings, h, mm, splice)
                warnings += found
                lines.append({"pos": pos, "chord": chord, "bars": bars, "spans": spans, "gaps": gaps})
        out[(face, axis)] = lines
    seen = []
    for w in warnings:
        if w not in seen:
            seen.append(w)
    return out, seen


def bar_sets(lines, tol=1e-3):
    """Lines with the same bars side by side, grouped as one rebar set each:
    [(path, key, first pos, count, spacing)]. A set breaks where the bars
    change or the spacing does."""
    runs, order = {}, []
    for line in lines:
        for bar in line["bars"]:
            sig = (bar["key"], tuple((round(s, 3), round(z, 3)) for s, z in bar["path"]))
            if sig not in runs:
                runs[sig] = []
                order.append(sig)
            runs[sig].append(line["pos"])
    out = []
    for sig in order:
        group = []
        for p in sorted(runs[sig]):
            if len(group) >= 2 and abs((p - group[-1]) - (group[1] - group[0])) > tol:
                out.append((list(sig[1]), sig[0], group[0], len(group), group[1] - group[0]))
                group = []
            group.append(p)
        out.append((list(sig[1]), sig[0], group[0], len(group), group[1] - group[0] if len(group) > 1 else 0.0))
    return out


def default_cuts(slab_tris):
    """Cut 1-1 (bars along x) at a third of the slab's depth in y, 2-2 at a
    third of its width in x."""
    x0, x1, y0, y1 = extent(slab_tris)
    return {X: round(y0 + (y1 - y0) / 3.0, 3), Y: round(x0 + (x1 - x0) / 3.0, 3)}


def nearest_line(lines, pos):
    """The lines nearest `pos` (one per chord)."""
    if not lines:
        return []
    best = min(abs(l["pos"] - pos) for l in lines)
    return [l for l in lines if abs(abs(l["pos"] - pos) - best) < 1e-6]


def crossing(lines, s):
    """Positions of the bars of the lines (perpendicular to a cut at s along
    them) that cross s: the dots of a cut."""
    out = []
    for line in lines:
        for bar in line["bars"]:
            if bar["s0"] - 1e-6 <= s <= bar["s1"] + 1e-6:
                out.append(line["pos"])
                break
    return out


def steel_per_m(key, spacing, mm):
    """cm2/m of bars of `key` every `spacing` m."""
    return math.pi * (mm[key] / 10.0) ** 2 / 4.0 / spacing


# --- losa aligerada (one-way ribbed slab with clay bricks) ------------------------
MACIZA, ALIGERADA = u"maciza", u"aligerada"
ENS_NONE, ENS_FULL, ENS_ALT = u"ninguno", u"corrido", u"alternado"


def default_ribbed():
    return {"dir": None,  # x / y: the viguetas' direction; None: across the shorter clear spans
            "b": 0.10, "s": 0.40, "top": 0.05,  # vigueta width, spacing, slab over the bricks
            "inf": {"d": u'1/2"'}, "sup": {"d": u'1/2"', "frac": 1.0 / 3.0, "end_frac": 0.25},
            "temp": {"on": True, "d": u'1/4"', "s": 0.25},
            "ens": {"mode": ENS_NONE, "len": 0.25},
            "brick": None}  # the brick family type chosen (its name)


def ribbed(settings):
    out = default_ribbed()
    for k, v in (settings.get("alig") or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k].update(v)
        else:
            out[k] = v
    return out


def auto_direction(slab_tris, support_tris):
    """The viguetas run across the shorter clear spans: the direction whose
    typical clear span (at the slab's middle) is shorter."""
    x0, x1, y0, y1 = extent(slab_tris)
    best = None
    for axis, pos in ((X, (y0 + y1) / 2.0), (Y, (x0 + x1) / 2.0)):
        spans = []
        for chord in line_intervals(slab_tris, axis, pos):
            spans += clear_and_gaps(chord, line_intervals(support_tris, axis, pos))[0]
        if spans:
            longest = max(b - a for a, b in spans)
            if best is None or longest < best[0]:
                best = (longest, axis)
    return best[1] if best else X


def bays(slab_tris, support_tris, axis):
    """The stretches across the viguetas between parallel supports (sampled
    along the slab's middle): [(t0, t1)]."""
    other = Y if axis == X else X
    x0, x1, y0, y1 = extent(slab_tris)
    pos = (x0 + x1) / 2.0 if axis == X else (y0 + y1) / 2.0
    out = []
    for chord in line_intervals(slab_tris, other, pos):
        out += clear_and_gaps(chord, line_intervals(support_tris, other, pos))[0]
    return out


def rib_layout(bay, b, s, brick_w):
    """(vigueta centers, brick strips [(t0, t1)]) of a bay: a brick strip
    against each face, then vigueta - bricks - vigueta...; the last strip
    is narrower (a narrower brick or concrete)."""
    t0, t1 = bay
    centers, strips = [], []
    t = t0
    while t < t1 - 1e-6:
        w = min(brick_w, t1 - t)
        strips.append((t, t + w))
        t += w
        if t1 - t > b + 0.05:
            centers.append(t + b / 2.0)
            t += b
        else:
            break
    return centers, strips


def fill_bricks(a, b, lib, width, min_piece=0.10):
    """Pieces [(type, s0, s1)] filling a ... b with the bricks of `lib`
    [(type, largo, ancho)] of this width: whole bricks, then the longest
    shorter one that fits the rest (a cut brick of the family); what is
    left stays concrete."""
    fit = sorted([(largo, t) for t, largo, ancho in lib if abs(ancho - width) < 1e-3], reverse=True)
    if not fit:
        return []
    main_l, main_t = fit[0]
    out, s = [], a
    while s + main_l <= b + 1e-6:
        out.append((main_t, s, s + main_l))
        s += main_l
    for largo, t in fit[1:]:
        if largo >= min_piece and s + largo <= b + 1e-6:
            out.append((t, s, s + largo))
            s += largo
            break
    return out


def brick_width(lib, room):
    """The widest brick width of `lib` that fits `room`."""
    widths = sorted(set(round(ancho, 3) for _, _, ancho in lib), reverse=True)
    for w in widths:
        if w <= room + 1e-6:
            return w
    return None


def plan_ribbed(slab_tris, support_tris, h, settings, mm, splice=None, lib=None):
    """The ribbed slab: {(face, axis): lines} like plan_slab (bottom and top
    bars of each vigueta; "temp" lines across), "viguetas" [centers],
    "bricks" [{"type", "s0", "s1", "t0", "t1"}], "axis", and warnings."""
    rb = ribbed(settings)
    axis = rb["dir"] or auto_direction(slab_tris, support_tris)
    other = Y if axis == X else X
    c = float(settings.get("cover") or 2.0) / 100.0
    lib = lib or []
    main_w = max([ancho for _, _, ancho in lib] or [rb["s"] - rb["b"]])
    di = mm[rb["inf"]["d"]] / 1000.0
    ds = mm[rb["sup"]["d"]] / 1000.0
    dt = mm[rb["temp"]["d"]] / 1000.0
    z_temp = h - c - dt / 2.0
    # the vigueta bars: the maciza routine with this vigueta's steel and heights
    st = {"cover": settings.get("cover") or 2.0, "anchor": settings.get("anchor", ANCHOR_HOOK),
          "leg": settings.get("leg"), "bast": settings.get("bast") or {},
          INF: {"on": True, axis: {"d": rb["inf"]["d"], "s": rb["s"]}},
          SUP: {"on": True, "mode": TOP_BASTONES, "frac": rb["sup"]["frac"], "end_frac": rb["sup"]["end_frac"],
                axis: {"d": rb["sup"]["d"], "s": rb["s"]}},
          "_z": {(INF, axis): c + di / 2.0, (SUP, axis): h - c - dt - ds / 2.0}}
    plan = {(INF, axis): [], (SUP, axis): [], (u"temp", other): []}
    warnings, centers, bricks = [], [], []
    for bay in bays(slab_tris, support_tris, axis):
        cs, strips = rib_layout(bay, rb["b"], rb["s"], main_w)
        centers += cs
        for k, (t0, t1) in enumerate(strips):
            w = brick_width(lib, t1 - t0) if lib else None
            if w is None:
                continue
            tm = (t0 + t1) / 2.0
            for chord in line_intervals(slab_tris, axis, tm):
                spans, _ = clear_and_gaps(chord, line_intervals(support_tris, axis, tm))
                for a, b in spans:
                    e = rb["ens"]
                    cut = float(e.get("len") or 0.0) if (e.get("mode") == ENS_FULL
                                                       or (e.get("mode") == ENS_ALT and k % 2 == 0)) else 0.0
                    for typ, s0, s1 in fill_bricks(a + cut, b - cut, lib, w):
                        bricks.append({"type": typ, "s0": s0, "s1": s1, "t0": t0, "t1": t0 + w})
    for pos in centers:
        sup = line_intervals(support_tris, axis, pos)
        for chord in line_intervals(slab_tris, axis, pos):
            for face in (INF, SUP):
                bars, spans, gaps, found = plan_line(chord, sup, face, axis, st, h, mm, splice)
                warnings += found
                plan[(face, axis)].append({"pos": pos, "chord": chord, "bars": bars, "spans": spans, "gaps": gaps})
    if rb["temp"].get("on", True):
        tst = {"cover": settings.get("cover") or 2.0, "anchor": ANCHOR_STRAIGHT, "leg": None, "bast": {},
               INF: {"on": True, other: {"d": rb["temp"]["d"], "s": rb["temp"]["s"]}},
               SUP: {"on": False, "mode": TOP_NONE}, "_z": {(INF, other): z_temp}}
        x0, x1, y0, y1 = extent(slab_tris)
        lo, hi = (y0, y1) if other == X else (x0, x1)
        for pos in positions(lo + c + dt / 2.0, hi - c - dt / 2.0, float(rb["temp"]["s"])):
            sup = line_intervals(support_tris, other, pos)
            for chord in line_intervals(slab_tris, other, pos):
                bars, spans, gaps, found = plan_line(chord, sup, INF, other, tst, h, mm, splice)
                warnings += found
                plan[(u"temp", other)].append({"pos": pos, "chord": chord, "bars": bars, "spans": spans, "gaps": gaps})
    seen = []
    for w in warnings:
        if w not in seen:
            seen.append(w)
    return {"lines": plan, "viguetas": sorted(centers), "bricks": bricks, "axis": axis}, seen