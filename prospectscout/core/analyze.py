"""The sales brain: prompt, answer schema, and the rules every message must pass.

Claude acts as a senior B2B salesperson. It receives our company profile, the contact,
code-verified Facts about the prospect's website, and page text, and returns one JSON
object: fit, findings, buying intent, competitors, an 8-message sequence, a 1-page
report, and a call-prep sheet. ``validate`` enforces the user's rules (no prices or
timelines, one real finding per opener, every problem claim backed by a Fact). One retry
with the broken rules listed; anything still wrong is surfaced as "needs review".

Pure: the LLM is passed in as ``run`` (see ``adapters.claude_runner.run_llm``).
"""

from __future__ import annotations

import copy
import json
import re
from typing import Callable

BUDGET_LINE = ("We can talk through scope and timing on a call or at our next meeting, "
               "and we'll plan it around your budget.")
MESSAGE_KEYS = ("linkedin_note", "linkedin_after_accept", "email_subject", "email_body", "whatsapp",
                "report_delivery", "followup_1", "followup_2", "followup_3")
OPENER_KEYS = ("linkedin_note", "email_body", "whatsapp")
SIGNED_KEYS = ("email_body", "report_delivery", "followup_1", "followup_2", "followup_3")
LIMITS_CHARS = {"linkedin_note": 300}
LIMITS_WORDS = {"email_subject": 7, "email_body": 90, "whatsapp": 60, "linkedin_after_accept": 90,
                "followup_1": 90, "followup_2": 90, "followup_3": 60, "report_delivery": 110}

_CURRENCY = r"usd|eur|gbp|inr|aed|sar|qar|kwd|omr|bhd|aud|cad|sgd|dhs?|rs\.?"
_NUMBER_WORD = r"one|two|three|four|five|six|seven|eight|nine|ten|twelve|a couple of|a few|several"
BANNED = [
    (re.compile(rf"[$€£₹]\s?\d|\b\d[\d,.]*\s?(?:{_CURRENCY}|dollars?|rupees?|dirhams?|riyals?|bucks)\b|"
                rf"\b(?:{_CURRENCY})\s?\d", re.I), "mentions a price"),
    (re.compile(rf"\b\d+\s*(?:-\s*\d+\s*)?(?:hours?|days?|weeks?|months?)\b|"
                rf"\b(?:{_NUMBER_WORD})\s+(?:business\s+|working\s+)?(?:days?|weeks?|months?)\b|"
                r"\b(?:in|within) (?:a|one) (?:day|week|month)\b", re.I), "mentions a timeline"),
    (re.compile(r"\d+\s?%|\bpercent\b", re.I), "mentions a percentage"),
    (re.compile(r"\b(?:cheaper|discount(?:ed)?|lowest price|low cost|affordable rates?)\b", re.I), "talks about cheap pricing"),
    (re.compile(r"\b(?:ai|auto(?:matically)?|machine|computer)[- ]generated\b|\bgenerated (?:by|with|using) "
                r"(?:ai|a tool|software)\b|\bthis (?:message|email|note) was (?:generated|written by)\b|\bas an ai\b|"
                r"\bthis tool\b|\bautomated (?:message|scan|audit|analysis|review)\b|\bwritten by ai\b|\bchatgpt\b|"
                r"\b(?:our|an?|the|my)\s+(?:audit|scanning|scan|analysis|ai)\s+tool\b", re.I),
     "implies it was written by AI or a tool"),
    (re.compile(r"\+\d[\d\s().-]{7,}\d|\b0\d{2,4}[\s.-]?\d{3,4}[\s.-]?\d{3,4}\b|\(\d{2,5}\)\s?\d{3,4}[\s.-]?\d{3,4}"),
     "includes a phone number (we never share phone numbers)"),
    (re.compile(r"\[[^\]\n]{1,40}\]|\{[^}\n]{1,40}\}|<[A-Za-z][^>\n]{0,30}>"),
     "has an unfilled placeholder (write it out, e.g. 'Hi there' when the name is unknown)"),
    (re.compile(r"hope this (?:email |message )?finds you well|\bi am excited\b|\bi'm excited\b|\bwe are a leading\b", re.I),
     "uses a template opener"),
]

# -- schema -----------------------------------------------------------------------------------

def _s(desc: str = "") -> dict:
    return {"type": "string", "description": desc} if desc else {"type": "string"}


def _obj(props: dict, required: list | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or list(props)}


def _arr(items: dict) -> dict:
    return {"type": "array", "items": items}


_MESSAGES = _obj({k: _s() for k in MESSAGE_KEYS})

ANALYSIS_SCHEMA: dict = _obj({
    "fit": {"type": "string", "enum": ["strong", "possible", "skip", "manual"]},
    "score": {"type": "integer", "minimum": 0, "maximum": 100},
    "summary": _s("2 sentences on who they are and why they are or aren't a fit"),
    "skip_reason": _s("empty unless fit is skip/manual"),
    "company": _obj({"name": _s(), "industry": _s(), "country": _s(), "city": _s(), "size_hint": _s()}),
    "findings": _arr(_obj({
        "id": _s("O1, O2, ..."),
        "type": {"type": "string", "enum": ["broken", "missing", "competitor_gap", "growth", "ai"]},
        "title": _s(), "evidence": _s(), "fact_ids": _arr(_s()), "business_impact": _s(), "our_solution": _s(),
        "complexity": {"type": "string", "enum": ["S", "M", "L"]},
        "priority": {"type": "integer", "minimum": 1, "maximum": 5}})),
    "buying_intent": _obj({
        "level": {"type": "string", "enum": ["low", "medium", "high"]},
        "signals": _arr(_obj({"signal": _s(), "evidence": _s(), "source_url": _s(),
                              "sop_type": {"type": "string", "enum": ["hiring_tech_roles", "funding_news",
                                                                      "expansion_launch", "talks_tech_growth", "none"]}}))}),
    "competitors": _arr(_obj({"name": _s(), "url": _s(),
                              "features": _arr(_obj({"feature": _s(), "has": {"type": "boolean"}, "source_url": _s()}))})),
    "timezone_hint": _obj({"country": _s(), "city": _s(), "iana": _s("IANA zone like America/Chicago, or empty")}),
    "services_to_pitch": _arr(_s()),
    "opener_finding_id": _s(),
    "followup_finding_ids": _arr(_s()),
    "messages": _MESSAGES,
    "report": _obj({
        "headline": _s(), "whats_working": _arr(_s()),
        "opportunities": _arr(_obj({"title": _s(), "why_it_matters": _s(), "finding_id": _s("the finding it is based on")})),
        "comparison": _arr(_obj({"feature": _s(), "prospect_has": {"type": "boolean"}, "prospect_fact_id": _s(),
                                 "competitors": _arr(_obj({"name": _s(), "has": {"type": "boolean"}}))}))}),
    "call_prep": _obj({"angle": _s(), "questions": _arr(_s()),
                       "objections": _arr(_obj({"objection": _s(), "response": _s()})), "notes": _s()}),
})

MESSAGES_SCHEMA: dict = _obj({"messages": _MESSAGES, "opener_finding_id": _s(), "followup_finding_ids": _arr(_s())})

# -- prompt -----------------------------------------------------------------------------------

PLAYBOOK = f"""You are a senior B2B salesperson with 25 years of experience, working for the software company described
below. Your job: study ONE prospect's website and decide whether it is worth contacting, then write outreach that
earns a reply because it shows real, specific insight about THEIR business, not a generic pitch.

HOW WE SELL (non-negotiable):
- Lead with the prospect's problem, never with our company. Tease, don't dump: each opener names exactly ONE real,
  specific finding (name the page or element) and hints there are "a couple more". Save the rest for the call.
- The first ask is small: offer a free 1-page website review ("Want me to send it?"). The call comes after.
- NEVER mention prices, costs, rates, budgets in numbers, timelines, durations (days/weeks/months), percentages,
  "cheaper", "discount" or "low cost". Price and timing are always discussed live; use exactly: "{BUDGET_LINE}"
- NEVER include a phone number (ours or theirs) in any message or the report.
- NEVER say or imply the message was generated or written by AI or a tool. Pitching AI/automation AS A SERVICE
  for them is fine and encouraged where it genuinely helps.
- No template openers ("I hope this finds you well", "I'm excited", "we are a leading..."). Be warm, direct,
  plain English, respectful: never insult their site. Write in English.
- Only claim case studies listed under OUR COMPANY. Never invent clients, numbers or results.

EVIDENCE RULES:
- Problems of type "broken" or "missing" MUST cite the Fact ids (F1, F2, ...) that prove them in fact_ids, and
  must only cite Facts of kind issue/missing. Never claim something is broken or missing without a Fact.
- Facts marked [low confidence] may inform your thinking but must NOT be used for findings mentioned in messages.
- "growth", "ai" and "competitor_gap" findings are opportunities; phrase them as ideas, not as accusations.

FIT & SCORE (0-100):
- Weigh: size and seriousness of opportunities, buying-intent signals, ability to pay (business type, size
  cues), and match with OUR services.
- fit "skip" (with skip_reason) when: they are a software/web/IT agency or other competitor of ours, a hobby or
  very tiny site, an already excellent site with no meaningful gaps, or no clear business.
- fit "manual" when the site could not be read properly. "strong" >= 70, "possible" 40-69.
- For skip/manual you may leave messages as empty strings.

BUYING INTENT: look for hiring (tech or ops roles), recent funding, expansion/new location/launch news (use
WebSearch for recent news about the company), "coming soon" features, growth language with an outdated site,
visible manual processes (PDF forms, "email us to book", phone-only ordering), old platform versions. Each signal
needs evidence and a source_url; set sop_type when it matches hiring_tech_roles, funding_news, expansion_launch or
talks_tech_growth, else "none".

COMPETITORS: use WebSearch to find 2-3 direct competitors (same industry, ideally same region). Use WebFetch to
read their homepages. Record only clearly visible features (online booking, live chat, customer login/portal,
pricing page, order tracking, mobile app links, online quote) and give the source_url where you saw each.
In report.comparison, include a row ONLY when the prospect's side is backed by a Fact id (prospect_fact_id).

ANGLE BY POSITION: founder/CEO/owner -> revenue, growth, competitors. CTO/IT/tech -> tech debt, stack, scaling.
Marketing -> leads, conversion, SEO. Operations -> automation, time saved.

MESSAGES (address the contact by first name; if no name is given, open with "Hi there". Never leave
placeholders such as [First name], {{name}} or <Name>. Do NOT add a signature, we append it):
- linkedin_note: connection request, MAX 300 characters. One specific observation + offer the free review, ends
  with a question. No pitch.
- linkedin_after_accept: thanks for connecting + offer the review. Max 90 words.
- email_subject: max 7 words, names their company or site, no clickbait, no ALL CAPS.
- email_body: max 90 words. One credible finding (name the page), hint at a couple more, offer the free 1-page
  review, end with "Want me to send it?" or similar question.
- whatsapp: max 60 words, conversational, same hook, ends with a question.
- report_delivery: sent after they say yes: shares the review, suggests a 15-minute call, includes exactly
  "{BUDGET_LINE}"
- followup_1: a NEW held-back finding (followup_finding_ids[0]), re-offers the review. Max 90 words.
- followup_2: competitor or AI-opportunity angle. Max 90 words.
- followup_3: short, polite close-the-loop. Max 60 words.
Set opener_finding_id to the single finding the openers use, followup_finding_ids to the ones follow-ups use.

REPORT (the free 1-page review the prospect receives): headline (no numbers about money/time), whats_working
(max 2 genuine compliments, max 20 words each), exactly 3 opportunities (title max 8 words + why_it_matters max
35 words, for their business; no "how", no price), each with finding_id naming the finding it is based on (the
same evidence rules apply), comparison rows as described above. It must fit on one page.

CALL PREP (private, for our salesperson): angle for this person, 5 questions to ask, 3 likely objections with
responses, notes. Our_solution and complexity (S/M/L) in findings are private too.

Also fill timezone_hint from the address/phone/contact page if you can (IANA zone), company details, and
services_to_pitch (from OUR services only).
"""


def _company_block(settings: dict) -> str:
    c = settings.get("company") or {}
    lines = [f"Name: {c.get('name', '')} ({c.get('short_name', '')}) - {c.get('website', '')}",
             f"Pitch: {c.get('pitch', '')}",
             "Services: " + "; ".join(c.get("services") or []),
             "Industries we have served: " + ", ".join(c.get("industries") or []),
             "Markets: " + ", ".join(c.get("markets") or []),
             "Case studies you may name (only these): " + ", ".join(c.get("case_studies") or [])]
    return "\n".join(lines)


def _contact_block(contact: dict) -> str:
    fields = [("Name", "full_name"), ("Position", "job_title"), ("Company", "company_name"), ("Website", "website"),
              ("Email", "email"), ("Phone", "phone"), ("LinkedIn", "linkedin_url"), ("Country", "country"),
              ("Industry", "industry"), ("Employees", "employee_count"), ("Notes", "notes")]
    lines = [f"{label}: {contact.get(key)}" for label, key in fields if contact.get(key)]
    return "\n".join(lines) or "(no contact details; write to the business owner)"


def _fact_line(f: dict) -> str:
    conf = " [low confidence]" if f.get("confidence") == "low" else ""
    return f"{f.get('id')} [{f.get('kind')}]{conf} {f.get('label')}: {f.get('evidence', '')}"


def build_analysis_prompt(settings: dict, contact: dict, crawl_summary: dict, facts: list[dict], today: str) -> str:
    pages = []
    for p in (crawl_summary or {}).get("pages", []):
        heads = " | ".join(p.get("headings") or [])
        pages.append(f"--- PAGE {p.get('url')} — {p.get('title', '')}\n"
                     + (f"Headings: {heads}\n" if heads else "") + (p.get("text") or ""))
    return "\n\n".join([
        PLAYBOOK,
        "=== OUR COMPANY ===\n" + _company_block(settings),
        "=== THE CONTACT ===\n" + _contact_block(contact),
        "=== VERIFIED FACTS ABOUT THEIR WEBSITE (from our own checks) ===\n" + "\n".join(_fact_line(f) for f in facts),
        "=== THEIR WEBSITE PAGES (text) ===\n" + ("\n\n".join(pages) or "(no page text)"),
        f"Today's date: {today}. Now research and return the JSON answer.",
    ])


def build_messages_prompt(settings: dict, contact: dict, analysis: dict) -> str:
    keep = {k: analysis.get(k) for k in ("summary", "company", "findings", "buying_intent", "competitors",
                                         "opener_finding_id", "followup_finding_ids", "services_to_pitch")}
    return "\n\n".join([
        PLAYBOOK,
        "=== OUR COMPANY ===\n" + _company_block(settings),
        "=== THE CONTACT (write to this person) ===\n" + _contact_block(contact),
        "=== RESEARCH ALREADY DONE FOR THIS COMPANY (use it; do not research again) ===\n"
        + json.dumps(keep, ensure_ascii=False, indent=1),
        "Write the messages for this contact, adapting the angle to their position. Use only the findings above; "
        "set opener_finding_id and followup_finding_ids to the finding ids your messages use.",
    ])

# -- validation -------------------------------------------------------------------------------

def _text_problems(where: str, text: str) -> list[str]:
    return [f"{where} {why}: \"{m.group(0)}\"" for rx, why in BANNED for m in [rx.search(text or "")] if m]


def validate_messages(messages: dict) -> list[str]:
    problems: list[str] = []
    messages = messages or {}
    for key in MESSAGE_KEYS:
        text = (messages.get(key) or "").strip()
        if not text:
            problems.append(f"{key} is empty")
            continue
        problems += _text_problems(key, text)
        if key in LIMITS_CHARS and len(text) > LIMITS_CHARS[key]:
            problems.append(f"{key} is {len(text)} characters; the limit is {LIMITS_CHARS[key]}")
        if key in LIMITS_WORDS and len(text.split()) > LIMITS_WORDS[key]:
            problems.append(f"{key} is {len(text.split())} words; the limit is {LIMITS_WORDS[key]}")
        if key in OPENER_KEYS and "?" not in text:
            problems.append(f"{key} must end with a question offering the free review")
    rd = (messages.get("report_delivery") or "").lower()
    if rd and not ("your budget" in rd and ("call" in rd or "meeting" in rd)):
        problems.append(f"report_delivery must include: {BUDGET_LINE}")
    return problems


def _finding_problems(analysis: dict, facts_by_id: dict) -> list[str]:
    problems: list[str] = []
    findings = {f.get("id"): f for f in analysis.get("findings") or []}
    report_ids = [o.get("finding_id") for o in (analysis.get("report") or {}).get("opportunities") or []]
    used = [analysis.get("opener_finding_id")] + list(analysis.get("followup_finding_ids") or []) + report_ids
    if not analysis.get("opener_finding_id") or analysis.get("opener_finding_id") not in findings:
        problems.append("opener_finding_id must name one of the findings")
    for fid in list(analysis.get("followup_finding_ids") or []) + report_ids:
        if fid not in findings:
            problems.append(f"follow-up or report opportunity cites unknown finding {fid!r}")
    for fid in dict.fromkeys(used):
        f = findings.get(fid)
        if not f or f.get("type") not in ("broken", "missing"):
            continue
        ids = f.get("fact_ids") or []
        if not ids:
            problems.append(f"finding {fid} claims something {f.get('type')} but cites no Fact")
        for i in ids:
            fact = facts_by_id.get(i)
            if not fact:
                problems.append(f"finding {fid} cites unknown Fact {i}")
            elif fact.get("kind") not in ("issue", "missing"):
                problems.append(f"finding {fid} cites Fact {i}, which is not a problem ({fact.get('kind')})")
            elif fact.get("confidence") == "low":
                problems.append(f"finding {fid} relies on low-confidence Fact {i}; don't use it in messages")
    return problems


def validate(analysis: dict, facts: list[dict]) -> list[str]:
    if not isinstance(analysis, dict):
        return ["answer is not a JSON object"]
    if analysis.get("fit") in ("skip", "manual"):
        return []
    problems = validate_messages(analysis.get("messages") or {})
    problems += _finding_problems(analysis, {f.get("id"): f for f in facts})
    report = analysis.get("report") or {}
    texts = [report.get("headline", "")] + list(report.get("whats_working") or [])
    texts += [o.get("title", "") + " " + o.get("why_it_matters", "") for o in report.get("opportunities") or []]
    for t in texts:
        problems += _text_problems("report", t)
    return problems


def clean_analysis(analysis: dict, facts: list[dict]) -> dict:
    a = copy.deepcopy(analysis or {})
    by_id = {f.get("id"): f for f in facts}
    try:
        a["score"] = max(0, min(100, int(a.get("score") or 0)))
    except (TypeError, ValueError):
        a["score"] = 0
    report = a.setdefault("report", {})
    rows = []
    for row in report.get("comparison") or []:
        fact = by_id.get(row.get("prospect_fact_id"))
        # only a confident "has it" (ok) or "doesn't have it" (missing) can go in the prospect's column
        if not fact or fact.get("kind") not in ("ok", "missing") or fact.get("confidence") == "low":
            continue
        row = dict(row, prospect_has=fact.get("kind") == "ok")
        rows.append(row)
    report["comparison"] = rows
    for comp in a.get("competitors") or []:  # every competitor claim needs the page it was seen on
        comp["features"] = [f for f in comp.get("features") or []
                            if str(f.get("source_url") or "").lower().startswith(("http://", "https://"))]
    for key in ("findings", "competitors", "services_to_pitch", "followup_finding_ids"):
        a.setdefault(key, [])
    a.setdefault("messages", {k: "" for k in MESSAGE_KEYS})
    return a


def _signature(settings: dict) -> str:
    sender = settings.get("sender") or {}
    short = (settings.get("company") or {}).get("short_name") or (settings.get("company") or {}).get("name", "")
    if (sender.get("signature") or "").strip():
        return sender["signature"].strip()
    if sender.get("name"):
        sig = f"{sender['name']}\n" + (f"{sender['title']}, {short}" if sender.get("title") else short)
    else:
        sig = f"Best regards,\nThe {short} team"
    if sender.get("booking_link"):
        sig += f"\n{sender['booking_link']}"
    return sig


def finalize_messages(messages: dict, settings: dict) -> dict:
    out = dict(messages or {})
    sig = _signature(settings)
    for key in SIGNED_KEYS:
        text = (out.get(key) or "").rstrip()
        if text and not text.endswith(sig):
            out[key] = f"{text}\n\n{sig}"
    return out

# -- orchestration (LLM injected) ---------------------------------------------------------------

def _feedback(problems: list[str]) -> str:
    return ("\n\nYour previous answer broke these rules:\n- " + "\n- ".join(problems[:20])
            + "\nFix them and answer again with the complete JSON.")


def analyze_company(settings: dict, contact: dict, crawl_summary: dict, facts: list[dict],
                    run: Callable, today: str | None = None) -> tuple[dict, list[str]]:
    from datetime import date
    prompt = build_analysis_prompt(settings, contact, crawl_summary, facts, today or date.today().isoformat())
    kwargs = {"schema": ANALYSIS_SCHEMA, "tools": ["WebSearch", "WebFetch"],
              "model": settings.get("model") or None, "timeout": 900}
    analysis = run(prompt, **kwargs).data or {}
    problems = validate(analysis, facts)
    if problems:
        analysis = run(prompt + _feedback(problems), **kwargs).data or {}
        problems = validate(analysis, facts)
    return clean_analysis(analysis, facts), problems


def write_messages(settings: dict, contact: dict, analysis: dict, facts: list[dict],
                   run: Callable) -> tuple[dict, list[str]]:
    prompt = build_messages_prompt(settings, contact, analysis)
    kwargs = {"schema": MESSAGES_SCHEMA, "tools": [], "model": settings.get("model") or None, "timeout": 300}
    facts_by_id = {f.get("id"): f for f in facts}

    def check(data: dict) -> tuple[dict, list[str]]:
        messages = data.get("messages") or {}
        refs = {"findings": analysis.get("findings") or [], "opener_finding_id": data.get("opener_finding_id"),
                "followup_finding_ids": data.get("followup_finding_ids") or []}
        return messages, validate_messages(messages) + _finding_problems(refs, facts_by_id)

    messages, problems = check(run(prompt, **kwargs).data or {})
    if problems:
        messages, problems = check(run(prompt + _feedback(problems), **kwargs).data or {})
    return finalize_messages(messages, settings), problems
