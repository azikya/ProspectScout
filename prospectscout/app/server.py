"""Local web server: the UI plus a small JSON API. Standard library only.

Run:  python -m prospectscout     (opens http://127.0.0.1:8902)

Optional access password (needed before sharing through a tunnel such as ngrok):
set ``access_password`` in Settings or the ``PROSPECTSCOUT_PASSWORD`` environment variable.
Every route then requires HTTP Basic auth (any username, that password).
"""

from __future__ import annotations

import base64
import binascii
import hmac
import json
import os
import re
import socketserver
import threading
import time
import webbrowser
from collections import Counter
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from .. import config
from ..adapters import claude_runner, db
from ..adapters.sheet_fetch import SheetFetchError, fetch_url_rows
from ..core import analyze, exports, importer, report, sop, timing

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
UI_DIR = os.path.join(ROOT, "ui")
MAX_BODY = 20 * 1024 * 1024
CTYPES = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
          ".css": "text/css; charset=utf-8", ".png": "image/png", ".svg": "image/svg+xml", ".ico": "image/x-icon",
          ".json": "application/json; charset=utf-8"}
MANUAL_SIGNALS = ("changed_job_recently", "active_on_linkedin", "talks_tech_growth", "hiring_tech_roles",
                  "funding_news", "expansion_launch")
SETTINGS_KEYS = ("company", "sender", "user_timezone", "send_windows", "model", "pagespeed", "max_pages",
                 "max_link_checks", "concurrency", "sop", "access_password", "followup_spacing")
BOUNDS = {"max_pages": (1, 20), "max_link_checks": (0, 200), "concurrency": (1, 3)}


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def access_password() -> str:
    return str(os.environ.get("PROSPECTSCOUT_PASSWORD") or config.load().get("access_password") or "")


MIN_PASSWORD = 8
LOGIN_WINDOW, LOGIN_MAX_FAILS = 300, 10  # 10 wrong passwords in 5 minutes → locked out for the rest of the window


# -- view helpers ---------------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _slots(tz: str | None, settings: dict) -> dict:
    if not tz:
        return {}
    try:
        return timing.next_slots(tz, _now(), settings.get("send_windows") or timing.DEFAULT_WINDOWS,
                                 settings.get("user_timezone") or None)
    except Exception:  # noqa: BLE001 - bad tz in data must not break the page
        return {}


def _rows(conn, settings: dict) -> list[dict]:
    rows = db.list_rows(conn)
    for r in rows:
        r["next_email"] = (_slots(r.get("tz"), settings).get("email") or {}).get("label", "")
    return rows


def _data_version(conn) -> tuple:
    a = conn.execute("SELECT COUNT(*), COALESCE(MAX(updated_at), 0) FROM companies").fetchone()
    b = conn.execute("SELECT COUNT(*), COALESCE(MAX(updated_at), 0) FROM contacts").fetchone()
    return (tuple(a), tuple(b))


def _stats(conn) -> dict:
    companies = db.all_companies(conn)
    contacts = db.all_contacts(conn)
    analyzed = [c for c in companies if c.get("fit")]
    by_fit = Counter(c["fit"] for c in analyzed)
    by_status = Counter(c["status"] for c in companies)
    funnel = Counter(c.get("outreach_status") or "new" for c in contacts)
    hist = [0] * 5
    intent = Counter()
    finding_types = Counter()
    for c in analyzed:
        a = c.get("analysis_json") or {}
        if c["fit"] in ("strong", "possible") and c.get("score") is not None:
            hist[min(4, int(c["score"]) // 20)] += 1
            intent[(a.get("buying_intent") or {}).get("level") or "low"] += 1
            finding_types.update(f.get("type") for f in a.get("findings") or [] if f.get("type"))
    return {
        "by_fit": {k: by_fit.get(k, 0) for k in ("strong", "possible", "skip", "manual")},
        "by_status": {k: by_status.get(k, 0) for k in db.COMPANY_STATUSES},
        "funnel": {k: funnel.get(k, 0) for k in db.OUTREACH_STATUSES},
        "score_hist": hist,
        "intent": {k: intent.get(k, 0) for k in ("low", "medium", "high")},
        "finding_types": {k: finding_types.get(k, 0) for k in ("broken", "missing", "competitor_gap", "growth", "ai")},
        "totals": {"companies": len(companies), "contacts": len(contacts),
                   "messages_ready": sum(1 for c in contacts if c.get("messages_status") in ("done", "needs_review")),
                   "needs_website": sum(1 for c in contacts if c.get("status") == "needs_website")},
    }


def _export_rows(conn, settings: dict) -> list[dict]:
    out = []
    companies = {c["id"]: c for c in db.all_companies(conn)}
    for c in db.all_contacts(conn):
        co = companies.get(c.get("company_id")) or {}
        row = {**c, "fit": co.get("fit"), "score": co.get("score"),
               "company_name": c.get("company_name") or co.get("company_name"),
               "next_email": (_slots(co.get("tz"), settings).get("email") or {}).get("label", "")}
        row.update(c.get("messages_json") or {})
        out.append(row)
    return out


def _public_settings(cfg: dict) -> dict:
    out = {k: v for k, v in cfg.items() if k != "access_password"}
    out["access_password_set"] = bool(access_password())
    ps = dict(out.get("pagespeed") or {})
    ps["api_key_set"], ps["api_key"] = bool(ps.get("api_key")), ""  # secrets never leave the server
    out["pagespeed"] = ps
    return out


def _clean_settings_patch(body: dict) -> dict:
    patch = {k: v for k, v in (body or {}).items() if k in SETTINGS_KEYS}
    for key, (lo, hi) in BOUNDS.items():
        if key in patch:
            try:
                patch[key] = max(lo, min(hi, int(patch[key])))
            except (TypeError, ValueError):
                patch.pop(key)
    if "access_password" in patch:
        pw = str(patch["access_password"] or "")
        if len(pw) < MIN_PASSWORD:
            raise ApiError(400, f"Use a password of at least {MIN_PASSWORD} characters.")
        patch["access_password"] = pw
    if isinstance(patch.get("pagespeed"), dict) and not patch["pagespeed"].get("api_key"):
        patch["pagespeed"] = {k: v for k, v in patch["pagespeed"].items() if k != "api_key"}  # blank = keep current
    if "followup_spacing" in patch:
        try:
            spacing = [[max(0, min(30, int(lo))), max(0, min(30, int(hi)))] for lo, hi in patch["followup_spacing"]][:5]
            patch["followup_spacing"] = [[lo, max(lo, hi)] for lo, hi in spacing] or config.DEFAULTS["followup_spacing"]
        except (TypeError, ValueError):
            raise ApiError(400, "Follow-up spacing should look like 2-3, 3-5, 4-7.") from None
    return patch


def _read_upload(body: dict) -> tuple[list[str], list[dict]]:
    kind = body.get("kind")
    if kind == "url":
        try:
            return fetch_url_rows(body.get("url") or "")
        except SheetFetchError as exc:
            raise ApiError(400, str(exc)) from exc
    try:
        data = base64.b64decode(body.get("data_b64") or "", validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ApiError(400, "That file couldn't be read.") from exc
    if not data:
        raise ApiError(400, "The file is empty.")
    try:
        return importer.read_bytes(data)
    except Exception as exc:  # noqa: BLE001 - corrupt xlsx etc.
        raise ApiError(400, f"That file couldn't be read as CSV or Excel ({exc.__class__.__name__}).") from exc


# -- handler -------------------------------------------------------------------------------------

LOCAL_HOSTS = ("127.0.0.1", "localhost", "[::1]", "::1")


class Handler(BaseHTTPRequestHandler):
    server_version = "ProspectScout/0.1"
    timeout = 30  # seconds; stops slow or idle connections from holding threads

    def log_message(self, *args):
        pass

    @property
    def db_path(self) -> str:
        return self.server.db_path  # type: ignore[attr-defined]

    @property
    def worker(self):
        return self.server.worker  # type: ignore[attr-defined]

    @property
    def quick(self):
        return self.server.quick  # type: ignore[attr-defined]

    def _quick_view(self, conn, qid: int, since: int) -> dict:
        row = db.get_quick(conn, qid)
        if not row:
            raise ApiError(404, "not found")
        live = self.quick.live(qid) if self.quick else None
        text = (live or {}).get("text") if live else (row.get("markdown") or "")
        text = text or ""
        src = live or {}
        return {"id": qid, "url": row["url"], "domain": row["domain"], "status": src.get("status") or row["status"],
                "text": text[max(0, since):], "length": len(text),
                "steps": src.get("steps") if live else (row.get("steps_json") or []),
                "snapshot": src.get("snapshot") if live else row.get("snapshot_json"),
                "verdict": src.get("verdict") if live else row.get("verdict_json"),
                "problems": src.get("problems") if live else (row.get("problems_json") or []),
                "error": src.get("error") if live else (row.get("error") or "")}

    # responses
    def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, status: int = 200):
        self._send(status, json.dumps(obj, default=str).encode("utf-8"), "application/json; charset=utf-8")

    def _file(self, base: str, rel: str):
        rel = unquote(rel).lstrip("/\\")
        full = os.path.realpath(os.path.join(base, rel or "index.html"))
        if not full.startswith(os.path.realpath(base) + os.sep) or not os.path.isfile(full):
            return self._json({"error": "not found"}, 404)
        with open(full, "rb") as fh:
            self._send(200, fh.read(), CTYPES.get(os.path.splitext(full)[1].lower(), "application/octet-stream"))

    def _read_raw_body(self) -> bool:
        """Read the whole request body up front. Replying before the body is consumed makes Windows reset the
        socket, so even rejected requests (415/403/401) must be drained first. Returns False if it replied."""
        self._raw_body = b""
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if n < 0 or n > MAX_BODY:
            self.close_connection = True
            self._json({"error": "That upload is larger than 20 MB." if n > MAX_BODY else "Bad request."},
                       413 if n > MAX_BODY else 400)
            return False
        self._raw_body = self.rfile.read(n) if n else b""
        return True

    def _body(self) -> dict:
        raw = getattr(self, "_raw_body", b"")
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise ApiError(400, "Invalid JSON body.") from exc
        return data if isinstance(data, dict) else {}

    def _client_key(self) -> str:
        """Client address; behind a local tunnel (ngrok) use the last X-Forwarded-For hop it added."""
        ip = self.client_address[0]
        fwd = self.headers.get("X-Forwarded-For")
        if fwd and ip in ("127.0.0.1", "::1"):
            ip = fwd.split(",")[-1].strip() or ip
        return ip

    def _authorized(self) -> bool:
        pw = access_password()
        if not pw:
            return True
        key, now = self._client_key(), time.time()
        fails = [t for t in self.server.login_failures.get(key, []) if now - t < LOGIN_WINDOW]  # type: ignore[attr-defined]
        if len(fails) >= LOGIN_MAX_FAILS:
            self._send(429, b'{"error": "too many wrong passwords, try again in a few minutes"}', "application/json",
                       {"Retry-After": str(LOGIN_WINDOW)})
            return False
        header = self.headers.get("Authorization") or ""
        if header.startswith("Basic "):
            try:
                _, _, given = base64.b64decode(header[6:]).decode("utf-8").partition(":")
                if hmac.compare_digest(given.encode(), pw.encode()):
                    self.server.login_failures.pop(key, None)  # type: ignore[attr-defined]
                    return True
            except (binascii.Error, UnicodeDecodeError):
                pass
            fails.append(now)
            self.server.login_failures[key] = fails  # type: ignore[attr-defined]
        self._send(401, b'{"error": "password required"}', "application/json",
                   {"WWW-Authenticate": 'Basic realm="ProspectScout", charset="UTF-8"'})
        return False

    def _host_name(self) -> str:
        host = (self.headers.get("Host") or "").strip().lower()
        return host.split("]")[0] + "]" if host.startswith("[") else host.rsplit(":", 1)[0]

    def _request_allowed(self, method: str) -> bool:
        """Host allow-list (DNS rebinding / tunnel without password) and cross-site POST protection."""
        if not access_password() and self._host_name() not in LOCAL_HOSTS:
            self._json({"error": "This address isn't allowed. Set an access password in Settings before sharing "
                                 "the app."}, 403)
            return False
        if method != "POST":
            return True
        if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
            self._json({"error": "Send JSON."}, 415)
            return False
        site = (self.headers.get("Sec-Fetch-Site") or "").lower()
        origin = self.headers.get("Origin")
        if (site and site not in ("same-origin", "none")) or (
                origin and urlparse(origin).netloc.lower() != (self.headers.get("Host") or "").lower()):
            self._json({"error": "Cross-site requests are not allowed."}, 403)
            return False
        return True

    def _dispatch(self, method: str):
        if method == "POST" and not self._read_raw_body():
            return
        if not self._request_allowed(method) or not self._authorized():
            return
        url = urlparse(self.path)
        conn = None
        try:
            conn = db.connect(self.db_path)
            return self._route(method, url.path, parse_qs(url.query), conn)
        except ApiError as exc:
            return self._json({"error": str(exc)}, exc.status)
        except Exception as exc:  # noqa: BLE001 - never leak a stack trace to the browser
            return self._json({"error": f"Something went wrong: {exc.__class__.__name__}"}, 500)
        finally:
            if conn is not None:
                conn.close()

    def do_GET(self):
        self._dispatch("GET")

    def do_HEAD(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    # routes
    def _route(self, method: str, path: str, query: dict, conn):  # noqa: C901 - flat route table
        settings = config.load()
        if method == "GET":
            if path in ("/", "/index.html"):
                return self._file(UI_DIR, "index.html")
            if path.startswith("/static/"):
                return self._file(UI_DIR, path[len("/static/"):])
            if path.startswith("/brand/"):
                return self._file(os.path.join(UI_DIR, "brand"), path[len("/brand/"):])
            if path == "/favicon.ico":
                return self._file(os.path.join(UI_DIR, "brand"), "favicon.png")
            if path == "/api/state":
                sender = settings.get("sender") or {}
                return self._json({
                    "claude_available": claude_runner.claude_argv() is not None,
                    "worker": self.worker.status() if self.worker else
                    {"running": False, "paused": False, "pause_reason": "", "current": None},
                    "counts": db.counts(conn), "rows": _rows(conn, settings),
                    "settings_ready": bool(sender.get("name")),
                })
            if path == "/api/quick":
                return self._json({"checks": db.list_quick(conn)})
            m = re.fullmatch(r"/api/quick/(\d+)", path)
            if m:
                since = (query.get("since") or ["0"])[0]
                return self._json(self._quick_view(conn, int(m.group(1)), int(since) if since.isdigit() else 0))
            if path == "/api/stats":
                version = _data_version(conn)
                cached = self.server.stats_cache  # type: ignore[attr-defined]
                if not cached or cached[0] != version:
                    cached = self.server.stats_cache = (version, _stats(conn))  # type: ignore[attr-defined]
                return self._json(cached[1])
            if path == "/api/settings":
                return self._json(_public_settings(settings))
            m = re.fullmatch(r"/api/prospect/(\d+)", path)
            if m:
                return self._prospect(conn, int(m.group(1)), settings)
            m = re.fullmatch(r"/report/(\d+)", path)
            if m:
                co = db.get_company(conn, int(m.group(1)))
                if not co:
                    raise ApiError(404, "not found")
                cid = (query.get("contact") or [None])[0]
                contact = db.get_contact(conn, int(cid)) if cid and cid.isdigit() else None
                html = report.render_report(co, contact, settings, date.today())
                return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            if path == "/api/export.csv":
                return self._download(exports.rows_csv(_export_rows(conn, settings)), "prospects.csv", "text/csv")
            if path == "/api/export_prospectiq.csv":
                return self._download(exports.prospectiq_csv(db.all_contacts(conn)), "prospectiq-import.csv", "text/csv")
            if path == "/api/export.json":
                return self._download(exports.full_json(db.all_companies(conn), db.all_contacts(conn)),
                                      "prospectscout-export.json", "application/json")
            raise ApiError(404, "not found")

        body = self._body()
        if path == "/api/settings":
            return self._json(_public_settings(config.update(_clean_settings_patch(body))))
        if path == "/api/test_claude":
            return self._json(claude_runner.test_connection())
        if path == "/api/quick":
            if not self.quick:
                raise ApiError(503, "Quick Prospect isn't running.")
            try:
                return self._json({"id": self.quick.start(str(body.get("url") or ""))})
            except ValueError as exc:
                raise ApiError(400, str(exc)) from exc
        m = re.fullmatch(r"/api/quick/(\d+)/add", path)
        if m:
            row = db.get_quick(conn, int(m.group(1)))
            if not row:
                raise ApiError(404, "not found")
            snap = row.get("snapshot_json") or {}
            return self._json(db.add_contact(conn, {"company_domain": row["domain"], "website": row["url"],
                                                    "company_name": (snap.get("title") or "")[:80] or None}))
        if path == "/api/add_one":
            fields = {k: v for k, v in (body.get("fields") or {}).items()
                      if k in importer.FIELDS or k in importer.EXTRA_FIELDS}
            row = importer.normalize_row(fields, {k: k for k in fields})
            if not row.get("company_domain") and not row.get("email"):
                raise ApiError(400, "Add at least a website or a work email.")
            return self._json(db.add_contact(conn, row))
        if path == "/api/import/preview":
            headers, rows = _read_upload(body)
            if not rows:
                raise ApiError(400, "No rows found. Check the file has a header row.")
            return self._json(importer.preview(headers, rows, body.get("mapping") or None))
        if path == "/api/import/commit":
            headers, rows = _read_upload(body)
            normalized = importer.normalize_rows(headers, rows, body.get("mapping") or None)
            batch = db.create_batch(conn, body.get("kind") or "file", len(normalized))
            res = Counter()
            for r in normalized:
                if not any(r.get(k) for k in ("company_domain", "email", "full_name")):
                    continue
                out = db.add_contact(conn, r, batch)
                res["duplicates" if out["duplicate"] else "added"] += 1
                res["companies_new"] += out["company_new"]
                res["needs_website"] += out["needs_website"]
            return self._json({k: res.get(k, 0) for k in ("added", "companies_new", "needs_website", "duplicates")})
        if path in ("/api/worker/pause", "/api/worker/resume"):
            if not self.worker:
                raise ApiError(503, "The research worker isn't running.")
            (self.worker.pause("Paused by you") if path.endswith("pause") else self.worker.resume())
            return self._json({"ok": True, "worker": self.worker.status()})
        m = re.fullmatch(r"/api/company/(\d+)/rerun", path)
        if m:
            db.requeue_company(conn, int(m.group(1)))
            return self._json({"ok": True})
        m = re.fullmatch(r"/api/contact/(\d+)/(regenerate|messages|status|manual_signals|delete|website)", path)
        if m:
            return self._contact_action(conn, int(m.group(1)), m.group(2), body)
        raise ApiError(404, "not found")

    def _download(self, text: str, filename: str, ctype: str):
        self._send(200, text.encode("utf-8"), f"{ctype}; charset=utf-8",
                   {"Content-Disposition": f'attachment; filename="{filename}"'})

    def _prospect(self, conn, contact_id: int, settings: dict):
        contact = db.get_contact(conn, contact_id)
        if not contact:
            raise ApiError(404, "not found")
        co = db.get_company(conn, contact["company_id"]) if contact.get("company_id") else None
        co = co or {}
        tz = co.get("tz")
        followups = []
        if contact.get("contacted_at"):
            sent = datetime.fromtimestamp(int(contact["contacted_at"]), timezone.utc).date()
            spacing = settings.get("followup_spacing") or config.DEFAULTS["followup_spacing"]
            followups = [[a.isoformat(), b.isoformat()] for a, b in timing.followup_dates(sent, spacing, tz)]
        local_now = ""
        if tz:
            try:
                local_now = timing.local_now_label(tz, _now())
            except Exception:  # noqa: BLE001
                local_now = ""
        company_view = {k: v for k, v in co.items() if k != "crawl_json"}
        return self._json({
            "contact": contact, "company": company_view, "facts": co.get("facts_json") or [],
            "analysis": co.get("analysis_json") or {}, "messages": contact.get("messages_json") or {},
            "slots": _slots(tz, settings), "local_now": local_now, "followups": followups,
            "sop": sop.sop_guide(co, contact, settings) if co else None,
            "report_url": f"/report/{co['id']}?contact={contact_id}" if co.get("id") else "",
            "message_keys": list(analyze.MESSAGE_KEYS),
        })

    def _contact_action(self, conn, contact_id: int, action: str, body: dict):
        contact = db.get_contact(conn, contact_id)
        if not contact:
            raise ApiError(404, "not found")
        if action == "website":
            row = importer.normalize_row({"website": str(body.get("website") or "")}, {"website": "website"})
            if not row.get("company_domain"):
                raise ApiError(400, "That doesn't look like a company website (social profiles don't count).")
            return self._json(db.set_contact_website(conn, contact_id, row["website"], row["company_domain"]))
        if action == "delete":
            db.delete_contact(conn, contact_id)
        elif action == "regenerate":
            db.set_contact(conn, contact_id, messages_status="pending")
        elif action == "messages":
            msgs = dict(contact.get("messages_json") or {})
            msgs.update({k: str(v) for k, v in (body.get("messages") or {}).items() if k in analyze.MESSAGE_KEYS})
            db.set_contact(conn, contact_id, messages_json=msgs)
        elif action == "status":
            status = body.get("outreach_status")
            if status not in db.OUTREACH_STATUSES:
                raise ApiError(400, "Unknown status.")
            db.set_contact(conn, contact_id, outreach_status=status)
        elif action == "manual_signals":
            signals = [s for s in body.get("signals") or [] if s in MANUAL_SIGNALS]
            db.set_contact(conn, contact_id, manual_signals_json=signals)
        return self._json({"ok": True})


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self):  # skip HTTPServer's slow reverse-DNS lookup
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = "localhost", self.server_address[1]


def make_server(host: str, port: int, db_path: str, worker, quick=None) -> ThreadingHTTPServer:
    httpd = _Server((host, port), Handler)
    httpd.db_path = db_path  # type: ignore[attr-defined]
    httpd.worker = worker  # type: ignore[attr-defined]
    httpd.quick = quick  # type: ignore[attr-defined]
    httpd.login_failures = {}  # type: ignore[attr-defined]
    httpd.stats_cache = None  # type: ignore[attr-defined]
    return httpd


def main(argv=None) -> None:
    import argparse

    from .quick_runner import QuickRunner
    from .worker import Worker

    p = argparse.ArgumentParser(prog="prospectscout", description="ProspectScout: website-audit prospecting")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8902)
    p.add_argument("--no-browser", action="store_true")
    args = p.parse_args(argv)

    db_path = db.default_path()
    db.connect(db_path).close()
    worker = Worker(db_path)
    worker.start()
    httpd = make_server(args.host, args.port, db_path, worker, quick=QuickRunner(db_path))
    url = f"http://{args.host}:{args.port}/"
    print(f"\n  ProspectScout is running at {url}")
    print("  Messages are never sent automatically. Press Ctrl+C to stop.\n")
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopping…")
    finally:
        httpd.shutdown()
        worker.stop()
