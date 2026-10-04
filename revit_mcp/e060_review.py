# -*- coding: utf-8 -*-
"""Review of the configured reinforcement against the Norma E.060
(Concreto Armado, RNE Peru 2009) - the "Revision E.060" button.

Each check returns findings: {"status", "category", "element", "rule",
"value", "limit", "ref"}; status OK / AVISO / NO CUMPLE. The checks are
a summary of the norm's prescriptive limits (the engineer still checks
the design forces): the article of each one goes with it.

Pure: plain numbers in, findings out (tests/unit/test_e060_review.py).
"""
import math

OK = u"OK"
WARN = u"AVISO"
FAIL = u"NO CUMPLE"
STATUSES = (FAIL, WARN, OK)

C_COLUMN = u"Columna"
C_WALL = u"Muro"
C_FOOTING = u"Cimentacion"
C_STAIR = u"Escalera"
C_BEAM = u"Viga"


def finding(status, category, element, rule, value, limit, ref):
    return {"status": status, "category": category, "element": element, "rule": rule,
            "value": value, "limit": limit, "ref": ref}


def _check(ok, category, element, rule, value, limit, ref, warn=False):
    return finding(OK if ok else (WARN if warn else FAIL), category, element, rule, value, limit, ref)


def bar_area_m2(diameter_mm):
    return math.pi * (diameter_mm / 1000.0) ** 2 / 4.0


def cm(v):
    return u"{:.1f} cm".format(v * 100.0)


# --- columns -----------------------------------------------------------------------
def stirrup_min_diameter_mm(long_max_mm):
    """E.060 7.10.5.1: 8 mm for bars up to 5/8", 3/8" up to 1", 1/2" above."""
    if long_max_mm <= 15.9:
        return 8.0
    if long_max_mm <= 25.4:
        return 9.5
    return 12.7


def confinement(zones, rest):
    """(lo, so, first, rest) of a stirrup distribution read from one end:
    lo = the zones' length, so = their largest spacing after the first."""
    lo = sum(n * s for n, s in zones)
    first = zones[0][1] if zones else rest
    inner = [s for k, (n, s) in enumerate(zones) if k > 0 or n > 1]
    so = max(inner) if inner else first
    return lo, so, first, rest


def check_column(name, b, h, area, bars, circular, cover_cm, stirrup_mm, zones, rest, ln):
    """b, h: the section's sides (m), area (m2); bars: [diameter mm];
    zones/rest: the edge stirrups' distribution (m) from each end; ln: clear
    height (m). Findings of one column type."""
    out = []
    if not bars:
        return [finding(WARN, C_COLUMN, name, u"Acero longitudinal", u"sin armado", u"-", u"-")]
    rho = sum(bar_area_m2(d) for d in bars) / area
    out.append(_check(0.01 <= rho <= 0.06, C_COLUMN, name, u"Cuantia longitudinal",
                      u"{:.2%}".format(rho), u"1% a 6%", u"E.060 10.9.1"))
    if rho > 0.04 and rho <= 0.06:
        out.append(finding(WARN, C_COLUMN, name, u"Cuantia alta (congestion en empalmes)",
                           u"{:.2%}".format(rho), u"<= 4% recomendado", u"E.060 10.9.1"))
    need = 6 if circular else 4
    out.append(_check(len(bars) >= need, C_COLUMN, name, u"Numero minimo de barras",
                      u"{}".format(len(bars)), u">= {}".format(need), u"E.060 10.9.2"))
    if cover_cm is not None:
        out.append(_check(cover_cm >= 4.0, C_COLUMN, name, u"Recubrimiento", u"{:g} cm".format(cover_cm),
                          u">= 4 cm", u"E.060 7.7.1"))
    if stirrup_mm:
        need_mm = stirrup_min_diameter_mm(max(bars))
        out.append(_check(stirrup_mm >= need_mm - 0.1, C_COLUMN, name, u"Diametro de estribo",
                          u"{:g} mm".format(stirrup_mm), u">= {:g} mm".format(need_mm), u"E.060 7.10.5.1"))
    if zones is None:
        out.append(finding(WARN, C_COLUMN, name, u"Distribucion de estribos", u"sin distribucion", u"-",
                           u"E.060 21.4.5"))
        return out
    db = min(bars) / 1000.0
    bmin = min(b, h)
    lo, so, first, rest_s = confinement(zones, rest)
    lo_min = max(ln / 6.0, max(b, h), 0.50)
    out.append(_check(lo >= lo_min - 1e-3, C_COLUMN, name, u"Longitud de confinamiento lo", cm(lo),
                      u">= {} (ln/6, lado mayor, 50 cm)".format(cm(lo_min)), u"E.060 21.4.5.3"))
    so_max = min(8 * db, bmin / 2.0, 0.10)
    out.append(_check(so <= so_max + 1e-3, C_COLUMN, name, u"Espaciado en zona de confinamiento so", cm(so),
                      u"<= {} (8db, b/2, 10 cm)".format(cm(so_max)), u"E.060 21.4.5.3"))
    out.append(_check(first <= max(so_max / 2.0, 0.05) + 1e-3, C_COLUMN, name, u"Primer estribo",
                      cm(first), u"<= {}".format(cm(max(so_max / 2.0, 0.05))), u"E.060 21.4.5.3"))
    if rest_s is not None and stirrup_mm:
        rest_max = min(16 * db, 48 * stirrup_mm / 1000.0, bmin, 0.30)
        out.append(_check(rest_s <= rest_max + 1e-3, C_COLUMN, name, u"Espaciado fuera de lo", cm(rest_s),
                          u"<= {} (16db, 48de, b, 30 cm)".format(cm(rest_max)), u"E.060 7.10.5.2"))
    return out


# --- walls -------------------------------------------------------------------------
def check_wall(name, t, vertical, horizontal, cover_cm):
    """t: thickness (m); vertical: [(diameter mm, spacing m)] (each a bar
    line repeated along the wall); horizontal: (diameter mm, spacing m,
    faces) or None."""
    out = []
    smax = min(3 * t, 0.40)
    if vertical:
        rho_v = sum(bar_area_m2(d) / s for d, s in vertical) / t
        status = OK if rho_v >= 0.0025 else (WARN if rho_v >= 0.0015 else FAIL)
        out.append(finding(status, C_WALL, name, u"Cuantia vertical", u"{:.4f}".format(rho_v),
                           u">= 0.0025 (0.0015 si Vu < 0.27 raiz(f'c) Acv)", u"E.060 21.9.4 / 11.10.7"))
        s = max(s for d, s in vertical)
        out.append(_check(s <= smax + 1e-3, C_WALL, name, u"Espaciado vertical", cm(s),
                          u"<= {} (3t, 40 cm)".format(cm(smax)), u"E.060 11.10.7"))
    else:
        out.append(finding(WARN, C_WALL, name, u"Acero vertical", u"sin dibujar", u"-", u"-"))
    if horizontal:
        d, s, faces = horizontal
        rho_h = faces * bar_area_m2(d) / s / t
        status = OK if rho_h >= 0.0025 else (WARN if rho_h >= 0.0020 else FAIL)
        out.append(finding(status, C_WALL, name, u"Cuantia horizontal", u"{:.4f}".format(rho_h),
                           u">= 0.0025 (0.0020 si Vu < 0.27 raiz(f'c) Acv)", u"E.060 21.9.4 / 11.10.7"))
        out.append(_check(s <= smax + 1e-3, C_WALL, name, u"Espaciado horizontal", cm(s),
                          u"<= {} (3t, 40 cm)".format(cm(smax)), u"E.060 11.10.7"))
    else:
        out.append(finding(WARN, C_WALL, name, u"Acero horizontal", u"sin configurar", u"-", u"-"))
    if cover_cm is not None:
        out.append(_check(cover_cm >= 2.0, C_WALL, name, u"Recubrimiento", u"{:g} cm".format(cover_cm),
                          u">= 2 cm", u"E.060 7.7.1"))
    return out


# --- footings ------------------------------------------------------------------------
def check_footing(name, h, meshes, bottom_cover_cm):
    """h: depth (m); meshes: [(layer "inferior"/"superior", direction, diameter
    mm, spacing m)]."""
    out = []
    if bottom_cover_cm is not None:
        out.append(_check(bottom_cover_cm >= 7.5, C_FOOTING, name, u"Recubrimiento contra el terreno",
                          u"{:g} cm".format(bottom_cover_cm), u">= 7.5 cm", u"E.060 7.7.1"))
    smax = min(3 * h, 0.40)
    for layer, direction, d, s in meshes:
        label = u"{} {}".format(layer, direction)
        if layer == u"inferior":
            rho = bar_area_m2(d) / s / h
            out.append(_check(rho >= 0.0018, C_FOOTING, name, u"Cuantia minima ({})".format(label),
                              u"{:.4f}".format(rho), u">= 0.0018", u"E.060 9.7.2 / 10.5.4"))
        out.append(_check(s <= smax + 1e-3, C_FOOTING, name, u"Espaciado ({})".format(label), cm(s),
                          u"<= {} (3h, 40 cm)".format(cm(smax)), u"E.060 10.5.4"))
    if not meshes:
        out.append(finding(WARN, C_FOOTING, name, u"Mallas", u"sin acero", u"-", u"-"))
    return out


# --- stairs ----------------------------------------------------------------------
def check_stair(name, t, cover_cm, main, temperature):
    """t: waist (m); main: (diameter mm, spacing m) of the bottom bars or
    None; temperature: idem of the cross bars or None."""
    out = []
    if cover_cm is not None:
        out.append(_check(cover_cm >= 2.0, C_STAIR, name, u"Recubrimiento", u"{:g} cm".format(cover_cm),
                          u">= 2 cm", u"E.060 7.7.1"))
    if main:
        d, s = main
        rho = bar_area_m2(d) / s / t
        out.append(_check(rho >= 0.0018, C_STAIR, name, u"Cuantia minima (acero principal)",
                          u"{:.4f}".format(rho), u">= 0.0018", u"E.060 9.7.2"))
        smax = min(3 * t, 0.40)
        out.append(_check(s <= smax + 1e-3, C_STAIR, name, u"Espaciado del acero principal", cm(s),
                          u"<= {} (3t, 40 cm)".format(cm(smax)), u"E.060 10.5.4"))
    if temperature:
        d, s = temperature
        rho = bar_area_m2(d) / s / t
        out.append(_check(rho >= 0.0018, C_STAIR, name, u"Cuantia de temperatura", u"{:.4f}".format(rho),
                          u">= 0.0018", u"E.060 9.7.2"))
        smax = min(5 * t, 0.40)
        out.append(_check(s <= smax + 1e-3, C_STAIR, name, u"Espaciado de temperatura", cm(s),
                          u"<= {} (5t, 40 cm)".format(cm(smax)), u"E.060 9.7.3"))
    return out


def summary(findings):
    """{status: count}."""
    out = dict((s, 0) for s in STATUSES)
    for f in findings:
        out[f["status"]] = out.get(f["status"], 0) + 1
    return out


def sort_findings(findings):
    order = dict((s, k) for k, s in enumerate(STATUSES))
    return sorted(findings, key=lambda f: (order.get(f["status"], 9), f["category"], f["element"]))


def to_csv(findings):
    """The findings as CSV text (';' separated, opens in Excel)."""
    head = u"ESTADO;CATEGORIA;ELEMENTO;VERIFICACION;VALOR;LIMITE;NORMA"
    rows = [u";".join(f[k].replace(u";", u",") for k in ("status", "category", "element", "rule", "value",
                                                           "limit", "ref")) for f in findings]
    return u"\r\n".join([head] + rows) + u"\r\n"
