# -*- coding: utf-8 -*-
"""Loose bars of one element joined into Revit rebar sets.

Bars of the same type and the same shape, in a line and equally spaced,
become one set (Revit tags it "3 Ø5/8""), only when every bar stays where
it was (within GROUP_TOL_M). A pair whose hook bends the way the bars are
spaced can't be a Revit set: it is left loose."""
from Autodesk.Revit import DB
from Autodesk.Revit.DB.Structure import RebarLayoutRule, MultiplanarOption

FT = 0.3048
GROUP_TOL_M = 0.003


def _points(rebar, k=0):
    curves = list(rebar.GetCenterlineCurves(False, False, False, MultiplanarOption.IncludeAllMultiplanarCurves, k))
    if rebar.NumberOfBarPositions > 1:
        t = rebar.GetShapeDrivenAccessor().GetBarPositionTransform(k)
        curves = [c.CreateTransformed(t) for c in curves]
    return [c.GetEndPoint(0) for c in curves] + [curves[-1].GetEndPoint(1)]


def _signature(rebar):
    pts = _points(rebar)
    o = pts[0]
    return (rebar.GetTypeId().IntegerValue, rebar.GetHostId().IntegerValue,
            tuple((round((p - o).X * FT, 3), round((p - o).Y * FT, 3), round((p - o).Z * FT, 3)) for p in pts))


def _lines(rebars):
    """Runs of bars in a line, equally spaced (largest found first)."""
    origin = dict((r.Id, _points(r)[0]) for r in rebars)
    used, out = set(), []
    for a in rebars:
        for b in rebars:
            if a.Id == b.Id or a.Id in used or b.Id in used:
                continue
            d = origin[b.Id] - origin[a.Id]
            if d.GetLength() < 1e-6:
                continue
            u = d.Normalize()
            line = []
            for r in rebars:
                if r.Id in used:
                    continue
                v = origin[r.Id] - origin[a.Id]
                if (v - u * v.DotProduct(u)).GetLength() * FT < GROUP_TOL_M:
                    line.append(r)
            line.sort(key=lambda r: (origin[r.Id] - origin[a.Id]).DotProduct(u))
            s = [(origin[r.Id] - origin[line[0].Id]).DotProduct(u) for r in line]
            gaps = [s[i + 1] - s[i] for i in range(len(s) - 1)]
            if len(line) >= 2 and (max(gaps) - min(gaps)) * FT < GROUP_TOL_M:
                out.append(line)
                used.update(r.Id for r in line)
    return out


def _merge(doc, line):
    """One set from the bars of `line`; None if a bar would move."""
    wanted = [_points(r) for r in line]
    n = len(line)
    for order in (list(range(n)), list(range(n))[::-1]):
        base = line[order[0]]
        want = [wanted[i] for i in order]
        length = (want[-1][0] - want[0][0]).GetLength()
        for side in (True, False):
            sub = DB.SubTransaction(doc)
            sub.Start()
            try:
                base.GetShapeDrivenAccessor().SetLayoutAsFixedNumber(n, length, side, True, True)
                doc.Regenerate()
                err = max(max(p.DistanceTo(q) for p, q in zip(_points(base, k), want[k])) for k in range(n))
                if err * FT < GROUP_TOL_M:
                    for r in line:
                        if r.Id != base.Id:
                            doc.Delete(r.Id)
                    sub.Commit()
                    return base
            except Exception:
                pass
            sub.RollBack()
    return None


def group_loose(doc, rebars):
    """Join the loose bars among `rebars` into sets. Returns the ids of
    the bars deleted (now positions of another bar's set)."""
    by = {}
    for r in rebars:
        if r is None or not r.IsValidObject or r.LayoutRule != RebarLayoutRule.Single or not r.IsRebarShapeDriven():
            continue
        by.setdefault(_signature(r), []).append(r)
    gone = set()
    for bars in by.values():
        if len(bars) < 2:
            continue
        for line in _lines(bars):
            ids = [r.Id for r in line]
            base = _merge(doc, line)
            if base is not None:
                gone.update(i for i in ids if i != base.Id)
    return gone
