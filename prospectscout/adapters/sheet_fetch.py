"""Download a public Google Sheet / CSV / XLSX link and hand the bytes to the importer.

Uses the crawler's SSRF guard (also on redirects), so a shared instance can't be used
to read services on the host's localhost or LAN.
"""

from __future__ import annotations

import urllib.request

from ..core import importer
from .crawl import FetchError, check_host, guarded_opener

MAX_BYTES = 20 * 1024 * 1024
UA = "Mozilla/5.0 (compatible; ProspectScout/1.0)"


class SheetFetchError(Exception):
    pass


def fetch_url_rows(url: str, timeout: int = 30, allow_private: bool = False) -> tuple[list[str], list[dict]]:
    target = importer.sheet_url_to_csv(url)
    if not target.lower().startswith(("http://", "https://")):
        raise SheetFetchError("Paste a full http(s) link to a public sheet or CSV file.")
    try:
        check_host(target, allow_private)
    except FetchError as exc:
        raise SheetFetchError(f"That link can't be used ({exc}).") from exc
    req = urllib.request.Request(target, headers={"User-Agent": UA})
    try:
        with guarded_opener(allow_private).open(req, timeout=timeout) as resp:
            data = resp.read(MAX_BYTES + 1)
            ctype = (resp.headers.get("Content-Type") or "").lower()
    except FetchError as exc:
        raise SheetFetchError(f"That link can't be used ({exc}).") from exc
    except Exception as exc:  # noqa: BLE001 - surface any network problem as one friendly error
        raise SheetFetchError(f"Couldn't download that link ({exc}). Is the sheet shared publicly?") from exc
    if len(data) > MAX_BYTES:
        raise SheetFetchError("That file is larger than 20 MB.")
    if "text/html" in ctype and b"<html" in data[:2000].lower():
        raise SheetFetchError("That link opened a web page, not a sheet. Share it as 'Anyone with the link'.")
    return importer.read_bytes(data)
