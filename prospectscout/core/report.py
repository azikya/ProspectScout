"""The free 1-page Website Opportunity Review the prospect receives.

Print-ready HTML (A4). Shows what's working, three opportunities (what and why, never
how or how much), a fact-backed comparison with competitors, and a clear next step.
"""

from __future__ import annotations

from datetime import date
from html import escape
from pathlib import Path

from .analyze import BUDGET_LINE

CSS_PATH = Path(__file__).resolve().parents[2] / "ui" / "report.css"


def _e(value) -> str:
    return escape(str(value or ""), quote=True)


def _safe_url(url) -> str:
    """Only http(s) links are rendered; anything else (javascript:, data:) is dropped."""
    url = str(url or "").strip()
    return url if url.lower().startswith(("http://", "https://")) else ""


def _css() -> str:
    try:
        return CSS_PATH.read_text(encoding="utf-8")
    except OSError:
        return ""


def _page(title: str, body: str) -> str:
    return (f"<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            f"<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><title>{_e(title)}</title>"
            f"<style>{_css()}</style></head><body>"
            f"<div class=\"toolbar\"><button onclick=\"window.print()\">Save as PDF</button></div>"
            f"<main class=\"sheet\">{body}</main></body></html>")


def render_report(company: dict, contact: dict | None, settings: dict, today: date) -> str:
    domain = company.get("domain") or ""
    ours = settings.get("company") or {}
    sender = settings.get("sender") or {}
    analysis = company.get("analysis_json") or {}
    rep = analysis.get("report") or {}
    when = f"{today.day} {today:%B %Y}"
    logo = ours.get("logo") or "/brand/logo.png"

    head = (f"<header class=\"r-head\"><div><div class=\"kicker\">Website Opportunity Review</div>"
            f"<h1>{_e(rep.get('headline') or domain)}</h1>"
            f"<div class=\"meta\">{_e(domain)} · Prepared"
            + (f" for {_e(contact.get('full_name'))}" if contact and contact.get("full_name") else "")
            + f" by {_e(ours.get('name'))} · {_e(when)}</div></div>"
            f"<img src=\"{_e(logo)}\" alt=\"{_e(ours.get('short_name') or ours.get('name'))}\"></header>")

    if (company.get("fit") or analysis.get("fit")) in ("skip", "manual"):
        return _page(f"Website Opportunity Review: {domain}",
                     head + "<p>A review is not available for this company: it was marked as not a fit or needs a "
                            "manual check, so there is nothing to send.</p>")
    if not rep.get("opportunities"):
        return _page(f"Website Opportunity Review: {domain}",
                     head + "<p>This review is not ready yet. Research for this company is still running "
                            "or did not find opportunities worth sharing.</p>")

    parts = [head]
    working = [w for w in (rep.get("whats_working") or []) if w][:2]
    if working:
        parts.append("<h2>What's working</h2><ul class=\"working\">"
                     + "".join(f"<li>{_e(w)}</li>" for w in working) + "</ul>")
    opps = (rep.get("opportunities") or [])[:3]
    parts.append("<h2>Three opportunities we noticed</h2><div class=\"opps\">" + "".join(
        f"<div class=\"opp\"><div class=\"num\">{i}</div><div><h3>{_e(o.get('title'))}</h3>"
        f"<p>{_e(o.get('why_it_matters'))}</p></div></div>" for i, o in enumerate(opps, 1)) + "</div>")

    rows = rep.get("comparison") or []
    if rows:
        names: list[str] = []
        for r in rows:
            for c in r.get("competitors") or []:
                if c.get("name") and c["name"] not in names:
                    names.append(c["name"])
        names = names[:3]
        label = company.get("company_name") or domain

        def mark(v):
            return "<span class=\"yes\">✓</span>" if v else "<span class=\"no\">✗</span>"
        body_rows = ""
        for r in rows[:6]:
            comp = {c.get("name"): c.get("has") for c in r.get("competitors") or []}
            body_rows += (f"<tr><td>{_e(r.get('feature'))}</td><td>{mark(r.get('prospect_has'))}</td>"
                          + "".join(f"<td>{mark(comp.get(n)) if n in comp else '–'}</td>" for n in names) + "</tr>")
        parts.append("<h2>How you compare</h2><table class=\"compare\"><thead><tr><th>Feature</th>"
                     f"<th>{_e(label)}</th>" + "".join(f"<th>{_e(n)}</th>" for n in names)
                     + f"</tr></thead><tbody>{body_rows}</tbody></table>"
                     f"<div class=\"source\">From public websites, {_e(when)}.</div>")

    contact_bits = [b for b in (sender.get("name"), sender.get("title"), sender.get("email")) if b]  # never a phone number
    booking = _safe_url(sender.get("booking_link"))
    parts.append("<section class=\"next\"><h2>Next step: a 15-minute call</h2>"
                 "<p>We'll walk you through these ideas and answer your questions, with no obligation.</p>"
                 f"<p class=\"budget\">{_e(BUDGET_LINE)}</p>"
                 + (f"<p>{_e(' · '.join(contact_bits))}</p>" if contact_bits else "")
                 + (f"<p><a href=\"{_e(booking)}\">{_e(booking)}</a></p>" if booking else "") + "</section>")
    parts.append(f"<footer class=\"r-foot\">{_e(ours.get('name'))} · {_e(ours.get('website'))}</footer>")
    return _page(f"Website Opportunity Review: {domain}", "".join(parts))
