import io

import openpyxl
import pytest

from prospectscout.core import importer


def test_fields_match_prospectiq_order():
    assert importer.FIELDS == (
        "first_name", "last_name", "full_name", "job_title", "seniority", "department", "linkedin_url",
        "email", "email_status", "phone", "company_name", "company_domain", "website", "company_linkedin",
        "industry", "country", "city", "employee_count", "funding", "latest_funding", "latest_funding_date",
        "technologies", "company_description")


def test_common_headers():
    m = importer.detect_mapping(["Name", "Position", "Website", "Email", "Phone Number", "LinkedIn"])
    assert m == {"Name": "full_name", "Position": "job_title", "Website": "website",
                 "Email": "email", "Phone Number": "phone", "LinkedIn": "linkedin_url"}


def test_apollo_headers():
    m = importer.detect_mapping(["First Name", "Last Name", "Title", "Company", "Company Website",
                                 "Person Linkedin Url", "# Employees"])
    assert m["Title"] == "job_title" and m["Company Website"] == "website" and m["# Employees"] == "employee_count"


def test_designation_whatsapp_aliases():
    m = importer.detect_mapping(["Designation", "WhatsApp", "Mobile", "Organisation", "URL", "Random"])
    assert m["Designation"] == "job_title" and m["WhatsApp"] == "phone" and m["Organisation"] == "company_name"
    assert m["URL"] == "website" and m["Random"] is None


@pytest.mark.parametrize("raw", ["Acme.com ", "www.acme.com/contact?x=1", "HTTP://ACME.COM", "acme.com/",
                                 "https://www.Acme.com/about"])
def test_messy_websites_normalize(raw):
    row = importer.normalize_row({"Website": raw}, {"Website": "website"})
    assert row["company_domain"] == "acme.com" and row["website"].startswith(("http://", "https://"))


def test_domain_from_company_email():
    row = importer.normalize_row({"Email": "Sarah@Acme.co.uk"}, {"Email": "email"})
    assert row["email"] == "sarah@acme.co.uk" and row["company_domain"] == "acme.co.uk"


def test_no_domain_from_free_email():
    assert not importer.normalize_row({"Email": "x@gmail.com"}, {"Email": "email"}).get("company_domain")


def test_full_name_split_both_ways():
    r = importer.normalize_row({"Name": "Sarah Lee"}, {"Name": "full_name"})
    assert (r["first_name"], r["last_name"]) == ("Sarah", "Lee")
    r = importer.normalize_row({"F": "Sarah", "L": "Lee"}, {"F": "first_name", "L": "last_name"})
    assert r["full_name"] == "Sarah Lee"


def test_formula_injection_stripped():
    assert importer.normalize_row({"N": "=HYPERLINK(1)"}, {"N": "notes"})["notes"] == "HYPERLINK(1)"


def test_employee_count_parsed():
    assert importer.normalize_row({"E": "1,200"}, {"E": "employee_count"})["employee_count"] == 1200


def test_sheet_url_to_csv():
    u = "https://docs.google.com/spreadsheets/d/AbC123/edit#gid=456"
    assert importer.sheet_url_to_csv(u) == "https://docs.google.com/spreadsheets/d/AbC123/export?format=csv&gid=456"
    assert importer.sheet_url_to_csv("https://docs.google.com/spreadsheets/d/AbC123/edit").endswith("gid=0")
    assert importer.sheet_url_to_csv("https://example.com/list.csv") == "https://example.com/list.csv"


def test_csv_bom_and_semicolons():
    h, rows = importer.read_csv_bytes("﻿Name;Website\nSarah;acme.com\n".encode("utf-8"))
    assert h == ["Name", "Website"] and rows[0]["Website"] == "acme.com"


def test_csv_cp1252_fallback():
    h, rows = importer.read_csv_bytes("Name,Website\nJosé,acme.com\n".encode("cp1252"))
    assert rows[0]["Name"] == "José"


def test_xlsx_roundtrip():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Name", "Website"])
    ws.append(["Sarah", "acme.com"])
    buf = io.BytesIO()
    wb.save(buf)
    h, rows = importer.read_xlsx_bytes(buf.getvalue())
    assert h == ["Name", "Website"] and rows[0]["Name"] == "Sarah"


def test_header_row_detected_after_title_and_blank_rows():
    data = "Prospects Nov 2026,,\n,,\nName,Website,Email\nSarah,acme.com,s@acme.com\n".encode()
    h, rows = importer.read_csv_bytes(data)
    assert h == ["Name", "Website", "Email"] and len(rows) == 1


def test_blank_rows_skipped():
    h, rows = importer.read_csv_bytes(b"Name,Website\nSarah,acme.com\n,\n\nBob,bob.io\n")
    assert [r["Name"] for r in rows] == ["Sarah", "Bob"]


def test_preview_counts():
    p = importer.preview(["Name", "Website", "Email"], [
        {"Name": "A", "Website": "acme.com", "Email": ""},
        {"Name": "B", "Website": "", "Email": "b@gmail.com"},
        {"Name": "C", "Website": "www.acme.com", "Email": ""}])
    assert p["total"] == 3 and p["missing_website"] == 1 and p["duplicate_domains"] == 1
    assert p["mapping"]["Website"] == "website" and len(p["sample"]) == 3


def test_mapping_override_respected():
    rows = importer.normalize_rows(["Col A"], [{"Col A": "acme.com"}], mapping={"Col A": "website"})
    assert rows[0]["company_domain"] == "acme.com"


# --- final-review fixes ---------------------------------------------------------------------

def test_deep_link_website_becomes_site_root():
    row = importer.normalize_row({"Website": "www.acme.com/contact?x=1"}, {"Website": "website"})
    assert row["website"] == "https://www.acme.com" and row["company_domain"] == "acme.com"


def test_malformed_urls_do_not_crash():
    rows = importer.normalize_rows(["Website"], [{"Website": "acme.com:99999"}, {"Website": "https://[your-domain]/"}])
    assert all(not r.get("company_domain") for r in rows)


def test_javascript_urls_rejected():
    row = importer.normalize_row({"W": "javascript://acme.com/%0aalert(1)", "L": "javascript:alert(1)"},
                                 {"W": "website", "L": "linkedin_url"})
    assert not row.get("website") and not row.get("company_domain") and not row.get("linkedin_url")


def test_platform_links_need_website():
    for raw in ("facebook.com/joesplumbing", "https://www.facebook.com/acmedental", "https://linktr.ee/acme",
                "https://www.instagram.com/acme", "https://goo.gl/maps/xyz"):
        assert not importer.normalize_row({"W": raw}, {"W": "website"}).get("company_domain"), raw


def test_isp_and_regional_free_emails_are_not_company_domains():
    for e in ("joe@bigpond.com", "x@btinternet.com", "y@yahoo.co.uk", "z@web.de", "a@hotmail.co.uk"):
        assert not importer.normalize_row({"E": e}, {"E": "email"}).get("company_domain"), e


# --- deferred minors -----------------------------------------------------------------------

@pytest.mark.parametrize("evil", ["+1+HYPERLINK(\"http://x\")", "-1+cmd|' /C calc'!A0", "\t=1+1", "\r=1+1", "@SUM(A1)"])
def test_formula_injection_variants_neutralized(evil):
    out = importer.sanitize_cell(evil)
    assert not out or out[0] not in "=+-@\t\r"


def test_real_phone_numbers_keep_their_plus():
    assert importer.sanitize_cell("+44 20 7946 0958") == "+44 20 7946 0958"
    assert importer.sanitize_cell("+1 (512) 555-0100") == "+1 (512) 555-0100"


def test_pubhtml_link_keeps_gid():
    u = "https://docs.google.com/spreadsheets/d/e/2PACX-abc/pubhtml?gid=77&single=true"
    assert importer.sheet_url_to_csv(u) == "https://docs.google.com/spreadsheets/d/e/2PACX-abc/pub?output=csv&gid=77"
