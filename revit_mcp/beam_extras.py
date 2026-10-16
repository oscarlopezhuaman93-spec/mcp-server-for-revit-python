# -*- coding: utf-8 -*-
"""Per-beam settings of "Acero Viga": each clear span's own stirrup
distribution, the extra bars ("bastones" over the supports, top;
"balancines" in the spans, bottom) and the cuts where the steel is
checked. Kept per beam line (JSON on its elements, EA_Viga_Linea), not
per type: each beam has its own spans.

Settings: {"spans": {"0": {"edge": text, "conf": text}}, "extras":
[{"kind", "face" ("sup"/"inf"), "n", "d", "s0", "s1"}], "cuts": [s]}
(m, along the beam line). Pure (tests/unit/test_beam_extras.py).
"""
import json
import math

BASTON = u"baston"
BALANCIN = u"balancin"
TOP, BOTTOM = u"sup", u"inf"
MIN_CLEAR_M = 0.025  # E.060 7.6.1: clear space between parallel bars (>= db, 2.5 cm)


def default_settings():
    return {"spans": {}, "extras": [], "cuts": []}


def read(text):
    try:
        data = json.loads(text or u"{}")
    except ValueError:
        data = {}
    out = default_settings()
    out["spans"] = dict(data.get("spans") or {})
    out["extras"] = list(data.get("extras") or [])
    out["cuts"] = [float(c) for c in data.get("cuts") or []]
    return out


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


def extra_ends(extra, bar_start, bar_end, tol=0.02):
    """(s0, s1, hooked at start, hooked at end): the extra clipped to the
    main bars' reach; it takes their anchorage where it reaches a beam end."""
    s0 = max(extra["s0"], bar_start)
    s1 = min(extra["s1"], bar_end)
    return s0, s1, s0 <= bar_start + tol, s1 >= bar_end - tol
