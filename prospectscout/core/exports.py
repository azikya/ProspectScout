"""CSV / JSON exports, including a CSV in ProspectIQ's import format."""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime, timezone

from .importer import FIELDS, is_formula_like

ROW_COLUMNS = ("full_name", "job_title", "company_name", "website", "email", "phone", "linkedin_url", "fit", "score",
               "outreach_status", "next_email", "linkedin_note", "email_subject", "email_body", "whatsapp",
               "report_delivery", "followup_1", "followup_2", "followup_3")


def _safe(value) -> str:
    """Neutralize spreadsheet formulas while keeping phone numbers like +1 512…"""
    s = "" if value is None else str(value)
    return "'" + s if is_formula_like(s) else s


def _csv(columns, rows) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(columns)
    for r in rows:
        w.writerow([_safe(r.get(c)) for c in columns])
    return buf.getvalue()


def rows_csv(rows: list[dict]) -> str:
    return _csv(ROW_COLUMNS, rows)


def prospectiq_csv(contacts: list[dict]) -> str:
    return _csv(FIELDS, contacts)


def full_json(companies: list[dict], contacts: list[dict]) -> str:
    return json.dumps({"exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                       "companies": companies, "contacts": contacts}, ensure_ascii=False, indent=2, default=str)
