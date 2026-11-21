"""Turn messy prospect lists (typed in, CSV, Excel, Google Sheets) into clean rows.

Pure: no network. Field names and header aliases are ported from ProspectIQ's
``import_normalization`` so exports can flow back into ProspectIQ.
"""

from __future__ import annotations

import csv
import io
import re
from urllib.parse import urlparse

FIELDS: tuple[str, ...] = (
    "first_name", "last_name", "full_name", "job_title", "seniority", "department", "linkedin_url",
    "email", "email_status", "phone", "company_name", "company_domain", "website", "company_linkedin",
    "industry", "country", "city", "employee_count", "funding", "latest_funding", "latest_funding_date",
    "technologies", "company_description",
)
EXTRA_FIELDS: tuple[str, ...] = ("notes",)

HEADER_ALIASES: dict[str, str] = {
    # ProspectIQ / Apollo / ZoomInfo-style
    "first name": "first_name", "firstname": "first_name",
    "last name": "last_name", "lastname": "last_name", "surname": "last_name",
    "name": "full_name", "full name": "full_name", "contact name": "full_name", "contact": "full_name",
    "title": "job_title", "job title": "job_title", "position": "job_title", "designation": "job_title",
    "role": "job_title",
    "seniority": "seniority", "departments": "department", "department": "department",
    "person linkedin url": "linkedin_url", "linkedin url": "linkedin_url", "linkedin": "linkedin_url",
    "linkedin profile": "linkedin_url", "profile": "linkedin_url",
    "email": "email", "email address": "email", "e mail": "email", "mail": "email",
    "email status": "email_status",
    "phone": "phone", "phone number": "phone", "mobile": "phone", "mobile number": "phone",
    "whatsapp": "phone", "whatsapp number": "phone", "cell": "phone", "contact number": "phone",
    "company": "company_name", "company name": "company_name", "organization": "company_name",
    "organisation": "company_name", "business": "company_name", "business name": "company_name",
    "company domain": "company_domain", "domain": "company_domain",
    "website": "website", "company website": "website", "url": "website", "site": "website",
    "web": "website", "website url": "website",
    "company linkedin url": "company_linkedin", "company linkedin": "company_linkedin",
    "industry": "industry", "country": "country", "company country": "country",
    "city": "city", "company city": "city",
    "# employees": "employee_count", "employees": "employee_count", "employee count": "employee_count",
    "company size": "employee_count", "no of employees": "employee_count",
    "total funding": "funding", "funding": "funding",
    "latest funding": "latest_funding", "latest funding amount": "latest_funding",
    "latest funding date": "latest_funding_date",
    "technologies": "technologies", "tech stack": "technologies",
    "company description": "company_description", "description": "company_description",
    "notes": "notes", "note": "notes", "comments": "notes", "comment": "notes",
}

FREE_EMAIL_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "msn.com", "yahoo.com",
    "ymail.com", "icloud.com", "me.com", "mac.com", "aol.com", "proton.me", "protonmail.com", "zoho.com",
    "gmx.com", "mail.com", "yandex.com", "rediffmail.com",
    # regional free mail + ISPs (a person's inbox, not a company website)
    "yahoo.co.uk", "yahoo.co.in", "yahoo.com.au", "yahoo.ca", "yahoo.fr", "yahoo.de", "hotmail.co.uk", "hotmail.fr",
    "hotmail.de", "outlook.in", "live.co.uk", "live.com.au", "btinternet.com", "sky.com", "virginmedia.com",
    "talktalk.net", "bigpond.com", "bigpond.net.au", "optusnet.com.au", "iinet.net.au", "web.de", "gmx.de", "gmx.net",
    "t-online.de", "freenet.de", "orange.fr", "free.fr", "laposte.net", "sfr.fr", "libero.it", "comcast.net",
    "verizon.net", "att.net", "sbcglobal.net", "cox.net", "charter.net", "shaw.ca", "rogers.com", "sympatico.ca",
    "qq.com", "163.com", "126.com", "mail.ru", "naver.com",
})
# Links that point at a platform profile, not the company's own site -> treat as "needs website".
PLATFORM_HOSTS = frozenset({
    "facebook.com", "fb.com", "instagram.com", "linkedin.com", "twitter.com", "x.com", "youtube.com", "tiktok.com",
    "pinterest.com", "linktr.ee", "google.com", "goo.gl", "g.page", "bit.ly", "wa.me", "t.me", "yelp.com",
    "tripadvisor.com", "amazon.com", "etsy.com", "booking.com", "apple.com", "medium.com", "github.com",
})
# Hosting platforms: only the bare root is a platform; acme.wixsite.com is the company's own site.
PLATFORM_ROOTS = frozenset({"wixsite.com", "wordpress.com", "blogspot.com", "squarespace.com", "webflow.io",
                            "shopify.com", "myshopify.com", "weebly.com", "godaddysites.com"})


# -- cell / field normalization --------------------------------------------------------------

_PLAIN_NUMBER = re.compile(r"[+-][\d\s().\-]+")


def is_formula_like(s: str) -> bool:
    """Would a spreadsheet run this as a formula? Phone numbers like '+44 20 7946 0958' are not."""
    return bool(s) and (s[0] in "=@\t\r" or (s[0] in "+-" and not _PLAIN_NUMBER.fullmatch(s)))


def sanitize_cell(value) -> str:
    """Trim and strip spreadsheet formula prefixes (keeps '+44…' phone numbers)."""
    s = "" if value is None else str(value).strip()
    while is_formula_like(s):
        s = s[1:].strip()
    return s


def normalize_header(header: str) -> str:
    h = sanitize_cell(header).lower()
    h = re.sub(r"[_\-./]+", " ", h)
    return re.sub(r"\s+", " ", h).strip()


def _http_url(raw: str | None):
    """Parse a user-typed web address (scheme optional). None unless it's a valid http(s) URL with a dotted host."""
    s = sanitize_cell(raw)
    if not s:
        return None
    if not re.match(r"^[a-z][a-z0-9+.-]*:", s, re.I) or re.match(r"^[\w.-]+:\d", s):
        s = "https://" + s  # no scheme (also "host:port" forms)
    try:
        p = urlparse(s)
        host, port = (p.hostname or "").strip(".").lower(), p.port
    except ValueError:
        return None
    if p.scheme.lower() not in ("http", "https") or "." not in host:
        return None
    return p, host, port


def is_platform_host(host: str) -> bool:
    host = host[4:] if host.startswith("www.") else host
    return (host in PLATFORM_ROOTS or host in PLATFORM_HOSTS
            or any(host.endswith("." + p) for p in PLATFORM_HOSTS))


def normalize_domain(raw: str | None) -> str | None:
    parsed = _http_url(raw)
    if not parsed:
        return None
    host = parsed[1]
    return host[4:] if host.startswith("www.") else host


def normalize_website(raw: str | None) -> str | None:
    """The site's root URL (we research from the homepage, even if a deep link was given)."""
    parsed = _http_url(raw)
    if not parsed:
        return None
    p, host, port = parsed
    return f"{p.scheme.lower()}://{host}" + (f":{port}" if port else "")


def normalize_email(raw: str | None) -> str | None:
    s = sanitize_cell(raw).lower()
    return s if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", s) else None


def normalize_linkedin(raw: str | None) -> str | None:
    s = sanitize_cell(raw)
    if not s or "linkedin." not in s.lower():
        return None
    parsed = _http_url(s)
    if not parsed or "linkedin." not in parsed[1]:
        return None
    p = parsed[0]
    return f"https://{parsed[1]}{p.path}".rstrip("/")


def parse_employee_count(raw) -> int | None:
    nums = re.findall(r"\d+", sanitize_cell(raw).replace(",", ""))
    return int(nums[-1]) if nums else None


def detect_mapping(headers: list[str]) -> dict[str, str | None]:
    return {h: HEADER_ALIASES.get(normalize_header(h)) for h in headers}


def normalize_row(raw: dict, mapping: dict[str, str | None]) -> dict:
    row: dict = {}
    for header, field in mapping.items():
        if not field:
            continue
        value = sanitize_cell(raw.get(header))
        if value and not row.get(field):
            row[field] = value

    if row.get("email"):
        row["email"] = normalize_email(row["email"]) or ""
    if row.get("linkedin_url"):
        row["linkedin_url"] = normalize_linkedin(row["linkedin_url"])
    if row.get("employee_count") is not None:
        row["employee_count"] = parse_employee_count(row["employee_count"])

    website = normalize_website(row.get("website")) if row.get("website") else None
    domain = normalize_domain(row.get("company_domain")) or normalize_domain(website)
    if domain and is_platform_host(domain):  # a Facebook page or Linktree isn't the company's website
        domain, website = None, None
    if not domain and row.get("email"):
        email_domain = row["email"].split("@", 1)[1]
        if email_domain not in FREE_EMAIL_DOMAINS and not is_platform_host(email_domain):
            domain = email_domain
    if website:
        row["website"] = website
    elif domain:
        row["website"] = f"https://{domain}"
    else:
        row.pop("website", None)  # unusable value (javascript:, platform link, malformed)
    if domain:
        row["company_domain"] = domain
    else:
        row.pop("company_domain", None)

    first, last, full = row.get("first_name"), row.get("last_name"), row.get("full_name")
    if full and not (first or last):
        parts = full.split()
        row["first_name"] = parts[0]
        row["last_name"] = " ".join(parts[1:]) if len(parts) > 1 else ""
    elif (first or last) and not full:
        row["full_name"] = " ".join(p for p in (first, last) if p)
    return row


# -- file readers ----------------------------------------------------------------------------

def _rows_from_matrix(matrix: list[list]) -> tuple[list[str], list[dict]]:
    cleaned = [[sanitize_cell(c) for c in r] for r in matrix]
    header_idx = None
    for i, r in enumerate(cleaned[:25]):
        if sum(1 for c in r if c and HEADER_ALIASES.get(normalize_header(c))) >= 2:
            header_idx = i
            break
    if header_idx is None:
        header_idx = next((i for i, r in enumerate(cleaned) if any(r)), None)
    if header_idx is None:
        return [], []
    headers, seen = [], {}
    for c in cleaned[header_idx]:
        name = c or f"Column {len(headers) + 1}"
        seen[name] = seen.get(name, 0) + 1
        headers.append(name if seen[name] == 1 else f"{name} ({seen[name]})")
    rows = []
    for r in cleaned[header_idx + 1:]:
        if not any(r):
            continue
        rows.append({h: (r[i] if i < len(r) else "") for i, h in enumerate(headers)})
    return headers, rows


def read_csv_bytes(data: bytes) -> tuple[list[str], list[dict]]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252", errors="replace")
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ","
    return _rows_from_matrix(list(csv.reader(io.StringIO(text), delimiter=delimiter)))


def read_xlsx_bytes(data: bytes) -> tuple[list[str], list[dict]]:
    import openpyxl  # local import keeps module import cheap

    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        ws = wb.worksheets[0]
        matrix = [list(r) for r in ws.iter_rows(values_only=True)]
    finally:
        wb.close()
    return _rows_from_matrix(matrix)


def read_bytes(data: bytes) -> tuple[list[str], list[dict]]:
    """Detect xlsx (zip magic) vs CSV."""
    return read_xlsx_bytes(data) if data[:2] == b"PK" else read_csv_bytes(data)


def sheet_url_to_csv(url: str) -> str:
    """Google Sheets share/edit links -> CSV export link. Other URLs pass through."""
    url = (url or "").strip()
    m = re.match(r"https?://docs\.google\.com/spreadsheets/d/([\w-]+)", url)
    if not m:
        return url
    gid = re.search(r"[#&?]gid=(\d+)", url)
    if "/pubhtml" in url:
        return url.replace("/pubhtml", "/pub").split("?")[0].split("#")[0] + "?output=csv" + (
            f"&gid={gid.group(1)}" if gid else "")
    return f"https://docs.google.com/spreadsheets/d/{m.group(1)}/export?format=csv&gid={gid.group(1) if gid else 0}"


# -- preview -------------------------------------------------------------------------------

def normalize_rows(headers: list[str], rows: list[dict], mapping: dict | None = None) -> list[dict]:
    mapping = mapping or detect_mapping(headers)
    return [normalize_row(r, mapping) for r in rows]


def preview(headers: list[str], rows: list[dict], mapping: dict | None = None) -> dict:
    mapping = mapping or detect_mapping(headers)
    normalized = normalize_rows(headers, rows, mapping)
    seen, dupes = set(), 0
    for r in normalized:
        d = r.get("company_domain")
        if d:
            dupes += d in seen
            seen.add(d)
    return {
        "headers": headers,
        "mapping": mapping,
        "fields": list(FIELDS) + list(EXTRA_FIELDS),
        "sample": normalized[:5],
        "total": len(normalized),
        "missing_website": sum(1 for r in normalized if not r.get("company_domain")),
        "duplicate_domains": dupes,
    }
