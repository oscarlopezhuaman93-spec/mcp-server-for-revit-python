# -*- coding: utf-8 -*-
"""The bars of a two-way roof ("techo a dos aguas"): two sloped slabs meeting
at a ridge (beam), their viguetas running down the slope. Along one vigueta
the roof is a profile in a vertical plane: s (m, in plan) from the ridge
axis - side A negative, side B positive - and z up.

What crosses the ridge, by the user's choice (for the bottom bars and the
top bastones each):
- DOBLEZ: each side's bar crosses the ridge, bent, and runs on into the
  other slab its "longitud de doblez" (the two side by side);
- CONTINUO: one bar eave to eave, bent at the ridge;
- ANCLADO: each side's bar stops in the ridge beam with a hook.
Bastones at the eaves too (Ln/4 by default). Every length is measured along
the slope - from the ridge axis, or from the eave beam's face - and every
one can be typed (settings "roof"). Pure (tests/unit/test_roof_rebar.py)."""
import math

DOBLEZ, CONTINUO, ANCLADO = u"doblez", u"continuo", u"anclado"
MODES = (DOBLEZ, CONTINUO, ANCLADO)
A, B = u"A", u"B"


def default_roof():
    return {
        "inf": {"mode": DOBLEZ, "d": u'1/2"', "dA": 0.60, "dB": 0.60, "hA": None, "hB": None},
        "sup": {"mode": DOBLEZ, "d": u'1/2"', "a": None, "b": None, "dA": 0.60, "dB": 0.60, "hA": None, "hB": None},
        "eave": {"A": None, "B": None, "frac": 0.25},
        "frac": 1.0 / 3.0,  # the ridge bastones: Ln/3 down each side
        "hook": None,  # the eave hooks (m); None: 12 db
        "cover": 2.0,  # cm
    }


def read_roof(saved):
    out = default_roof()
    for k, v in (saved or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k].update(v)
        else:
            out[k] = v
    return out


class Profile(object):
    """The roof along one vigueta: the top line [(s, z)] (eave A, ridge,
    eave B), the thickness across the face of each side, and the supports
    along it [(s0, s1)] (the eave beams, the ridge beam, any other)."""

    def __init__(self, top, t_a, t_b, supports, ridge_s=0.0):
        self.top = sorted(top)
        self.t = {A: t_a, B: t_b}
        self.supports = sorted(supports)
        self.ridge = ridge_s
        self.s0, self.s1 = self.top[0][0], self.top[-1][0]

    def side(self, s):
        return A if s < self.ridge else B

    def slope(self, side):
        """(rise / run) of a side, as it climbs to the ridge."""
        pts = [p for p in self.top if (p[0] <= self.ridge + 1e-9 if side == A else p[0] >= self.ridge - 1e-9)]
        (s0, z0), (s1, z1) = pts[0], pts[-1]
        return abs(z1 - z0) / max(abs(s1 - s0), 1e-9)

    def top_z(self, s):
        pts = self.top
        if s <= pts[0][0]:
            (a, za), (b, zb) = pts[0], pts[1]
        elif s >= pts[-1][0]:
            (a, za), (b, zb) = pts[-2], pts[-1]
        else:
            for (a, za), (b, zb) in zip(pts, pts[1:]):
                if a <= s <= b:
                    break
        return za + (zb - za) * (s - a) / (b - a) if b != a else za

    def depth_z(self, s, depth):
        """The height `depth` under the top, across the face (a bar line)."""
        m = self.slope(self.side(s))
        return self.top_z(s) - depth * math.sqrt(1.0 + m * m)

    def along(self, side, length):
        """Plan distance from the ridge of `length` measured on the slope."""
        m = self.slope(side)
        return length / math.sqrt(1.0 + m * m)

    def slope_length(self, s0, s1):
        """Length on the slope between two plan positions."""
        total, pts = 0.0, [s0] + [self.ridge] * (1 if s0 < self.ridge < s1 else 0) + [s1]
        for a, b in zip(pts, pts[1:]):
            m = self.slope(self.side((a + b) / 2.0))
            total += abs(b - a) * math.sqrt(1.0 + m * m)
        return total

    def eaves(self):
        """(eave A beam (s0, s1), eave B beam) - the supports at the ends."""
        a = [g for g in self.supports if g[0] <= self.s0 + 0.05]
        b = [g for g in self.supports if g[1] >= self.s1 - 0.05]
        return (a[0] if a else None), (b[-1] if b else None)

    def ridge_beam(self):
        r = [g for g in self.supports if g[0] - 0.05 <= self.ridge <= g[1] + 0.05]
        return r[0] if r else None

    def clear(self, side):
        """(from, to) plan of a side's clear span: eave beam face to ridge beam face."""
        ea, eb = self.eaves()
        rb = self.ridge_beam()
        if side == A:
            return (ea[1] if ea else self.s0), (rb[0] if rb else self.ridge)
        return (rb[1] if rb else self.ridge), (eb[0] if eb else self.s1)


def _leg(key_mm, value):
    if value is not None:
        return max(0.0, float(value))
    return math.ceil(12.0 * key_mm / 1000.0 / 0.05 - 1e-6) * 0.05


def bar_path(profile, s_a, s_b, depth, leg_a=0.0, leg_b=0.0, up=True):
    """[(s, z)] of a bar from s_a to s_b at `depth` under the top (across
    the face), bent where the roof bends (the ridge), hooked at either end
    (towards the other face: up for a bottom bar, down for a top one)."""
    pts = [(s_a, profile.depth_z(s_a, depth))]
    if s_a < profile.ridge < s_b:
        # the two offset lines meet over the ridge axis (a symmetric bend; for
        # sides of different slope the meeting point moves a little)
        ma, mb = profile.slope(A), profile.slope(B)
        za = profile.top_z(profile.ridge) - depth * math.sqrt(1 + ma * ma)
        zb = profile.top_z(profile.ridge) - depth * math.sqrt(1 + mb * mb)
        # z = za + ma*(s - r) on A (climbing), z = zb - mb*(s - r) on B
        ds = (zb - za) / (ma + mb) if ma + mb > 1e-9 else 0.0
        pts.append((profile.ridge + ds, za + ma * ds))
    pts.append((s_b, profile.depth_z(s_b, depth)))
    sign = 1.0 if up else -1.0
    if leg_a > 0:
        pts.insert(0, (pts[0][0], pts[0][1] + sign * leg_a))
    if leg_b > 0:
        pts.append((pts[-1][0], pts[-1][1] + sign * leg_b))
    return pts


def plan_roof(profile, settings, mm):
    """The bars of one vigueta line: [{"name", "face" (inf/sup), "key",
    "path" [(s, z)], "shift" (m across, side by side), "cotas": [{"tag",
    "value", "s0", "s1", "side"}]}] - cotas are the editable lengths (on the
    slope)."""
    st = read_roof(settings)
    c = float(st.get("cover") or 2.0) / 100.0
    ea, eb = profile.eaves()
    start = (ea[0] + c) if ea else profile.s0 + c  # into the eave beams, to their far face
    end = (eb[1] - c) if eb else profile.s1 - c
    r = profile.ridge
    out = []
    # bottom bars ------------------------------------------------------------------
    cfg = st["inf"]
    key = cfg["d"]
    d = mm[key] / 1000.0
    t_min = min(profile.t[A], profile.t[B])
    depth_inf = t_min - c - d / 2.0
    eave_leg = _leg(mm[key], st.get("hook"))
    room = t_min - 2 * c - 2 * d
    eave_leg = min(eave_leg, room)
    mode = cfg.get("mode", DOBLEZ)
    if mode == CONTINUO:
        out.append({"name": u"inferior", "face": u"inf", "key": key, "shift": 0.0,
                    "path": bar_path(profile, start, end, depth_inf, eave_leg, eave_leg, up=True), "cotas": []})
    elif mode == DOBLEZ:
        sb = r + profile.along(B, float(cfg.get("dB") or 0.6))
        sa = r - profile.along(A, float(cfg.get("dA") or 0.6))
        out.append({"name": u"inferior A", "face": u"inf", "key": key, "shift": -(d / 2.0 + 0.003),
                    "path": bar_path(profile, start, sb, depth_inf, eave_leg, 0.0, up=True),
                    "cotas": [{"tag": u"roof|inf|dB", "value": float(cfg.get("dB") or 0.6), "s0": r, "s1": sb, "side": B}]})
        out.append({"name": u"inferior B", "face": u"inf", "key": key, "shift": d / 2.0 + 0.003,
                    "path": bar_path(profile, sa, end, depth_inf, 0.0, eave_leg, up=True),
                    "cotas": [{"tag": u"roof|inf|dA", "value": float(cfg.get("dA") or 0.6), "s0": sa, "s1": r, "side": A}]})
    else:  # ANCLADO: each side stops in the ridge beam, hooked up
        ha, hb = _leg(mm[key], cfg.get("hA")), _leg(mm[key], cfg.get("hB"))
        out.append({"name": u"inferior A", "face": u"inf", "key": key, "shift": 0.0,
                    "path": bar_path(profile, start, r - 0.02, depth_inf, eave_leg, min(ha, room), up=True),
                    "cotas": [{"tag": u"roof|inf|hA", "value": min(ha, room), "s0": r - 0.02, "s1": r - 0.02, "side": A, "hook": True}]})
        out.append({"name": u"inferior B", "face": u"inf", "key": key, "shift": 0.0,
                    "path": bar_path(profile, r + 0.02, end, depth_inf, min(hb, room), eave_leg, up=True),
                    "cotas": [{"tag": u"roof|inf|hB", "value": min(hb, room), "s0": r + 0.02, "s1": r + 0.02, "side": B, "hook": True}]})
    # top bars: the ridge bastones ------------------------------------------------------
    cfg = st["sup"]
    key = cfg["d"]
    d = mm[key] / 1000.0
    depth_sup = c + d / 2.0
    ca, cb = profile.clear(A), profile.clear(B)
    frac = float(st.get("frac") or 1.0 / 3.0)
    ln_a = profile.slope_length(ca[0], ca[1])
    ln_b = profile.slope_length(cb[0], cb[1])
    rb = profile.ridge_beam()
    face_a = rb[0] if rb else r
    face_b = rb[1] if rb else r
    # lengths from the ridge axis on the slope: half the ridge beam + Ln/3
    half_a = profile.slope_length(face_a, r)
    half_b = profile.slope_length(r, face_b)
    a_len = float(cfg["a"]) if cfg.get("a") is not None else round(half_a + frac * ln_a, 2)
    b_len = float(cfg["b"]) if cfg.get("b") is not None else round(half_b + frac * ln_b, 2)
    s_a = max(start, r - profile.along(A, a_len))
    s_b = min(end, r + profile.along(B, b_len))
    mode = cfg.get("mode", DOBLEZ)
    room = t_min - 2 * c - 2 * d
    cot_a = {"tag": u"roof|sup|a", "value": a_len, "s0": s_a, "s1": r, "side": A}
    cot_b = {"tag": u"roof|sup|b", "value": b_len, "s0": r, "s1": s_b, "side": B}
    if mode == CONTINUO:
        out.append({"name": u"bastón cumbrera", "face": u"sup", "key": key, "shift": 0.0,
                    "path": bar_path(profile, s_a, s_b, depth_sup, up=False), "cotas": [cot_a, cot_b]})
    elif mode == DOBLEZ:
        db, da = float(cfg.get("dB") or 0.6), float(cfg.get("dA") or 0.6)
        out.append({"name": u"bastón cumbrera A", "face": u"sup", "key": key, "shift": -(d / 2.0 + 0.003),
                    "path": bar_path(profile, s_a, r + profile.along(B, db), depth_sup, up=False),
                    "cotas": [cot_a, {"tag": u"roof|sup|dB", "value": db, "s0": r, "s1": r + profile.along(B, db), "side": B, "row": 1}]})
        out.append({"name": u"bastón cumbrera B", "face": u"sup", "key": key, "shift": d / 2.0 + 0.003,
                    "path": bar_path(profile, r - profile.along(A, da), s_b, depth_sup, up=False),
                    "cotas": [cot_b, {"tag": u"roof|sup|dA", "value": da, "s0": r - profile.along(A, da), "s1": r, "side": A, "row": 1}]})
    else:
        ha, hb = _leg(mm[key], cfg.get("hA")), _leg(mm[key], cfg.get("hB"))
        out.append({"name": u"bastón cumbrera A", "face": u"sup", "key": key, "shift": 0.0,
                    "path": bar_path(profile, s_a, r - 0.02, depth_sup, 0.0, min(ha, room), up=False),
                    "cotas": [cot_a, {"tag": u"roof|sup|hA", "value": min(ha, room), "s0": r - 0.02, "s1": r - 0.02, "side": A, "hook": True}]})
        out.append({"name": u"bastón cumbrera B", "face": u"sup", "key": key, "shift": 0.0,
                    "path": bar_path(profile, r + 0.02, s_b, depth_sup, min(hb, room), 0.0, up=False),
                    "cotas": [cot_b, {"tag": u"roof|sup|hB", "value": min(hb, room), "s0": r + 0.02, "s1": r + 0.02, "side": B, "hook": True}]})
    # the eave bastones, hooked down into the eave beams ----------------------------------
    eave = st["eave"]
    efrac = float(eave.get("frac") or 0.25)
    leg = min(_leg(mm[key], st.get("hook")), room)
    for side, (f0, f1), ln in ((A, ca, ln_a), (B, cb, ln_b)):
        own = eave.get(side)
        length = float(own) if own is not None else round(efrac * ln, 2)
        if side == A:
            s_end = f0 + profile.along(A, length)
            path = bar_path(profile, start, s_end, depth_sup, leg, 0.0, up=False)
            cot = {"tag": u"roof|eave|A", "value": length, "s0": f0, "s1": s_end, "side": A}
        else:
            s_beg = f1 - profile.along(B, length)
            path = bar_path(profile, s_beg, end, depth_sup, 0.0, leg, up=False)
            cot = {"tag": u"roof|eave|B", "value": length, "s0": s_beg, "s1": f1, "side": B}
        out.append({"name": u"bastón alero " + side, "face": u"sup", "key": key, "shift": 0.0, "path": path, "cotas": [cot]})
    return out


def set_value(settings, tag, value):
    """Store an edited length: tag 'roof|<group>|<field>' (m; None = the
    automatic one)."""
    _, group, field = tag.split(u"|")
    roof = settings.setdefault("roof", {})
    g = roof.setdefault(group, {})
    if group == u"eave":
        g[field] = value
    else:
        g[field] = value
    return settings


def slope_length_of(path):
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(path, path[1:]))
