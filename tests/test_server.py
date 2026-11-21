import base64
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from prospectscout.adapters import db
from prospectscout.app import server

GOOD = json.loads((Path(__file__).parent / "fixtures" / "analysis_good.json").read_text(encoding="utf-8"))


@pytest.fixture
def srv(tmp_path):
    path = str(tmp_path / "s.db")
    httpd = server.make_server("127.0.0.1", 0, path, None)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    conn = db.connect(path)
    yield base, conn
    conn.close()
    httpd.shutdown()


def call(base, method, path, body=None, auth=None, headers=None, ctype="application/json"):
    if method == "POST" and body is None:
        body = {}
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method)
    if data is not None and ctype:
        req.add_header("Content-Type", ctype)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    if auth:
        req.add_header("Authorization", "Basic " + base64.b64encode(auth.encode()).decode())
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            raw, status, ctype = r.read(), r.status, r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        raw, status, ctype = e.read(), e.code, e.headers.get("Content-Type", "")
    text = raw.decode("utf-8", "replace")
    return status, (json.loads(text) if "json" in ctype else text)


def seed_researched(conn):
    r = db.add_contact(conn, {"full_name": "Sarah Lee", "job_title": "COO", "company_domain": "acme.com",
                              "website": "https://acme.com"})
    db.set_company(conn, r["company_id"], status="done", fit="strong", score=82, tz="Europe/London",
                   analysis_json=GOOD, facts_json=[{"id": "F1", "kind": "ok", "label": "Contact form", "evidence": ""}])
    db.set_contact(conn, r["contact_id"], messages_json=GOOD["messages"], messages_status="done")
    return r


def test_state_shape(srv):
    base, _ = srv
    s, j = call(base, "GET", "/api/state")
    assert s == 200 and {"claude_available", "worker", "counts", "rows", "settings_ready"} <= j.keys()


def test_index_and_static(srv):
    base, _ = srv
    assert call(base, "GET", "/")[0] == 200


def test_add_one_then_listed(srv):
    base, _ = srv
    s, j = call(base, "POST", "/api/add_one", {"fields": {"full_name": "Sarah Lee", "job_title": "COO", "website": "acme.com"}})
    assert s == 200 and j["company_new"]
    row = call(base, "GET", "/api/state")[1]["rows"][0]
    assert row["company_domain"] == "acme.com" and row["full_name"] == "Sarah Lee"


def test_add_one_requires_website_or_email(srv):
    base, _ = srv
    assert call(base, "POST", "/api/add_one", {"fields": {"full_name": "Nobody"}})[0] == 400


def test_import_preview_and_commit(srv):
    base, _ = srv
    b = {"kind": "file", "data_b64": base64.b64encode(b"Name,Website,Email\nA,acme.com,\nB,,b@gmail.com\n").decode()}
    s, p = call(base, "POST", "/api/import/preview", b)
    assert s == 200 and p["missing_website"] == 1 and p["mapping"]["Website"] == "website"
    s, j = call(base, "POST", "/api/import/commit", b)
    assert j == {"added": 2, "companies_new": 1, "needs_website": 1, "duplicates": 0}
    assert call(base, "POST", "/api/import/commit", b)[1]["duplicates"] == 2  # A: name+domain, B: email


def test_import_with_mapping_override(srv):
    base, conn = srv
    b = {"kind": "file", "data_b64": base64.b64encode(b"Who,Site\nA,acme.com\n").decode(),
         "mapping": {"Who": "full_name", "Site": "website"}}
    assert call(base, "POST", "/api/import/commit", b)[1]["companies_new"] == 1


def test_import_bad_file_is_400(srv):
    base, _ = srv
    assert call(base, "POST", "/api/import/preview", {"kind": "file", "data_b64": "!!notbase64"})[0] == 400


def test_settings_roundtrip_and_validation(srv):
    base, _ = srv
    call(base, "POST", "/api/settings", {"sender": {"name": "Alex"}, "max_pages": 99})
    j = call(base, "GET", "/api/settings")[1]
    assert j["sender"]["name"] == "Alex" and j["company"]["short_name"] == "Azikya" and j["max_pages"] == 20
    assert "access_password" not in j and j["access_password_set"] is False


def test_prospect_detail_and_report(srv):
    base, conn = srv
    r = seed_researched(conn)
    s, j = call(base, "GET", f"/api/prospect/{r['contact_id']}")
    assert s == 200 and "email" in j["slots"] and j["sop"]["label"] in ("cold", "warm", "hot")
    assert j["messages"]["email_subject"] and j["company"]["domain"] == "acme.com" and "there now" in j["local_now"]
    s, html = call(base, "GET", f"/report/{r['company_id']}?contact={r['contact_id']}")
    assert s == 200 and "Website Opportunity Review" in html and "Sarah Lee" in html


def test_status_update_and_validation(srv):
    base, conn = srv
    r = seed_researched(conn)
    assert call(base, "POST", f"/api/contact/{r['contact_id']}/status", {"outreach_status": "contacted"})[0] == 200
    j = call(base, "GET", f"/api/prospect/{r['contact_id']}")[1]
    assert j["contact"]["outreach_status"] == "contacted" and len(j["followups"]) == 3
    assert call(base, "POST", f"/api/contact/{r['contact_id']}/status", {"outreach_status": "banana"})[0] == 400


def test_edit_messages_and_regenerate_queues(srv):
    base, conn = srv
    r = seed_researched(conn)
    call(base, "POST", f"/api/contact/{r['contact_id']}/messages", {"messages": {"whatsapp": "Edited?"}})
    c = db.get_contact(conn, r["contact_id"])
    assert c["messages_json"]["whatsapp"] == "Edited?" and c["messages_json"]["email_subject"] == GOOD["messages"]["email_subject"]
    assert call(base, "POST", f"/api/contact/{r['contact_id']}/regenerate")[0] == 200
    assert db.get_contact(conn, r["contact_id"])["messages_status"] == "pending"


def test_delete_contact_and_orphan_company(srv):
    base, conn = srv
    r = seed_researched(conn)
    other = db.add_contact(conn, {"full_name": "Tom", "company_domain": "acme.com"})
    assert call(base, "POST", f"/api/contact/{r['contact_id']}/delete")[0] == 200
    assert db.get_contact(conn, r["contact_id"]) is None and db.get_company(conn, r["company_id"])  # Tom still there
    call(base, "POST", f"/api/contact/{other['contact_id']}/delete")
    assert db.get_company(conn, r["company_id"]) is None


def test_rerun_requeues_company(srv):
    base, conn = srv
    r = seed_researched(conn)
    call(base, "POST", f"/api/company/{r['company_id']}/rerun")
    assert db.get_company(conn, r["company_id"])["status"] == "queued"


def test_manual_signals(srv):
    base, conn = srv
    r = seed_researched(conn)
    call(base, "POST", f"/api/contact/{r['contact_id']}/manual_signals", {"signals": ["active_on_linkedin", "bogus"]})
    assert db.get_contact(conn, r["contact_id"])["manual_signals_json"] == ["active_on_linkedin"]


def test_stats_for_dashboard(srv):
    base, conn = srv
    seed_researched(conn)
    db.add_contact(conn, {"full_name": "B", "company_domain": "b.com"})
    s, j = call(base, "GET", "/api/stats")
    assert s == 200 and j["by_fit"]["strong"] == 1 and j["by_status"]["queued"] == 1
    assert j["funnel"]["new"] == 2 and sum(j["score_hist"]) == 1 and j["finding_types"]["broken"] == 1
    assert j["totals"]["messages_ready"] == 1


def test_exports(srv):
    base, conn = srv
    seed_researched(conn)
    assert call(base, "GET", "/api/export_prospectiq.csv")[1].splitlines()[0].startswith("first_name,last_name,full_name")
    assert "Sarah Lee" in call(base, "GET", "/api/export.csv")[1]
    assert call(base, "GET", "/api/export.json")[1]["contacts"][0]["full_name"] == "Sarah Lee"


def test_worker_controls_without_worker(srv):
    base, _ = srv
    assert call(base, "POST", "/api/worker/pause")[0] == 503


def test_404_and_traversal(srv):
    base, _ = srv
    assert call(base, "GET", "/nope")[0] == 404
    assert call(base, "GET", "/static/../prospectscout/config.py")[0] == 404
    assert call(base, "GET", "/static/%2e%2e/prospectscout/config.py")[0] == 404


def test_password_required_when_set(srv, monkeypatch):
    base, _ = srv
    monkeypatch.setenv("PROSPECTSCOUT_PASSWORD", "s3cret")
    assert call(base, "GET", "/api/state")[0] == 401
    assert call(base, "GET", "/api/state", auth="team:wrong")[0] == 401
    assert call(base, "GET", "/api/state", auth="team:s3cret")[0] == 200


# --- final-review fixes ---------------------------------------------------------------------

def test_post_requires_json_content_type(srv):
    base, _ = srv
    assert call(base, "POST", "/api/settings", {"sender": {"name": "Mallory"}}, ctype="text/plain")[0] == 415
    assert call(base, "GET", "/api/settings")[1]["sender"]["name"] != "Mallory"


def test_cross_site_post_rejected(srv):
    base, _ = srv
    assert call(base, "POST", "/api/settings", {"model": "x"}, headers={"Origin": "https://evil.example"})[0] == 403
    assert call(base, "POST", "/api/settings", {"model": "x"}, headers={"Sec-Fetch-Site": "cross-site"})[0] == 403
    assert call(base, "POST", "/api/settings", {"model": "x"}, headers={"Sec-Fetch-Site": "same-site"})[0] == 403
    assert call(base, "POST", "/api/settings", {"model": "ok"}, headers={"Origin": base, "Sec-Fetch-Site": "same-origin"})[0] == 200


def test_foreign_host_rejected_without_password(srv):
    base, _ = srv
    assert call(base, "GET", "/api/state", headers={"Host": "evil.example:8902"})[0] == 403
    assert call(base, "GET", "/api/state", headers={"Host": "localhost"})[0] == 200


def test_tunnel_host_allowed_with_password(srv, monkeypatch):
    base, _ = srv
    monkeypatch.setenv("PROSPECTSCOUT_PASSWORD", "s3cret")
    assert call(base, "GET", "/api/state", auth="team:s3cret", headers={"Host": "abc.ngrok-free.app"})[0] == 200


def test_set_contact_website_endpoint(srv):
    base, conn = srv
    r = db.add_contact(conn, {"full_name": "C", "email": "c@gmail.com"})
    assert call(base, "POST", f"/api/contact/{r['contact_id']}/website", {"website": "javascript:alert(1)"})[0] == 400
    s, j = call(base, "POST", f"/api/contact/{r['contact_id']}/website", {"website": "www.acme.com/about"})
    c = db.get_contact(conn, r["contact_id"])
    assert s == 200 and c["status"] == "ready" and c["company_domain"] == "acme.com" and c["website"] == "https://www.acme.com"


def test_handler_has_socket_timeout():
    assert server.Handler.timeout == 30


# --- deferred minors -----------------------------------------------------------------------

def test_non_string_password_is_coerced(srv):
    base, _ = srv
    from prospectscout import config as cfg
    cfg.update({"access_password": 12345678})
    assert call(base, "GET", "/api/state", auth="team:12345678")[0] == 200


def test_short_password_rejected(srv):
    base, _ = srv
    assert call(base, "POST", "/api/settings", {"access_password": "abc"})[0] == 400


def test_negative_content_length_is_400(srv):
    import socket
    base, _ = srv
    host, port = base.replace("http://", "").split(":")
    with socket.create_connection((host, int(port)), timeout=5) as s:
        s.sendall(b"POST /api/settings HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\n"
                  b"Content-Length: -5\r\n\r\n")
        assert b" 400 " in s.recv(200)


def test_failed_logins_are_rate_limited(srv, monkeypatch):
    base, _ = srv
    monkeypatch.setenv("PROSPECTSCOUT_PASSWORD", "s3cretpass")
    codes = [call(base, "GET", "/api/state", auth="team:wrong")[0] for _ in range(11)]
    assert codes[:10] == [401] * 10 and codes[10] == 429


def test_pagespeed_key_is_masked_and_kept(srv):
    base, _ = srv
    from prospectscout import config as cfg
    call(base, "POST", "/api/settings", {"pagespeed": {"enabled": True, "api_key": "KEY123"}})
    j = call(base, "GET", "/api/settings")[1]
    assert j["pagespeed"]["api_key"] == "" and j["pagespeed"]["api_key_set"] is True
    call(base, "POST", "/api/settings", {"pagespeed": {"enabled": False, "api_key": ""}})
    assert cfg.load()["pagespeed"]["api_key"] == "KEY123"


def test_stats_are_cached_until_data_changes(srv, monkeypatch):
    base, conn = srv
    calls = []
    real = db.all_companies
    monkeypatch.setattr(db, "all_companies", lambda c: calls.append(1) or real(c))
    call(base, "GET", "/api/stats")
    call(base, "GET", "/api/stats")
    assert len(calls) == 1
    db.add_contact(conn, {"full_name": "X", "company_domain": "x.com"})
    call(base, "GET", "/api/stats")
    assert len(calls) == 2


def test_followups_use_settings_spacing_and_prospect_weekend(srv):
    from datetime import date as _d
    from prospectscout.core import timing as _t
    base, conn = srv
    r = seed_researched(conn)
    db.set_company(conn, r["company_id"], tz="Asia/Riyadh")
    call(base, "POST", "/api/settings", {"followup_spacing": [[1, 1], [1, 2], [2, 2]]})
    call(base, "POST", f"/api/contact/{r['contact_id']}/status", {"outreach_status": "contacted"})
    got = call(base, "GET", f"/api/prospect/{r['contact_id']}")[1]["followups"]
    want = [[a.isoformat(), b.isoformat()] for a, b in _t.followup_dates(_d.today(), ((1, 1), (1, 2), (2, 2)), "Asia/Riyadh")]
    assert got == want


def test_early_rejections_never_reset_the_connection(srv, monkeypatch):
    """Rejected POSTs (415/403/401) must still read the body, or Windows resets the socket mid-response."""
    base, _ = srv
    body = {"sender": {"name": "x" * 2000}}
    codes = [call(base, "POST", "/api/settings", body, ctype="text/plain")[0] for _ in range(25)]
    codes += [call(base, "POST", "/api/settings", body, headers={"Origin": "https://evil.example"})[0] for _ in range(10)]
    monkeypatch.setenv("PROSPECTSCOUT_PASSWORD", "s3cretpass")
    codes += [call(base, "POST", "/api/settings", body)[0] for _ in range(5)]
    assert set(codes[:25]) == {415} and set(codes[25:35]) == {403} and set(codes[35:]) == {401}


# --- Quick Prospect API ----------------------------------------------------------------------

class FakeQuick:
    def __init__(self, path):
        self.path = path
        self.texts = {}

    def start(self, url):
        from prospectscout.core import importer
        domain = importer.normalize_domain(url)
        if not domain:
            raise ValueError("Enter the company's own website")
        qid = db.create_quick(db.connect(self.path), importer.normalize_website(url), domain)
        self.texts[qid] = "## First look\nHello"
        return qid

    def live(self, qid):
        if qid not in self.texts:
            return None
        return {"text": self.texts[qid], "steps": ["Reading the website"], "status": "running",
                "snapshot": {"title": "Acme"}, "verdict": None, "problems": [], "error": ""}


@pytest.fixture
def qsrv(tmp_path):
    path = str(tmp_path / "q.db")
    fake = FakeQuick(path)
    db.connect(path).close()
    httpd = server.make_server("127.0.0.1", 0, path, None, quick=fake)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", fake, path
    httpd.shutdown()


def test_quick_start_poll_list_add(qsrv):
    base, fake, path = qsrv
    s, j = call(base, "POST", "/api/quick", {"url": "acme.com"})
    assert s == 200 and j["id"]
    qid = j["id"]
    s, p = call(base, "GET", f"/api/quick/{qid}?since=0")
    assert p["text"] == "## First look\nHello" and p["length"] == 19 and p["status"] == "running"
    assert p["steps"] == ["Reading the website"] and p["snapshot"]["title"] == "Acme"
    fake.texts[qid] += "\n\n## Verdict"
    assert call(base, "GET", f"/api/quick/{qid}?since=19")[1]["text"] == "\n\n## Verdict"
    assert call(base, "GET", "/api/quick")[1]["checks"][0]["domain"] == "acme.com"
    s, added = call(base, "POST", f"/api/quick/{qid}/add")
    assert s == 200 and added["company_new"] is True


def test_quick_bad_url_and_missing(qsrv):
    base, _, _ = qsrv
    assert call(base, "POST", "/api/quick", {"url": "not a site"})[0] == 400
    assert call(base, "GET", "/api/quick/999")[0] == 404


def test_quick_done_check_served_from_storage(qsrv):
    base, fake, path = qsrv
    qid = db.create_quick(db.connect(path), "https://b.com", "b.com")
    db.update_quick(db.connect(path), qid, status="done", markdown="## Verdict\n**Skip** · 2/10",
                    verdict_json={"label": "skip", "score": 2.0, "text": "Skip · 2/10"}, steps_json=["x"])
    p = call(base, "GET", f"/api/quick/{qid}")[1]
    assert p["status"] == "done" and p["verdict"]["label"] == "skip" and p["text"].startswith("## Verdict")


def test_quick_without_runner_is_503(srv):
    base, _ = srv
    assert call(base, "POST", "/api/quick", {"url": "acme.com"})[0] == 503
