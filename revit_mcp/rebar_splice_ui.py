# -*- coding: utf-8 -*-
"""Lap splice settings shared by the Acero windows (pyRevit settings
section "OLSTR_Acero"): the maximum bar length and, per diameter, whether
it is spliced and its lap (cm, prefilled from Norma E.060)."""
from pyrevit import script
from System.Windows import Thickness, VerticalAlignment
from System.Windows.Controls import CheckBox, Orientation, StackPanel, TextBox

import rebar_spec as rs

CONFIG_SECTION = "OLSTR_Acero"


def load_splice_settings():
    """{"on": bool, "max": m, "laps": {key: cm}, "active": {keys}}."""
    settings = {"on": True, "max": rs.MAX_BAR_LENGTH, "active": set(),
                "laps": dict((k, float(rs.e060_lap_cm(k))) for k in rs.bar_diameter_keys())}
    try:
        config = script.get_config(CONFIG_SECTION)
        settings["on"] = (config.get_option("splice_on", u"1") or u"1") == u"1"
        settings["max"] = float(config.get_option("splice_max", u"") or rs.MAX_BAR_LENGTH)
        for pair in (config.get_option("splice_laps", u"") or u"").split(u"|"):
            if u"=" in pair:
                key, cm = pair.split(u"=", 1)
                settings["laps"][rs.parse_diameter(key)] = float(cm)
        for key in (config.get_option("splice_active", u"") or u"").split(u"|"):
            if key.strip():
                settings["active"].add(rs.parse_diameter(key))
    except Exception:
        pass
    return settings


def save_splice_settings(settings):
    try:
        config = script.get_config(CONFIG_SECTION)
        config.splice_on = u"1" if settings["on"] else u"0"
        config.splice_max = u"{}".format(settings["max"])
        config.splice_laps = u"|".join(u"{}={}".format(k.replace(u'"', u"pulg"), v)
                                       for k, v in sorted(settings["laps"].items()))
        config.splice_active = u"|".join(k.replace(u'"', u"pulg") for k in sorted(settings["active"]))
        script.save_config()
    except Exception:
        pass


def splice_for_generation(settings):
    """None when splicing is off, else {"max": m, "laps": {key: m}} with
    only the diameters marked."""
    if not settings["on"]:
        return None
    return {"max": settings["max"],
            "laps": dict((k, settings["laps"][k] / 100.0) for k in settings["active"] if k in settings["laps"])}


def lap_rows(panel, settings, handler):
    """One cell per diameter in `panel`: a check and its lap (cm)."""
    boxes, checks = {}, {}
    panel.Children.Clear()
    for key in rs.bar_diameter_keys():
        cell = StackPanel()
        cell.Orientation = Orientation.Horizontal
        cell.Margin = Thickness(0, 0, 8, 4)
        check = CheckBox()
        check.Content = u"Ø{}".format(key)
        check.Width = 62
        check.VerticalAlignment = VerticalAlignment.Center
        check.IsChecked = key in settings["active"]
        check.ToolTip = u"Marcado: las barras de este diametro se empalman al superar la longitud maxima."
        check.Click += handler
        box = TextBox()
        box.Width = 34
        cm = settings["laps"].get(key)
        box.Text = u"{:g}".format(cm) if cm else u""
        box.ToolTip = u"Longitud de empalme (cm). Sugerido: E.060 clase B = {} cm.".format(rs.e060_lap_cm(key))
        box.TextChanged += handler
        cell.Children.Add(check)
        cell.Children.Add(box)
        panel.Children.Add(cell)
        boxes[key] = box
        checks[key] = check
    return boxes, checks


def lap_form(boxes, checks):
    laps = {}
    for key, box in boxes.items():
        try:
            cm = float((box.Text or u"").replace(u",", u"."))
        except ValueError:
            continue
        if cm > 0:
            laps[key] = cm
    return laps, set(key for key, check in checks.items() if check.IsChecked)
