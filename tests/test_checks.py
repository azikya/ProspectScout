import json
from datetime import date
from pathlib import Path

from prospectscout.adapters import crawl, pagespeed
from prospectscout.core import checks
from prospectscout.core.site import CrawlResult, Page, extract

TODAY = date(2026, 11, 21)


def mk(pages_html: dict, **overrides) -> CrawlResult:
    pages = []
    for url, html in pages_html.items():
        pages.append(Page(url=url, final_url=url, status=200, elapsed_ms=300, content_type="text/html",
                          size=len(html), html=html, **extract(url, html)))
    first = next(iter(pages_html))
    r = CrawlResult(start_url=first, final_url=first, reachable=True, pages=pages, https=True,
                    http_redirects_to_https=True, ssl_days_left=200)
    for k, v in overrides.items():
        setattr(r, k, v)
    return r


def labels(facts):
    return " | ".join(f.label.lower() for f in facts)


def test_fixture_site_facts(site_server):
    facts = checks.run_checks(crawl.crawl(site_server + "/site/index.html", allow_private=True, delay=0), today=TODAY)
    text = labels(facts)
    assert any(f.kind == "issue" and "missing.html" in f.evidence for f in facts)
    assert any(f.kind == "issue" and "nope.png" in f.evidence for f in facts)
    assert "viewport" in text and "2019" in text
    assert "placeholder" in text
    assert "broken characters" in text
    assert "plan" in text and "no price" in text
    assert any(f.kind == "ok" and "contact form" in f.label.lower() for f in facts)
    assert any(f.signal == "hiring_tech_roles" and "backend engineer" in f.evidence.lower() for f in facts)
    assert any(f.kind == "info" and "google analytics" in f.label.lower() for f in facts)


def test_unreachable_site_gives_single_issue():
    facts = checks.run_checks(CrawlResult(start_url="https://x.com", error="DNS lookup failed"))
    assert len(facts) == 1 and facts[0].kind == "issue" and "unreachable" in facts[0].label.lower()


def test_chat_widget_detected_and_missing():
    assert any(f.kind == "ok" and "live chat" in f.label.lower()
               for f in checks.run_checks(mk({"https://a.com/": '<script src="https://embed.tawk.to/x"></script>'})))
    assert any(f.kind == "missing" and "live chat" in f.label.lower()
               for f in checks.run_checks(mk({"https://a.com/": "<p>hello</p>"})))


def test_inline_chat_loader_detected():
    html = '<script>var s=document.createElement("script");s.src="https://embed.tawk.to/abc";</script>'
    assert any(f.kind == "ok" and "live chat" in f.label.lower() for f in checks.run_checks(mk({"https://a.com/": html})))


def test_crm_signals():
    f = checks.run_checks(mk({"https://a.com/": '<form action="https://webto.salesforce.com/servlet/servlet.WebToLead">'
                                                '<input name="email"></form><script src="https://pi.pardot.com/pd.js"></script>'}))
    assert any("salesforce" in x.label.lower() for x in f) and any("pardot" in x.label.lower() for x in f)


def test_no_crm_is_info_not_claim():
    f = checks.run_checks(mk({"https://a.com/": "<p>hello</p>"}))
    crm = [x for x in f if x.category == "crm"]
    assert crm and all(x.kind == "info" for x in crm)


def test_booking_and_whatsapp_detected():
    html = '<a href="https://calendly.com/acme/30min">Book</a><a href="https://wa.me/15125550100">WhatsApp</a>'
    t = labels([x for x in checks.run_checks(mk({"https://a.com/": html})) if x.kind == "ok"])
    assert "online booking" in t and "whatsapp" in t


def test_ssl_and_https_issues():
    f = checks.run_checks(mk({"https://a.com/": "<p>x</p>"}, ssl_days_left=10, http_redirects_to_https=False))
    assert any(x.kind == "issue" and "ssl" in x.label.lower() for x in f)
    assert any(x.kind == "issue" and "https" in x.label.lower() for x in f)


def test_no_https_issue():
    f = checks.run_checks(mk({"http://a.com/": "<p>x</p>"}, https=False, http_redirects_to_https=None, ssl_days_left=None))
    assert any(x.kind == "issue" and "https" in x.label.lower() for x in f)


def test_old_jquery_and_wordpress_detected():
    html = ('<meta name="generator" content="WordPress 5.2.1"><script src="https://a.com/wp-includes/js/jquery/jquery-1.12.4.min.js">'
            '</script><link href="/wp-content/themes/x.css">')
    t = labels(checks.run_checks(mk({"https://a.com/": html})))
    assert "wordpress 5.2.1" in t and "jquery 1.12" in t


def test_inactive_blog():
    pages = {"https://a.com/": '<a href="/blog">Blog</a>', "https://a.com/blog": "<h1>News</h1><p>Posted March 2021</p>"}
    assert any(x.kind == "issue" and "blog" in x.label.lower() for x in checks.run_checks(mk(pages), today=TODAY))


def test_funding_and_expansion_signals():
    pages = {"https://a.com/": "<p>x</p>",
             "https://a.com/news": "<p>Acme raised a $5M Series A round led by Foo.</p><p>We are opening a new office in Austin.</p>"}
    sig = {f.signal for f in checks.run_checks(mk(pages), today=TODAY)}
    assert {"funding_news", "expansion_launch"} <= sig


def test_non_english_missing_is_low_confidence(site_server):
    facts = checks.run_checks(crawl.crawl(site_server + "/site_de/index.html", allow_private=True, delay=0))
    missing = [f for f in facts if f.kind == "missing"]
    assert missing and all(f.confidence == "low" for f in missing)
    assert checks.site_language(crawl.crawl(site_server + "/site_de/index.html", allow_private=True, delay=0)) == "de"


def test_ids_stable_and_sequential():
    r = mk({"https://a.com/": "<p>x</p>"})
    a, b = checks.run_checks(r), checks.run_checks(r)
    assert [f.id for f in a] == [f.id for f in b] and a[0].id == "F1"
    assert [f.id for f in a] == [f"F{i}" for i in range(1, len(a) + 1)]


def test_fact_to_dict_shape():
    d = checks.run_checks(mk({"https://a.com/": "<p>x</p>"}))[0].to_dict()
    assert set(d) == {"id", "kind", "category", "label", "evidence", "confidence", "signal"}


# --- regressions found on a real WordPress site (azikya.com) ------------------------------------

def _ok_labels(html_pages):
    return labels([x for x in checks.run_checks(mk(html_pages), today=TODAY) if x.kind == "ok"])


def test_contact_form_detected_by_input_types():
    html = ('<form id="wpforms-form-1" class="wpforms-form" method="post"><input type="text" name="wpforms[fields][0]">'
            '<input type="email" name="wpforms[fields][1]"><input type="tel" name="wpforms[fields][3]">'
            '<textarea name="wpforms[fields][5]"></textarea></form>')
    assert "contact form" in _ok_labels({"https://a.com/contact/": html})


def test_search_form_is_not_contact_form():
    html = '<form role="search" class="search-form"><input type="search" name="s"></form>'
    facts = checks.run_checks(mk({"https://a.com/": html}))
    assert any(x.kind == "missing" and "contact form" in x.label.lower() for x in facts)


def test_store_needs_cart_links_not_script_references():
    scripts_only = '<script>var wc={"cart_url":"/cart","ajax":"add_to_cart"}; /* woocommerce checkout */</script>'
    assert "online store" not in labels(checks.run_checks(mk({"https://a.com/": scripts_only})))
    with_cart = '<a href="/cart">Cart</a><button>Add to cart</button>'
    assert "online store" in labels(checks.run_checks(mk({"https://a.com/": with_cart})))


def test_homepage_career_nav_link_is_not_hiring():
    home = '<nav><a href="/career/">Career</a></nav><h2>UI/UX &amp; Logo Design</h2><p>Web development services.</p>'
    assert not any(f.signal == "hiring_tech_roles" for f in checks.run_checks(mk({"https://a.com/": home})))
    pages = {"https://a.com/": home, "https://a.com/career/": "<h1>Careers</h1><p>Open position: Senior Backend Engineer</p>"}
    assert any(f.signal == "hiring_tech_roles" for f in checks.run_checks(mk(pages)))


def test_generator_label_is_trimmed():
    html = '<meta name="generator" content="Elementor 3.22.1; features: e_optimized_assets_loading, e_font_icon_svg">'
    t = [x.label for x in checks.run_checks(mk({"https://a.com/": html})) if x.category == "tech"]
    assert "Built with Elementor 3.22.1" in t


def test_dummy_phone_number_flagged():
    t = labels(checks.run_checks(mk({"https://a.com/": "<footer>Call us: +91-9876543210</footer>"})))
    assert "placeholder phone number" in t


def test_html_entity_apostrophe_is_not_broken_characters():
    t = labels(checks.run_checks(mk({"https://a.com/": "<p>We value our client&#8217;s trust.</p>"})))
    assert "broken characters" not in t


def test_pagespeed_facts():
    psi = json.loads((Path(__file__).parent / "fixtures" / "pagespeed_sample.json").read_text())
    f = pagespeed.facts_from(psi, start_index=50)
    assert f[0].id == "F50"
    assert any(x.kind == "issue" and "mobile speed" in x.label.lower() for x in f)
    assert any(x.kind == "issue" and "layout" in x.label.lower() for x in f)


# --- final-review fixes ---------------------------------------------------------------------

def test_national_phone_formats_and_tel_links_detected():
    for html in ('<a href="tel:+442079460958">Call us</a>', "<p>Call 020 7946 0958</p>", "<p>Ring (02) 9876 5432</p>",
                 "<p>Tel. 030 12345678</p>", "<p>Freecall 1800 123 4567</p>"):
        facts = checks.run_checks(mk({"https://a.com/": html}))
        assert any(f.kind == "ok" and "phone" in f.label.lower() for f in facts), html


def test_embedded_forms_count_as_contact_form():
    for html in ('<script src="//js.hsforms.net/forms/v2.js"></script><script>hbspt.forms.create({portalId:"1"})</script>',
                 '<iframe src="https://form.jotform.com/123"></iframe>', '<iframe src="https://docs.google.com/forms/d/e/x/viewform"></iframe>',
                 '<div data-tf-widget="abc"></div><script src="//embed.typeform.com/next/embed.js"></script>'):
        assert "contact form" in _ok_labels({"https://a.com/contact": html}), html


def test_tag_manager_makes_js_widget_missing_facts_low_confidence():
    html = '<script src="https://www.googletagmanager.com/gtm.js?id=GTM-X"></script><p>hello</p>'
    facts = checks.run_checks(mk({"https://a.com/": html}))
    chat = next(f for f in facts if "live chat" in f.label.lower())
    assert chat.kind == "missing" and chat.confidence == "low"


def test_only_404_410_and_5xx_count_as_broken():
    r = mk({"https://acme.com/": "<p>x</p>"}, link_status={"https://acme.com/members": 401, "https://acme.com/q": 429,
                                                         "https://acme.com/t": 0, "https://acme.com/f": 403,
                                                         "https://acme.com/gone": 404, "https://acme.com/err": 503})
    facts = checks.run_checks(r)
    broken = [f.evidence for f in facts if f.kind == "issue" and "broken" in f.label.lower()]
    assert any("/gone" in e for e in broken) and any("/err" in e for e in broken)
    assert not any(x in " ".join(broken) for x in ("/members", "/q ", "/t ", "/f "))
    assert any(f.kind == "info" and "couldn't check" in f.label.lower() for f in facts)


def test_non_html_homepage_is_info_not_unreachable():
    r = CrawlResult(start_url="https://a.com", final_url="https://a.com/x.pdf", reachable=True,
                    skipped=[{"url": "https://a.com/x.pdf", "content_type": "application/pdf"}])
    facts = checks.run_checks(r)
    assert len(facts) == 1 and facts[0].kind == "info" and "application/pdf" in facts[0].label
