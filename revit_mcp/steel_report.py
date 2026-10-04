# -*- coding: utf-8 -*-
"""The steel report ("Reporte" button): quantities of the model's
reinforcement by category and diameter, concrete volumes and ratios, and
the review / clash summaries - as a table, CSV and a printable HTML page.

Pure (tests/unit/test_steel_report.py).
"""

def aggregate(bars, weight_kg_m, diameter_mm):
    """bars: [(category, diameter key, bar count, total length m)] ->
    rows [(category, key, count, length, weight)] sorted by category and
    diameter, and totals per category {category: (count, length, weight)}."""
    acc = {}
    for cat, key, count, length in bars:
        c, l = acc.get((cat, key), (0, 0.0))
        acc[(cat, key)] = (c + count, l + length)
    rows = []
    for (cat, key), (count, length) in acc.items():
        rows.append((cat, key, count, length, length * weight_kg_m(key)))
    rows.sort(key=lambda r: (r[0], diameter_mm.get(r[1], 0)))
    totals = {}
    for cat, key, count, length, weight in rows:
        c, l, w = totals.get(cat, (0, 0.0, 0.0))
        totals[cat] = (c + count, l + length, w + weight)
    return rows, totals


def ratios(totals, volumes):
    """{category: kg of steel per m3 of concrete} where both are known."""
    return dict((cat, totals[cat][2] / volumes[cat]) for cat in totals if volumes.get(cat, 0) > 1e-6)


def grand_total(totals):
    count = sum(t[0] for t in totals.values())
    length = sum(t[1] for t in totals.values())
    weight = sum(t[2] for t in totals.values())
    return count, length, weight


def to_csv(rows, totals, volumes):
    out = [u"CATEGORIA;DIAMETRO;N BARRAS;LONGITUD (m);PESO (kg)"]
    for cat, key, count, length, weight in rows:
        out.append(u"{};{};{};{:.2f};{:.2f}".format(cat, key, count, length, weight))
    out.append(u"")
    out.append(u"CATEGORIA;N BARRAS;LONGITUD (m);PESO (kg);CONCRETO (m3);CUANTIA (kg/m3)")
    rat = ratios(totals, volumes)
    for cat in sorted(totals):
        c, l, w = totals[cat]
        vol = volumes.get(cat, 0.0)
        out.append(u"{};{};{:.2f};{:.2f};{:.2f};{}".format(cat, c, l, w, vol,
                                                          u"{:.1f}".format(rat[cat]) if cat in rat else u"-"))
    c, l, w = grand_total(totals)
    out.append(u"TOTAL;{};{:.2f};{:.2f};;".format(c, l, w))
    return u"\r\n".join(out) + u"\r\n"


def _e(text):
    """HTML-escaped text (IronPython 2.7 and Python 3 alike)."""
    return u"{}".format(text).replace(u"&", u"&amp;").replace(u"<", u"&lt;").replace(u">", u"&gt;")


def to_html(title, project, date, rows, totals, volumes, review=None, clashes=None):
    """A printable page: summary cards, the totals per category with a bar
    of each one's weight, the detail by diameter and the summaries.
    review: {"OK": n, "AVISO": n, "NO CUMPLE": n} or None; clashes: (n
    clashes, n warnings) or None."""
    c, l, w = grand_total(totals)
    vol = sum(volumes.get(cat, 0.0) for cat in totals)
    rat = ratios(totals, volumes)
    top = max([t[2] for t in totals.values()] + [1e-9])
    css = (u"body{font-family:Segoe UI,Arial,sans-serif;margin:28px;color:#222}"
           u"h1{font-size:22px;margin:0}h2{font-size:16px;margin:26px 0 8px;color:#1f4e79}"
           u".sub{color:#666;margin:2px 0 18px}.cards{display:flex;gap:12px;flex-wrap:wrap}"
           u".card{background:#f3f6f9;border-radius:8px;padding:10px 16px;min-width:150px}"
           u".card b{display:block;font-size:20px}.card span{color:#666;font-size:12px}"
           u"table{border-collapse:collapse;width:100%;font-size:13px}"
           u"th{background:#5a707d;color:#fff;text-align:left;padding:6px}"
           u"td{border-bottom:1px solid #ddd;padding:5px 6px}td.n{text-align:right}"
           u".bar{background:#378add;height:10px;border-radius:3px}"
           u".ok{color:#3b6d11}.warn{color:#854f0b}.fail{color:#a32d2d}"
           u"@media print{body{margin:10mm}}")
    h = [u"<!doctype html><html><head><meta charset='utf-8'><title>{}</title><style>{}</style></head><body>"
         .format(_e(title), css)]
    h.append(u"<h1>{}</h1><div class='sub'>{} &middot; {}</div>".format(_e(title), _e(project), _e(date)))
    h.append(u"<div class='cards'>")
    for value, label in ((u"{:,.0f} kg".format(w), u"Peso total de acero"), (u"{:,.0f} m".format(l), u"Longitud total"),
                         (u"{}".format(c), u"Barras"), (u"{:,.1f} m3".format(vol), u"Concreto con acero"),
                         (u"{:,.1f} kg/m3".format(w / vol) if vol > 1e-6 else u"-", u"Cuantia global")):
        h.append(u"<div class='card'><b>{}</b><span>{}</span></div>".format(value, label))
    h.append(u"</div><h2>Resumen por categoria</h2><table><tr><th>Categoria</th><th>Barras</th>"
             u"<th>Longitud (m)</th><th>Peso (kg)</th><th>Concreto (m3)</th><th>Cuantia (kg/m3)</th>"
             u"<th style='width:30%'></th></tr>")
    for cat in sorted(totals, key=lambda k: -totals[k][2]):
        cc, ll, ww = totals[cat]
        h.append(u"<tr><td>{}</td><td class='n'>{}</td><td class='n'>{:,.2f}</td><td class='n'>{:,.2f}</td>"
                 u"<td class='n'>{:,.2f}</td><td class='n'>{}</td><td><div class='bar' style='width:{:.0f}%'></div>"
                 u"</td></tr>".format(_e(cat), cc, ll, ww, volumes.get(cat, 0.0),
                                      u"{:,.1f}".format(rat[cat]) if cat in rat else u"-", 100.0 * ww / top))
    h.append(u"</table><h2>Detalle por diametro</h2><table><tr><th>Categoria</th><th>Diametro</th><th>Barras</th>"
             u"<th>Longitud (m)</th><th>Peso (kg)</th></tr>")
    for cat, key, cc, ll, ww in rows:
        h.append(u"<tr><td>{}</td><td>{}</td><td class='n'>{}</td><td class='n'>{:,.2f}</td>"
                 u"<td class='n'>{:,.2f}</td></tr>".format(_e(cat), _e(key), cc, ll, ww))
    h.append(u"</table>")
    if review is not None:
        h.append(u"<h2>Revision E.060</h2><p><span class='fail'><b>NO CUMPLE: {}</b></span> &nbsp; "
                 u"<span class='warn'><b>AVISO: {}</b></span> &nbsp; <span class='ok'><b>CUMPLE: {}</b></span></p>"
                 .format(review.get(u"NO CUMPLE", 0), review.get(u"AVISO", 0), review.get(u"OK", 0)))
    if clashes is not None:
        h.append(u"<h2>Interferencias</h2><p>Choques entre barras: <b>{}</b> &nbsp; Avisos de Revit sobre el acero: "
                 u"<b>{}</b></p>".format(clashes[0], clashes[1]))
    h.append(u"<p class='sub' style='margin-top:30px'>Generado por BOKI Estructura (Revit).</p></body></html>")
    return u"".join(h)
