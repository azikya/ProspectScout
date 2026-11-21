import json
from pathlib import Path

import pytest

from prospectscout import config
from prospectscout.adapters import db
from prospectscout.adapters.claude_runner import LLMError
from prospectscout.app.worker import Worker
from prospectscout.core import analyze
from prospectscout.core.site import CrawlResult, Page, extract

GOOD = json.loads((Path(__file__).parent / "fixtures" / "analysis_good.json").read_text(encoding="utf-8"))
HOME = '<html lang="en"><title>Acme</title><p>Freight. Call +1 512 555 0100</p><a href="/quote">Quote</a></html>'


def fake_crawl(reachable=True):
    def crawl_fn(website, max_pages=8, max_link_checks=40, progress=None, **_):
        if progress:
            progress("Reading website…")
        if not reachable:
            return CrawlResult(start_url=website, error="DNS lookup failed")
        page = Page(url=website, final_url=website, status=200, elapsed_ms=200, content_type="text/html",
                    html=HOME, **extract(website, HOME))
        return CrawlResult(start_url=website, final_url=website, reachable=True, pages=[page], https=True,
                           http_redirects_to_https=True, ssl_days_left=200,
                           link_status={website: 200, website.rstrip("/") + "/quote": 404})
    return crawl_fn


class Recorder:
    def __init__(self, analysis=None, problems=None, error=None):
        self.analysis = analysis if analysis is not None else GOOD
        self.problems = problems or []
        self.error = error
        self.analyze_calls = 0
        self.message_calls = 0

    def analyze_fn(self, settings, contact, crawl_summary, facts):
        self.analyze_calls += 1
        if self.error:
            raise self.error
        return json.loads(json.dumps(self.analysis)), list(self.problems)

    def messages_fn(self, settings, contact, analysis, facts):
        self.message_calls += 1
        return analyze.finalize_messages({k: f"msg for {contact.get('full_name')}?" for k in analyze.MESSAGE_KEYS},
                                         settings), []


@pytest.fixture
def setup(tmp_path):
    path = str(tmp_path / "w.db")
    conn = db.connect(path)

    def make(rec=None, reachable=True):
        rec = rec or Recorder()
        w = Worker(path, settings_loader=config.load, crawl_fn=fake_crawl(reachable),
                   analyze_fn=rec.analyze_fn, messages_fn=rec.messages_fn)
        return w, rec
    yield conn, path, make
    conn.close()


def add_two(conn):
    a = db.add_contact(conn, {"full_name": "Sarah Lee", "job_title": "COO", "company_domain": "acme.com",
                              "website": "https://acme.com", "phone": "+1 512 555 0100"})
    db.add_contact(conn, {"full_name": "Tom Ray", "job_title": "CTO", "company_domain": "acme.com"})
    return a["company_id"]


def test_happy_path_two_contacts(setup):
    conn, path, make = setup
    cid = add_two(conn)
    w, rec = make()
    assert w.process_one() is True
    co = db.get_company(conn, cid)
    assert co["status"] == "done" and co["fit"] == "strong" and co["score"] == 82 and co["facts_json"]
    assert co["tz"] == "America/Chicago" and co["tz_source"] == "phone" and co["stage"] is None
    contacts = db.contacts_for_company(conn, cid)
    assert contacts[0]["messages_status"] == "done" and contacts[0]["messages_json"]["email_body"].endswith("The Azikya team")
    assert rec.analyze_calls == 1 and rec.message_calls == 0  # second contact handled as its own job
    assert w.process_one() is True and rec.message_calls == 1
    assert db.contacts_for_company(conn, cid)[1]["messages_json"]["linkedin_note"] == "msg for Tom Ray?"
    assert w.process_one() is False


def test_new_contact_for_researched_company_gets_messages(setup):
    conn, path, make = setup
    cid = db.add_contact(conn, {"full_name": "Sarah Lee", "company_domain": "acme.com"})["company_id"]
    w, rec = make()
    w.process_one()
    late = db.add_contact(conn, {"full_name": "Late Larry", "company_domain": "acme.com"})
    assert not late["company_new"]
    assert w.process_one() is True
    assert db.get_contact(conn, late["contact_id"])["messages_status"] == "done" and rec.analyze_calls == 1
    assert db.get_company(conn, cid)["status"] == "done"


def test_unreachable_no_llm(setup):
    conn, path, make = setup
    cid = add_two(conn)
    w, rec = make(reachable=False)
    w.process_one()
    co = db.get_company(conn, cid)
    assert co["status"] == "unreachable" and co["fit"] == "manual" and rec.analyze_calls == 0
    assert co["facts_json"][0]["label"] == "Website unreachable"
    assert all(c["messages_status"] == "skipped" for c in db.contacts_for_company(conn, cid))


def test_usage_limit_pauses_and_requeues(setup):
    conn, path, make = setup
    cid = add_two(conn)
    w, rec = make(Recorder(error=LLMError("usage_limit", "Claude AI usage limit reached")))
    w.process_one()
    st = w.status()
    assert db.get_company(conn, cid)["status"] == "queued" and st["paused"] and "Max plan" in st["pause_reason"]


def test_llm_error_marks_failed(setup):
    conn, path, make = setup
    cid = add_two(conn)
    w, rec = make(Recorder(error=LLMError("error", "boom")))
    w.process_one()
    co = db.get_company(conn, cid)
    assert co["status"] == "failed" and "boom" in co["error"]


def test_unexpected_exception_marks_failed(setup):
    conn, path, make = setup
    cid = add_two(conn)
    w, rec = make(Recorder(error=RuntimeError("kaput")))
    w.process_one()
    assert db.get_company(conn, cid)["status"] == "failed"


def test_skip_fit_no_messages(setup):
    conn, path, make = setup
    cid = add_two(conn)
    w, rec = make(Recorder(analysis=dict(GOOD, fit="skip", skip_reason="They are a web agency")))
    w.process_one()
    assert db.get_company(conn, cid)["status"] == "skipped"
    assert all(c["messages_status"] == "skipped" for c in db.contacts_for_company(conn, cid))
    assert w.process_one() is False and rec.message_calls == 0


def test_problems_mark_needs_review(setup):
    conn, path, make = setup
    cid = add_two(conn)
    w, rec = make(Recorder(problems=["email_body mentions a price"]))
    w.process_one()
    co = db.get_company(conn, cid)
    assert co["status"] == "needs_review" and co["analysis_json"]["_problems"] == ["email_body mentions a price"]
    assert db.contacts_for_company(conn, cid)[0]["messages_status"] == "needs_review"


def test_restart_requeues_in_progress(setup):
    conn, path, make = setup
    cid = add_two(conn)
    db.next_queued_company(conn)
    make()
    assert db.get_company(conn, cid)["status"] == "queued"


def test_pause_resume_and_no_work(setup):
    conn, path, make = setup
    w, _ = make()
    assert w.process_one() is False
    w.pause("manual")
    assert w.status()["paused"] and w.status()["pause_reason"] == "manual"
    w.resume()
    assert not w.status()["paused"]


def test_thread_loop_processes_queue(setup):
    import time
    conn, path, make = setup
    cid = add_two(conn)
    w, rec = make()
    w.start()
    try:
        deadline = time.time() + 10
        while time.time() < deadline and db.get_company(conn, cid)["status"] != "done":
            time.sleep(0.05)
    finally:
        w.stop()
    assert db.get_company(conn, cid)["status"] == "done"


def test_non_html_homepage_is_marked_without_llm(setup):
    conn, path, make = setup
    cid = add_two(conn)
    rec = Recorder()

    def crawl_fn(website, **_):
        return CrawlResult(start_url=website, final_url=website + "/brochure.pdf", reachable=True,
                           skipped=[{"url": website + "/brochure.pdf", "content_type": "application/pdf"}])
    w = Worker(path, settings_loader=config.load, crawl_fn=crawl_fn, analyze_fn=rec.analyze_fn, messages_fn=rec.messages_fn)
    w.process_one()
    co = db.get_company(conn, cid)
    assert co["status"] == "unreachable" and co["fit"] == "manual" and rec.analyze_calls == 0
    assert "application/pdf" in co["error"]


def test_first_contact_messages_written_before_company_is_done(setup, monkeypatch):
    conn, path, make = setup
    cid = add_two(conn)
    w, rec = make()
    real = db.set_company
    seen = []

    def spy(c, company_id, **fields):
        if fields.get("status") in ("done", "needs_review"):
            first = db.contacts_for_company(conn, company_id)[0]
            seen.append(first["messages_status"])
        return real(c, company_id, **fields)
    monkeypatch.setattr(db, "set_company", spy)
    w.process_one()
    assert seen == ["done"]


def test_rerun_keeps_messages_of_contacted_prospects(setup):
    conn, path, make = setup
    cid = add_two(conn)
    w, rec = make()
    w.process_one()
    w.process_one()  # second contact's messages
    first = db.contacts_for_company(conn, cid)[0]
    db.set_contact(conn, first["id"], outreach_status="contacted", messages_json={"email_body": "what I actually sent"})
    db.requeue_company(conn, cid)
    while w.process_one():
        pass
    assert db.get_contact(conn, first["id"])["messages_json"] == {"email_body": "what I actually sent"}
