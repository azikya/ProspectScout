import threading
import time

import pytest

from prospectscout import config
from prospectscout.adapters import db
from prospectscout.adapters.claude_runner import LLMError
from prospectscout.app.quick_runner import QuickRunner
from prospectscout.core.site import CrawlResult, Page, extract

HOME = '<html lang="en"><title>HealthWare Systems</title><meta name="description" content="AI automation for healthcare"><p>We automate documents.</p></html>'
ANSWER = ("## First look\nHealthcare automation firm.\n\n## Verdict\n**Worth connecting** · 7.5/10\nPartner angle.\n"
          "- **Connection note:**\n> Hi, I came across HealthWare Systems and liked your automation work. Glad to connect.\n")


def fake_crawl(seen, reachable=True):
    def crawl_fn(website, **kw):
        seen.append(kw)
        if not reachable:
            return CrawlResult(start_url=website, error="DNS lookup failed")
        p = Page(url=website, final_url=website, status=200, content_type="text/html", html=HOME, **extract(website, HOME))
        return CrawlResult(start_url=website, final_url=website, reachable=True, pages=[p], https=True)
    return crawl_fn


@pytest.fixture
def path(tmp_path):
    p = str(tmp_path / "q.db")
    db.connect(p).close()
    return p


def test_happy_path_streams_and_stores(path):
    seen, prompts, gate = [], [], threading.Event()

    def stream_fn(prompt, tools, on_text, on_step, model=None, timeout=600):
        prompts.append(prompt)
        on_text(ANSWER[:30])
        on_step("WebSearch", {"query": "HealthWare news"})
        gate.wait(5)
        on_text(ANSWER[30:])
        return ANSWER
    r = QuickRunner(path, settings_loader=config.load, crawl_fn=fake_crawl(seen), stream_fn=stream_fn)
    qid = r.start("www.healthwaresystems.com/contact")
    deadline = time.time() + 5
    while time.time() < deadline and len((r.live(qid) or {}).get("steps") or []) < 2:
        time.sleep(0.02)
    live = r.live(qid)
    assert live["status"] == "running" and live["text"] == ANSWER[:30]
    assert live["steps"] == ["Reading the website", "Searching: HealthWare news"]
    assert live["snapshot"]["title"] == "HealthWare Systems"
    gate.set()
    r.wait(qid, 5)
    conn = db.connect(path)
    got = db.get_quick(conn, qid)
    assert got["status"] == "done" and got["markdown"] == ANSWER and got["verdict_json"]["label"] == "worth"
    assert got["url"] == "https://www.healthwaresystems.com" and got["domain"] == "healthwaresystems.com"
    assert seen[0]["max_pages"] == 2 and seen[0]["max_link_checks"] == 0 and seen[0]["deep"] is False
    assert "HealthWare Systems" in prompts[0] and "AI automation for healthcare" in prompts[0]


def test_usage_limit_fails_with_plain_message(path):
    def stream_fn(*a, **k):
        raise LLMError("usage_limit", "Claude AI usage limit reached")
    r = QuickRunner(path, crawl_fn=fake_crawl([]), stream_fn=stream_fn)
    qid = r.start("https://acme.com")
    r.wait(qid, 5)
    got = db.get_quick(db.connect(path), qid)
    assert got["status"] == "failed" and "Claude plan" in got["error"]


def test_unreachable_site_still_asks_claude(path):
    prompts = []

    def stream_fn(prompt, tools, on_text, on_step, model=None, timeout=600):
        prompts.append(prompt)
        return ANSWER
    r = QuickRunner(path, crawl_fn=fake_crawl([], reachable=False), stream_fn=stream_fn)
    qid = r.start("https://acme.com")
    r.wait(qid, 5)
    assert "could not be loaded" in prompts[0] and db.get_quick(db.connect(path), qid)["status"] == "done"


def test_bad_url_rejected(path):
    r = QuickRunner(path, crawl_fn=fake_crawl([]), stream_fn=lambda *a, **k: ANSWER)
    with pytest.raises(ValueError):
        r.start("javascript:alert(1)")


def test_at_most_two_run_at_once(path):
    started, gate = [], threading.Event()

    def stream_fn(prompt, tools, on_text, on_step, model=None, timeout=600):
        started.append(1)
        gate.wait(5)
        return ANSWER
    r = QuickRunner(path, crawl_fn=fake_crawl([]), stream_fn=stream_fn, max_parallel=2)
    ids = [r.start(f"https://site{i}.com") for i in range(3)]
    time.sleep(0.5)
    assert len(started) == 2 and r.live(ids[2])["status"] == "queued"
    gate.set()
    for i in ids:
        r.wait(i, 5)
    assert len(started) == 3


def test_restart_marks_interrupted_checks_failed(path):
    conn = db.connect(path)
    qid = db.create_quick(conn, "https://acme.com", "acme.com")
    QuickRunner(path, crawl_fn=fake_crawl([]), stream_fn=lambda *a, **k: ANSWER)
    assert db.get_quick(conn, qid)["status"] == "failed"
