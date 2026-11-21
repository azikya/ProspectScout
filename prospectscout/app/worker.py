"""Background research queue.

One job = one company: crawl → fact checks → (optional PageSpeed) → one Claude analysis
→ store → messages for the first contact. Extra contacts at an already-researched
company are separate, lighter jobs (messages only). Survives restarts (in-progress
work is re-queued) and pauses itself when the Claude plan's usage limit is hit.
"""

from __future__ import annotations

import functools
import threading
import time
from typing import Callable

from .. import config
from ..adapters import claude_runner, crawl, db, pagespeed
from ..adapters.claude_runner import LLMError
from ..core import analyze, checks, timing

USAGE_PAUSE = "Max plan limit reached. Resume later."


class Worker:
    def __init__(self, db_path: str, settings_loader: Callable[[], dict] = config.load,
                 crawl_fn: Callable | None = None, analyze_fn: Callable | None = None,
                 messages_fn: Callable | None = None):
        self.db_path = db_path
        self.settings_loader = settings_loader
        self.crawl_fn = crawl_fn or crawl.crawl
        self.analyze_fn = analyze_fn or functools.partial(analyze.analyze_company, run=claude_runner.run_llm)
        self.messages_fn = messages_fn or functools.partial(analyze.write_messages, run=claude_runner.run_llm)
        self._local = threading.local()
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()
        self._paused = False
        self._pause_reason = ""
        self._current: dict | None = None
        conn = db.connect(db_path)
        try:
            db.reset_in_progress(conn)
        finally:
            conn.close()

    # -- control ------------------------------------------------------------------------------
    def _conn(self):
        if getattr(self._local, "conn", None) is None:
            self._local.conn = db.connect(self.db_path)
        return self._local.conn

    def start(self) -> None:
        self._stop.clear()
        n = max(1, min(3, int(self.settings_loader().get("concurrency") or 1)))
        for i in range(n):
            t = threading.Thread(target=self._loop, name=f"prospectscout-worker-{i}", daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=5)
        self._threads = []

    def pause(self, reason: str = "") -> None:
        self._paused, self._pause_reason = True, reason

    def resume(self) -> None:
        self._paused, self._pause_reason = False, ""

    def status(self) -> dict:
        return {"running": any(t.is_alive() for t in self._threads), "paused": self._paused,
                "pause_reason": self._pause_reason, "current": self._current}

    def _loop(self) -> None:
        while not self._stop.is_set():
            if self._paused:
                time.sleep(1)
                continue
            try:
                worked = self.process_one()
            except Exception:  # noqa: BLE001 - never let the loop die
                worked = False
            if not worked:
                self._stop.wait(1)

    # -- jobs ---------------------------------------------------------------------------------
    def process_one(self) -> bool:
        conn = self._conn()
        contact = db.next_pending_contact(conn)
        if contact:
            self._write_messages_job(conn, contact)
            return True
        company = db.next_queued_company(conn)
        if company:
            self._research_job(conn, company)
            return True
        return False

    def _write_messages_job(self, conn, contact: dict) -> None:
        company = db.get_company(conn, contact["company_id"]) or {}
        self._current = {"company_id": company.get("id"), "domain": company.get("domain"), "stage": "Writing messages…"}
        try:
            msgs, problems = self.messages_fn(self.settings_loader(), contact, company.get("analysis_json") or {},
                                              company.get("facts_json") or [])
            db.set_contact(conn, contact["id"], messages_json=msgs, messages_status="needs_review" if problems else "done")
        except LLMError as exc:
            if exc.kind == "usage_limit":
                db.set_contact(conn, contact["id"], messages_status="pending")
                self.pause(USAGE_PAUSE)
            else:
                db.set_contact(conn, contact["id"], messages_status="failed")
        except Exception:  # noqa: BLE001
            db.set_contact(conn, contact["id"], messages_status="failed")
        finally:
            self._current = None

    def _research_job(self, conn, co: dict) -> None:
        cid = co["id"]
        self._current = {"company_id": cid, "domain": co["domain"], "stage": "Starting…"}

        def stage(msg: str) -> None:
            db.set_company(conn, cid, stage=msg)
            if self._current:
                self._current["stage"] = msg

        settings = self.settings_loader()
        try:
            result = self.crawl_fn(co.get("website") or f"https://{co['domain']}",
                                   max_pages=int(settings.get("max_pages") or 8),
                                   max_link_checks=int(settings.get("max_link_checks") or 40), progress=stage)
            facts = [f.to_dict() for f in checks.run_checks(result)]
            summary = result.summary()
            if not result.reachable or not result.pages:  # down, or the "homepage" is a PDF/binary
                db.set_company(conn, cid, status="unreachable", fit="manual", score=0, stage=None,
                               facts_json=facts, crawl_json=summary, error=result.error or facts[0]["label"])
                self._skip_contacts(conn, cid)
                return
            stage("Checking site…")
            ps = settings.get("pagespeed") or {}
            if ps.get("enabled"):
                try:
                    psi = pagespeed.fetch(result.final_url, ps.get("api_key", ""))
                    facts += [f.to_dict() for f in pagespeed.facts_from(psi, start_index=len(facts) + 1)]
                except Exception:  # noqa: BLE001 - PageSpeed is optional
                    pass
            db.set_company(conn, cid, status="analyzing", facts_json=facts, crawl_json=summary)

            stage("Analyzing with Claude…")
            contacts = [c for c in db.contacts_for_company(conn, cid) if c["status"] == "ready"]
            fresh = [c for c in contacts if (c.get("outreach_status") or "new") == "new"]  # not contacted yet
            first = (fresh or contacts or [{"company_name": co.get("company_name"), "website": co.get("website")}])[0]
            analysis, problems = self.analyze_fn(settings, first, summary, facts)
            analysis["_problems"] = problems
            fit = analysis.get("fit") or "possible"
            tz, tz_source = timing.resolve_timezone(first.get("phone"), analysis.get("timezone_hint"), co["domain"])
            info = analysis.get("company") or {}
            status = "skipped" if fit == "skip" else "needs_review" if (problems or fit == "manual") else "done"
            # Write the first contact's messages BEFORE the company is marked done, so a parallel worker
            # can't also claim that contact as a "messages only" job. Contacted prospects keep what was sent.
            if fit not in ("skip", "manual") and fresh:
                db.set_contact(conn, fresh[0]["id"],
                               messages_json=analyze.finalize_messages(analysis.get("messages") or {}, settings),
                               messages_status="needs_review" if problems else "done")
            db.set_company(conn, cid, status=status, stage=None, error=None, analysis_json=analysis,
                           score=analysis.get("score"), fit=fit, tz=tz, tz_source=tz_source,
                           industry=info.get("industry"), country=info.get("country"),
                           company_name=co.get("company_name") or info.get("name"))
            if fit in ("skip", "manual"):
                self._skip_contacts(conn, cid)
                return
        except LLMError as exc:
            if exc.kind == "usage_limit":
                db.set_company(conn, cid, status="queued", stage=None)
                self.pause(USAGE_PAUSE)
            else:
                db.set_company(conn, cid, status="failed", stage=None, error=str(exc))
        except Exception as exc:  # noqa: BLE001 - record and move on to the next company
            db.set_company(conn, cid, status="failed", stage=None, error=f"{exc.__class__.__name__}: {exc}")
        finally:
            self._current = None

    @staticmethod
    def _skip_contacts(conn, company_id: int) -> None:
        for c in db.contacts_for_company(conn, company_id):
            db.set_contact(conn, c["id"], messages_status="skipped")
