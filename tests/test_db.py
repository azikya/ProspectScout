import pytest

from prospectscout.adapters import db


@pytest.fixture
def conn(tmp_path):
    c = db.connect(str(tmp_path / "t.db"))
    yield c
    c.close()


def test_two_contacts_same_domain_share_one_company(conn):
    a = db.add_contact(conn, {"full_name": "A", "company_domain": "acme.com", "website": "https://acme.com"})
    b = db.add_contact(conn, {"full_name": "B", "company_domain": "acme.com"})
    assert a["company_new"] and not b["company_new"] and a["company_id"] == b["company_id"]


def test_contact_without_domain_needs_website(conn):
    r = db.add_contact(conn, {"full_name": "C", "email": "c@gmail.com"})
    assert r["needs_website"] and r["company_id"] is None
    assert db.get_contact(conn, r["contact_id"])["status"] == "needs_website"


def test_same_email_same_company_is_duplicate(conn):
    f = {"full_name": "A", "email": "a@acme.com", "company_domain": "acme.com"}
    db.add_contact(conn, f)
    r = db.add_contact(conn, dict(f, email="A@ACME.com"))
    assert r["duplicate"] and r["contact_id"] is None and len(db.list_rows(conn)) == 1


def test_same_name_same_domain_without_email_is_duplicate(conn):
    db.add_contact(conn, {"full_name": "Sarah Lee", "company_domain": "acme.com"})
    assert db.add_contact(conn, {"full_name": "Sarah Lee", "company_domain": "acme.com"})["duplicate"]


def test_next_queued_claims_in_order(conn):
    db.add_contact(conn, {"company_domain": "a.com"})
    db.add_contact(conn, {"company_domain": "b.com"})
    c = db.next_queued_company(conn)
    assert c["domain"] == "a.com" and db.get_company(conn, c["id"])["status"] == "crawling"
    assert db.next_queued_company(conn)["domain"] == "b.com"
    assert db.next_queued_company(conn) is None


def test_reset_in_progress_requeues(conn):
    db.add_contact(conn, {"company_domain": "a.com"})
    c = db.next_queued_company(conn)
    assert db.reset_in_progress(conn) == 1 and db.get_company(conn, c["id"])["status"] == "queued"


def test_json_columns_roundtrip(conn):
    cid = db.add_contact(conn, {"company_domain": "a.com"})["company_id"]
    db.set_company(conn, cid, analysis_json={"fit": "strong"}, score=80)
    got = db.get_company(conn, cid)
    assert got["analysis_json"] == {"fit": "strong"} and got["score"] == 80


def test_contacted_sets_contacted_at_once(conn):
    r = db.add_contact(conn, {"full_name": "A", "company_domain": "a.com"})
    db.set_contact(conn, r["contact_id"], outreach_status="contacted")
    first = db.get_contact(conn, r["contact_id"])["contacted_at"]
    assert first
    db.set_contact(conn, r["contact_id"], outreach_status="replied")
    db.set_contact(conn, r["contact_id"], outreach_status="contacted")
    assert db.get_contact(conn, r["contact_id"])["contacted_at"] == first


def test_list_rows_joins_company_and_counts(conn):
    db.add_contact(conn, {"full_name": "A", "job_title": "COO", "company_domain": "acme.com"})
    db.add_contact(conn, {"full_name": "B", "email": "b@gmail.com"})
    rows = db.list_rows(conn)
    acme = next(r for r in rows if r["full_name"] == "A")
    assert acme["company_status"] == "queued" and acme["domain"] == "acme.com"
    assert db.counts(conn) == {"total": 1, "queued": 1, "in_progress": 0, "done": 0, "failed": 0,
                               "contacts": 2, "needs_website": 1}


def test_next_pending_contact_only_for_researched_companies(conn):
    r = db.add_contact(conn, {"full_name": "A", "company_domain": "a.com"})
    assert db.next_pending_contact(conn) is None  # company not researched yet
    db.set_company(conn, r["company_id"], status="done")
    c = db.next_pending_contact(conn)
    assert c["id"] == r["contact_id"] and db.get_contact(conn, c["id"])["messages_status"] == "writing"
    assert db.next_pending_contact(conn) is None
    assert db.reset_in_progress(conn) == 1 and db.get_contact(conn, c["id"])["messages_status"] == "pending"


def test_requeue_company(conn):
    cid = db.add_contact(conn, {"company_domain": "a.com"})["company_id"]
    db.set_company(conn, cid, status="failed", error="boom")
    db.requeue_company(conn, cid)
    co = db.get_company(conn, cid)
    assert co["status"] == "queued" and co["error"] in (None, "")


def test_websites_only_reupload_is_duplicate(conn):
    db.add_contact(conn, {"company_domain": "acme.com", "website": "https://acme.com"})
    assert db.add_contact(conn, {"company_domain": "acme.com", "website": "https://acme.com"})["duplicate"]


def test_name_only_rows_dedupe_on_phone_or_linkedin(conn):
    db.add_contact(conn, {"full_name": "Jamie Fox", "phone": "+1 512 555 0100"})
    assert db.add_contact(conn, {"full_name": "Jamie Fox", "phone": "+1 512 555 0100"})["duplicate"]
    db.add_contact(conn, {"full_name": "Kim Lo", "linkedin_url": "https://linkedin.com/in/kimlo"})
    assert db.add_contact(conn, {"full_name": "Kim Lo", "linkedin_url": "https://linkedin.com/in/kimlo"})["duplicate"]
    assert not db.add_contact(conn, {"full_name": "Jamie Fox", "phone": "+44 20 7946 0958"})["duplicate"]


def test_set_contact_website_attaches_company(conn):
    r = db.add_contact(conn, {"full_name": "C", "email": "c@gmail.com"})
    out = db.set_contact_website(conn, r["contact_id"], "https://acme.com", "acme.com")
    c = db.get_contact(conn, r["contact_id"])
    assert out["company_new"] and c["status"] == "ready" and c["company_id"] == out["company_id"]
    assert db.get_company(conn, out["company_id"])["status"] == "queued"


# --- quick checks ----------------------------------------------------------------------------

def test_quick_check_roundtrip_and_listing(conn):
    a = db.create_quick(conn, "https://acme.com", "acme.com")
    b = db.create_quick(conn, "https://acme.com", "acme.com")
    db.update_quick(conn, a, status="done", markdown="## Verdict\n**Maybe** · 5/10", verdict_json={"label": "maybe"},
                    steps_json=["Searching: acme"])
    got = db.get_quick(conn, a)
    assert got["status"] == "done" and got["verdict_json"] == {"label": "maybe"} and got["steps_json"] == ["Searching: acme"]
    rows = db.list_quick(conn)
    assert [r["id"] for r in rows] == [b, a] and "markdown" not in rows[0]


def test_reset_running_quick_marks_failed(conn):
    q = db.create_quick(conn, "https://acme.com", "acme.com")
    assert db.reset_running_quick(conn) == 1
    got = db.get_quick(conn, q)
    assert got["status"] == "failed" and "interrupted" in got["error"].lower()
