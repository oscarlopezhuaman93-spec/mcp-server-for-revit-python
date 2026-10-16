# -*- coding: utf-8 -*-
"""Per-beam settings of "Acero Viga": each clear span's own stirrup
distribution, the extra bars ("bastones" over the supports, top;
"balancines" in the spans, bottom) and the cuts where the steel is
checked. Kept per beam line (JSON on its elements, EA_Viga_Linea), not
per type: each beam has its own spans.

Settings: {"spans": {"0": {"edge": text, "conf": text}}, "extras":
[{"kind", "face" ("sup"/"inf"), "n", "d", "s0", "s1", "pos" (optional:
[[x, y]] of each bar in the section, m from its center)}], "cuts": [s],
"hooks": {"0": m, "1": m} (optional: the 90-degree hook leg at the
start / end of the main bars, 0 = straight)} (m, along the beam line). Pure (tests/unit/test_beam_extras.py).
"""
import json
import math

BASTON = u"baston"
BALANCIN = u"balancin"
TOP, BOTTOM = u"sup", u"inf"
MIN_CLEAR_M = 0.025  # E.060 7.6.1: clear space between parallel bars (>= db, 2.5 cm)


def default_settings():
    return {"spans": {}, "extras": [], "cuts": [], "hooks": {}}


def read(text):
    try:
        data = json.loads(text or u"{}")
    except ValueError:
        data = {}
    out = default_settings()
    out["spans"] = dict(data.get("spans") or {})
    out["extras"] = list(data.get("extras") or [])
    out["cuts"] = [float(c) for c in data.get("cuts") or []]
    out["hooks"] = dict((str(k), float(v)) for k, v in (data.get("hooks") or {}).items())
    return out


def default_leg(key, diameters_mm):
    """The 90-degree hook leg of a bar (E.060 7.1.2: 12 db), up to 5 cm."""
    m = 12.0 * diameters_mm[key] / 1000.0
    return math.ceil(m / 0.05 - 1e-6) * 0.05


def hook_leg(settings, end, key, legs, diameters_mm, hooked=True):
    """The hook leg (m) at end 0 / 1 of the beam for bars of `key`: the
    beam's own, else the diameter table's, else 12 db; 0 when straight."""
    own = ((settings or {}).get("hooks") or {}).get(str(end))
    if own is not None:
        return max(0.0, float(own))
    if not hooked:
        return 0.0
    return (legs or {}).get(key) or default_leg(key, diameters_mm)


def placed_positions(extra, main_bars, diameters_mm):
    """The section points of an extra's bars: where they were placed by
    hand ("pos", one per bar), else laid out like extra_positions."""
    auto = extra_positions(main_bars, extra["face"], int(extra["n"]), extra["d"], diameters_mm)
    pos = extra.get("pos") or []
    if len(pos) == len(auto):
        return [(float(x), float(y)) for x, y in pos]
    return auto


def set_position(extra, main_bars, diameters_mm, k, x, y):
    """Place bar k of an extra at (x, y) of the section (the others keep
    where they are)."""
    pts = [list(p) for p in placed_positions(extra, main_bars, diameters_mm)]
    pts[k] = [round(x, 4), round(y, 4)]
    extra["pos"] = pts
    return extra


def position_text(points, polygon, face):
    """'x;y | x;y' in cm of bars: x from the section's left face, y from
    its top face (top bars) or bottom face (bottom bars) to the center."""
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    out = []
    for x, y in points:
        dy = (max(ys) - y) if face == TOP else (y - min(ys))
        out.append(u"{:g};{:g}".format(round((x - min(xs)) * 100.0, 1), round(dy * 100.0, 1)))
    return u" | ".join(out)


def parse_position_text(text, polygon, face, n):
    """The inverse of position_text: [(x, y)] m; ValueError when it doesn't
    hold n points inside the section."""
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    pts = []
    for part in (text or u"").replace(u",", u".").split(u"|"):
        if not part.strip():
            continue
        a, b = part.split(u";")
        x = min(xs) + float(a) / 100.0
        dy = float(b) / 100.0
        y = (max(ys) - dy) if face == TOP else (min(ys) + dy)
        if not (min(xs) < x < max(xs) and min(ys) < y < max(ys)):
            raise ValueError(part)
        pts.append((x, y))
    if len(pts) != n:
        raise ValueError(text)
    return pts


def write(settings):
    return json.dumps(settings, ensure_ascii=False, sort_keys=True)


def span_text(settings, index, kind):
    """The span's own distribution of the family `kind` ("edge"/"conf"),
    u"" when it takes the type's."""
    return ((settings.get("spans") or {}).get(str(index)) or {}).get(kind) or u""


def set_span_text(settings, index, kind, text):
    spans = settings.setdefault("spans", {})
    entry = spans.setdefault(str(index), {})
    if (text or u"").strip():
        entry[kind] = text.strip()
    else:
        entry.pop(kind, None)
        if not entry:
            spans.pop(str(index), None)


def gaps(spans, start_face, end_face):
    """The supports as the stretches between clear spans:
    [(s0, s1)] - the first and last are the end supports."""
    out = [(start_face, spans[0][0])]
    out += [(a[1], b[0]) for a, b in zip(spans, spans[1:])]
    out.append((spans[-1][1], end_face))
    return out


def nearest_gap(s, spans, start_face, end_face):
    """Index of the support nearest to s."""
    g = gaps(spans, start_face, end_face)
    return min(range(len(g)), key=lambda k: abs((g[k][0] + g[k][1]) / 2.0 - s))


def span_at(s, spans):
    for k, (a, b) in enumerate(spans):
        if a - 1e-6 <= s <= b + 1e-6:
            return k
    return None


def default_baston(gap_index, spans, start_face, end_face, n, key):
    """A top extra over a support: Ln/3 into each span beside it (at an end
    support it starts at the beam end, anchored as the main bars)."""
    g = gaps(spans, start_face, end_face)
    g0, g1 = g[gap_index]
    left = spans[gap_index - 1] if gap_index > 0 else None
    right = spans[gap_index] if gap_index < len(spans) else None
    s0 = g0 - (left[1] - left[0]) / 3.0 if left else start_face
    s1 = g1 + (right[1] - right[0]) / 3.0 if right else end_face
    return {"kind": BASTON, "face": TOP, "n": n, "d": key, "s0": round(s0, 3), "s1": round(s1, 3)}


def default_balancin(span_index, spans, n, key):
    """A bottom extra in a span: its middle, 0.15 Ln clear of each face."""
    a, b = spans[span_index]
    ln = b - a
    return {"kind": BALANCIN, "face": BOTTOM, "n": n, "d": key,
            "s0": round(a + 0.15 * ln, 3), "s1": round(b - 0.15 * ln, 3)}


def default_cuts(spans):
    """Three cuts a span: near each support (0.10 Ln) and the middle."""
    out = []
    for a, b in spans:
        ln = b - a
        out += [round(a + 0.10 * ln, 3), round((a + b) / 2.0, 3), round(b - 0.10 * ln, 3)]
    return out


def extra_label(extra, spans, start_face, end_face):
    """"2Ø5/8\" 1.20|1.30" for a baston (lengths from the support's
    faces), "2Ø5/8\" L=2.40" for a balancin."""
    head = u"{}Ø{}".format(int(extra["n"]), extra["d"])
    if extra["kind"] == BASTON:
        g = gaps(spans, start_face, end_face)
        mid = (extra["s0"] + extra["s1"]) / 2.0
        k = min(range(len(g)), key=lambda i: 0 if g[i][0] - 1e-6 <= mid <= g[i][1] + 1e-6
                else min(abs(mid - g[i][0]), abs(mid - g[i][1])))
        left = max(0.0, g[k][0] - extra["s0"])
        right = max(0.0, extra["s1"] - g[k][1])
        return u"{} {:.2f}|{:.2f}".format(head, left, right)
    return u"{} L={:.2f}".format(head, extra["s1"] - extra["s0"])


def parse_count_diameter(text, keys):
    """"2 5/8", "2Ø5/8\"", "3 1/2" -> (2, '5/8"'); ValueError otherwise."""
    t = (text or u"").replace(u"Ø", u" ").replace(u'"', u" ").replace(u"ø", u" ").strip()
    parts = t.split()
    if len(parts) < 2:
        raise ValueError(text)
    n = int(parts[0])
    d = u" ".join(parts[1:]).strip()
    key = d + u'"' if (d + u'"') in keys else (d if d in keys else None)
    if key is None or n < 1:
        raise ValueError(text)
    return n, key


def parse_lengths(text):
    """"1.20|1.30" or "1.20 1.30" -> (1.2, 1.3); one value -> both."""
    t = (text or u"").replace(u",", u".").replace(u"|", u" ").replace(u";", u" ").split()
    vals = [float(v) for v in t]
    if not vals or any(v < 0 for v in vals):
        raise ValueError(text)
    return (vals[0], vals[1] if len(vals) > 1 else vals[0])


def extra_positions(main_bars, face, n, key, diameters_mm):
    """Section points (x, y) of n extra bars of `key` on a face, among the
    main bars there: in the widest gaps of that layer while the clear
    space holds (E.060 7.6.1), else a second layer inside it, spread."""
    on_face = [(x, y, k) for x, y, k in main_bars if (y > 0) == (face == TOP)]
    if not on_face:
        return []
    d = diameters_mm[key] / 1000.0
    y_face = max(y for _, y, _ in on_face) if face == TOP else min(y for _, y, _ in on_face)
    layer = sorted([(x, k) for x, y, k in on_face if abs(y - y_face) < 0.005])
    xs = [x for x, _ in layer]
    radii = [diameters_mm[k] / 2000.0 for _, k in layer]
    clear = max(MIN_CLEAR_M, d)
    # how many fit in each gap, then fill the widest gaps first
    room = []
    for i in range(len(xs) - 1):
        free = xs[i + 1] - xs[i] - radii[i] - radii[i + 1]
        fit = int((free - clear) // (d + clear)) if free > clear else 0
        room.append([fit, i, 0])
    if sum(r[0] for r in room) >= n:
        for _ in range(n):
            best = max(room, key=lambda r: ((xs[r[1] + 1] - xs[r[1]]) / (r[2] + 2) if r[2] < r[0] else -1))
            best[2] += 1
        out = []
        for fit, i, used in room:
            for j in range(used):
                out.append((xs[i] + (xs[i + 1] - xs[i]) * (j + 1) / (used + 1), y_face))
        return sorted(out)
    # a second layer, inside the first: clear space below / above it
    main_d = max(diameters_mm[k] for _, k in layer) / 1000.0
    step = main_d / 2.0 + clear + d / 2.0
    y2 = y_face - step if face == TOP else y_face + step
    if n == 1:
        return [((xs[0] + xs[-1]) / 2.0, y2)]
    return [(xs[0] + (xs[-1] - xs[0]) * j / (n - 1), y2) for j in range(n)]


def bar_area_cm2(key, diameters_mm):
    return math.pi * (diameters_mm[key] / 10.0) ** 2 / 4.0


def steel_at(s, main_bars, extras, diameters_mm, face):
    """(text, cm2) of the steel crossing the cut at s on a face: the main
    bars plus the extras whose stretch holds s."""
    count = {}
    for x, y, k in main_bars:
        if (y > 0) == (face == TOP):
            count[k] = count.get(k, 0) + 1
    extra = {}
    for e in extras:
        if e["face"] == face and e["s0"] - 1e-6 <= s <= e["s1"] + 1e-6:
            extra[e["d"]] = extra.get(e["d"], 0) + int(e["n"])
    area = 0.0  # a float (IronPython's sum of nothing is an int "0")
    area += sum(n * bar_area_cm2(k, diameters_mm) for k, n in count.items())
    area += sum(n * bar_area_cm2(k, diameters_mm) for k, n in extra.items())
    parts = [u"{}Ø{}".format(n, k) for k, n in sorted(count.items(), key=lambda kv: -diameters_mm[kv[0]])]
    parts += [u"{}Ø{}".format(n, k) for k, n in sorted(extra.items(), key=lambda kv: -diameters_mm[kv[0]])]
    return u" + ".join(parts) or u"-", area


SNAP_STEP_M = 0.05


def snap_s(s, faces, tol, step=SNAP_STEP_M):
    """s snapped to a support face / beam end within tol, else to step."""
    near = [f for f in faces if abs(f - s) <= tol]
    if near:
        return min(near, key=lambda f: abs(f - s))
    return round(round(s / step) * step, 3)


def faces_of(spans, start_face, end_face):
    """Every support face and the beam ends (where bars stop or anchor)."""
    out = [start_face, end_face]
    for a, b in spans:
        out += [a, b]
    return sorted(set(round(f, 3) for f in out))


def drawn_extra(s_a, s_b, face, n, key):
    """An extra drawn by hand from s_a to s_b on a face: top = baston,
    bottom = balancin."""
    s0, s1 = sorted((s_a, s_b))
    return {"kind": BASTON if face == TOP else BALANCIN, "face": face, "n": n, "d": key,
            "s0": round(s0, 3), "s1": round(s1, 3)}


def cota_rows(extras, face, gap=0.15):
    """{extra index: row} of the extras of a face: overlapping ones (or
    closer than `gap`) on different rows, the fewest rows (the dimension
    lines stacked as on a drawing)."""
    order = sorted((e["s0"], e["s1"], i) for i, e in enumerate(extras) if e["face"] == face)
    rows_end = []
    out = {}
    for s0, s1, i in order:
        for r, end in enumerate(rows_end):
            if s0 >= end + gap:
                rows_end[r] = s1
                out[i] = r
                break
        else:
            rows_end.append(s1)
            out[i] = len(rows_end) - 1
    return out


def extra_cotas(extra, spans, start_face, end_face, tol=0.02):
    """The dimensions of an extra, always from a column (support) face, as
    on the drawings: a bar over a support - from each face out to its end
    on that side; a bar in a span - from each face of the span in to its
    start / end. [{"lo", "hi", "end" (0: s0, 1: s1), "face"}]."""
    s0, s1 = extra["s0"], extra["s1"]
    g = gaps(spans, start_face, end_face)
    over = [(g0, g1) for g0, g1 in g if s0 < g1 + tol and s1 > g0 - tol]
    out = []
    if over:
        (l0, _), (_, r1) = over[0], over[-1]
        if s0 < l0 - tol:
            out.append({"lo": s0, "hi": l0, "end": 0, "face": l0})
        if s1 > r1 + tol:
            out.append({"lo": r1, "hi": s1, "end": 1, "face": r1})
        if not out:  # all of it inside the support
            out.append({"lo": s0, "hi": s1, "end": 1, "face": s0})
        return out
    k = span_at((s0 + s1) / 2.0, spans)
    a, b = spans[k] if k is not None else (s0, s1)
    if s0 > a + tol:
        out.append({"lo": a, "hi": s0, "end": 0, "face": a})
    if s1 < b - tol:
        out.append({"lo": s1, "hi": b, "end": 1, "face": b})
    return out or [{"lo": s0, "hi": s1, "end": 1, "face": s0}]


def set_cota(extra, cota, value):
    """Change the end a dimension measures to `value` m from its face (the
    face stays where it is)."""
    key = "s0" if cota["end"] == 0 else "s1"
    sign = 1.0 if extra[key] >= cota["face"] - 1e-9 else -1.0
    extra[key] = round(cota["face"] + sign * max(0.0, float(value)), 3)
    if extra["s1"] - extra["s0"] < 0.10:
        extra[key] = round(extra["s0" if key == "s1" else "s1"] + (0.10 if key == "s1" else -0.10), 3)
    return extra


def cota_rows_all(extras, spans, start_face, end_face, face, gap=0.10):
    """{(extra index, cota index): row} of the dimensions of a face, packed
    in rows so none overlaps another."""
    items = []
    for i, e in enumerate(extras):
        if e["face"] != face:
            continue
        for j, c in enumerate(extra_cotas(e, spans, start_face, end_face)):
            items.append((c["lo"], c["hi"], (i, j)))
    items.sort()
    rows_end, out = [], {}
    for lo, hi, key in items:
        for r, end in enumerate(rows_end):
            if lo >= end + gap:
                rows_end[r] = hi
                out[key] = r
                break
        else:
            rows_end.append(hi)
            out[key] = len(rows_end) - 1
    return out


def set_length(extra, length, faces):
    """A new length, keeping the end that sits on a support face / beam
    end (else the start) where it is."""
    length = max(0.10, float(length))
    d0 = min(abs(extra["s0"] - f) for f in faces) if faces else 0.0
    d1 = min(abs(extra["s1"] - f) for f in faces) if faces else 1.0
    if d1 < d0 - 1e-6:
        extra["s0"] = round(extra["s1"] - length, 3)
    else:
        extra["s1"] = round(extra["s0"] + length, 3)
    return extra


def auto_cuts(spans, extras, min_len=0.10):
    """One cut in every stretch of each span where the steel is the same:
    the span cut at every extra's ends, neighbouring stretches with the
    same extras merged; the cut in the middle of each."""
    out = []
    for a, b in spans:
        stops = sorted(set([a, b] + [e[k] for e in extras for k in ("s0", "s1") if a < e[k] < b]))
        pieces = []
        for lo, hi in zip(stops, stops[1:]):
            if hi - lo < 1e-6:
                continue
            mid = (lo + hi) / 2.0
            holding = tuple(sorted(i for i, e in enumerate(extras) if e["s0"] - 1e-6 <= mid <= e["s1"] + 1e-6))
            if pieces and pieces[-1][2] == holding:
                pieces[-1][1] = hi
            else:
                pieces.append([lo, hi, holding])
        for lo, hi, _ in pieces:
            if hi - lo >= min_len:
                out.append(round((lo + hi) / 2.0, 3))
    return out


def extra_ends(extra, bar_start, bar_end, tol=0.02):
    """(s0, s1, hooked at start, hooked at end): the extra clipped to the
    main bars' reach; it takes their anchorage where it reaches a beam end."""
    s0 = max(extra["s0"], bar_start)
    s1 = min(extra["s1"], bar_end)
    return s0, s1, s0 <= bar_start + tol, s1 >= bar_end - tol
