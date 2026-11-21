"""Quick Prospect: a fast, streamed qualification of one website for a LinkedIn connection request.

The answer is markdown with fixed headings so the UI can show the verdict first while text streams in.
Pure: the prompt, verdict parsing and rule checks. Streaming and storage live in adapters/app.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from .analyze import BANNED, _company_block

NOTE_LIMIT = 300
MAX_LOOKUPS = 6  # web searches + page reads; keeps a quick check to about a minute
_PHONE = re.compile(r"\+\d[\d\s().-]{7,}\d|\b0\d{2,4}[\s.-]?\d{3,4}[\s.-]?\d{3,4}\b|\(\d{2,5}\)\s?\d{3,4}[\s.-]?\d{3,4}")

# Plain string (not an f-string) so the markdown examples can contain braces safely.
QUICK_PLAYBOOK = """You are a senior B2B sales researcher for the software company described below. Someone on our team
is deciding whether to send a LinkedIn Sales Navigator connection request to people at ONE company. Give a fast,
honest qualification, like an experienced colleague would, with the most important information first.

ORDER OF WORK (important, the reader watches your answer appear live):
1. BEFORE using any tool, immediately write the "## First look" section: 2-3 lines based only on the website
   snapshot below (what they do, who they serve, first impression of fit).
2. Then research with WebSearch and WebFetch, using at most __MAX_LOOKUPS__ web searches and page reads in total
   (this is a QUICK check; speed matters more than completeness). Prioritise, in order: recent news/partnerships/
   funding/launches; leadership names and titles; company size and LinkedIn page; hiring of engineering, QA, AI or
   integration roles. Combine topics into one search where you can. Stop researching once you can decide.
3. Then write the final answer in EXACTLY this structure:

## Verdict
**Worth connecting** · 7.5/10      (or **Maybe** · 5/10, or **Skip** · 2/10)
One or two sentences: should we connect, and how to position ourselves (e.g. delivery partner vs vendor).
- **Buying now:** X/10 (one short reason: is there public evidence they need outside development help now?)
- **Partnership potential:** X/10 (one short reason: could they buy or subcontract our services?)
- **Contact first:** Name, Title; Name, Title (only people you found in a source; add "verify in Sales Navigator")
- **Connection note:**
> A ready LinkedIn connection note, max 300 characters, friendly, specific to them, no pitch, no price.

## Company
Location · industry · size · founded · links (website, LinkedIn page if found)

## What they do
A short markdown table: | Area | What they offer |

## Buying signals
The top 3 signals, strongest first. Each: **Signal name**: what happened, why it matters for us, and a
[source](url) link. Say clearly when something shows expansion but NOT that they are looking for a vendor.

## What to pitch
A short markdown table: | Our capability | Their likely need |, plus one positioning sentence.

## Scores
A markdown table with: Industry fit, Technical service fit, Company size, Partnership potential, Confirmed
outsourcing intent, Potential recurring work, Final decision (each X/10). Add one line saying these are
judgement scores, not measured intent data.

## Check next in Sales Navigator
Three numbered, concrete checks that would confirm or rule out real buying intent.

RULES:
- Be concise; this is a quick check, not a report. Do not invent facts, people or numbers. If you could not
  confirm something, say so.
- NEVER include phone numbers. Never mention prices, rates or delivery timelines in the connection note.
- Never say or imply the text was written by AI. Write in English.
- A software or web agency that sells the same services as us is usually **Skip**, unless there is a clear
  partnership angle (then **Maybe**, and say why).
- Judge fit against OUR services and markets below.
"""


def build_quick_prompt(settings: dict, url: str, snapshot: dict, today: str) -> str:
    snap = snapshot or {}
    pages = "\n".join(f"- {p.get('url')}: {p.get('title', '')}" for p in snap.get("pages") or [])
    facts = "\n".join(f"- {f}" for f in snap.get("facts") or [])
    text = (snap.get("text") or "")[:6000]
    lines = [
        QUICK_PLAYBOOK.replace("__MAX_LOOKUPS__", str(MAX_LOOKUPS)),
        "=== OUR COMPANY ===\n" + _company_block(settings),
        f"=== THE WEBSITE TO QUALIFY ===\n{url}",
        "=== WEBSITE SNAPSHOT (from our own quick fetch) ===\n"
        + (f"Title: {snap.get('title', '')}\nDescription: {snap.get('description', '')}\n" if snap.get("reachable")
           else f"The website could not be loaded ({snap.get('error') or 'no response'}); rely on web research.\n")
        + (f"Pages read:\n{pages}\n" if pages else "")
        + (f"Observations from our checks:\n{facts}\n" if facts else "")
        + (f"Page text (excerpt):\n{text}" if text else ""),
        f"Today's date: {today}. Start with '## First look' now.",
    ]
    return "\n\n".join(lines)


def _section(md: str, heading: str) -> str:
    m = re.search(rf"^##\s*{re.escape(heading)}\s*$(.*?)(?=^##\s|\Z)", md or "", re.M | re.S | re.I)
    return m.group(1) if m else ""


def parse_verdict(md: str) -> dict:
    sec = _section(md, "Verdict")
    first = next((ln.strip() for ln in sec.splitlines() if ln.strip()), "")
    lm = re.search(r"\*\*\s*(worth connecting|maybe|skip)\s*\*\*", first, re.I)
    sm = re.search(r"(\d+(?:\.\d+)?)\s*/\s*10", first)
    label = {"worth connecting": "worth", "maybe": "maybe", "skip": "skip"}[lm.group(1).lower()] if lm else "unknown"
    return {"label": label, "score": float(sm.group(1)) if sm else None,
            "text": re.sub(r"\*\*", "", first).strip() if lm else ""}


def connection_note(md: str) -> str:
    sec = _section(md, "Verdict")
    quoted = [ln.strip()[1:].strip() for ln in sec.splitlines() if ln.strip().startswith(">")]
    return " ".join(q for q in quoted if q).strip()


def validate_quick(md: str) -> list[str]:
    problems: list[str] = []
    note = connection_note(md)
    if not note:
        problems.append("No connection note was written.")
    else:
        if len(note) > NOTE_LIMIT:
            problems.append(f"The connection note is {len(note)} characters; LinkedIn allows {NOTE_LIMIT}.")
        for rx, why in BANNED:
            m = rx.search(note)
            if m:
                problems.append(f"The connection note {why}: \"{m.group(0)}\"")
    m = _PHONE.search(md or "")
    if m:
        problems.append(f"The answer includes a phone number (\"{m.group(0)}\"); we never share phone numbers.")
    return problems


def step_label(tool_name: str, tool_input: dict) -> str:
    tool_input = tool_input or {}
    if tool_name == "WebSearch":
        return f"Searching: {tool_input.get('query', '').strip()}"
    if tool_name == "WebFetch":
        u = urlparse(str(tool_input.get("url", "")))
        host = u.netloc[4:] if u.netloc.startswith("www.") else u.netloc
        return f"Reading {host}{u.path if u.path not in ('', '/') else ''}"
    return f"Working ({tool_name})"
