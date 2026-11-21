"""Runs Quick Prospect checks in the background and keeps a live, streaming view of each one.

Flow per check: quick website snapshot (3 pages, no link checks) → one streamed Claude run with web
lookups → rule checks + verdict parsing → saved. At most ``max_parallel`` run at once, separate from
the full research queue.
"""

from __future__ import annotations

import threading
from datetime import date
from typing import Callable

from .. import config
from ..adapters import claude_runner, crawl, db
from ..adapters.claude_runner import LLMError
from ..core import checks, importer, quick

_PLAIN_ERRORS = {
    "usage_limit": "Your Claude plan's usage limit is reached. Try again after it resets.",
    "not_installed": "Claude Code isn't installed or isn't on PATH on this computer.",
    "timeout": "Claude took too long to answer. Try again.",
}


class QuickRunner:
    def __init__(self, db_path: str, settings_loader: Callable[[], dict] = config.load,
                 crawl_fn: Callable | None = None, stream_fn: Callable | None = None, max_parallel: int = 2):
        self.db_path = db_path
        self.settings_loader = settings_loader
        self.crawl_fn = crawl_fn or crawl.crawl
        self.stream_fn = stream_fn or claude_runner.stream_llm
        self._sem = threading.BoundedSemaphore(max(1, max_parallel))
        self._lock = threading.Lock()
        self._live: dict[int, dict] = {}
        self._threads: dict[int, threading.Thread] = {}
        conn = db.connect(db_path)
        try:
            db.reset_running_quick(conn)
        finally:
            conn.close()

    # -- public ---------------------------------------------------------------------------------
    def start(self, url: str) -> int:
        website, domain = importer.normalize_website(url), importer.normalize_domain(url)
        if not website or not domain or importer.is_platform_host(domain):
            raise ValueError("Enter the company's own website, e.g. healthwaresystems.com")
        conn = db.connect(self.db_path)
        try:
            qid = db.create_quick(conn, website, domain)
        finally:
            conn.close()
        with self._lock:
            self._live[qid] = {"chunks": [], "steps": [], "status": "queued", "snapshot": None,
                               "verdict": None, "problems": [], "error": ""}
        t = threading.Thread(target=self._run, args=(qid, website), name=f"quick-{qid}", daemon=True)
        self._threads[qid] = t
        t.start()
        return qid

    def live(self, qid: int) -> dict | None:
        with self._lock:
            st = self._live.get(qid)
            if st is None:
                return None
            return {"text": "".join(st["chunks"]), "steps": list(st["steps"]), "status": st["status"],
                    "snapshot": st["snapshot"], "verdict": st["verdict"], "problems": list(st["problems"]),
                    "error": st["error"]}

    def wait(self, qid: int, timeout: float | None = None) -> None:
        t = self._threads.get(qid)
        if t:
            t.join(timeout)

    # -- worker ---------------------------------------------------------------------------------
    def _set(self, qid: int, **fields) -> None:
        with self._lock:
            self._live[qid].update(fields)

    def _run(self, qid: int, website: str) -> None:
        with self._sem:
            self._set(qid, status="running")
            conn = db.connect(self.db_path)
            try:
                settings = self.settings_loader()
                with self._lock:
                    self._live[qid]["steps"].append("Reading the website")
                result = self.crawl_fn(website, max_pages=3, max_link_checks=0, deep=False, delay=0.2)
                snapshot = self._snapshot(result)
                public = {k: v for k, v in snapshot.items() if k != "text"}
                self._set(qid, snapshot=public)
                db.update_quick(conn, qid, snapshot_json=public)

                def on_text(chunk: str) -> None:
                    with self._lock:
                        self._live[qid]["chunks"].append(chunk)

                def on_step(name: str, tool_input: dict) -> None:
                    with self._lock:
                        self._live[qid]["steps"].append(quick.step_label(name, tool_input))

                prompt = quick.build_quick_prompt(settings, website, snapshot, date.today().isoformat())
                text = self.stream_fn(prompt, ["WebSearch", "WebFetch"], on_text=on_text, on_step=on_step,
                                      model=settings.get("model") or None, timeout=600)
                text = text or self.live(qid)["text"]
                verdict, problems = quick.parse_verdict(text), quick.validate_quick(text)
                with self._lock:
                    steps = list(self._live[qid]["steps"])
                    self._live[qid].update(status="done", verdict=verdict, problems=problems, chunks=[text])
                db.update_quick(conn, qid, status="done", markdown=text, steps_json=steps, verdict_json=verdict,
                                problems_json=problems)
            except LLMError as exc:
                self._fail(conn, qid, _PLAIN_ERRORS.get(exc.kind) or f"Claude returned an error: {exc}")
            except Exception as exc:  # noqa: BLE001 - a quick check must never take the app down
                self._fail(conn, qid, f"Something went wrong ({exc.__class__.__name__}). Try again.")
            finally:
                conn.close()

    def _fail(self, conn, qid: int, message: str) -> None:
        with self._lock:
            text = "".join(self._live[qid]["chunks"])
            self._live[qid].update(status="failed", error=message)
        db.update_quick(conn, qid, status="failed", error=message, markdown=text)

    @staticmethod
    def _snapshot(result) -> dict:
        if not result.reachable or not result.pages:
            return {"reachable": False, "error": result.error or "no web page found", "title": "", "description": "",
                    "pages": [], "facts": [], "text": ""}
        home = result.pages[0]
        facts = [f.label for f in checks.run_checks(result)
                 if f.kind in ("issue", "missing") or f.category == "intent"][:12]
        return {"reachable": True, "error": "", "title": home.title, "description": home.meta.get("description", ""),
                "pages": [{"url": p.final_url, "title": p.title} for p in result.pages], "facts": facts,
                "text": "\n\n".join(p.text[:2500] for p in result.pages)[:6000]}
