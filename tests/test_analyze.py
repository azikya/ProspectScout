import json
from pathlib import Path

import pytest

from prospectscout import config
from prospectscout.adapters.claude_runner import LLMResult
from prospectscout.core import analyze

FACTS = [{"id": "F1", "kind": "ok", "confidence": "high", "label": "Contact form present", "evidence": "https://acme.com/contact"},
         {"id": "F3", "kind": "issue", "confidence": "high", "label": "Broken link", "evidence": "https://acme.com/quote (404)"},
         {"id": "F7", "kind": "missing", "confidence": "low", "label": "No live chat seen", "evidence": "https://acme.com"}]


def good():
    return json.loads((Path(__file__).parent / "fixtures" / "analysis_good.json").read_text(encoding="utf-8"))


def with_msg(key, text):
    a = good()
    a["messages"][key] = text
    return a


def test_good_fixture_is_valid():
    assert analyze.validate(good(), FACTS) == []


@pytest.mark.parametrize("bad", ["We can do it for $500?", "Done in 2 weeks?", "About 40% cheaper than agencies?",
                                 "This message was generated for you?", "I hope this email finds you well. Want it?",
                                 "We offer a discount this month?", "Costs only 300 dollars?"])
def test_rejects_banned_content(bad):
    assert analyze.validate(with_msg("email_body", bad), FACTS)


def test_allows_ai_service_pitch_and_short_call():
    assert analyze.validate(with_msg("followup_2", "An AI chatbot could answer quote requests 24/7. Worth a look?"), FACTS) == []
    assert analyze.validate(with_msg("followup_1", "Open to a 15-minute call to walk through it?"), FACTS) == []


def test_rejects_long_linkedin_note():
    assert any("300" in p for p in analyze.validate(with_msg("linkedin_note", "x" * 300 + "?"), FACTS))


def test_rejects_long_email_and_whatsapp():
    assert analyze.validate(with_msg("email_body", "word " * 91 + "?"), FACTS)
    assert analyze.validate(with_msg("whatsapp", "word " * 61 + "?"), FACTS)


def test_rejects_long_subject():
    assert analyze.validate(with_msg("email_subject", "one two three four five six seven eight"), FACTS)


def test_openers_need_question():
    assert analyze.validate(with_msg("whatsapp", "Saw an issue on your quote page."), FACTS)


def test_report_delivery_needs_budget_line():
    assert analyze.validate(with_msg("report_delivery", "Here it is. Call?"), FACTS)


def test_empty_message_rejected():
    assert analyze.validate(with_msg("followup_3", ""), FACTS)


def test_rejects_uncited_broken_opener():
    a = good()
    a["findings"][0]["fact_ids"] = []
    assert analyze.validate(a, FACTS)


def test_rejects_opener_citing_unknown_fact():
    a = good()
    a["findings"][0]["fact_ids"] = ["F99"]
    assert analyze.validate(a, FACTS)


def test_rejects_broken_claim_citing_an_ok_fact():
    a = good()
    a["findings"][0]["fact_ids"] = ["F1"]
    assert analyze.validate(a, FACTS)


def test_blocks_low_confidence_missing_in_messages():
    a = good()
    a["findings"].append({"id": "O9", "type": "missing", "fact_ids": ["F7"], "title": "No chat", "evidence": "",
                          "business_impact": "", "our_solution": "", "complexity": "S", "priority": 3})
    a["followup_finding_ids"] = ["O9"]
    assert analyze.validate(a, FACTS)


def test_rejects_price_in_report():
    a = good()
    a["report"]["opportunities"][0]["why_it_matters"] = "Saves you $2,000 a year."
    assert analyze.validate(a, FACTS)


def test_skip_needs_no_messages():
    a = good()
    a["fit"] = "skip"
    a["messages"] = {k: "" for k in analyze.MESSAGE_KEYS}
    assert analyze.validate(a, FACTS) == []


def test_clean_drops_unverified_comparison_rows_and_trusts_facts():
    a = good()
    a["report"]["comparison"] = [
        {"feature": "Chat", "prospect_has": True, "prospect_fact_id": "F99", "competitors": []},
        {"feature": "Form", "prospect_has": False, "prospect_fact_id": "F1", "competitors": []}]
    rows = analyze.clean_analysis(a, FACTS)["report"]["comparison"]
    assert [r["feature"] for r in rows] == ["Form"] and rows[0]["prospect_has"] is True


def test_clean_clamps_score():
    a = good()
    a["score"] = 140
    assert analyze.clean_analysis(a, FACTS)["score"] == 100


def test_prompt_contents():
    p = analyze.build_analysis_prompt(config.load(), {"full_name": "Sarah Lee", "job_title": "COO"},
                                      {"pages": [{"url": "https://acme.com/", "title": "Acme", "text": "hello"}]},
                                      FACTS, "2026-11-21")
    for s in ("Azikya", "Sarah Lee", "COO", "F3", "Broken link", "Age Up Health", "plan it around your budget",
              "2026-11-21", "https://acme.com/"):
        assert s in p
    assert "[low confidence]" in p or "low" in p


def test_messages_prompt_contents():
    p = analyze.build_messages_prompt(config.load(), {"full_name": "Tom Ray", "job_title": "CTO"}, good())
    assert "Tom Ray" in p and "CTO" in p and "Broken 'Get a Quote' link" in p


def test_schema_requires_core_keys():
    req = set(analyze.ANALYSIS_SCHEMA["required"])
    assert {"fit", "score", "findings", "messages", "report", "call_prep", "opener_finding_id"} <= req
    assert set(analyze.MESSAGES_SCHEMA["properties"]["messages"]["required"]) == set(analyze.MESSAGE_KEYS)


def test_finalize_appends_signature_once():
    s = config.load()
    s["sender"].update(name="Alex", title="Business Development")
    m = analyze.finalize_messages(good()["messages"], s)
    assert m["email_body"].rstrip().endswith("Business Development, Azikya") and "Alex" not in m["linkedin_note"]
    assert analyze.finalize_messages(m, s)["email_body"] == m["email_body"]


def test_finalize_without_sender_uses_team_signoff():
    m = analyze.finalize_messages(good()["messages"], config.load())
    assert m["email_body"].rstrip().endswith("The Azikya team")


def _runner(answers):
    calls = []

    def run(prompt, schema=None, tools=None, model=None, timeout=300):
        calls.append({"prompt": prompt, "tools": tools, "schema": schema})
        return LLMResult(text="", data=answers[min(len(calls) - 1, len(answers) - 1)], cost_usd=None, duration_ms=None)
    return run, calls


def test_analyze_retries_once_with_feedback():
    run, calls = _runner([with_msg("email_body", "Only $99?"), good()])
    out, problems = analyze.analyze_company(config.load(), {"full_name": "S"}, {"pages": []}, FACTS, run=run)
    assert problems == [] and len(calls) == 2 and "$99" not in out["messages"]["email_body"]
    assert "broke these rules" in calls[1]["prompt"] and calls[0]["tools"] == ["WebSearch", "WebFetch"]


def test_analyze_gives_up_after_one_retry():
    run, calls = _runner([with_msg("email_body", "Only $99?")])
    out, problems = analyze.analyze_company(config.load(), {}, {"pages": []}, FACTS, run=run)
    assert problems and len(calls) == 2


def test_write_messages_uses_no_tools_and_finalizes():
    run, calls = _runner([{"messages": good()["messages"], "opener_finding_id": "O1", "followup_finding_ids": ["O2", "O3"]}])
    msgs, problems = analyze.write_messages(config.load(), {"full_name": "Tom Ray"}, good(), FACTS, run=run)
    assert problems == [] and calls[0]["tools"] == [] and msgs["email_body"].rstrip().endswith("The Azikya team")


# --- final-review fixes ---------------------------------------------------------------------

def test_write_messages_requires_valid_finding_ids():
    run, _ = _runner([{"messages": good()["messages"]}])
    _, problems = analyze.write_messages(config.load(), {"full_name": "Tom"}, good(), FACTS, run=run)
    assert any("opener_finding_id" in p for p in problems)
    run, _ = _runner([{"messages": good()["messages"], "opener_finding_id": "O9", "followup_finding_ids": []}])
    assert analyze.write_messages(config.load(), {"full_name": "Tom"}, good(), FACTS, run=run)[1]


def test_messages_schema_asks_for_finding_ids():
    assert {"opener_finding_id", "followup_finding_ids"} <= set(analyze.MESSAGES_SCHEMA["required"])


def test_clean_comparison_keeps_only_high_confidence_ok_or_missing():
    facts = [{"id": "F1", "kind": "ok", "confidence": "high"}, {"id": "F2", "kind": "info", "confidence": "high"},
             {"id": "F3", "kind": "missing", "confidence": "high"}, {"id": "F4", "kind": "missing", "confidence": "low"}]
    a = good()
    a["report"]["comparison"] = [{"feature": f"r{i}", "prospect_has": True, "prospect_fact_id": f"F{i}", "competitors": []}
                                 for i in range(1, 5)]
    rows = {r["feature"]: r["prospect_has"] for r in analyze.clean_analysis(a, facts)["report"]["comparison"]}
    assert rows == {"r1": True, "r3": False}


def test_report_opportunities_must_reference_findings():
    a = good()
    a["report"]["opportunities"][1]["finding_id"] = "O99"
    assert any("O99" in p or "opportunit" in p for p in analyze.validate(a, FACTS))


def test_report_opportunity_backed_by_uncited_broken_finding_is_rejected():
    a = good()
    a["findings"][0]["fact_ids"] = []
    a["opener_finding_id"] = "O2"
    a["followup_finding_ids"] = ["O3"]
    assert analyze.validate(a, FACTS)  # O1 still used by report opportunity 1


# --- policy: no prices/timelines/phone numbers; budget + timing talked through on a call ----

@pytest.mark.parametrize("bad", ["It would cost about AED 2,000?", "Roughly Rs. 50,000 for this?", "USD 500 covers it?",
                                 "That usually takes two weeks?", "We can finish it in a couple of weeks?",
                                 "Our audit tool flagged this, want the report?", "Call me on +971 50 123 4567?",
                                 "Reach me at 050 123 4567?"])
def test_rejects_more_banned_content(bad):
    assert analyze.validate(with_msg("email_body", bad), FACTS)


def test_generated_in_normal_business_sense_is_allowed():
    assert analyze.validate(with_msg("followup_2", "Your quote page could have generated more enquiries. Worth a look?"), FACTS) == []


def test_budget_line_offers_call_or_meeting():
    line = analyze.BUDGET_LINE.lower()
    assert "your budget" in line and "call" in line and "meeting" in line


def test_report_delivery_needs_budget_and_call_line():
    assert analyze.validate(with_msg("report_delivery", "Here is the review. Want a quick call?"), FACTS)
    ok = f"Here is the review. {analyze.BUDGET_LINE} Does Tuesday work?"
    assert analyze.validate(with_msg("report_delivery", ok), FACTS) == []


def test_prompt_forbids_phone_numbers():
    p = analyze.build_analysis_prompt(config.load(), {}, {"pages": []}, FACTS, "2026-11-21")
    assert "phone number" in p.lower()


def test_clean_drops_competitor_features_without_source():
    a = good()
    a["competitors"] = [{"name": "X", "url": "https://x.example", "features": [
        {"feature": "Online booking", "has": True, "source_url": ""},
        {"feature": "Live chat", "has": True, "source_url": "https://x.example/"}]}]
    feats = analyze.clean_analysis(a, FACTS)["competitors"][0]["features"]
    assert [f["feature"] for f in feats] == ["Live chat"]


def test_signature_uses_custom_signature_when_set():
    s = config.load()
    s["sender"].update(name="Alex", signature="Alex Morgan\nAzikya Software Solutions")
    m = analyze.finalize_messages(good()["messages"], s)
    assert m["email_body"].rstrip().endswith("Alex Morgan\nAzikya Software Solutions")


@pytest.mark.parametrize("bad", ["Hi [First name], quick one. Want it?", "Hi {name}, quick one. Want it?",
                                 "Hi <Name>, quick one. Want it?"])
def test_rejects_unfilled_placeholders(bad):
    assert any("placeholder" in p for p in analyze.validate(with_msg("whatsapp", bad), FACTS))


def test_prompt_says_hi_there_when_name_unknown():
    p = analyze.build_analysis_prompt(config.load(), {"job_title": "Clinic Manager"}, {"pages": []}, FACTS, "2026-11-21")
    assert "Hi there" in p
