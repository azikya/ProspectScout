"""Guards the layering: core/ is pure business logic; I/O lives in adapters/, wiring in app/."""

import ast
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "prospectscout"
FORBIDDEN_IN_CORE = ("prospectscout.adapters", "prospectscout.app", "urllib.request", "subprocess", "sqlite3",
                     "socket", "http.server", "http.client", "requests", "httpx")


def forbidden_imports(source: str, module_pkg: str = "prospectscout.core") -> list[str]:
    bad = []
    for node in ast.walk(ast.parse(source)):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import: "." is the core package, ".." its parent, ...
                base = module_pkg.rsplit(".", node.level - 1)[0] if node.level > 1 else module_pkg
                names = [f"{base}.{node.module}" if node.module else base]
            else:
                names = [node.module or ""]
        bad += [n for n in names if any(n == f or n.startswith(f + ".") for f in FORBIDDEN_IN_CORE)]
    return bad


def test_checker_catches_violations():
    assert forbidden_imports("import subprocess") == ["subprocess"]
    assert forbidden_imports("from urllib.request import urlopen") == ["urllib.request"]
    assert forbidden_imports("from ..adapters import db") == ["prospectscout.adapters"]
    assert forbidden_imports("from urllib.parse import urlparse\nfrom .site import Page") == []


def test_core_has_no_io_or_outer_layer_imports():
    problems = {}
    for path in sorted((PKG / "core").glob("*.py")):
        bad = forbidden_imports(path.read_text(encoding="utf-8"))
        if bad:
            problems[path.name] = bad
    assert not problems, problems


def test_adapters_do_not_import_app():
    for path in sorted((PKG / "adapters").glob("*.py")):
        src = path.read_text(encoding="utf-8")
        assert "from ..app" not in src and "prospectscout.app" not in src, path.name
