"""Polite, bounded website fetcher.

Reads a handful of a prospect's pages like a visitor would: same-site only, honors
robots.txt, pauses between requests, caps page count / body size / link checks, never
submits forms. Blocks private/loopback addresses (SSRF guard, ported from ProspectIQ's
``SafeHttpFetcher`` idea) unless ``allow_private`` is set (tests use a local server).
"""

from __future__ import annotations

import ipaddress
import re
import socket
import ssl
import time
import urllib.error
import urllib.request
import urllib.robotparser
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import urlparse

from ..core.site import (CrawlResult, Page, canonical_url, extract, is_asset, is_same_site,  # re-exported
                         prioritize_links)

__all__ = ["crawl", "fetch", "extract", "prioritize_links", "Page", "CrawlResult", "FetchError"]

UA = "Mozilla/5.0 (compatible; ProspectScout/1.0)"
ROBOTS_TOKEN = "ProspectScout"  # the product token robots.txt rules are matched against
MAX_BYTES = 2_000_000
_HTML_TYPES = ("text/html", "application/xhtml+xml")
_CHARSET_RE = re.compile(rb"""<meta[^>]+charset=["']?([\w-]+)""", re.I)


class FetchError(Exception):
    pass


@dataclass
class Fetched:
    status: int
    final_url: str
    content_type: str
    body: str
    elapsed_ms: int
    size: int


def check_host(url: str, allow_private: bool) -> None:
    """SSRF guard: refuse anything that resolves to a private, loopback, link-local or reserved address."""
    if allow_private:
        return
    try:
        parsed = urlparse(url)
        host = parsed.hostname
    except ValueError as exc:
        raise FetchError("Invalid URL") from exc
    if parsed.scheme not in ("http", "https") or not host:
        raise FetchError("Invalid URL")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise FetchError(f"Couldn't find {host} (DNS lookup failed)") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global or ip.is_multicast:
            raise FetchError(f"Blocked private or local address for {host}")


_check_host = check_host  # backwards-compatible alias


class _GuardedRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, allow_private: bool):
        self.allow_private = allow_private

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_host(newurl, self.allow_private)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def guarded_opener(allow_private: bool) -> urllib.request.OpenerDirector:
    """An opener whose redirects are re-checked by the SSRF guard."""
    return urllib.request.build_opener(_GuardedRedirect(allow_private))


def _decode(raw: bytes, content_type: str) -> str:
    """Declared charset (header, then <meta> anywhere in the first 64 KB); otherwise strict UTF-8 with a
    Windows-1252 fallback, so legacy pages don't show fake "broken characters"."""
    m = re.search(r"charset=([\w-]+)", content_type or "", re.I)
    charset = m.group(1) if m else None
    if not charset:
        mm = _CHARSET_RE.search(raw[:65536])
        charset = mm.group(1).decode("ascii", "ignore") if mm else None
    if charset:
        try:
            return raw.decode(charset, errors="replace")
        except LookupError:
            pass
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def _peer_ip(resp) -> str | None:
    """The IP address the connection actually reached (best effort; CPython internals)."""
    try:
        return resp.fp.raw._sock.getpeername()[0]
    except Exception:  # noqa: BLE001
        return None


def fetch(url: str, timeout: int = 15, max_bytes: int = MAX_BYTES, allow_private: bool = False,
          method: str = "GET") -> Fetched:
    check_host(url, allow_private)
    opener = guarded_opener(allow_private)
    req = urllib.request.Request(url, method=method, headers={"User-Agent": UA, "Accept": "text/html,*/*"})
    t0 = time.perf_counter()
    try:
        with opener.open(req, timeout=timeout) as resp:
            peer = _peer_ip(resp)  # re-check after connecting: defeats DNS rebinding between check and connect
            if not allow_private and peer:
                ip = ipaddress.ip_address(peer.split("%")[0])
                if not ip.is_global or ip.is_multicast:
                    raise FetchError(f"Blocked private or local address ({peer})")
            ctype = resp.headers.get("Content-Type") or ""
            raw = resp.read(max_bytes) if method == "GET" else b""
            return Fetched(resp.status, resp.geturl(), ctype.lower(), _decode(raw, ctype),
                           int((time.perf_counter() - t0) * 1000), len(raw))
    except urllib.error.HTTPError as exc:
        return Fetched(exc.code, exc.geturl() or url, (exc.headers.get("Content-Type") or "").lower()
                       if exc.headers else "", "", int((time.perf_counter() - t0) * 1000), 0)
    except FetchError:
        raise
    except Exception as exc:  # noqa: BLE001 - DNS, TLS, timeouts, resets
        raise FetchError(str(getattr(exc, "reason", exc)) or exc.__class__.__name__) from exc


def _link_status(url: str, timeout: int, allow_private: bool) -> int:
    try:
        r = fetch(url, timeout=timeout, allow_private=allow_private, method="HEAD")
        if r.status >= 400:  # many servers mishandle HEAD; confirm with a real GET before calling it broken
            r = fetch(url, timeout=timeout, max_bytes=1024, allow_private=allow_private)
        return r.status
    except FetchError:
        return 0


def _ssl_days_left(host: str, port: int = 443, timeout: int = 10) -> int | None:
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as s:
                not_after = s.getpeercert()["notAfter"]
        expires = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        return (expires - datetime.now(timezone.utc)).days
    except Exception:  # noqa: BLE001 - expired/invalid certs show up as https failures elsewhere
        return None


def robots_from_response(status: int, body: str) -> urllib.robotparser.RobotFileParser | None:
    """200 → parsed rules; 401/403 → disallow everything (same as Python's robotparser); otherwise allow all."""
    rp = urllib.robotparser.RobotFileParser()
    if status in (401, 403):
        rp.disallow_all = True
        return rp
    if status != 200:
        return None
    rp.parse((body or "").splitlines())
    return rp


def robots_allowed(rp: urllib.robotparser.RobotFileParser | None, url: str) -> bool:
    return rp is None or rp.can_fetch(ROBOTS_TOKEN, url)


def _robots(base_url: str, timeout: int, allow_private: bool) -> urllib.robotparser.RobotFileParser | None:
    p = urlparse(base_url)
    try:
        r = fetch(f"{p.scheme}://{p.netloc}/robots.txt", timeout=timeout, max_bytes=200_000,
                  allow_private=allow_private)
    except FetchError:
        return None
    return robots_from_response(r.status, r.body)


def _page_from(url: str, r: Fetched) -> Page:
    d = extract(r.final_url, r.body)
    return Page(url=url, final_url=r.final_url, status=r.status, elapsed_ms=r.elapsed_ms,
                content_type=r.content_type, size=r.size, html=r.body, **d)


def crawl(website: str, max_pages: int = 8, max_link_checks: int = 40, allow_private: bool = False,
          delay: float = 0.5, timeout: int = 15, progress: Callable[[str], None] | None = None) -> CrawlResult:
    say = progress or (lambda _msg: None)
    start = website.strip()
    if "://" not in start:
        start = "https://" + start
    result = CrawlResult(start_url=start)

    say("Reading website…")
    try:
        home = fetch(start, timeout=timeout, allow_private=allow_private)
    except FetchError as exc:
        if start.startswith("https://"):  # some small sites only answer on http
            try:
                home = fetch("http://" + start[len("https://"):], timeout=timeout, allow_private=allow_private)
            except FetchError:
                result.error = str(exc)
                return result
        else:
            result.error = str(exc)
            return result
    result.final_url = home.final_url
    if home.status >= 400:
        result.error = f"Homepage returned HTTP {home.status}"
        return result
    result.reachable = True
    result.https = home.final_url.startswith("https://")
    result.link_status[home.final_url] = home.status
    if not any(t in home.content_type for t in _HTML_TYPES):
        result.skipped.append({"url": home.final_url, "content_type": home.content_type})
        return result
    pages = [_page_from(start, home)]

    robots = _robots(home.final_url, timeout, allow_private)

    def allowed(u: str) -> bool:
        return robots_allowed(robots, u)

    candidates = prioritize_links(home.final_url, pages[0].links, len(pages[0].links))
    for link in candidates:
        if len(pages) >= max_pages:
            break
        if not allowed(link):
            result.robots_blocked.append(link)
            continue
        if delay:
            time.sleep(delay)
        try:
            r = fetch(link, timeout=timeout, allow_private=allow_private)
        except FetchError:
            result.link_status[link] = 0
            continue
        result.link_status[link] = r.status
        if r.status >= 400:
            continue
        if not any(t in r.content_type for t in _HTML_TYPES):
            result.skipped.append({"url": link, "content_type": r.content_type})
            continue
        pages.append(_page_from(link, r))
    result.pages = pages
    for link in candidates:  # report robots-blocked links even beyond the page budget
        if link not in result.robots_blocked and not allowed(link):
            result.robots_blocked.append(link)

    say("Checking links…")
    known = {canonical_url(u) for u in result.link_status}
    to_check: list[str] = []
    for p in pages:
        for u in [*p.links, *p.images]:
            key = canonical_url(u)
            if (key is not None and key not in known and is_same_site(u, home.final_url) and allowed(u)
                    and not (is_asset(u) and u not in p.images)):
                known.add(key)
                to_check.append(u)
    to_check = to_check[:max_link_checks]
    with ThreadPoolExecutor(max_workers=5) as pool:  # a few at a time; still gentle on the site
        for u, status in zip(to_check, pool.map(lambda x: _link_status(x, timeout, allow_private), to_check)):
            result.link_status[u] = status

    host = urlparse(home.final_url).hostname or ""
    if result.https:
        result.ssl_days_left = _ssl_days_left(host)
        try:
            plain = fetch(f"http://{urlparse(home.final_url).netloc}/", timeout=timeout,
                          max_bytes=1024, allow_private=allow_private)
            result.http_redirects_to_https = plain.final_url.startswith("https://")
        except FetchError:
            result.http_redirects_to_https = None
    return result
