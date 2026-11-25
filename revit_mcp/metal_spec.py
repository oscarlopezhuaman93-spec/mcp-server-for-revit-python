# -*- coding: utf-8 -*-
"""BOKI Metalicas - reglas de los porticos de alma llena (nave industrial).

Reglas PROPIAS de las estructuras metalicas: no importa nada de los botones
de acero de refuerzo (rebar_*, viga_*, columna_*, ...), y ellos no importan
esto; un cambio aqui solo afecta a Porticos (2026-11-25, pedido del usuario:
"reglas independientes para no tener el mismo inconveniente").

Puro Python (sin Revit): perfiles W, geometria del portico, cantidades y
pesos. Unidades: metros y kilogramos, salvo donde dice mm.
Norma de referencia: E.090 / AISC 360-16 (LRFD); acero A572 Gr.50 / A36.
"""
import json
import math

STEEL_DENSITY = 7850.0  # kg/m3

# Perfiles W ofrecidos: nombre imperial -> datos del tipo metrico de la
# familia de Revit "W-Ala ancha" (biblioteca Spanish): d, bf, tw, tf en mm;
# el peso en kg/m es el numero del nombre metrico (W360X72 = 72 kg/m).
W_PROFILES = [
    ("W8x10", "W200X15", 200, 100, 4.32, 5.21),
    ("W8x18", "W200X26.6", 207, 133, 5.84, 8.38),
    ("W10x22", "W250X32.7", 259, 146, 6.10, 9.14),
    ("W10x30", "W250X44.8", 267, 148, 7.62, 13.0),
    ("W12x26", "W310X38.7", 310, 165, 5.84, 9.65),
    ("W12x35", "W310X52", 318, 167, 7.62, 13.2),
    ("W12x40", "W310X60", 302, 203, 7.49, 13.1),
    ("W14x30", "W360X44", 351, 171, 6.86, 9.78),
    ("W14x38", "W360X57.8", 358, 172, 7.87, 13.1),
    ("W14x48", "W360X72", 351, 204, 8.64, 15.1),
    ("W14x61", "W360X91", 353, 254, 9.53, 16.4),
    ("W16x26", "W410X38.8", 399, 140, 6.35, 8.76),
    ("W16x31", "W410X46.1", 404, 140, 6.99, 11.2),
    ("W16x40", "W410X60", 406, 178, 7.75, 12.8),
    ("W18x35", "W460X52", 450, 152, 7.62, 10.8),
    ("W18x50", "W460X74", 457, 191, 9.02, 14.5),
    ("W21x44", "W530X66", 526, 165, 8.89, 11.4),
    ("W21x55", "W530X82", 528, 209, 9.53, 13.3),
    ("W24x55", "W610X82", 599, 178, 10.0, 12.8),
    ("W24x68", "W610X101", 602, 228, 10.5, 14.9),
]

FRAME_FAMILY = u"W-Ala ancha"  # Armazon estructural (vigas)
COLUMN_FAMILY = u"W-Ala ancha-Pilar"  # Pilares estructurales
TAG = u"BOKI Portico"  # comentario de todo lo que crea el boton

DEFAULTS = {
    "span": 20.0,  # m, eje a eje de columnas
    "eave": 7.0,  # m, altura de alero sobre la base
    "slope": 10.0,  # % de pendiente del techo
    "column": "W14x48",
    "rafter": "W16x31",
    "haunch": 1.50,  # m, largo de cartela en alero y cumbrera
    "tie": "W8x10",  # viga de amarre en el alero (a lo largo de la nave)
    "plate": [400.0, 400.0, 25.0],  # mm, placa base
    "bolts": 4,  # pernos de anclaje (base articulada)
    "bolt_d": 19.05,  # mm (3/4")
    "grout": 25.0,  # mm
    "base": "pinned",  # base articulada (pedido del usuario)
}


class SpecError(Exception):
    pass


def profile(name):
    """(imperial, metric, d, bf, tw, tf) of a W name (either naming)."""
    key = (name or u"").strip().upper().replace(u" ", u"")
    for p in W_PROFILES:
        if key in (p[0].upper(), p[1].upper()):
            return p
    raise SpecError(u"perfil no disponible: {}".format(name))


def kg_per_m(name):
    return float(profile(name)[1].split("X")[1])


def depth_m(name):
    return profile(name)[2] / 1000.0


def config(data=None):
    """The settings with defaults filled and checked (raises SpecError)."""
    cfg = dict(DEFAULTS)
    cfg.update(dict((k, v) for k, v in (data or {}).items() if v is not None))
    for k in ("span", "eave", "slope", "haunch", "grout"):
        cfg[k] = float(cfg[k])
    if not 6.0 <= cfg["span"] <= 40.0:
        raise SpecError(u"luz fuera de rango (6 a 40 m para alma llena)")
    if not 3.0 <= cfg["eave"] <= 15.0:
        raise SpecError(u"altura de alero fuera de rango (3 a 15 m)")
    if not 3.0 <= cfg["slope"] <= 30.0:
        raise SpecError(u"pendiente fuera de rango (3 a 30 %)")
    if not 0.0 <= cfg["haunch"] <= cfg["span"] / 4.0:
        raise SpecError(u"cartela mayor que L/4")
    for k in ("column", "rafter", "tie"):
        cfg[k] = profile(cfg[k])[0]
    cfg["plate"] = [float(x) for x in cfg["plate"]]
    cfg["bolts"] = int(cfg["bolts"])
    return cfg


def dumps(cfg):
    return json.dumps(cfg, sort_keys=True)


def loads(text):
    try:
        return config(json.loads(text))
    except (ValueError, TypeError, SpecError):
        return None


def rise(cfg):
    """Altura de la cumbrera sobre el alero (m)."""
    return cfg["span"] / 2.0 * cfg["slope"] / 100.0


def rafter_length(cfg):
    """Largo de cada viga inclinada, eje de columna a cumbrera (m)."""
    return math.hypot(cfg["span"] / 2.0, rise(cfg))


def frame_points(cfg):
    """Puntos del portico en su plano: u a lo largo de la luz (0 = eje de
    la columna izquierda), z sobre la base. Lineas de eje de las piezas:
    columnas de z 0 al alero, vigas del alero a la cumbrera (cara superior
    del acero sobre esa linea)."""
    s, h = cfg["span"], cfg["eave"]
    t = cfg["slope"] / 100.0
    c = depth_m(cfg["column"]) / 2.0  # la viga llega a la cara de la columna (plancha de extremo)
    return {
        "col_left": ((0.0, 0.0), (0.0, h)),
        "col_right": ((s, 0.0), (s, h)),
        "rafter_left": ((c, h + c * t), (s / 2.0, h + rise(cfg))),
        "rafter_right": ((s - c, h + c * t), (s / 2.0, h + rise(cfg))),
    }


def haunch_triangles(cfg):
    """Cartelas (triangulo del alma) en el plano del portico, bajo la viga:
    en cada alero desde la cara de la columna, y en la cumbrera. Su peralte
    en el arranque = el de la viga (cartela tipica). [(u, z) x 3] por pieza."""
    L = cfg["haunch"]
    if L <= 0:
        return []
    s, h = cfg["span"], cfg["eave"]
    t = cfg["slope"] / 100.0
    d = depth_m(cfg["rafter"])
    c = depth_m(cfg["column"]) / 2.0
    cos = 1.0 / math.sqrt(1.0 + t * t)

    def bottom(u):  # cara inferior de la viga izquierda a la distancia u
        uu = min(u, s - u)
        return h + uu * t - d / cos

    out = []
    for side in (1, -1):
        u0 = c if side == 1 else s - c
        u1 = u0 + side * L * cos
        out.append([(u0, bottom(u0)), (u1, bottom(u1)), (u0, bottom(u0) - d)])
    m = s / 2.0
    half = L * cos / 2.0
    out.append([(m - half, bottom(m - half)), (m + half, bottom(m + half)), (m, bottom(m) - d / 2.0)])
    return out


def haunch_flanges(cfg):
    """Bordes con ala inferior de cada cartela: en el alero la hipotenusa;
    en la cumbrera los dos lados inclinados (simetrica, como se fabrica)."""
    out = []
    tris = haunch_triangles(cfg)
    for i, tri in enumerate(tris):
        if i < 2:
            out.append([(tri[1], tri[2])])
        else:
            out.append([(tri[0], tri[2]), (tri[1], tri[2])])
    return out


def triangle_area(tri):
    (a, b), (c, d), (e, f) = tri
    return abs((c - a) * (f - b) - (e - a) * (d - b)) / 2.0


def summary(cfg, frames, bays):
    """Cantidades y pesos (kg) antes de generar: `frames` porticos y
    `bays` paños entre ellos (vigas de amarre a cada lado)."""
    col_len = cfg["eave"]
    (u0, z0), (u1, z1) = frame_points(cfg)["rafter_left"]
    raf_len = math.hypot(u1 - u0, z1 - z0)  # de la cara de la columna a la cumbrera
    bay = cfg.get("spacing", 6.0)
    tw = profile(cfg["rafter"])[4] / 1000.0
    bf, tf = profile(cfg["rafter"])[3] / 1000.0, profile(cfg["rafter"])[5] / 1000.0
    haunch_kg = 0.0
    for tri, edges in zip(haunch_triangles(cfg), haunch_flanges(cfg)):
        flange = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in edges)
        haunch_kg += (triangle_area(tri) * tw + flange * bf * tf) * STEEL_DENSITY
    a, b, t = [x / 1000.0 for x in cfg["plate"]]
    plate_kg = a * b * t * STEEL_DENSITY
    out = {
        "frames": frames,
        "columns": 2 * frames,
        "rafters": 2 * frames,
        "haunches": len(haunch_triangles(cfg)) * frames,
        "plates": 2 * frames,
        "bolts": 2 * frames * cfg["bolts"],
        "ties": 2 * bays,
        "kg_columns": 2 * frames * col_len * kg_per_m(cfg["column"]),
        "kg_rafters": 2 * frames * raf_len * kg_per_m(cfg["rafter"]),
        "kg_ties": 2 * bays * bay * kg_per_m(cfg["tie"]),
        "kg_haunches": frames * haunch_kg,
        "kg_plates": 2 * frames * plate_kg,
        "col_len": col_len,
        "raf_len": raf_len,
    }
    out["kg_total"] = sum(v for k, v in out.items() if k.startswith("kg_"))
    return out
