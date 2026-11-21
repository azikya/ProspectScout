import copy
import csv
import io
import json
import re
from datetime import date
from pathlib import Path

from prospectscout import config
from prospectscout.core import exports, importer, report, sop

GOOD = json.loads((Path(__file__).parent / "fixtures" / "analysis_good.json").read_text(encoding="utf-8"))


def company(**over):
    co = {"id": 1, "domain": "acme.com", "website": "https://acme.com", "company_name": "Acme Logistics",
          "analysis_json": copy.deepcopy(GOOD), "facts_json": [], "industry": "Logistics", "country": "USA"}
    co.update(over)
    return co


def test_report_core_sections():
    s = config.load()
    s["sender"].update(name="Alex", title="Business Development", email="sales@azikya.com", phone="+91 98765 00000")
    h = report.render_report(company(), {"full_name": "Sarah Lee", "job_title": "COO"}, s, date(2026, 11, 21))
    for text in ("Website Opportunity Review", "acme.com", "Sarah Lee", "Azikya Software Solutions", "What's working",
                 "How you compare", "FastFreight", "Alex", "sales@azikya.com", "21 November 2026",
                 "we&#x27;ll plan it around your budget"):
        assert text in h, text
    assert not re.search(r"[$€£₹]\s?\d", h)
    assert "<style>" in h and "@page" in h and "/brand/logo.png" in h
    assert "+91 98765 00000" not in h  # our phone number is never shared


def test_report_escapes_html():
    co = company()
    co["analysis_json"]["report"]["opportunities"][0]["title"] = "<script>alert(1)</script>"
    h = report.render_report(co, None, config.load(), date(2026, 11, 21))
    assert "&lt;script&gt;" in h and "<script>alert" not in h


def test_report_hides_comparison_when_empty():
    co = company()
    co["analysis_json"]["report"]["comparison"] = []
    assert "How you compare" not in report.render_report(co, None, config.load(), date(2026, 11, 21))


def test_report_without_analysis_is_friendly():
    h = report.render_report(company(analysis_json=None), None, config.load(), date(2026, 11, 21))
    assert "not ready" in h.lower()


def test_sop_hot_from_hiring_plus_funding():
    co = company(facts_json=[{"signal": "hiring_tech_roles", "evidence": "Backend Engineer"}], industry="Fintech", country="UK")
    co["analysis_json"]["buying_intent"]["signals"] = [{"sop_type": "funding_news", "evidence": "Raised Series A"}]
    g = sop.sop_guide(co, {"manual_signals_json": []}, config.load())
    assert g["score"] == 6 and g["label"] == "hot" and g["industry_match"] is True and g["country_match"] is True
    assert co["analysis_json"]["fit"] == "strong"  # never changes fit


def test_sop_counts_each_type_once():
    co = company(facts_json=[{"signal": "hiring_tech_roles", "evidence": "a"}, {"signal": "hiring_tech_roles", "evidence": "b"}])
    co["analysis_json"]["buying_intent"]["signals"] = [{"sop_type": "hiring_tech_roles", "evidence": "c"}]
    assert sop.sop_guide(co, {}, config.load())["score"] == 3


def test_sop_europe_size_and_manual_signal():
    g = sop.sop_guide(company(country="Germany", industry="Mining", facts_json=[], analysis_json={}),
                      {"employee_count": "5000", "manual_signals_json": ["active_on_linkedin"]}, config.load())
    assert g["country_match"] is True and g["industry_match"] is False and g["size_match"] is False
    assert g["score"] == 1 and g["label"] == "cold"


def test_sop_country_aliases():
    g = sop.sop_guide(company(country="United States", analysis_json={}), {}, config.load())
    assert g["country_match"] is True


def test_sop_unknown_values_are_none():
    g = sop.sop_guide(company(country="", industry="", analysis_json={}), {"manual_signals_json": []}, config.load())
    assert g["size_match"] is None and g["country_match"] is None and g["industry_match"] is None


def test_prospectiq_headers():
    assert exports.prospectiq_csv([]).splitlines()[0].split(",") == list(importer.FIELDS)


def test_prospectiq_rows_and_formula_safety():
    out = exports.prospectiq_csv([{"full_name": "A", "email": "a@acme.com", "phone": "+1 512", "notes": "x",
                                   "company_name": "=cmd()"}])
    row = list(csv.reader(io.StringIO(out)))[1]
    assert row[importer.FIELDS.index("full_name")] == "A" and row[importer.FIELDS.index("phone")] == "+1 512"
    assert row[importer.FIELDS.index("company_name")] == "'=cmd()"


def test_rows_csv_keeps_multiline_messages():
    out = exports.rows_csv([{"full_name": "A", "email_body": "line1\nline2"}])
    rows = list(csv.reader(io.StringIO(out)))
    assert rows[0][0] == "full_name" and "line1\nline2" in rows[1]


def test_full_json_roundtrips():
    data = json.loads(exports.full_json([company()], [{"full_name": "A"}]))
    assert data["companies"][0]["domain"] == "acme.com" and data["contacts"][0]["full_name"] == "A"


def test_report_never_links_unsafe_urls():
    s = config.load()
    s["sender"]["booking_link"] = "javascript:alert(1)"
    h = report.render_report(company(), None, s, date(2026, 11, 21))
    assert 'href="javascript' not in h


# --- deferred minors -----------------------------------------------------------------------

def test_export_neutralizes_formula_variants_but_keeps_phones():
    out = exports.prospectiq_csv([{"company_name": "+1+HYPERLINK(1)", "phone": "+44 20 7946 0958", "notes": "\t=1"}])
    row = list(csv.reader(io.StringIO(out)))[1]
    assert row[importer.FIELDS.index("company_name")].startswith("'") and row[importer.FIELDS.index("phone")] == "+44 20 7946 0958"


def test_industry_match_uses_whole_words():
    s = config.load()
    s["sop"]["industries"] = ["IT", "Hospitality"]
    assert sop.sop_guide(company(industry="Healthcare", analysis_json={}), {}, s)["industry_match"] is False
    assert sop.sop_guide(company(industry="Hotels & Hospitality", analysis_json={}), {}, s)["industry_match"] is True
    s["sop"]["industries"] = ["IT"]
    assert sop.sop_guide(company(industry="Hospitality", analysis_json={}), {}, s)["industry_match"] is False


def test_no_prospect_report_for_skipped_companies():
    co = company()
    co["analysis_json"]["fit"] = "skip"
    co["fit"] = "skip"
    h = report.render_report(co, None, config.load(), date(2026, 11, 21))
    assert "not available" in h.lower() and "Three opportunities" not in h
