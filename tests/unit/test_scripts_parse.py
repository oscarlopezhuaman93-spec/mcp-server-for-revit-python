# -*- coding: utf-8 -*-
"""Every pyRevit script of the extension must at least parse: a syntax
error shows the user a red traceback window instead of the tool."""
import ast
import io
import os

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCRIPTS = sorted(
    os.path.join(d, f)
    for d, _, files in os.walk(ROOT)
    if ".tab" in d and ".git" not in d
    for f in files if f == "script.py"
)


@pytest.mark.parametrize("path", SCRIPTS, ids=[os.path.relpath(p, ROOT) for p in SCRIPTS])
def test_script_parses(path):
    ast.parse(io.open(path, encoding="utf-8-sig").read(), path)
