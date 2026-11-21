"""Optional Google PageSpeed Insights (mobile) check. Off by default; enable in Settings."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request

from ..core.checks import Fact

API = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"


def fetch(url: str, api_key: str = "", timeout: int = 60) -> dict:
    params = [("url", url), ("strategy", "mobile"), ("category", "performance"), ("category", "seo"),
              ("category", "accessibility")]
    if api_key:
        params.append(("key", api_key))
    with urllib.request.urlopen(f"{API}?{urllib.parse.urlencode(params)}", timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def facts_from(psi: dict, start_index: int) -> list[Fact]:
    lh = (psi or {}).get("lighthouseResult") or {}
    cats = lh.get("categories") or {}
    audits = lh.get("audits") or {}
    out: list[Fact] = []

    def add(kind, label, evidence):
        out.append(Fact(f"F{start_index + len(out)}", kind, "performance", label, evidence))

    perf = (cats.get("performance") or {}).get("score")
    if perf is not None:
        score = round(perf * 100)
        add("issue" if perf < 0.5 else "ok", f"{'Slow' if perf < 0.5 else 'Good'} mobile speed (score {score}/100)",
            "Google PageSpeed Insights, mobile")
    lcp = (audits.get("largest-contentful-paint") or {}).get("numericValue")
    if lcp is not None and lcp > 4000:
        add("issue", f"Main content takes {lcp / 1000:.1f}s to appear on mobile", "Google PageSpeed (LCP)")
    cls = (audits.get("cumulative-layout-shift") or {}).get("numericValue")
    if cls is not None and cls > 0.25:
        add("issue", "Page layout jumps while loading on mobile", f"Google PageSpeed (CLS {cls:.2f})")
    for key, name in (("seo", "SEO"), ("accessibility", "Accessibility")):
        s = (cats.get(key) or {}).get("score")
        if s is not None and s < 0.7:
            add("issue", f"Low {name} score on mobile ({round(s * 100)}/100)", "Google PageSpeed Insights")
    return out
