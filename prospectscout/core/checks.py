"""Turn a crawl into verifiable Facts.

Every claim a message makes about a prospect's site must trace back to one of these
Facts (id ``F1``, ``F2``…), each with evidence (a URL or a short snippet). Claude
interprets Facts; it never invents them.

Pure and deterministic. Tech-role patterns are ported from ProspectIQ's ``detection``.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date
from urllib.parse import unquote, urlparse

from .site import CrawlResult, Page, is_same_site


@dataclass
class Fact:
    id: str
    kind: str          # ok | issue | missing | info
    category: str      # reachability|security|performance|mobile|seo|content|feature|tech|crm|analytics|contact|intent
    label: str
    evidence: str
    confidence: str = "high"
    signal: str = ""   # "" | hiring_tech_roles | funding_news | expansion_launch

    def to_dict(self) -> dict:
        return asdict(self)


# -- signature tables -------------------------------------------------------------------------

CHAT_WIDGETS = {
    "Intercom": ("widget.intercom.io", "js.intercomcdn.com", "intercomsettings"),
    "Drift": ("js.driftt.com", "drift.com/include"),
    "Tawk.to": ("embed.tawk.to",),
    "Tidio": ("code.tidio.co",),
    "Crisp": ("client.crisp.chat",),
    "HubSpot chat": ("js.usemessages.com",),
    "Zendesk": ("static.zdassets.com", "zopim"),
    "LiveChat": ("cdn.livechatinc.com",),
    "Freshchat": ("wchat.freshchat.com", "freshworks.com/widget"),
}
BOOKING_WIDGETS = {
    "Calendly": ("calendly.com",), "Acuity": ("acuityscheduling.com",), "Setmore": ("setmore.com",),
    "SimplyBook": ("simplybook.",), "Booksy": ("booksy.com",), "Square Appointments": ("squareup.com/appointments",
                                                                                         "square.site/book"),
    "Cal.com": ("cal.com/",), "Bookly": ("bookly",), "OpenTable": ("opentable.com",), "Zoho Bookings": ("zohobookings",),
}
CRM_SIGNATURES = {
    "Salesforce (Web-to-Lead form)": ("webto.salesforce.com", "servlet.webtolead"),
    "Salesforce Pardot": ("pi.pardot.com", "go.pardot.com", "pardot.com/pd.js"),
    "HubSpot": ("js.hs-scripts.com", "js.hsforms.net", "hs-analytics", "hbspt.forms"),
    "Zoho": ("zohopublic", "crm.zoho", "salesiq.zoho"),
    "Marketo": ("munchkin.marketo.net", "mktoforms"),
    "Mailchimp": ("list-manage.com", "chimpstatic.com"),
}
ANALYTICS = {
    "Google Analytics": ("gtag/js?id=g-", "google-analytics.com/analytics.js", "googletagmanager.com/gtag"),
    "Google Tag Manager": ("googletagmanager.com/gtm.js",),
    "Meta Pixel": ("connect.facebook.net",),
    "LinkedIn Insight": ("snap.licdn.com",),
}
CMS_SIGNATURES = {
    "WordPress": ("wp-content", "wp-includes"),
    "Shopify": ("cdn.shopify.com", "shopify.theme"),
    "Wix": ("static.wixstatic.com", "wix.com"),
    "Squarespace": ("squarespace.com", "static1.squarespace"),
    "Webflow": ("webflow.js", "assets.website-files.com", "data-wf-site"),
    "Next.js": ("/_next/", "__next_data__"),
}
SOCIAL = {"LinkedIn": "linkedin.com/", "Facebook": "facebook.com/", "Instagram": "instagram.com/",
          "X/Twitter": ("twitter.com/", "x.com/"), "YouTube": "youtube.com/"}

TECH_ROLE_PATTERNS: tuple[tuple[str, str], ...] = (  # from ProspectIQ domain/detection.py
    ("software engineer", r"\bsoftware engineers?\b"),
    ("backend engineer", r"\bback[\s-]?end engineers?\b"),
    ("frontend engineer", r"\bfront[\s-]?end engineers?\b"),
    ("full-stack engineer", r"\bfull[\s-]?stack engineers?\b"),
    ("data engineer", r"\bdata engineers?\b"),
    ("AI/ML engineer", r"\b(?:ai(?:[\s/-]?ml)?|machine learning) engineers?\b"),
    ("DevOps", r"\bdevops(?: engineers?)?\b"),
    ("QA", r"\b(?:qa engineers?|qa analysts?|quality assurance engineers?|hiring (?:a |an )?qa)\b"),
    ("UI/UX", r"\b(?:ui[\s/-]?ux|ux designers?|ui designers?|ui engineers?|ux engineers?)\b"),
    ("product manager", r"\bproduct managers?\b"),
    ("developer", r"\b(?:web|software|mobile|php|python|java|\.net|react|salesforce) developers?\b"),
)
LEADERSHIP_OPENING = (r"\b(?:hiring|opening for|seeking)(?: a| an| our)? "
                      r"(?:cto|vp of engineering|head of engineering|engineering manager|tech(?:nical)? lead)\b")
FUNDING_RE = re.compile(r"\b(?:raised|raises|secured|closes?|closed)\b[^.\n]{0,60}\b(?:seed|series [a-e]|funding|"
                        r"investment|round)\b", re.I)
EXPANSION_RE = re.compile(r"\b(?:opening|opened|opens) (?:a |our )?new (?:office|location|branch|warehouse|facility|store)"
                          r"|\bexpand(?:s|ed|ing)? (?:to|into)\b|\blaunch(?:es|ed|ing)? (?:our|a|the) new\b", re.I)

PLACEHOLDER_PATTERNS = (
    (r"lorem ipsum", "Placeholder text left on site ('Lorem ipsum')"),
    (r"\bjohn doe\b", "Placeholder testimonial name ('John Doe')"),
    (r"\bsample text\b", "Placeholder text left on site ('sample text')"),
    (r"\byour (?:company|text|title) here\b", "Placeholder text left on site ('your … here')"),
    (r"(?<!\d)(?:\+?91[-\s]?)?9876543210(?!\d)|(?<!\d)1234567890(?!\d)", "Placeholder phone number on site"),
)
CAREERS_URL_RE = re.compile(r"career|jobs?\b|vacanc|karriere|stellen|empleo|join-?us|hiring|work-with-us", re.I)
HIRING_TEXT_RE = re.compile(r"we.?re hiring|we are hiring|open positions?|job openings?|current openings|"
                            r"vacanc(?:y|ies)|apply now|join our team", re.I)
STORE_LINK_RE = re.compile(r"/(?:cart|checkout|basket|shop|store|products?)(?:/|$)", re.I)
EMPTY_PRICE_RE = re.compile(r"\bplan\b[^$€£₹\d]{0,30}?/\s*(?:month|mo|year|yr)\b", re.I)
MOJIBAKE_RE = re.compile(r"�|â€|Ã©|Ã¨|Ã¢|Ã¶|Ã¼")
COPYRIGHT_RE = re.compile(r"(?:©|&copy;|\(c\)|copyright)\s*(?:\d{4}\s*[-–]\s*)?(\d{4})", re.I)
PHONE_RE = re.compile(
    r"(?:\+\d[\d\s().-]{7,}\d)"                              # international: +44 20 7946 0958
    r"|(?:\(\d{2,5}\)\s?\d{3,4}[\s.-]?\d{3,4})"               # (02) 9876 5432, (512) 555-0100
    r"|(?:\b0\d{2,4}[\s.-]?\d{3,4}[\s.-]?\d{3,4}\b)"          # national trunk 0: 020 7946 0958, 030 12345678
    r"|(?:\b1[38]00[\s.-]?\d{3}[\s.-]?\d{3,4}\b)"             # 1800 / 1300 numbers
    r"|(?:\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b)")                  # 512-555-0100
EMBED_FORM_SIGS = {"HubSpot form": ("hsforms.net", "hbspt.forms"), "JotForm": ("jotform.com",),
                   "Typeform": ("typeform.com", "data-tf-widget"), "Google Form": ("docs.google.com/forms", "forms.gle"),
                   "WPForms": ("wpforms",), "Contact Form 7": ("wpcf7",), "Gravity Forms": ("gform_wrapper", "gravityforms"),
                   "Formstack": ("formstack.com",), "Cognito Forms": ("cognitoforms.com",), "Tally": ("tally.so",),
                   "Zoho Forms": ("forms.zohopublic", "zfrmz"), "123FormBuilder": ("123formbuilder",)}
TAG_MANAGERS = ("googletagmanager.com/gtm.js", "gtm.js?id=", "segment.com/analytics.js", "tealium")
EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
YEAR_RE = re.compile(r"\b(20[0-3]\d)\b")


# -- helpers ----------------------------------------------------------------------------------

def _is_contact_form(form: dict) -> bool:
    attrs = (form.get("attrs") or "").lower()
    types = form.get("types") or []
    if "search" in attrs or (types and all(t == "search" for t in types)):
        return False
    if "textarea" in types or re.search(r"contact|wpforms|wpcf7|gform|elementor-form|hs-form|enquir|inquir", attrs):
        return True
    if any(t in ("email", "tel") for t in types) and len(types) >= 2:
        return True
    return any(re.search(r"message|phone|subject|enquiry|inquiry", i, re.I) for i in form.get("inputs", []))


def _blob(p: Page) -> str:
    return " ".join([p.html or "", p.inline_js or "", " ".join(p.scripts), " ".join(p.links)]).lower()


def _snippet(text: str, start: int, end: int, pad: int = 30) -> str:
    s = text[max(0, start - pad):min(len(text), end + pad)]
    return " ".join(s.split())[:120]


def _find_sig(pages: list[Page], table: dict) -> dict[str, str]:
    found: dict[str, str] = {}
    for p in pages:
        blob = _blob(p)
        for name, sigs in table.items():
            sigs = (sigs,) if isinstance(sigs, str) else sigs
            if name not in found and any(s in blob for s in sigs):
                found[name] = p.final_url or p.url
    return found


def site_language(result: CrawlResult) -> str:
    if result.pages and result.pages[0].lang:
        return result.pages[0].lang
    return "en"


class _Builder:
    def __init__(self, low_confidence_missing: bool):
        self.facts: list[Fact] = []
        self.low = low_confidence_missing

    def add(self, kind, category, label, evidence="", signal="", confidence="high"):
        if kind == "missing" and self.low:
            confidence = "low"
        self.facts.append(Fact(f"F{len(self.facts) + 1}", kind, category, label, str(evidence)[:300],
                               confidence, signal))


# -- main -------------------------------------------------------------------------------------

def run_checks(result: CrawlResult, today: date | None = None) -> list[Fact]:
    today = today or date.today()
    if result.reachable and not result.pages:
        b = _Builder(False)
        skipped = (result.skipped or [{}])[0]
        ctype = (skipped.get("content_type") or "unknown type").split(";")[0]
        b.add("info", "reachability", f"Homepage is not a web page ({ctype})",
              skipped.get("url") or result.final_url or result.start_url)
        return b.facts
    if not result.reachable:
        b = _Builder(False)
        b.add("issue", "reachability", "Website unreachable", result.error or "No pages could be loaded")
        return b.facts

    lang = site_language(result)
    b = _Builder(low_confidence_missing=not lang.startswith("en"))
    pages: list[Page] = result.pages
    home = pages[0]
    base = result.final_url or home.final_url
    all_text = "\n".join(p.text for p in pages)
    low_text = all_text.lower()

    # reachability / security / performance
    b.add("ok", "reachability", "Website loads", f"{base} (HTTP {home.status})")
    if not result.https:
        b.add("issue", "security", "Site doesn't use HTTPS (browsers show 'Not secure')", base)
    else:
        if result.http_redirects_to_https is False:
            b.add("issue", "security", "HTTP version doesn't redirect to HTTPS", base.replace("https://", "http://", 1))
        if result.ssl_days_left is not None:
            if result.ssl_days_left < 0:
                b.add("issue", "security", "SSL certificate has expired", f"expired {-result.ssl_days_left} days ago")
            elif result.ssl_days_left < 21:
                b.add("issue", "security", f"SSL certificate expires in {result.ssl_days_left} days", base)
    secs = home.elapsed_ms / 1000
    if home.elapsed_ms > 3000:
        b.add("issue", "performance", f"Homepage is slow to respond ({secs:.1f}s)", base)
    else:
        b.add("ok", "performance", f"Homepage responds quickly ({secs:.1f}s)", base)

    # mobile / SEO
    if "viewport" not in home.meta:
        b.add("issue", "mobile", "No mobile viewport tag (site may not display well on phones)", base)
    else:
        b.add("ok", "mobile", "Mobile viewport tag present", base)
    if not home.title:
        b.add("issue", "seo", "Homepage has no page title", base)
    if not home.meta.get("description"):
        b.add("issue", "seo", "No meta description (weak Google search snippet)", base)
    if not home.headings:
        b.add("issue", "seo", "No main heading on the homepage", base)

    # broken links / images (cap 5 each)
    # Only 404/410 (gone) and 5xx (server error, already confirmed by a GET retry) count as broken.
    # 401/403/429/timeouts mean "couldn't verify" (member areas, bot protection, rate limits).
    images = {u for p in pages for u in p.images}
    same_site = [(u, s) for u, s in result.link_status.items() if is_same_site(u, base)]
    broken = [(u, s) for u, s in same_site if s in (404, 410) or 500 <= s < 600]
    unverified = [(u, s) for u, s in same_site if s == 0 or (400 <= s < 500 and s not in (404, 410))]
    for u, s in [x for x in broken if x[0] not in images][:5]:
        b.add("issue", "content", f"Broken link ({s})", f"{u} ({s})")
    for u, s in [x for x in broken if x[0] in images][:5]:
        b.add("issue", "content", f"Broken image ({s})", f"{u} ({s})")
    if unverified:
        sample = ", ".join(f"{u} ({s or 'no response'})" for u, s in unverified[:3])
        b.add("info", "content", f"Couldn't check {len(unverified)} link(s) (blocked, private or timed out)", sample,
              confidence="low")

    # content quality
    years = [int(y) for y in COPYRIGHT_RE.findall(home.text)]
    if years and max(years) < today.year - 1:
        b.add("issue", "content", f"Copyright year outdated (© {max(years)})", base)
    for pattern, label in PLACEHOLDER_PATTERNS:
        for p in pages:
            m = re.search(pattern, p.text, re.I)
            if m:
                b.add("issue", "content", label, f"{p.final_url}: \"{_snippet(p.text, m.start(), m.end())}\"")
                break
    for p in pages:
        m = MOJIBAKE_RE.search(p.text)
        if m:
            b.add("issue", "content", "Broken characters in text (encoding problem)",
                  f"{p.final_url}: \"{_snippet(p.text, m.start(), m.end(), 15)}\"")
            break
    for p in pages:
        m = EMPTY_PRICE_RE.search(p.text)
        if m:
            b.add("issue", "content", "Pricing shows a plan with no price",
                  f"{p.final_url}: \"{_snippet(p.text, m.start(), m.end(), 5)}\"")
            break
    if len(home.text) < 200 and len(home.scripts) >= 3:
        b.add("info", "tech", "Content loads by JavaScript; text checks are limited", base, confidence="low")

    # tech stack
    gen = home.meta.get("generator", "").split(";")[0].strip()[:60]
    cms = _find_sig(pages, CMS_SIGNATURES)
    if gen:
        b.add("info", "tech", f"Built with {gen}", base)
    for name in cms:
        if not gen or name.lower() not in gen.lower():
            b.add("info", "tech", f"Built with {name}", cms[name])
    for p in pages:
        m = re.search(r"jquery[-./@]?(?:min[-.])?v?(\d+)\.(\d+)(?:\.\d+)?", " ".join(p.scripts).lower())
        if m:
            major, minor = int(m.group(1)), int(m.group(2))
            kind = "issue" if major < 3 else "info"
            b.add(kind, "tech", f"Uses jQuery {major}.{minor}" + (" (outdated)" if major < 3 else ""), p.final_url)
            break

    # features
    # Widgets can be injected by a tag manager or JS app at runtime, where our HTML read can't see them.
    js_injected = any(any(t in _blob(p) for t in TAG_MANAGERS) for p in pages) or (
        len(home.text) < 200 and len(home.scripts) >= 3)

    def feature(found: dict, ok_label: str, missing_label: str, js_hidable: bool = False):
        if found:
            name, where = next(iter(found.items()))
            b.add("ok", "feature", f"{ok_label} ({name})", where)
        else:
            b.add("missing", "feature", missing_label, base, confidence="low" if js_hidable and js_injected else "high")

    contact_forms = [p for p in pages for f in p.forms if _is_contact_form(f)]
    embedded = _find_sig(pages, EMBED_FORM_SIGS)
    if contact_forms:
        b.add("ok", "feature", "Contact form present", contact_forms[0].final_url)
    elif embedded:
        name, where = next(iter(embedded.items()))
        b.add("ok", "feature", f"Contact form present ({name})", where)
    else:
        b.add("missing", "feature", "No contact form found", base, confidence="low" if js_injected else "high")
    feature(_find_sig(pages, BOOKING_WIDGETS), "Online booking", "No online booking or scheduling seen", js_hidable=True)
    feature(_find_sig(pages, CHAT_WIDGETS), "Live chat", "No live chat on the site", js_hidable=True)
    feature(_find_sig(pages, {"WhatsApp": ("wa.me/", "api.whatsapp.com", "whatsapp://")}),
            "WhatsApp contact", "No WhatsApp contact link")
    portal = [u for p in pages for u in p.links
              if re.search(r"log-?in|sign-?in|portal|my-?account|client-?area|dashboard", urlparse(u).path, re.I)]
    if portal:
        b.add("ok", "feature", "Customer login / portal", portal[0])
    else:
        b.add("missing", "feature", "No customer login or portal", base)
    store_links = [u for p in pages for u in p.links if is_same_site(u, base) and STORE_LINK_RE.search(urlparse(u).path)]
    if store_links or re.search(r"\badd to (?:cart|basket)\b", low_text):
        b.add("info", "feature", "Has an online store / cart", store_links[0] if store_links else base)
    pricing = [p.final_url for p in pages if re.search(r"pricing|plans|prices|preise|tarifs|precios", p.final_url, re.I)]
    if pricing:
        b.add("ok", "feature", "Pricing page", pricing[0])
    else:
        b.add("missing", "feature", "No pricing page", base)
    if "testimonial" in low_text or "what our clients say" in low_text:
        b.add("info", "feature", "Shows testimonials", base)

    blog_pages = [p for p in pages if re.search(r"blog|news|insights|press|aktuelles|actualit|noticias", p.final_url, re.I)]
    if blog_pages:
        latest = max((int(y) for p in blog_pages for y in YEAR_RE.findall(p.text) if int(y) <= today.year), default=None)
        if latest and latest < today.year - 1:
            b.add("issue", "content", f"Blog/news looks inactive (latest post year {latest})", blog_pages[0].final_url)
        else:
            b.add("info", "content", "Has a blog / news section" + (f" (latest {latest})" if latest else ""),
                  blog_pages[0].final_url)

    # analytics / CRM
    analytics = _find_sig(pages, ANALYTICS)
    for name, where in analytics.items():
        b.add("info", "analytics", f"Uses {name}", where)
    if not analytics:
        b.add("missing", "analytics", "No analytics tracking detected", base, confidence="low")
    crm = _find_sig(pages, CRM_SIGNATURES)
    for name, where in crm.items():
        b.add("info", "crm", f"Uses {name}", where)
    if not crm:
        b.add("info", "crm", "No CRM or marketing-automation tools visible on the site", base, confidence="low")

    # contact details / social
    tel = next((unquote(u[4:]) for p in pages for u in p.links if u.lower().startswith("tel:")), None)
    phone = tel or next((m.group(0) for p in pages for m in [PHONE_RE.search(p.text)] if m), None)
    if phone:
        b.add("ok", "contact", "Phone number visible", phone.strip())
    else:
        b.add("missing", "contact", "No phone number visible", base)
    email = next((m.group(0) for p in pages for m in [EMAIL_RE.search(p.text)] if m), None)
    if email:
        b.add("ok", "contact", "Email address visible", email)
    socials = [name for name, sig in SOCIAL.items()
               if any((s in u.lower()) for u in {x for p in pages for x in p.links}
                      for s in ((sig,) if isinstance(sig, str) else sig))]
    if socials:
        b.add("info", "contact", "Social links: " + ", ".join(socials), base)

    # buying-intent signals
    for p in pages:
        roles = [label for label, pat in TECH_ROLE_PATTERNS if re.search(pat, p.text, re.I)]
        if re.search(LEADERSHIP_OPENING, p.text, re.I):
            roles.append("engineering leadership")
        if roles and (CAREERS_URL_RE.search(urlparse(p.final_url).path) or HIRING_TEXT_RE.search(p.text)):
            b.add("info", "intent", f"Hiring tech roles ({', '.join(roles[:3])})",
                  f"{', '.join(roles)} — {p.final_url}", signal="hiring_tech_roles")
            break
    for p in pages:
        m = FUNDING_RE.search(p.text)
        if m:
            b.add("info", "intent", "Funding news on site", f"{p.final_url}: \"{_snippet(p.text, m.start(), m.end(), 10)}\"",
                  signal="funding_news")
            break
    for p in pages:
        m = EXPANSION_RE.search(p.text)
        if m:
            b.add("info", "intent", "Expansion / launch news on site",
                  f"{p.final_url}: \"{_snippet(p.text, m.start(), m.end(), 10)}\"", signal="expansion_launch")
            break
    return b.facts
