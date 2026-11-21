from prospectscout import config
from prospectscout.core import quick

SAMPLE = """## First look
Healthcare technology firm focused on document automation.

## Verdict
**Worth connecting** · 7.5/10
Approach them as a delivery partner rather than a buyer of a complete solution.
- **Buying now:** 3/10 (no public vendor search)
- **Partnership potential:** 7.5/10 (they sell custom AI and integrations)
- **Contact first:** Doug Gruner, CIO (verify in Sales Navigator)
- **Connection note:**
> Hi Doug, I came across HealthWare Systems and found your work on healthcare automation and integrations interesting. We build custom software for healthcare teams and would be glad to connect.

## Buying signals
1. **New Creatio partnership**: implementation work ahead. They also raised $5M last year. [source](https://example.com/news)
"""


def test_prompt_contains_contract_and_rules():
    p = quick.build_quick_prompt(config.load(), "https://www.healthwaresystems.com/",
                                 {"title": "HealthWare Systems", "description": "AI automation", "pages": [],
                                  "facts": ["No pricing page"], "reachable": True}, "2026-11-21")
    for s in ("https://www.healthwaresystems.com/", "## First look", "## Verdict", "Sales Navigator", "Azikya",
              "phone number", "HealthWare Systems", "No pricing page", "2026-11-21", "300"):
        assert s in p, s


def test_parse_verdict_worth_maybe_skip_unknown():
    assert quick.parse_verdict(SAMPLE) == {"label": "worth", "score": 7.5, "text": "Worth connecting · 7.5/10"}
    assert quick.parse_verdict("## Verdict\n**Maybe** · 5/10\nwhy")["label"] == "maybe"
    assert quick.parse_verdict("## Verdict\n**Skip** · 2/10")["score"] == 2.0
    v = quick.parse_verdict("Some text without the heading")
    assert v["label"] == "unknown" and v["score"] is None


def test_connection_note_extracted():
    assert quick.connection_note(SAMPLE).startswith("Hi Doug, I came across HealthWare Systems")


def test_clean_sample_passes_even_with_company_funding_amounts():
    assert quick.validate_quick(SAMPLE) == []


def test_validate_flags_note_rules_and_phone_numbers():
    long_note = SAMPLE.replace("would be glad to connect.", "would be glad to connect. " + "x" * 250)
    assert any("300" in p for p in quick.validate_quick(long_note))
    assert quick.validate_quick(SAMPLE.replace("would be glad to connect.", "can start for AED 2,000."))
    assert quick.validate_quick(SAMPLE.replace("would be glad to connect.", "can deliver in 2 weeks."))
    assert quick.validate_quick(SAMPLE + "\nCall the CIO on +971 50 123 4567")
    assert any("connection note" in p for p in quick.validate_quick("## Verdict\n**Maybe** · 5/10\n"))


def test_step_labels():
    assert quick.step_label("WebSearch", {"query": "HealthWare news"}) == "Searching: HealthWare news"
    assert quick.step_label("WebFetch", {"url": "https://a.com/b"}) == "Reading a.com/b"
    assert quick.step_label("Other", {}) == "Working (Other)"


def test_prompt_caps_research_for_speed():
    p = quick.build_quick_prompt(config.load(), "https://a.com", {"reachable": True}, "2026-11-21")
    assert f"at most {quick.MAX_LOOKUPS} web" in p
