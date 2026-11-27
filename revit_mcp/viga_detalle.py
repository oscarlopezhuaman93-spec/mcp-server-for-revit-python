# -*- coding: utf-8 -*-
"""Detalle del alzado de Acero Viga (2026-11-27): the elevation the window
shows, drawn in a Revit drafting view at true size - concrete, supports,
stirrups where they go, main bars with their hooks and laps, bastones and
balancines with their labels and dimensions from the support faces, the
clear spans, each span's stirrup distribution and a title. Its own rules
here (the window's elevation data in, detail lines and texts out)."""
import math

from Autodesk.Revit import DB
from System.Collections.Generic import List

FT = 0.3048
SCALE = 25
STYLES = {  # line styles: name -> (rgb, weight)
    u"BOKI Detalle Concreto": ((90, 90, 90), 3),
    u"BOKI Detalle Apoyo": ((150, 150, 150), 1),
    u"BOKI Detalle Acero": ((30, 90, 200), 5),
    u"BOKI Detalle Baston": ((30, 140, 230), 5),
    u"BOKI Detalle Balancin": ((216, 90, 48), 5),
    u"BOKI Detalle Estribo": ((230, 120, 40), 1),
    u"BOKI Detalle Cota": ((0, 120, 200), 1),
}
TEXTS = {  # text types: name -> (paper mm, rgb, bold)
    u"BOKI Detalle Titulo": (3.5, (0, 0, 0), True),
    u"BOKI Detalle Texto": (2.2, (0, 0, 0), False),
    u"BOKI Detalle Cota": (2.0, (0, 120, 200), False),
    u"BOKI Detalle Empalme": (2.0, (200, 40, 40), False),  # opaque: over the stirrups
}
OPAQUE = (u"BOKI Detalle Empalme",)


def _name(e):
    p = e.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM)
    return p.AsString() if p else e.Name


def ensure_styles(doc):
    """Its line styles and text types (made once; the user's edits kept)."""
    cat = doc.Settings.Categories.get_Item(DB.BuiltInCategory.OST_Lines)
    subs = dict((s.Name, s) for s in cat.SubCategories)
    for name, (rgb, weight) in STYLES.items():
        if name in subs:
            continue
        s = doc.Settings.Categories.NewSubcategory(cat, name)
        s.LineColor = DB.Color(rgb[0], rgb[1], rgb[2])
        s.SetLineWeight(weight, DB.GraphicsStyleType.Projection)
    texts = dict((_name(t), t) for t in DB.FilteredElementCollector(doc).OfClass(DB.TextNoteType))
    base = texts.get(u"BOKI Cuadro 2mm") or list(texts.values())[0]
    for name, (size, rgb, bold) in TEXTS.items():
        if name in texts:
            continue
        t = base.Duplicate(name)
        t.get_Parameter(DB.BuiltInParameter.TEXT_SIZE).Set(size / 304.8)
        t.get_Parameter(DB.BuiltInParameter.LINE_COLOR).Set(rgb[0] + rgb[1] * 256 + rgb[2] * 65536)
        for bip, v in ((DB.BuiltInParameter.TEXT_STYLE_BOLD, 1 if bold else 0),
                       (DB.BuiltInParameter.TEXT_BACKGROUND, 0 if name in OPAQUE else 1)):
            p = t.get_Parameter(bip)
            if p is not None and not p.IsReadOnly:
                p.Set(v)
        p = t.get_Parameter(DB.BuiltInParameter.TEXT_FONT)
        if p is not None and not p.IsReadOnly:
            p.Set(u"Bahnschrift")


class Drawer(object):
    def __init__(self, doc, view):
        self.doc, self.view = doc, view
        cat = doc.Settings.Categories.get_Item(DB.BuiltInCategory.OST_Lines)
        self.styles = dict((s.Name, s.GetGraphicsStyle(DB.GraphicsStyleType.Projection)) for s in cat.SubCategories)
        self.texts = dict((_name(t), t) for t in DB.FilteredElementCollector(doc).OfClass(DB.TextNoteType))
        self.paper = SCALE / 1000.0  # m of model per mm of paper

    @staticmethod
    def p(s, y):
        return DB.XYZ(s / FT, y / FT, 0.0)

    def line(self, a, b, style):
        if math.hypot(b[0] - a[0], b[1] - a[1]) < 0.002:
            return
        c = self.doc.Create.NewDetailCurve(self.view, DB.Line.CreateBound(self.p(*a), self.p(*b)))
        gs = self.styles.get(style)
        if gs is not None:
            c.LineStyle = gs

    def poly(self, pts, style, closed=False):
        pts = list(pts)
        for a, b in zip(pts, pts[1:] + (pts[:1] if closed else [])):
            self.line(a, b, style)

    def text(self, s, y, text, kind=u"BOKI Detalle Texto", align=u"center", rotate=False):
        t = self.texts.get(kind)
        opts = DB.TextNoteOptions(t.Id if t else self.doc.GetDefaultElementTypeId(DB.ElementTypeGroup.TextNoteType))
        opts.HorizontalAlignment = {u"center": DB.HorizontalTextAlignment.Center,
                                    u"left": DB.HorizontalTextAlignment.Left,
                                    u"right": DB.HorizontalTextAlignment.Right}[align]
        opts.VerticalAlignment = DB.VerticalTextAlignment.Middle
        if rotate:
            opts.Rotation = math.pi / 2.0
        DB.TextNote.Create(self.doc, self.view.Id, self.p(s, y), text, opts)

    def cota(self, s0, s1, y, text=None):
        """A dimension line from s0 to s1 at height y: ticks and its value."""
        k = self.paper
        self.line((s0, y), (s1, y), u"BOKI Detalle Cota")
        for s in (s0, s1):
            self.line((s, y - 1.2 * k), (s, y + 1.2 * k), u"BOKI Detalle Cota")
            self.line((s - 0.8 * k, y - 0.8 * k), (s + 0.8 * k, y + 0.8 * k), u"BOKI Detalle Cota")
        self.text((s0 + s1) / 2.0, y + 1.8 * k, text or u"{:.2f}".format(abs(s1 - s0)), u"BOKI Detalle Cota")


def draw(doc, view, elev, info):
    """The elevation `elev` (the window's _views_data) in `view`. info:
    {"title", "top_label", "bot_label", "stirrup_text"}."""
    d = Drawer(doc, view)
    k = d.paper
    tops = [y for _, y in elev.get("top") or []] or [0.2]
    bots = [y for _, y in elev.get("bottom") or []] or [-0.2]
    y_top, y_bot = max(tops), min(bots)
    h = y_top - y_bot
    # supports: the column (or wall) under / over the beam, cut a little past it
    for sp in elev.get("supports") or []:
        a, b = sp["s0"], sp["s1"]
        lo, hi = y_bot - max(0.5, h), y_top + 0.3
        d.poly([(a, lo), (a, hi)], u"BOKI Detalle Apoyo")
        d.poly([(b, lo), (b, hi)], u"BOKI Detalle Apoyo")
        for z in (lo, hi):  # break marks
            d.line((a, z), (b, z), u"BOKI Detalle Apoyo")
    # the concrete
    for outline in elev.get("outlines") or []:
        d.poly(outline, u"BOKI Detalle Concreto", closed=True)
    cover = float(elev.get("cover") or 0.04)
    # stirrups where they go
    for sp in elev.get("spans") or []:
        a = sp.get("a", 0.0)
        for fam in ("edge", "conf"):
            f = sp.get(fam)
            if not f:
                continue
            for s, _ in f.get("tagged") or []:
                d.line((a + s, y_bot + cover), (a + s, y_top - cover), u"BOKI Detalle Estribo")
            break  # one family is enough on the drawing (they share positions)
    # the bars (main, and the extras drawn in their own colors below)
    extra_paths = set()
    for item in elev.get("extras") or []:
        extra_paths.add(id(item["path"]))
    for path, _r in elev.get("bar_paths") or []:
        pts = [(q[2], q[1]) for q in path]
        style = u"BOKI Detalle Acero"
        if id(path) in extra_paths:
            continue
        d.poly(pts, style)
    for item in elev.get("extras") or []:
        pts = [(q[2], q[1]) for q in item["path"]]
        top = item["face"] == u"sup"
        d.poly(pts, u"BOKI Detalle Baston" if top else u"BOKI Detalle Balancin")
        ss = [p[0] for p in pts]
        mid = (min(ss) + max(ss)) / 2.0
        y_lab = (max(p[1] for p in pts) + 2.5 * k) if top else (min(p[1] for p in pts) - 2.5 * k)
        d.text(mid, y_lab, item["label"])
        # its dimensions from the support faces, in rows over / under the beam
        for c in item.get("cotas") or []:
            row = c.get("row", 0)
            y_c = (y_top + (6 + 5 * row) * k) if top else (y_bot - (6 + 5 * row) * k)
            d.cota(c["lo"], c["hi"], y_c)
    # laps
    for s0, s1, key, top in elev.get("laps") or []:  # inside, a quarter in from its face: off the bars and cotas
        y = (y_top - 0.3 * h) if top else (y_bot + 0.3 * h)
        d.text((s0 + s1) / 2.0, y, u"Emp. Ø{} {:.2f}".format(key, s1 - s0), u"BOKI Detalle Empalme")
    # hooks
    for s, leg, _tag in elev.get("hooks") or []:
        if leg:
            d.text(s, y_top + 2.5 * k, u"Gancho {:.2f}".format(leg), u"BOKI Detalle Cota")
    # the main bars' labels, at the start
    s_first = min([sp["s0"] for sp in elev.get("supports") or []] + [0.0])
    if info.get("top_label"):
        d.text(s_first - 2 * k, y_top - cover, info["top_label"], align=u"right")
    if info.get("bot_label"):
        d.text(s_first - 2 * k, y_bot + cover, info["bot_label"], align=u"right")
    # clear spans and each span's stirrups
    n_bot = (elev.get("cota_rows") or (0, 0))[1]
    y_span = y_bot - (6 + 5 * n_bot + 4) * k
    for sp in elev.get("spans") or []:
        a, b = sp.get("a", 0.0), sp.get("b", 0.0)
        d.cota(a, b, y_span)
    for a, b, text, _tag in elev.get("span_labels") or []:
        d.text((a + b) / 2.0, y_span - 5 * k, info.get("stirrup_text") or text)
    # title
    s_all = [p for o in elev.get("outlines") or [] for p in o]
    s_mid = (min(p[0] for p in s_all) + max(p[0] for p in s_all)) / 2.0 if s_all else 0.0
    d.text(s_mid, y_span - 14 * k, info.get("title") or u"DETALLE DE VIGA", u"BOKI Detalle Titulo")
    d.text(s_mid, y_span - 19 * k, u"ESC. 1/{}".format(SCALE))


def make(doc, elev, info, name):
    """A new drafting view `name` (a number added if taken) with the detail.
    Returns the view."""
    vft = [v for v in DB.FilteredElementCollector(doc).OfClass(DB.ViewFamilyType)
           if v.ViewFamily == DB.ViewFamily.Drafting][0]
    names = set(v.Name for v in DB.FilteredElementCollector(doc).OfClass(DB.View))
    final, n = name, 2
    while final in names:
        final = u"{} ({})".format(name, n)
        n += 1
    t = DB.Transaction(doc, u"Detalle de viga")
    t.Start()
    try:
        ensure_styles(doc)
        view = DB.ViewDrafting.Create(doc, vft.Id)
        view.Name = final
        view.Scale = SCALE
        draw(doc, view, elev, info)
        t.Commit()
    except Exception:
        t.RollBack()
        raise
    return view
