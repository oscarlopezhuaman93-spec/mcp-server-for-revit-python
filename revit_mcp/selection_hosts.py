# -*- coding: utf-8 -*-
"""What the user picked, as the Acero windows want it: a bar (Rebar) stands
for the element it is in (its host: a column, a beam, a wall, a slab...),
a roof brick placed by Acero Losa for its slab. Shared by every Acero
window (each still filters what it takes)."""
from pyrevit import DB

BRICK_TAG = u"BOKI LOSA "


def host_of(doc, element):
    """The element a picked bar or brick belongs to (or the element itself)."""
    if element is None:
        return None
    if isinstance(element, DB.Structure.Rebar):
        host = doc.GetElement(element.GetHostId())
        return host if host is not None else element
    if isinstance(element, DB.FamilyInstance):
        c = element.get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
        text = (c.AsString() or u"") if c is not None else u""
        if text.startswith(BRICK_TAG):
            try:
                floor = doc.GetElement(DB.ElementId(int(text[len(BRICK_TAG):].strip())))
                if floor is not None:
                    return floor
            except ValueError:
                pass
        host = getattr(element, "Host", None)
        if isinstance(host, DB.Floor) and u"LADRILLO" in (element.Symbol.FamilyName or u"").upper():
            return host
    return element


def expand(doc, ids):
    """The picked elements, each bar / brick replaced by its host, once each."""
    out, seen = [], set()
    for i in ids:
        e = host_of(doc, doc.GetElement(i))
        if e is None or e.Id.IntegerValue in seen:
            continue
        seen.add(e.Id.IntegerValue)
        out.append(e)
    return out
