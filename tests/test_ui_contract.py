import re
from pathlib import Path

UI = Path(__file__).resolve().parent.parent / "ui"


def test_every_js_id_exists_in_html():
    js = (UI / "app.js").read_text(encoding="utf-8")
    html = (UI / "index.html").read_text(encoding="utf-8")
    ids = set(re.findall(r'\$\("#([A-Za-z][\w-]*)', js)) | set(re.findall(r'getElementById\("([\w-]+)"\)', js))
    missing = sorted(i for i in ids if f'id="{i}"' not in html)
    assert not missing, missing


def test_api_paths_used_exist():
    js = (UI / "app.js").read_text(encoding="utf-8")
    for p in ("/api/state", "/api/stats", "/api/prospect/", "/api/import/preview", "/api/import/commit", "/api/add_one",
              "/api/settings", "/api/test_claude", "/api/worker/pause", "/api/worker/resume",
              "/api/export.csv", "/api/export_prospectiq.csv", "/api/export.json", "report_url"):
        assert p in js, p  # the report link itself comes from the server as report_url


def test_no_innerhtml_with_untrusted_data():
    js = (UI / "app.js").read_text(encoding="utf-8")
    # dynamic content must go through h()/textContent; innerHTML only for static icon markup
    for line in js.splitlines():
        if "innerHTML" in line:
            assert "ICONS" in line or "= \"\"" in line or "= ''" in line, line


def test_brand_assets_present():
    for f in ("logo.png", "logo-white.png", "favicon.png"):
        assert (UI / "brand" / f).stat().st_size > 0


def test_dynamic_hrefs_go_through_safe_url():
    js = (UI / "app.js").read_text(encoding="utf-8")
    assert "function safeUrl" in js
    raw = re.findall(r"href:\s*(?:co|c|s|cp|d\.contact)\.[\w.]+", js)
    assert not raw, raw


def test_ui_saves_browser_timezone_when_unset():
    js = (UI / "app.js").read_text(encoding="utf-8")
    assert "function autoTimezone" in js and "resolvedOptions().timeZone" in js


def test_settings_expose_sop_logo_signature_and_spacing():
    js = (UI / "app.js").read_text(encoding="utf-8")
    for path in ("sop.industries", "sop.countries", "sop.size_min", "sop.weights.hiring_tech_roles", "company.logo",
                 "sender.signature", "data-spacing"):
        assert path in js, path


def test_rerun_asks_for_confirmation():
    js = (UI / "app.js").read_text(encoding="utf-8")
    assert "confirm(" in js and "Re-run research" in js and "haven't contacted" in js


def test_quick_prospect_tab_wired():
    js = (UI / "app.js").read_text(encoding="utf-8")
    html = (UI / "index.html").read_text(encoding="utf-8")
    assert 'id="view-quick"' in html and 'data-view="quick"' in html and "Quick Prospect" in html
    for s in ("/api/quick", "function mdToNodes", "function renderQuick", "since="):
        assert s in js, s


def test_markdown_renderer_is_safe():
    js = (UI / "app.js").read_text(encoding="utf-8")
    body = js[js.index("function inlineNodes"):js.index("function mdSections")]  # inline + block renderer
    assert "function mdToNodes" in body and "innerHTML" not in body and "safeUrl(" in body
