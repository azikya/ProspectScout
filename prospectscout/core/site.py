"""The website model: pages, crawl results, HTML extraction and page prioritization.

Pure: this module parses HTML that something else fetched. ``adapters.crawl`` does
the network part and builds these objects.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from urllib.parse import unquote, urljoin, urldefrag, urlparse

ASSET_EXTENSIONS = (
    ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico", ".zip", ".rar", ".doc", ".docx",
    ".xls", ".xlsx", ".ppt", ".pptx", ".mp4", ".mp3", ".mov", ".avi", ".css", ".js", ".xml", ".rss", ".json",
)

# Ordered: earlier groups are fetched first. Includes common non-English words.
PRIORITY_GROUPS: tuple[tuple[str, ...], ...] = (
    ("about", "company", "who-we-are", "ueber-uns", "uber-uns", "über-uns", "a-propos", "quienes-somos",
     "chi-siamo", "من-نحن"),
    ("contact", "kontakt", "contacto", "contactez", "contatti", "اتصل"),
    ("services", "solutions", "what-we-do", "leistungen", "servicios", "prestations", "خدمات"),
    ("products", "product", "produkte", "productos", "produits"),
    ("pricing", "plans", "prices", "preise", "tarifs", "precios", "prezzi", "الأسعار"),
    ("careers", "jobs", "join", "karriere", "carrieres", "carrières", "empleo", "hiring", "lavora", "وظائف"),
    ("blog", "news", "insights", "press", "aktuelles", "actualites", "actualités", "noticias", "أخبار"),
)

_BLOCK_TAGS = {"p", "div", "section", "article", "header", "footer", "nav", "li", "ul", "ol", "br", "tr",
               "td", "th", "h1", "h2", "h3", "h4", "h5", "h6", "main", "aside", "form", "table", "blockquote"}
_SKIP_TAGS = {"script", "style", "noscript", "template", "svg"}


@dataclass
class Page:
    url: str
    final_url: str = ""
    status: int = 0
    elapsed_ms: int = 0
    content_type: str = ""
    size: int = 0
    html: str = ""
    text: str = ""
    title: str = ""
    meta: dict = field(default_factory=dict)
    links: list = field(default_factory=list)
    images: list = field(default_factory=list)
    scripts: list = field(default_factory=list)
    inline_js: str = ""
    forms: list = field(default_factory=list)
    headings: list = field(default_factory=list)
    lang: str = ""

    def to_dict(self, include_html: bool = False) -> dict:
        d = asdict(self)
        if not include_html:
            d.pop("html", None)
        return d


@dataclass
class CrawlResult:
    start_url: str
    final_url: str = ""
    reachable: bool = False
    error: str = ""
    pages: list = field(default_factory=list)
    https: bool = False
    http_redirects_to_https: bool | None = None
    ssl_days_left: int | None = None
    robots_blocked: list = field(default_factory=list)
    link_status: dict = field(default_factory=dict)
    skipped: list = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["pages"] = [p.to_dict() if isinstance(p, Page) else p for p in self.pages]
        return d

    def summary(self, max_chars_per_page: int = 3500, max_total: int = 24000) -> dict:
        """Compact view for the Claude prompt and for storage."""
        pages, budget = [], max_total
        for p in self.pages:
            text = (p.text or "")[:max(0, min(max_chars_per_page, budget))]
            budget -= len(text)
            pages.append({"url": p.final_url or p.url, "title": p.title, "headings": p.headings[:12],
                          "text": text})
        return {"start_url": self.start_url, "final_url": self.final_url, "reachable": self.reachable,
                "error": self.error, "https": self.https, "ssl_days_left": self.ssl_days_left,
                "pages": pages, "skipped": list(self.skipped)}


class _Extractor(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base = base_url
        self.text_parts: list[str] = []
        self.title = ""
        self.meta: dict[str, str] = {}
        self.links: list[str] = []
        self.images: list[str] = []
        self.scripts: list[str] = []
        self.inline_js: list[str] = []
        self.forms: list[dict] = []
        self.headings: list[str] = []
        self.lang = ""
        self._skip = 0
        self._in_title = False
        self._in_script = False
        self._heading: list[str] | None = None

    def _abs(self, href: str) -> str:
        """Absolute URL, or "" for malformed links (e.g. template placeholders like https://[your-domain]/)."""
        try:
            url = urldefrag(urljoin(self.base, href.strip()))[0]
            urlparse(url).port  # raises on invalid ports
            return url
        except ValueError:
            return ""

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "html" and a.get("lang"):
            self.lang = a["lang"].split("-")[0].lower()
        elif tag == "title":
            self._in_title = True
        elif tag == "meta":
            key = (a.get("name") or a.get("property") or a.get("http-equiv") or "").lower()
            if key:
                self.meta[key] = a.get("content", "")
            if a.get("charset"):
                self.meta["charset"] = a["charset"]
        elif tag == "a" and a.get("href"):
            if (u := self._abs(a["href"])):
                self.links.append(u)
        elif tag == "img" and a.get("src"):
            if (u := self._abs(a["src"])):
                self.images.append(u)
        elif tag == "script":
            if a.get("src") and (u := self._abs(a["src"])):
                self.scripts.append(u)
            self._in_script = True
        elif tag == "iframe" and a.get("src"):
            if (u := self._abs(a["src"])):
                self.scripts.append(u)
        elif tag == "form":
            self.forms.append({"action": self._abs(a.get("action") or self.base) or self.base,
                               "method": (a.get("method") or "get").lower(), "inputs": [], "types": [],
                               "attrs": " ".join(a.get(k, "") for k in ("id", "class", "role", "name")).strip()})
        elif tag in ("input", "textarea", "select") and self.forms:
            itype = "textarea" if tag == "textarea" else ("select" if tag == "select" else a.get("type", "text").lower())
            if itype not in ("hidden", "submit", "button", "image", "reset"):
                self.forms[-1]["inputs"].append(a.get("name") or a.get("type") or tag)
                self.forms[-1]["types"].append(itype)
        elif tag in ("h1", "h2", "h3"):
            self._heading = []
        if tag in _SKIP_TAGS:
            self._skip += 1
        if tag in _BLOCK_TAGS:
            self.text_parts.append("\n")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag == "script":
            self._in_script = False
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1
        if tag in ("h1", "h2", "h3") and self._heading is not None:
            h = " ".join("".join(self._heading).split())
            if h:
                self.headings.append(h)
            self._heading = None
        if tag in _BLOCK_TAGS:
            self.text_parts.append("\n")

    def handle_data(self, data):
        if self._in_script:
            if sum(len(s) for s in self.inline_js) < 50_000:
                self.inline_js.append(data)
            return
        if self._in_title:
            self.title += data
            return
        if self._skip:
            return
        self.text_parts.append(data)
        if self._heading is not None:
            self._heading.append(data)


def extract(url: str, html: str) -> dict:
    p = _Extractor(url)
    try:
        p.feed(html or "")
        p.close()
    except Exception:  # noqa: BLE001 - malformed HTML must never crash a crawl
        pass
    lines = [" ".join(line.split()) for line in "".join(p.text_parts).splitlines()]
    text = "\n".join(line for line in lines if line)
    seen: set[str] = set()
    links = [x for x in p.links if not (x in seen or seen.add(x))]
    return {"text": text, "title": " ".join(p.title.split()), "meta": p.meta, "links": links,
            "images": list(dict.fromkeys(p.images)), "scripts": list(dict.fromkeys(p.scripts)),
            "inline_js": "\n".join(p.inline_js), "forms": p.forms, "headings": p.headings, "lang": p.lang}


def site_host(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def canonical_url(url: str) -> str | None:
    """Same page regardless of scheme, www., trailing slash or fragment. None for malformed URLs."""
    try:
        u = urldefrag(url)[0]
        p = urlparse(u)
        port = p.port
    except ValueError:
        return None
    path = p.path.rstrip("/") or "/"
    return f"{site_host(u)}{':' + str(port) if port else ''}{path}" + (f"?{p.query}" if p.query else "")


def is_same_site(url: str, base_url: str) -> bool:
    host = site_host(url)
    try:
        scheme = urlparse(url).scheme
    except ValueError:
        return False
    return bool(host) and host == site_host(base_url) and scheme in ("http", "https")


def is_asset(url: str) -> bool:
    try:
        return urlparse(url).path.lower().endswith(ASSET_EXTENSIONS)
    except ValueError:
        return False


def _rank(url: str) -> int | None:
    path = unquote(urlparse(url).path).lower()
    for i, words in enumerate(PRIORITY_GROUPS):
        if any(w in path for w in words):
            return i
    return None


def prioritize_links(base_url: str, links: list[str], limit: int) -> list[str]:
    base_c = canonical_url(base_url)
    seen = {base_c}
    ranked: list[tuple[int, int, str]] = []
    unranked: list[str] = []
    for i, link in enumerate(links):
        if not link or not is_same_site(link, base_url) or is_asset(link):
            continue
        c = canonical_url(link)
        if c is None or c in seen:
            continue
        seen.add(c)
        r = _rank(link)
        if r is None:
            unranked.append(link)
        else:
            ranked.append((r, i, link))
    ordered = [link for _, _, link in sorted(ranked)] + unranked
    return ordered[:max(0, limit)]
