"""Company SOP guide panel (ported from ProspectIQ's ICP + signal weights).

Information only: it shows how a prospect stacks up against the sales SOP (target
industry/country/size, weighted buying signals → Cold/Warm/HOT) and never changes the
fit decision or skips anyone.
"""

from __future__ import annotations

import re

EUROPE = frozenset({
    "austria", "belgium", "bulgaria", "croatia", "cyprus", "czech republic", "czechia", "denmark", "estonia",
    "finland", "france", "germany", "greece", "hungary", "ireland", "italy", "latvia", "lithuania", "luxembourg",
    "malta", "netherlands", "poland", "portugal", "romania", "slovakia", "slovenia", "spain", "sweden",
    "uk", "united kingdom", "england", "scotland", "wales", "switzerland", "norway", "iceland",
})
COUNTRY_ALIASES = {
    "us": "usa", "united states": "usa", "united states of america": "usa", "america": "usa",
    "united kingdom": "uk", "england": "uk", "great britain": "uk", "britain": "uk",
    "united arab emirates": "uae", "ksa": "saudi arabia", "kingdom of saudi arabia": "saudi arabia",
}


def classify(score: int) -> str:
    return "cold" if score <= 2 else "warm" if score <= 5 else "hot"


def _norm_country(c: str) -> str:
    c = (c or "").strip().lower()
    return COUNTRY_ALIASES.get(c, c)


def _country_match(country: str, targets: list[str]) -> bool | None:
    c = _norm_country(country)
    if not c:
        return None
    norm_targets = {_norm_country(t) for t in targets}
    return c in norm_targets or ("europe" in norm_targets and (c in EUROPE or country.strip().lower() in EUROPE))


def _whole_word(needle: str, haystack: str) -> bool:
    return bool(needle) and re.search(r"(?<![a-z0-9])" + re.escape(needle) + r"(?![a-z0-9])", haystack) is not None


def _industry_match(industry: str, targets: list[str]) -> bool | None:
    i = (industry or "").strip().lower()
    if not i:
        return None
    return any(_whole_word(t.lower().strip(), i) or _whole_word(i, t.lower()) for t in targets)


def sop_guide(company: dict, contact: dict, settings: dict) -> dict:
    rules = settings.get("sop") or {}
    weights = rules.get("weights") or {}
    contact = contact or {}
    found: dict[str, str] = {}
    for f in company.get("facts_json") or []:
        if f.get("signal"):
            found.setdefault(f["signal"], f.get("evidence", ""))
    for s in ((company.get("analysis_json") or {}).get("buying_intent") or {}).get("signals") or []:
        t = s.get("sop_type")
        if t and t != "none":
            found.setdefault(t, s.get("evidence", ""))
    for t in contact.get("manual_signals_json") or []:
        found.setdefault(t, "Marked manually")
    signals = [{"type": t, "weight": int(weights[t]), "evidence": ev} for t, ev in found.items() if t in weights]
    score = sum(s["weight"] for s in signals)

    emp = contact.get("employee_count")
    try:
        size_match = None if emp in (None, "") else int(rules.get("size_min", 11)) <= int(emp) <= int(rules.get("size_max", 1000))
    except (TypeError, ValueError):
        size_match = None
    return {
        "industry_match": _industry_match(company.get("industry") or contact.get("industry") or "", rules.get("industries") or []),
        "country_match": _country_match(company.get("country") or contact.get("country") or "", rules.get("countries") or []),
        "size_match": size_match,
        "signals": signals,
        "score": score,
        "label": classify(score),
    }
