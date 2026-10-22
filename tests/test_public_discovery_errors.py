from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


@pytest.mark.parametrize("filename", ["validate-site.py", "check-links.py"])
def test_public_html_discovery_fails_when_directory_cannot_be_read(monkeypatch, filename):
    source = Path(__file__).resolve().parents[1] / "scripts" / filename
    monkeypatch.syspath_prepend(str(source.parent))
    name = "public_discovery_" + source.stem.replace("-", "_")
    spec = importlib.util.spec_from_file_location(name, source)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)

    def unreadable_walk(root, *, topdown, onerror):
        onerror(PermissionError("public route is unreadable"))
        return iter(())

    monkeypatch.setattr(module.os, "walk", unreadable_walk)
    with pytest.raises(PermissionError, match="public route is unreadable"):
        module.find_html_files()
