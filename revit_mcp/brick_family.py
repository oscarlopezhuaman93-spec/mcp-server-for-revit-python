# -*- coding: utf-8 -*-
"""A face-based copy of the project's roof brick ("ladrillo de techo"): the
floor-hosted family always stays level; this one, placed on the slab's top
face, takes its slope. Made from Revit's "basado en cara" template: a box
Largo x Ancho x Altura hanging Recubrimiento (5 cm) under the face, with
the same types (sizes) as the original family.

Family frame (as the original): x across, centered (Ancho); the length runs
from the insertion point back along -y (Largo); the box from Recubrimiento
to Recubrimiento + Altura on the +z side of the face - the instance is
flipped to the face's other side, into the slab."""
import os

from pyrevit import DB
from System.Collections.Generic import List

FT = 0.3048
FAMILY_NAME = u"BOKI_LADRILLO DE TECHO_BASADO EN CARA"
TEMPLATE_WORDS = (u"basado en cara", u"face based")


def template_path(app):
    root = app.FamilyTemplatePath
    for base, _, files in os.walk(root):
        for f in files:
            low = f.lower()
            if low.endswith(".rft") and any(w in low for w in TEMPLATE_WORDS) and (u"gen" in low):
                return os.path.join(base, f)
    return None


def _length_type():
    try:
        return DB.SpecTypeId.Length
    except AttributeError:
        return DB.ParameterType.Length


def _add_param(fm, name, formula=None):
    p = fm.get_Parameter(name)
    if p is None:
        try:
            p = fm.AddParameter(name, DB.GroupTypeId.Geometry, _length_type(), False)
        except Exception:
            p = fm.AddParameter(name, DB.BuiltInParameterGroup.PG_GEOMETRY, _length_type(), False)
    if formula:
        fm.SetFormula(p, formula)
    return p


def _plan_view(fdoc):
    for v in DB.FilteredElementCollector(fdoc).OfClass(DB.ViewPlan):
        if not v.IsTemplate:
            return v
    return None


def _center_planes(fdoc):
    """The template's two center planes: (left / right, front / back)."""
    lr = fb = None
    for rp in DB.FilteredElementCollector(fdoc).OfClass(DB.ReferencePlane):
        n = rp.Normal
        if abs(rp.BubbleEnd.X) < 1e-6 and abs(rp.FreeEnd.X) < 1e-6 and abs(n.X) > 0.99:
            lr = rp
        elif abs(rp.BubbleEnd.Y) < 1e-6 and abs(rp.FreeEnd.Y) < 1e-6 and abs(n.Y) > 0.99:
            fb = rp
    return lr, fb


def build(app, out_path, sizes):
    """Make the family file: sizes = [(type name, largo, ancho, altura)] m.
    Returns the path."""
    fdoc = app.NewFamilyDocument(template_path(app))
    t = DB.Transaction(fdoc, "ladrillo")
    try:
        fm = fdoc.FamilyManager
        view = _plan_view(fdoc)
        t.Start()
        p_l = _add_param(fm, u"Largo")
        p_a = _add_param(fm, u"Ancho")
        p_h = _add_param(fm, u"Altura")
        p_r = _add_param(fm, u"Recubrimiento")
        p_m = _add_param(fm, u"Medio ancho")
        p_f = _add_param(fm, u"Fondo")
        largo, ancho, altura = sizes[0][1], sizes[0][2], sizes[0][3]
        if fm.CurrentType is None:
            fm.NewType(sizes[0][0])
        fm.Set(p_l, largo / FT)
        fm.Set(p_a, ancho / FT)
        fm.Set(p_h, altura / FT)
        fm.Set(p_r, 0.05 / FT)
        fm.SetFormula(p_m, u"Ancho / 2")
        fm.SetFormula(p_f, u"Recubrimiento + Altura")
        lr, fb = _center_planes(fdoc)
        fc = fdoc.FamilyCreate
        z = DB.XYZ.BasisZ
        h, w, L = ancho / 2.0 / FT, ancho / FT, largo / FT

        def plane(p0, p1, name):
            rp = fc.NewReferencePlane(p0, p1, z, view)
            rp.Name = name
            return rp
        left = plane(DB.XYZ(-h, -5, 0), DB.XYZ(-h, 5, 0), u"Izquierda")
        right = plane(DB.XYZ(h, -5, 0), DB.XYZ(h, 5, 0), u"Derecha")
        back = plane(DB.XYZ(-5, -L, 0), DB.XYZ(5, -L, 0), u"Posterior")
        fdoc.Regenerate()

        def dim(a, b, param, y=None, x=None):
            arr = DB.ReferenceArray()
            arr.Append(a.GetReference())
            arr.Append(b.GetReference())
            if y is not None:
                ln = DB.Line.CreateBound(DB.XYZ(-5, y, 0), DB.XYZ(5, y, 0))
            else:
                ln = DB.Line.CreateBound(DB.XYZ(x, -5, 0), DB.XYZ(x, 5, 0))
            d = fc.NewDimension(view, ln, arr)
            d.FamilyLabel = param
            return d
        dim(lr, left, p_m, y=1.0)
        dim(lr, right, p_m, y=1.5)
        dim(fb, back, p_l, x=1.0)
        # the box: its outline on the face, then out from the face
        sp = DB.SketchPlane.Create(fdoc, DB.Plane.CreateByNormalAndOrigin(z, DB.XYZ.Zero))
        pts = [DB.XYZ(-h, -L, 0), DB.XYZ(h, -L, 0), DB.XYZ(h, 0, 0), DB.XYZ(-h, 0, 0)]
        arr = DB.CurveArray()
        for k in range(4):
            arr.Append(DB.Line.CreateBound(pts[k], pts[(k + 1) % 4]))
        prof = DB.CurveArrArray()
        prof.Append(arr)
        ext = fc.NewExtrusion(True, prof, sp, (0.05 + altura) / FT)
        ext.StartOffset = 0.05 / FT
        # clay colored, as the original brick
        mid = DB.Material.Create(fdoc, u"BOKI Ladrillo arcilla")
        mat = fdoc.GetElement(mid)
        mat.Color = DB.Color(196, 120, 80)
        mat.SurfaceForegroundPatternColor = DB.Color(150, 85, 55)
        mp = ext.get_Parameter(DB.BuiltInParameter.MATERIAL_ID_PARAM)
        if mp is not None and not mp.IsReadOnly:
            mp.Set(mid)
        fm.AssociateElementParameterToFamilyParameter(ext.get_Parameter(DB.BuiltInParameter.EXTRUSION_START_PARAM), p_r)
        fm.AssociateElementParameterToFamilyParameter(ext.get_Parameter(DB.BuiltInParameter.EXTRUSION_END_PARAM), p_f)
        fdoc.Regenerate()
        # its sides locked to the planes
        for curve in ext.Sketch.Profile.get_Item(0):
            a, b = curve.GetEndPoint(0), curve.GetEndPoint(1)
            target = None
            if abs(a.X + h) < 1e-6 and abs(b.X + h) < 1e-6:
                target = left
            elif abs(a.X - h) < 1e-6 and abs(b.X - h) < 1e-6:
                target = right
            elif abs(a.Y + L) < 1e-6 and abs(b.Y + L) < 1e-6:
                target = back
            elif abs(a.Y) < 1e-6 and abs(b.Y) < 1e-6:
                target = fb
            if target is not None:
                fc.NewAlignment(view, target.GetReference(), curve.Reference)
        # the types
        for name, lg, an, al in sizes:
            existing = [ty for ty in fm.Types if ty.Name == name]
            fm.CurrentType = existing[0] if existing else fm.NewType(name)
            fm.Set(p_l, lg / FT)
            fm.Set(p_a, an / FT)
            fm.Set(p_h, al / FT)
            fm.Set(p_r, 0.05 / FT)
        t.Commit()
        opts = DB.SaveAsOptions()
        opts.OverwriteExistingFile = True
        fdoc.SaveAs(out_path, opts)
        return out_path
    finally:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        fdoc.Close(False)


def ensure_loaded(doc, sizes, folder):
    """The face-based brick family in the project (built and loaded when
    missing; types added for new sizes). Returns {type name: symbol}."""
    fam = [f for f in DB.FilteredElementCollector(doc).OfClass(DB.Family) if f.Name == FAMILY_NAME]
    symbols = {}
    if fam:
        for i in fam[0].GetFamilySymbolIds():
            s = doc.GetElement(i)
            symbols[s.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM).AsString()] = s
    missing = [z for z in sizes if z[0] not in symbols]
    if fam and not missing:
        return symbols
    if fam:  # new sizes: duplicated types of the loaded family
        base = list(symbols.values())[0]
        for name, lg, an, al in missing:
            s = base.Duplicate(name)
            for pname, v in ((u"Largo", lg), (u"Ancho", an), (u"Altura", al)):
                p = s.LookupParameter(pname)
                if p is not None and not p.IsReadOnly:
                    p.Set(v / FT)
            symbols[name] = s
        return symbols
    path = os.path.join(folder, FAMILY_NAME + u".rfa")
    build(doc.Application, path, sizes)

    class _Opts(DB.IFamilyLoadOptions):
        def OnFamilyFound(self, inUse, overwrite):
            overwrite.Value = True
            return True

        def OnSharedFamilyFound(self, shared, inUse, source, overwrite):
            overwrite.Value = True
            return True
    loaded = doc.LoadFamily(path, _Opts())
    return ensure_loaded(doc, sizes, folder) if loaded else {}
