# -*- coding: utf-8 -*-
"""Conexiones de los porticos y cerchas BOKI. Reglas propias en
revit_mcp/metal_conexiones.py."""

__title__ = "Conexiones"
__author__ = "BOKI"

import os
import sys

SCRIPT_DIR = os.path.dirname(__file__)
EXT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", ".."))
REVIT_MCP_DIR = os.path.join(EXT_ROOT, "revit_mcp")
if REVIT_MCP_DIR not in sys.path:
    sys.path.append(REVIT_MCP_DIR)

import metal_conexiones as mx

reload(mx)

from pyrevit import revit, DB, forms

doc = revit.doc


def _num(text, default):
    try:
        return float((text or u"").replace(u",", u"."))
    except ValueError:
        return default


class ConexionesWindow(forms.WPFWindow):
    def __init__(self, xaml_file_path):
        forms.WPFWindow.__init__(self, xaml_file_path)
        try:
            import boki_brand  # BOKI ESTRUCTURAS and the seal in the header
            boki_brand.apply(self)
        except Exception:
            pass
        self.action = None
        m = mx.read_model(doc, DB)
        self.txt_found.Text = u"En el modelo: {} pórtico(s) y {} cercha(s) BOKI.".format(
            len(m["porticos"]), len(m["cerchas"]))
        cfg = mx.saved_config(doc, DB) or mx.config()
        self.chk_eave.IsChecked, self.chk_ridge.IsChecked = cfg["eave"], cfg["ridge"]
        self.chk_truss.IsChecked, self.chk_anchors.IsChecked = cfg["truss_supports"], cfg["anchors"]
        self.txt_end_plate.Text = u"{:g}".format(cfg["end_plate"])
        self.txt_ridge_plate.Text = u"{:g}".format(cfg["ridge_plate"])
        self.txt_gusset.Text = u"{:g}".format(cfg["gusset"])
        self.txt_pin.Text = u"{:g}".format(cfg["pin"])
        self.txt_anchor_len.Text = u"{:g}".format(cfg["anchor_len"])

    def form(self):
        return {
            "eave": bool(self.chk_eave.IsChecked), "ridge": bool(self.chk_ridge.IsChecked),
            "truss_supports": bool(self.chk_truss.IsChecked), "anchors": bool(self.chk_anchors.IsChecked),
            "end_plate": _num(self.txt_end_plate.Text, 25.0), "ridge_plate": _num(self.txt_ridge_plate.Text, 20.0),
            "gusset": _num(self.txt_gusset.Text, 12.0), "pin": _num(self.txt_pin.Text, 50.0),
            "anchor_len": _num(self.txt_anchor_len.Text, 0.6),
        }

    def cancel_click(self, sender, args):
        self.Close()

    def run_click(self, sender, args):
        try:
            self.cfg = mx.config(self.form())
        except mx.SpecError as e:
            self.txt_error.Text = u"{}".format(e)
            return
        self.action = "run"
        self.Close()


# --- main -------------------------------------------------------------------
if __name__ == "__main__":
    window = ConexionesWindow(os.path.join(SCRIPT_DIR, "ConexionesForm.xaml"))
    window.ShowDialog()
    if window.action == "run":
        t = DB.Transaction(doc, u"BOKI Conexiones")
        t.Start()
        try:
            made = mx.generate(doc, DB, window.cfg)
            t.Commit()
            w = mx.weight(made)
            forms.alert(u"Conexiones creadas: {} planchas ({:.0f} kg), {} grupos de pernos/pasadores ({:.0f} kg), "
                        u"{} grupos de anclajes ({:.0f} kg).".format(
                            len(made["plates"]), w["plates"], len(made["bolts"]), w["bolts"],
                            len(made["anchors"]), w["anchors"]), title=u"Conexiones")
        except Exception as e:
            t.RollBack()
            forms.alert(u"No se pudo generar:\n{}".format(e), title=u"Conexiones")
