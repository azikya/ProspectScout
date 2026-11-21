import openpyxl  # noqa: F401  (ensures dev deps present for sheet test)
import pytest

from prospectscout.adapters import crawl, sheet_fetch


def test_extract_basic():
    d = crawl.extract("https://a.com/", '<html lang="en"><title>T</title><h1>Hi</h1><a href="/about">About</a>'
                                        '<form action="/s"><input name="email"></form><script>var x=1</script></html>')
    assert d["title"] == "T" and "https://a.com/about" in d["links"]
    assert d["forms"][0]["inputs"] == ["email"] and d["lang"] == "en" and d["headings"] == ["Hi"]
    assert "var x" not in d["text"] and "var x" in d["inline_js"]


def test_prioritize_same_domain_and_order():
    links = ["https://a.com/blog", "https://a.com/contact", "https://b.com/about", "https://a.com/about-us",
             "https://a.com/x.pdf", "mailto:a@a.com"]
    assert crawl.prioritize_links("https://a.com/", links, 3) == [
        "https://a.com/about-us", "https://a.com/contact", "https://a.com/blog"]


def test_prioritize_non_english_words():
    out = crawl.prioritize_links("https://a.de/", ["https://a.de/impressum", "https://a.de/kontakt",
                                                   "https://a.de/karriere"], 2)
    assert out == ["https://a.de/kontakt", "https://a.de/karriere"]


def test_prioritize_treats_www_as_same_site_and_dedupes():
    out = crawl.prioritize_links("https://www.a.com/", ["https://a.com/about", "https://www.a.com/about/",
                                                        "https://www.a.com/about#team"], 5)
    assert out == ["https://a.com/about"]


def test_prioritize_treats_http_and_https_as_same_page():
    out = crawl.prioritize_links("https://a.com/", ["https://a.com/about-us/", "http://a.com/about-us/",
                                                    "http://a.com/contact"], 5)
    assert out == ["https://a.com/about-us/", "http://a.com/contact"]


def test_prioritize_skips_homepage_under_other_scheme():
    assert crawl.prioritize_links("https://a.com", ["http://a.com/", "http://www.a.com"], 5) == []


def test_crawl_fixture(site_server):
    r = crawl.crawl(site_server + "/site/index.html", allow_private=True, delay=0)
    urls = [p.url for p in r.pages]
    assert r.reachable and len(r.pages) <= 8
    assert any(u.endswith("about.html") for u in urls) and any(u.endswith("careers.html") for u in urls)
    assert r.link_status[site_server + "/site/missing.html"] == 404
    assert r.link_status[site_server + "/site/nope.png"] == 404
    assert not any("private" in u for u in urls) and any("private" in u for u in r.robots_blocked)
    assert not any("external.example" in u for u in r.link_status)


def test_crawl_respects_max_pages(site_server):
    r = crawl.crawl(site_server + "/site/index.html", max_pages=2, allow_private=True, delay=0)
    assert len(r.pages) == 2


def test_unreachable():
    r = crawl.crawl("http://127.0.0.1:1/", allow_private=True, delay=0, timeout=2)
    assert not r.reachable and r.error


def test_private_hosts_blocked_by_default(site_server):
    r = crawl.crawl(site_server + "/site/index.html", delay=0)
    assert not r.reachable and "private" in r.error.lower()


def test_cross_domain_redirect_uses_final_domain(site_server):
    r = crawl.crawl(site_server + "/go", allow_private=True, delay=0)
    assert r.final_url.startswith(("http://127.0.0.2:", "http://localhost:")) and len(r.pages) > 1
    assert all(p.final_url.startswith(r.final_url.split("/site/")[0]) for p in r.pages)


def test_non_html_and_oversized(site_server):
    r = crawl.crawl(site_server + "/big", allow_private=True, delay=0)
    assert r.reachable and r.pages[0].size <= 2_000_000
    r2 = crawl.crawl(site_server + "/site/index.html", allow_private=True, delay=0)
    assert "brochure.pdf" not in " ".join(p.url for p in r2.pages)


def test_summary_truncates(site_server):
    s = crawl.crawl(site_server + "/site/index.html", allow_private=True, delay=0).summary(
        max_chars_per_page=50, max_total=120)
    assert sum(len(p["text"]) for p in s["pages"]) <= 120 and s["final_url"]


def test_progress_callback(site_server):
    seen = []
    crawl.crawl(site_server + "/site/index.html", allow_private=True, delay=0, progress=seen.append)
    assert "Reading website…" in seen and "Checking links…" in seen


def test_sheet_fetch_downloads_csv(site_server):
    h, rows = sheet_fetch.fetch_url_rows(site_server + "/prospects.csv", allow_private=True)
    assert h == ["Name", "Website"] and rows[0]["Website"] == "acme.com"


def test_sheet_fetch_rejects_html_page(site_server):
    with pytest.raises(sheet_fetch.SheetFetchError):
        sheet_fetch.fetch_url_rows(site_server + "/site/about.html", allow_private=True)


# --- final-review fixes ---------------------------------------------------------------------

def test_extract_survives_malformed_href():
    d = crawl.extract("https://acme.com/", '<p>Intro</p><a href="https://[your-domain]/about">x</a><p>Call +44 20 7946 0958</p>'
                                           '<form class="contact"><textarea name="message"></textarea></form>')
    assert "Call +44" in d["text"] and d["forms"] and d["links"] == []


def test_prioritize_skips_invalid_port():
    assert crawl.prioritize_links("https://acme.com/", ["https://acme.com:99999/x", "https://acme.com/about"], 5) == [
        "https://acme.com/about"]


def test_head_404_but_get_ok_is_not_broken(site_server):
    assert crawl._link_status(site_server + "/headfail", 5, True) == 200


def test_summary_records_skipped_content(site_server):
    r = crawl.CrawlResult(start_url="https://a.com", reachable=True,
                          skipped=[{"url": "https://a.com", "content_type": "application/pdf"}])
    assert r.summary()["skipped"][0]["content_type"] == "application/pdf"


def test_sheet_fetch_blocks_private_addresses_by_default(site_server):
    with pytest.raises(sheet_fetch.SheetFetchError) as e:
        sheet_fetch.fetch_url_rows(site_server + "/prospects.csv", allow_private=False)
    assert "private" in str(e.value).lower()


# --- deferred minors -----------------------------------------------------------------------

def test_undeclared_windows_1252_page_decodes_without_replacement_chars():
    raw = "<html><body><p>Our client’s café – trusted</p></body></html>".encode("cp1252")
    text = crawl._decode(raw, "text/html")
    assert "�" not in text and "client’s" in text


def test_late_meta_charset_is_honoured():
    raw = ("<html><head>" + "<!-- pad -->" * 500 + '<meta charset="windows-1252"></head><body>café</body></html>').encode("cp1252")
    assert "café" in crawl._decode(raw, "text/html")


def test_robots_rules_for_our_token_apply():
    rp = crawl.robots_from_response(200, "User-agent: ProspectScout\nDisallow: /\n")
    assert not crawl.robots_allowed(rp, "https://a.com/about")


def test_robots_401_403_means_disallow_all_but_404_allows():
    assert not crawl.robots_allowed(crawl.robots_from_response(403, ""), "https://a.com/x")
    assert crawl.robots_allowed(crawl.robots_from_response(404, ""), "https://a.com/x")


def test_connected_peer_is_checked_against_private_ranges(site_server, monkeypatch):
    monkeypatch.setattr(crawl, "check_host", lambda url, allow_private: None)  # simulate a DNS-rebinding lie
    with pytest.raises(crawl.FetchError) as e:
        crawl.fetch(site_server + "/site/index.html", allow_private=False)
    assert "private" in str(e.value).lower()


def test_quick_mode_crawl_accepts_deep_false(site_server):
    r = crawl.crawl(site_server + "/site/index.html", max_pages=3, max_link_checks=0, allow_private=True, delay=0,
                    deep=False)
    assert r.reachable and len(r.pages) <= 3 and r.ssl_days_left is None and r.http_redirects_to_https is None
    assert set(r.link_status) <= {p.final_url for p in r.pages} | {site_server + "/site/missing.html"}


# --- compressed responses (found live on a real site that always sends gzip) -----------------

def test_gzip_response_is_decompressed(site_server):
    r = crawl.crawl(site_server + "/gzipped", allow_private=True, delay=0, max_link_checks=0)
    assert r.pages[0].title == "Gzip Co" and "Compressed hello" in r.pages[0].text


def test_gzip_without_header_is_detected(site_server):
    r = crawl.crawl(site_server + "/gzip-noheader", allow_private=True, delay=0, max_link_checks=0)
    assert r.pages[0].title == "Gzip Co"


def test_unsupported_compression_is_skipped_not_garbage(site_server):
    r = crawl.crawl(site_server + "/brotli", allow_private=True, delay=0, max_link_checks=0)
    assert not r.pages and r.skipped and "br" in r.skipped[0]["content_type"]


def test_gzip_bomb_is_capped():
    import gzip
    bomb = gzip.compress(b"<p>" + b"a" * (30 * 1024 * 1024) + b"</p>")
    out = crawl._decompress(bomb, "gzip")
    assert out is not None and len(out) <= crawl.MAX_BYTES
