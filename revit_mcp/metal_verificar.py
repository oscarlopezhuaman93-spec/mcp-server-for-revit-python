# -*- coding: utf-8 -*-
"""BOKI Metalicas - verificacion PRELIMINAR (E.090 / AISC 360-16 LRFD) de lo
generado por Porticos, Cerchas y Correas.

Reglas PROPIAS (2026-11-25): no importa los otros modulos metal_*; lee del
modelo las piezas marcadas "BOKI ..." y las medidas de sus tipos.

Metodo:
- Porticos: analisis matricial 2D del portico tipico interior (base
  articulada, viga discretizada; la cartela no se considera: conservador en
  resistencia). Cargas: D (cobertura + correas + peso propio), Lr (techo,
  E.020), W (E.020: q = 0.005 V^2 kgf/m2, barlovento +0.8 / sotavento -0.6
  en muros, -0.7 / -0.6 en techo). Combinaciones E.090: 1.4D; 1.2D+1.6Lr+0.8W;
  1.2D+1.3W+0.5Lr; 0.9D+1.3W. Columnas: KLx = 2.0 h (portico desplazable de
  base articulada), KLy = Lb = separacion de largueros; vigas: KLx = largo
  del agua, KLy = Lb = separacion de correas (ala inferior con tornapuntas).
  AISC E3 (compresion), F2 (flexion con pandeo lateral-torsional, Cb = 1),
  H1-1 (interaccion). Deflexiones: cumbrera L/240 (Lr), alero h/60 (W).
- Cerchas: armadura de nudos articulados (un plano; la espacial reparte la
  mitad a cada plano); recta y dos aguas apoyo fijo + deslizante, arco
  ambos fijos. Cargas en los nudos del cordon superior. Tubos: traccion
  0.9 Fy Ag; compresion E3 con K = 1 y su largo.
- Correas: viga simple entre porticos/cerchas, flexion con modulo bruto
  (sin pandeo local, preliminar) y deflexion L/180.
No reemplaza el analisis y diseno del ingeniero responsable.
"""
import json
import math

E = 2.0e8  # kN/m2
G_ACC = 9.81e-3  # kN por kg
STEEL = 7850.0

DEFAULTS = {
    "dead": 0.15,  # kN/m2 cobertura + accesorios (sin correas ni peso propio)
    "live": 0.30,  # kN/m2 carga viva de techo (E.020, cobertura liviana)
    "wind": 75.0,  # km/h (E.020 minimo)
    "fy_w": 345.0,  # MPa, perfiles W A572 Gr.50
    "fy_tube": 250.0,  # MPa, tubos LAC
    "fy_c": 250.0,  # MPa, correas C
    "girt_step": 1.50,  # m (Lb de columnas)
    "purlin_step": 1.20,  # m (Lb de vigas)
}


def config(data=None):
    cfg = dict(DEFAULTS)
    cfg.update(dict((k, v) for k, v in (data or {}).items() if v is not None))
    for k in DEFAULTS:
        cfg[k] = float(cfg[k])
    return cfg


# --- secciones (m) ------------------------------------------------------------
def w_section(d, bf, tw, tf):
    """Propiedades de un perfil W (mm) sin filetes."""
    d, bf, tw, tf = d / 1000.0, bf / 1000.0, tw / 1000.0, tf / 1000.0
    hw = d - 2 * tf
    A = 2 * bf * tf + hw * tw
    Ix = (bf * d ** 3 - (bf - tw) * hw ** 3) / 12.0
    Iy = 2 * tf * bf ** 3 / 12.0 + hw * tw ** 3 / 12.0
    Zx = bf * tf * (d - tf) + tw * hw ** 2 / 4.0
    Sx = Ix / (d / 2.0)
    J = (2 * bf * tf ** 3 + hw * tw ** 3) / 3.0
    h0 = d - tf
    Cw = Iy * h0 ** 2 / 4.0
    return {"kind": "W", "A": A, "Ix": Ix, "Iy": Iy, "Zx": Zx, "Sx": Sx, "J": J, "h0": h0, "Cw": Cw,
            "rx": math.sqrt(Ix / A), "ry": math.sqrt(Iy / A), "rts": math.sqrt(math.sqrt(Iy * Cw) / Sx)}


def tube_section(h, b, t):
    h, b, t = h / 1000.0, b / 1000.0, t / 1000.0
    A = 2 * t * (h + b - 2 * t)
    Ix = (b * h ** 3 - (b - 2 * t) * (h - 2 * t) ** 3) / 12.0
    Iy = (h * b ** 3 - (h - 2 * t) * (b - 2 * t) ** 3) / 12.0
    return {"kind": "TR", "A": A, "Ix": Ix, "Iy": Iy, "Sx": Ix / (h / 2.0), "Zx": Ix / (h / 2.0) * 1.15,
            "rx": math.sqrt(Ix / A), "ry": math.sqrt(Iy / A)}


def c_section(h, b, lip, t):
    h, b, lip, t = h / 1000.0, b / 1000.0, lip / 1000.0, t / 1000.0
    A = (h + 2 * b + 2 * lip) * t
    y = h / 2.0
    Ix = t * h ** 3 / 12.0 + 2 * b * t * (y - t / 2.0) ** 2 + 2 * (t * lip ** 3 / 12.0 + lip * t * (y - lip / 2.0) ** 2)
    return {"kind": "C", "A": A, "Ix": Ix, "Sx": Ix / y, "rx": math.sqrt(Ix / A)}


# --- AISC 360 -----------------------------------------------------------------
def phi_pn(sec, fy, KLx, KLy):
    Fy = fy * 1000.0
    r = min(KLx / sec["rx"], KLy / sec.get("ry", sec["rx"]))
    sl = max(KLx / sec["rx"], KLy / sec.get("ry", sec["rx"]))
    Fe = math.pi ** 2 * E / sl ** 2
    Fcr = (0.658 ** (Fy / Fe)) * Fy if sl <= 4.71 * math.sqrt(E / Fy) else 0.877 * Fe
    return 0.9 * Fcr * sec["A"], sl


def phi_mn_w(sec, fy, Lb, Cb=1.0):
    Fy = fy * 1000.0
    Mp = Fy * sec["Zx"]
    Lp = 1.76 * sec["ry"] * math.sqrt(E / Fy)
    jc = sec["J"] / (sec["Sx"] * sec["h0"])
    Lr = 1.95 * sec["rts"] * E / (0.7 * Fy) * math.sqrt(jc + math.sqrt(jc ** 2 + 6.76 * (0.7 * Fy / E) ** 2))
    if Lb <= Lp:
        Mn = Mp
    elif Lb <= Lr:
        Mn = min(Mp, Cb * (Mp - (Mp - 0.7 * Fy * sec["Sx"]) * (Lb - Lp) / (Lr - Lp)))
    else:
        Fcr = Cb * math.pi ** 2 * E / (Lb / sec["rts"]) ** 2 * math.sqrt(1 + 0.078 * jc * (Lb / sec["rts"]) ** 2)
        Mn = min(Mp, Fcr * sec["Sx"])
    return 0.9 * Mn


def interaction(Pu, phiPn, Mu, phiMn):
    a = abs(Pu) / phiPn if Pu < 0 else 0.0  # compresion (N < 0)
    m = abs(Mu) / phiMn
    return a + 8.0 / 9.0 * m if a >= 0.2 else a / 2.0 + m


# --- analisis matricial 2D ---------------------------------------------------------
def solve_frame(nodes, elems, supports, cases):
    """nodes [(x, z)]; elems [(i, j, EA, EI)] (EI = 0: barra articulada);
    supports {nodo: (u, w, rot) fijos}; cases {nombre: {nodo: (Fx, Fz, M)}}.
    Devuelve {caso: (desplazamientos [(u, w, r)], [(N, M_i, M_j)] por barra)}
    (N > 0 traccion)."""
    nn = len(nodes)
    ndof = 3 * nn
    K = [[0.0] * ndof for _ in range(ndof)]
    has_rot = [False] * nn
    data = []
    for i, j, EA, EI in elems:
        (x1, z1), (x2, z2) = nodes[i], nodes[j]
        L = math.hypot(x2 - x1, z2 - z1)
        c, s = (x2 - x1) / L, (z2 - z1) / L
        kl = [[0.0] * 6 for _ in range(6)]
        a = EA / L
        kl[0][0] = kl[3][3] = a
        kl[0][3] = kl[3][0] = -a
        if EI > 0:
            has_rot[i] = has_rot[j] = True
            b12, b6, b4, b2 = 12 * EI / L ** 3, 6 * EI / L ** 2, 4 * EI / L, 2 * EI / L
            for (p, q, v) in ((1, 1, b12), (1, 2, b6), (1, 4, -b12), (1, 5, b6), (2, 2, b4), (2, 4, -b6), (2, 5, b2),
                              (4, 4, b12), (4, 5, -b6), (5, 5, b4)):
                kl[p][q] = v
                kl[q][p] = v
        T = [[0.0] * 6 for _ in range(6)]
        for k in (0, 3):
            T[k][k] = c
            T[k][k + 1] = s
            T[k + 1][k] = -s
            T[k + 1][k + 1] = c
            T[k + 2][k + 2] = 1.0
        kg = [[sum(T[p][a2] * sum(kl[p][q] * T[q][b2] for q in range(6)) for p in range(6)) for b2 in range(6)]
              for a2 in range(6)]
        dofs = [3 * i, 3 * i + 1, 3 * i + 2, 3 * j, 3 * j + 1, 3 * j + 2]
        for p in range(6):
            for q in range(6):
                K[dofs[p]][dofs[q]] += kg[p][q]
        data.append((dofs, kl, T, L))
    fixed = set()
    for n, (fu, fw, fr) in supports.items():
        for k, f in enumerate((fu, fw, fr)):
            if f:
                fixed.add(3 * n + k)
    for n in range(nn):
        if not has_rot[n]:
            fixed.add(3 * n + 2)
    free = [d for d in range(ndof) if d not in fixed]
    m = len(free)
    A = [[K[free[p]][free[q]] for q in range(m)] for p in range(m)]
    # LU (Doolittle) con pivoteo parcial
    piv = list(range(m))
    for k in range(m):
        big = max(range(k, m), key=lambda r: abs(A[r][k]))
        if abs(A[big][k]) < 1e-12:
            raise ValueError(u"estructura inestable (mecanismo) en el analisis")
        if big != k:
            A[k], A[big] = A[big], A[k]
            piv[k], piv[big] = piv[big], piv[k]
        akk = A[k][k]
        rowk = A[k]
        for r in range(k + 1, m):
            f = A[r][k] / akk
            if f != 0.0:
                rowr = A[r]
                rowr[k] = f
                for cc in range(k + 1, m):
                    rowr[cc] -= f * rowk[cc]
    out = {}
    for name, loads in cases.items():
        F = [0.0] * ndof
        for n, (fx, fz, mm) in loads.items():
            F[3 * n] += fx
            F[3 * n + 1] += fz
            F[3 * n + 2] += mm
        b = [F[free[piv[p]]] for p in range(m)]
        y = [0.0] * m
        for p in range(m):
            y[p] = b[p] - sum(A[p][q] * y[q] for q in range(p))
        x = [0.0] * m
        for p in range(m - 1, -1, -1):
            x[p] = (y[p] - sum(A[p][q] * x[q] for q in range(p + 1, m))) / A[p][p]
        D = [0.0] * ndof
        for p in range(m):
            D[free[p]] = x[p]
        forces = []
        for dofs, kl, T, L in data:
            dg = [D[k] for k in dofs]
            dl = [sum(T[p][q] * dg[q] for q in range(6)) for p in range(6)]
            fl = [sum(kl[p][q] * dl[q] for q in range(6)) for p in range(6)]
            forces.append((fl[3], -fl[2], fl[5]))  # N (traccion +), M en i, M en j
        out[name] = ([(D[3 * n], D[3 * n + 1], D[3 * n + 2]) for n in range(nn)], forces)
    return out


COMBOS = [(u"1.4D", {"D": 1.4}), (u"1.2D+1.6Lr+0.8W", {"D": 1.2, "Lr": 1.6, "W": 0.8}),
          (u"1.2D+1.6Lr", {"D": 1.2, "Lr": 1.6}),
          (u"1.2D+1.3W+0.5Lr", {"D": 1.2, "W": 1.3, "Lr": 0.5}), (u"0.9D+1.3W", {"D": 0.9, "W": 1.3})]


def wind_q(cfg, h):
    """Presion dinamica E.020 (kN/m2) a la altura h (m)."""
    V = cfg["wind"] * max(1.0, (h / 10.0) ** 0.22)
    return 0.005 * V ** 2 * 9.81 / 1000.0


def combine(res, case_names, factors):
    """Suma de resultados por caso con factores."""
    disp = None
    forces = None
    for c, f in factors.items():
        if c not in res:
            continue
        d, fo = res[c]
        if disp is None:
            disp = [tuple(f * v for v in x) for x in d]
            forces = [tuple(f * v for v in x) for x in fo]
        else:
            disp = [tuple(a + f * b for a, b in zip(x, y)) for x, y in zip(disp, d)]
            forces = [tuple(a + f * b for a, b in zip(x, y)) for x, y in zip(forces, fo)]
    return disp, forces


# --- portico ---------------------------------------------------------------------
def check_portico(geo, cfg):
    """geo: span, eave, ridge (m sobre la base), bay (m, tributario), col y
    raf (dicts de w_section + kg_m), purlin_kg_m2 (kg/m2 de correas)."""
    s, h, top, bay = geo["span"], geo["eave"], geo["ridge"], geo["bay"]
    nseg = 8
    nodes = [(0.0, 0.0)]
    for k in range(1, 5):
        nodes.append((0.0, h * k / 4.0))
    for k in range(1, nseg + 1):
        nodes.append((s / 2.0 * k / nseg, h + (top - h) * k / nseg))
    for k in range(1, nseg + 1):
        nodes.append((s / 2.0 + s / 2.0 * k / nseg, top - (top - h) * k / nseg))
    for k in range(3, -1, -1):
        nodes.append((s, h * k / 4.0))
    n = len(nodes)
    cA, cB = ((0, 4)), ((n - 5, n - 1))
    elems, kinds = [], []
    for i in range(n - 1):
        is_col = i < 4 or i >= n - 5
        sec = geo["col"] if is_col else geo["raf"]
        elems.append((i, i + 1, E * sec["A"], E * sec["Ix"]))
        kinds.append("col" if is_col else "raf")
    supports = {0: (1, 1, 0), n - 1: (1, 1, 0)}
    # cargas por metro horizontal (rafters) y self weight
    qD = (cfg["dead"] + geo.get("purlin_kg_m2", 6.0) * G_ACC) * bay
    qL = cfg["live"] * bay
    raf_w = geo["raf"]["kg_m"] * G_ACC
    col_w = geo["col"]["kg_m"] * G_ACC
    caseD, caseL, caseW = {}, {}, {}

    def add(case, node, fx, fz):
        a = case.get(node, (0.0, 0.0, 0.0))
        case[node] = (a[0] + fx, a[1] + fz, a[2])
    qw = wind_q(cfg, top)
    slope = (top - h) / (s / 2.0)
    cos = 1.0 / math.sqrt(1 + slope ** 2)
    for i, (a, b) in enumerate(zip(nodes[:-1], nodes[1:])):
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        dx = abs(b[0] - a[0])
        if kinds[i] == "raf":
            for nd in (i, i + 1):
                add(caseD, nd, 0.0, -(qD * dx + raf_w * L) / 2.0)
                add(caseL, nd, 0.0, -qL * dx / 2.0)
            # viento en techo: barlovento -0.7 (succion) primera agua, sotavento -0.6
            cp = -0.7 if b[0] <= s / 2.0 + 1e-6 else -0.6
            pn = cp * qw * bay * L  # cp < 0: succion, la fuerza va por la normal exterior
            nx = -slope * cos if b[0] <= s / 2.0 + 1e-6 else slope * cos  # normal exterior (x)
            for nd in (i, i + 1):
                add(caseW, nd, -pn * nx / 2.0, -pn * cos / 2.0)  # hacia afuera / arriba
        else:
            for nd in (i, i + 1):
                add(caseD, nd, 0.0, -col_w * L / 2.0)
            # viento en muros: barlovento +0.8 (columna A, empuja +x), sotavento -0.6 (columna B, succion +x)
            cp = 0.8 if a[0] == 0.0 else 0.6
            for nd in (i, i + 1):
                add(caseW, nd, cp * qw * bay * L / 2.0, 0.0)
    res = solve_frame(nodes, elems, supports, {"D": caseD, "Lr": caseL, "W": caseW})
    rows = []
    crit = {}
    for cname, fac in COMBOS:
        disp, forces = combine(res, None, fac)
        for i, (N, M1, M2) in enumerate(forces):
            k = kinds[i]
            sec = geo["col"] if k == "col" else geo["raf"]
            fy = cfg["fy_w"]
            if k == "col":
                pp, sl = phi_pn(sec, fy, 2.0 * h, cfg["girt_step"])
                pm = phi_mn_w(sec, fy, cfg["girt_step"])
            else:
                pp, sl = phi_pn(sec, fy, math.hypot(s / 2.0, top - h), cfg["purlin_step"])
                pm = phi_mn_w(sec, fy, cfg["purlin_step"])
            Mu = max(abs(M1), abs(M2))
            ratio = interaction(N, pp, Mu, pm)
            if k not in crit or ratio > crit[k]["ratio"]:
                crit[k] = {"ratio": ratio, "combo": cname, "N": N, "M": Mu, "phiPn": pp, "phiMn": pm, "KL_r": sl}
    # deflexiones de servicio
    dL = res["Lr"][0]
    dW = res["W"][0]
    ridge = 4 + nseg
    defl_v = abs(dL[ridge][1])
    drift = max(abs(dW[4][0]), abs(dW[n - 5][0]))
    for k, label, prof in (("col", u"Columnas", geo["col"]["name"]), ("raf", u"Vigas", geo["raf"]["name"])):
        c = crit[k]
        rows.append({"elem": label, "perfil": prof, "combo": c["combo"], "Pu": c["N"], "Mu": c["M"],
                     "cap": u"φPn {:.0f} kN, φMn {:.0f} kN-m, KL/r {:.0f}".format(c["phiPn"], c["phiMn"], c["KL_r"]),
                     "ratio": c["ratio"]})
    rows.append({"elem": u"Deflexión cumbrera (Lr)", "perfil": u"", "combo": u"servicio", "Pu": 0.0, "Mu": 0.0,
                 "cap": u"{:.1f} mm ≤ L/240 = {:.0f} mm".format(defl_v * 1000, s / 240.0 * 1000),
                 "ratio": defl_v / (s / 240.0)})
    rows.append({"elem": u"Desplazamiento alero (W)", "perfil": u"", "combo": u"servicio", "Pu": 0.0, "Mu": 0.0,
                 "cap": u"{:.1f} mm ≤ h/60 = {:.0f} mm".format(drift * 1000, h / 60.0 * 1000),
                 "ratio": drift / (h / 60.0)})
    return rows


# --- cercha -----------------------------------------------------------------------
def check_truss(geo, cfg):
    """geo: nodes [(u, z)], members [(i, j, clase, seccion, nombre)], top
    [nodos del cordon superior en orden], supports (iA, iB), arch (bool),
    bay (m), planes (1 o 2), purlin_kg_m2."""
    nodes = geo["nodes"]
    elems = [(i, j, E * sec["A"], 0.0) for i, j, _, sec, _ in geo["members"]]
    iA, iB = geo["supports"]
    supports = {iA: (1, 1, 0), iB: (1, 1, 0) if geo["arch"] else (0, 1, 0)}
    share = 1.0 / geo["planes"]
    qD = (cfg["dead"] + geo.get("purlin_kg_m2", 6.0) * G_ACC) * geo["bay"] * share
    qL = cfg["live"] * geo["bay"] * share
    caseD, caseL = {}, {}
    top = geo["top"]
    for k, nd in enumerate(top):
        left = abs(nodes[nd][0] - nodes[top[k - 1]][0]) / 2.0 if k > 0 else 0.0
        right = abs(nodes[top[k + 1]][0] - nodes[nd][0]) / 2.0 if k + 1 < len(top) else 0.0
        trib = left + right
        caseD[nd] = (0.0, -qD * trib, 0.0)
        caseL[nd] = (0.0, -qL * trib, 0.0)
    for i, j, _, sec, _ in geo["members"]:
        L = math.hypot(nodes[j][0] - nodes[i][0], nodes[j][1] - nodes[i][1])
        w = sec["kg_m"] * G_ACC * L / 2.0
        for nd in (i, j):
            a = caseD.get(nd, (0.0, 0.0, 0.0))
            caseD[nd] = (a[0], a[1] - w, 0.0)
    res = solve_frame(nodes, elems, supports, {"D": caseD, "Lr": caseL})
    crit = {}
    for cname, fac in ((u"1.4D", {"D": 1.4}), (u"1.2D+1.6Lr", {"D": 1.2, "Lr": 1.6})):
        disp, forces = combine(res, None, fac)
        for (i, j, cls, sec, name), (N, _, _) in zip(geo["members"], forces):
            L = math.hypot(nodes[j][0] - nodes[i][0], nodes[j][1] - nodes[i][1])
            if N >= 0:
                cap = 0.9 * cfg["fy_tube"] * 1000.0 * sec["A"]
                sl = 0.0
            else:
                cap, sl = phi_pn(sec, cfg["fy_tube"], L, L)
            ratio = abs(N) / cap
            if cls not in crit or ratio > crit[cls]["ratio"]:
                crit[cls] = {"ratio": ratio, "combo": cname, "N": N, "cap": cap, "name": name, "L": L, "sl": sl}
    dL = res["Lr"][0]
    defl = max(abs(d[1]) for d in dL)
    span = abs(nodes[iB][0] - nodes[iA][0])
    rows = []
    for cls, label in (("cordon_sup", u"Cordón superior"), ("cordon_inf", u"Cordón inferior"),
                       ("montante", u"Montantes"), ("diagonal", u"Diagonales")):
        if cls in crit:
            c = crit[cls]
            rows.append({"elem": label, "perfil": c["name"], "combo": c["combo"], "Pu": c["N"], "Mu": 0.0,
                         "cap": u"{} {:.0f} kN (L {:.2f} m{})".format(u"φTn" if c["N"] >= 0 else u"φPn",
                                                                       c["cap"], c["L"],
                                                                       u", KL/r {:.0f}".format(c["sl"]) if c["sl"] else u""),
                         "ratio": c["ratio"]})
    rows.append({"elem": u"Deflexión máx. (Lr)", "perfil": u"", "combo": u"servicio", "Pu": 0.0, "Mu": 0.0,
                 "cap": u"{:.1f} mm ≤ L/240 = {:.0f} mm".format(defl * 1000, span / 240.0 * 1000),
                 "ratio": defl / (span / 240.0)})
    return rows


# --- lectura del modelo (Revit) ---------------------------------------------------
FT = 0.3048


def _comment(DB, e):
    try:
        p = e.get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
    except Exception:
        return u""
    return (p.AsString() or u"") if p is not None else u""


def _json(text):
    j = text.find(u"{")
    try:
        return json.loads(text[j:]) if j >= 0 else {}
    except ValueError:
        return {}


def _tmm(doc, e, name, default=0.0):
    t = doc.GetElement(e.GetTypeId())
    p = t.LookupParameter(name) if t is not None else None
    return p.AsDouble() * 304.8 if p is not None else default


def _tname(doc, e):
    from Autodesk.Revit.DB import Element
    return Element.Name.GetValue(doc.GetElement(e.GetTypeId()))


def _tagged(doc, DB, prefix):
    out = []
    for e in DB.FilteredElementCollector(doc).WhereElementIsNotElementType():
        t = _comment(DB, e)
        if t.startswith(prefix + u" "):
            out.append((e, t[len(prefix) + 1:]))
    return out


def purlin_info(doc, DB):
    """(nombre, seccion, separacion m, fy) de las correas BOKI, o None."""
    for e, text in _tagged(doc, DB, u"BOKI Correas"):
        if text.startswith(u"correa "):
            cfg = _json(text)
            name = cfg.get("purlin", u"C150x50x15x2.5")
            nums = [float(x) for x in name.replace(u"TR ", u"").replace(u"C", u"").split(u"x")]
            if name.startswith(u"TR"):
                sec = tube_section(*nums)
                fy = DEFAULTS["fy_tube"]
            else:
                sec = c_section(*nums)
                fy = DEFAULTS["fy_c"]
            sec["kg_m"] = sec["A"] * STEEL
            return name, sec, float(cfg.get("spacing", 1.2)), fy
    return None


def read_porticos(doc, DB):
    """Portico tipico interior: dict para check_portico, o None."""
    cols, rafs = {}, {}
    for e, text in _tagged(doc, DB, u"BOKI Portico"):
        w = text.split(u" ")
        if len(w) >= 2 and w[0] == u"columna":
            cols.setdefault(w[1], []).append(e)
        elif len(w) >= 2 and w[0] == u"viga":
            rafs.setdefault(w[1], []).append(e)
    names = [n for n in cols if len(cols[n]) == 2 and len(rafs.get(n, [])) == 2]
    if not names:
        return None
    pts = dict((n, cols[n][0].Location.Point) for n in names)
    o = pts[names[0]]
    a, b = [c.Location.Point for c in cols[names[0]]]
    d = DB.XYZ(b.X - a.X, b.Y - a.Y, 0).Normalize()
    along = DB.XYZ.BasisZ.CrossProduct(d)
    names.sort(key=lambda n: (pts[n] - o).DotProduct(along))
    n = names[len(names) // 2]
    c0, c1 = cols[n]
    pa, pb = c0.Location.Point, c1.Location.Point
    span = DB.XYZ(pb.X - pa.X, pb.Y - pa.Y, 0).GetLength() * FT
    ends = [r.Location.Curve.GetEndPoint(k) for r in rafs[n] for k in (0, 1)]
    base = min(pa.Z, pb.Z)
    top = (max(q.Z for q in ends) - base) * FT
    eave = (c0.get_BoundingBox(None).Max.Z - base) * FT
    k = names.index(n)
    bays = []
    for m in (k - 1, k + 1):
        if 0 <= m < len(names):
            bays.append(abs((pts[names[m]] - pts[n]).DotProduct(along)) * FT)
    bay = sum(bays) / 2.0 if len(bays) == 2 else (bays[0] if bays else 6.0)
    pcfg = _json(_comment(DB, c0))

    def wsec(e, name):
        s = w_section(_tmm(doc, e, u"d", 351), _tmm(doc, e, u"bf", 204), _tmm(doc, e, u"tw", 8.6), _tmm(doc, e, u"tf", 15))
        s["kg_m"] = s["A"] * STEEL
        s["name"] = u"{} ({})".format(name, _tname(doc, e))
        return s
    geo = {"span": span, "eave": eave, "ridge": top, "bay": bay, "frame": n,
           "col": wsec(c0, pcfg.get("column", u"")), "raf": wsec(rafs[n][0], pcfg.get("rafter", u""))}
    p = purlin_info(doc, DB)
    if p:
        geo["purlin_kg_m2"] = p[1]["kg_m"] / p[2]
    return geo


def read_truss(doc, DB):
    """Cercha tipica interior (un plano): dict para check_truss, o None."""
    by = {}
    for e, text in _tagged(doc, DB, u"BOKI Cercha"):
        w = text.split(u" ")
        if len(w) >= 2 and isinstance(getattr(e, "Location", None), DB.LocationCurve):
            by.setdefault(w[1], []).append((w[0], e, _json(text)))
    if not by:
        return None
    names = sorted(by)
    # orden a lo largo de la cubierta
    first = [e for k, e, c in by[names[0]] if k == u"cordon_inf"][0].Location.Curve
    a, b = first.GetEndPoint(0), first.GetEndPoint(1)
    d = DB.XYZ(b.X - a.X, b.Y - a.Y, 0).Normalize()
    along = DB.XYZ.BasisZ.CrossProduct(d)
    mid = lambda n: [e for k, e, c in by[n] if k == u"cordon_inf"][0].Location.Curve.Evaluate(0.5, True)
    names.sort(key=lambda n: (mid(n) - a).DotProduct(along))
    n = names[len(names) // 2]
    items = by[n]
    cfg = items[0][2]
    offs = sorted(set(round(e.Location.Curve.Evaluate(0.5, True).DotProduct(along) * FT, 3)
                      for k, e, c in items if k.startswith(u"cordon")))
    planes = len(offs)
    plane0 = lambda e: abs(e.Location.Curve.Evaluate(0.5, True).DotProduct(along) * FT - offs[0]) < 0.02
    mem = [(k, e) for k, e, c in items if k != u"travesano" and plane0(e)]
    infs = [e.Location.Curve for k, e in mem if k == u"cordon_inf"]
    zs = [q.Z for c in infs for q in (c.GetEndPoint(0), c.GetEndPoint(1))]
    base = min(zs)
    orig = min([c.GetEndPoint(kk) for c in infs for kk in (0, 1)], key=lambda q: (q - a).DotProduct(d))
    loc = lambda q: (round((q - orig).DotProduct(d) * FT, 3), round((q.Z - base) * FT, 3))
    nodes, index = [], {}

    def node(q):
        p = loc(q)
        key = (round(p[0], 2), round(p[1], 2))
        if key not in index:
            index[key] = len(nodes)
            nodes.append(p)
        return index[key]
    members = []
    webs = [(k, e) for k, e in mem if not k.startswith(u"cordon")]
    for k, e in webs:
        c = e.Location.Curve
        i, j = node(c.GetEndPoint(0)), node(c.GetEndPoint(1))
        sec = tube_section(_tmm(doc, e, u"Ht", 50), _tmm(doc, e, u"b", 50), _tmm(doc, e, u"t", 3))
        sec["kg_m"] = sec["A"] * STEEL
        members.append((i, j, k, sec, _tname(doc, e)))
    top_nodes = []
    for k, e in mem:
        if not k.startswith(u"cordon"):
            continue
        c = e.Location.Curve
        node(c.GetEndPoint(0))
        node(c.GetEndPoint(1))
        sec = tube_section(_tmm(doc, e, u"Ht", 100), _tmm(doc, e, u"b", 50), _tmm(doc, e, u"t", 3))
        sec["kg_m"] = sec["A"] * STEEL
        on = []
        for idx, p in enumerate(nodes):
            q = orig + d * (p[0] / FT) + DB.XYZ.BasisZ * (p[1] / FT)
            r = c.Project(q)
            if r is not None and r.Distance < 0.01 / FT:
                on.append((r.Parameter, idx))
        on.sort()
        seq = [idx for _, idx in on]
        for i, j in zip(seq[:-1], seq[1:]):
            members.append((i, j, k, sec, _tname(doc, e)))
        if k == u"cordon_sup":
            top_nodes += seq
    top_nodes = sorted(set(top_nodes), key=lambda i: nodes[i][0])
    bottom = [i for i, p in enumerate(nodes) if abs(p[1]) < 0.02]
    iA = min(bottom, key=lambda i: nodes[i][0])
    iB = max(bottom, key=lambda i: nodes[i][0])
    k = names.index(n)
    bays = []
    for m in (k - 1, k + 1):
        if 0 <= m < len(names):
            bays.append(abs((mid(names[m]) - mid(n)).DotProduct(along)) * FT)
    bay = sum(bays) / 2.0 if len(bays) == 2 else (bays[0] if bays else 5.0)
    geo = {"nodes": nodes, "members": members, "top": top_nodes, "supports": (iA, iB),
           "arch": cfg.get("shape") == u"arco", "bay": bay, "planes": planes, "frame": n,
           "shape": cfg.get("shape", u""), "span": abs(nodes[iB][0] - nodes[iA][0])}
    p = purlin_info(doc, DB)
    if p:
        geo["purlin_kg_m2"] = p[1]["kg_m"] / p[2]
    return geo


def verify(doc, DB, cfg=None):
    """Todas las verificaciones: [(titulo, filas)]."""
    cfg = config(cfg)
    out = []
    g = read_porticos(doc, DB)
    if g:
        out.append((u"Pórtico {} (luz {:.2f} m, alero {:.2f} m, paño {:.2f} m)".format(
            g["frame"], g["span"], g["eave"], g["bay"]), check_portico(g, cfg)))
    t = read_truss(doc, DB)
    if t:
        out.append((u"Cercha {} ({}, luz {:.2f} m, {} plano(s), paño {:.2f} m)".format(
            t["frame"], t["shape"], t["span"], t["planes"], t["bay"]), check_truss(t, cfg)))
    p = purlin_info(doc, DB)
    if p:
        span = g["bay"] if g else (t["bay"] if t else 6.0)
        out.append((u"Correas", check_purlin({"name": p[0], "sec": p[1], "span": span, "spacing": p[2], "fy": p[3]}, cfg)))
    return out


def check_purlin(p, cfg):
    """p: name, sec (c_section/tube_section con kg_m), span (m), spacing
    (m), fy (MPa)."""
    sec = p["sec"]
    wD = cfg["dead"] * p["spacing"] + sec["kg_m"] * G_ACC
    wL = cfg["live"] * p["spacing"]
    wu = max(1.4 * wD, 1.2 * wD + 1.6 * wL)
    Mu = wu * p["span"] ** 2 / 8.0
    phiMn = 0.9 * p["fy"] * 1000.0 * sec["Sx"]
    # deflexiones (techo sin cielo raso, IBC tabla 1604.3; la E.090 no fija limite):
    # Lr sola L/180, D + Lr L/120
    k = 5 * p["span"] ** 4 / (384.0 * E * sec["Ix"])
    dL, dT = k * wL, k * (wD + wL)
    return [{"elem": u"Correas", "perfil": p["name"], "combo": u"1.2D+1.6Lr", "Pu": 0.0, "Mu": Mu,
             "cap": u"φMn {:.1f} kN-m (sección bruta), luz {:.2f} m".format(phiMn, p["span"]),
             "ratio": Mu / phiMn},
            {"elem": u"Deflexión correas (Lr)", "perfil": p["name"], "combo": u"servicio", "Pu": 0.0, "Mu": 0.0,
             "cap": u"{:.1f} mm ≤ L/180 = {:.0f} mm".format(dL * 1000, p["span"] / 180.0 * 1000),
             "ratio": dL / (p["span"] / 180.0)},
            {"elem": u"Deflexión correas (D+Lr)", "perfil": p["name"], "combo": u"servicio", "Pu": 0.0, "Mu": 0.0,
             "cap": u"{:.1f} mm ≤ L/120 = {:.0f} mm".format(dT * 1000, p["span"] / 120.0 * 1000),
             "ratio": dT / (p["span"] / 120.0)}]
