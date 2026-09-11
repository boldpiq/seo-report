"""
Crawl-integrity gate.

The check no rendering-based audit tool performs: does every URL in the sitemap
return its own document? A site can score well in Lighthouse while being
structurally incapable of ranking a single page, because Lighthouse renders
JavaScript and tests one URL at a time.

A route that returns the same hash as a URL that cannot exist has no page behind
it. That is always the cause, and creating the page is always the fix.

Stdlib only.
"""

import hashlib
import html as html_mod
import re
import urllib.error
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126 Safari/537.36")
NONEXISTENT = "/boldpiq-gate-does-not-exist-9f3a2c"


def fetch(url, timeout=30):
    """Return (status, html). Never raises for HTTP errors — the status matters."""
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Cache-Control": "no-cache", "Pragma": "no-cache"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as ex:
        try:
            body = ex.read().decode("utf-8", "replace")
        except Exception:
            body = ""
        return ex.code, body
    except Exception:
        return 0, ""


def sitemap_urls(base):
    """Every non-anchor <loc> in sitemap.xml. Empty list if there isn't one."""
    _, xml = fetch(base.rstrip("/") + "/sitemap.xml")
    return [u.strip() for u in re.findall(r"<loc>([^<]+)</loc>", xml or "")
            if "#" not in u]


def _text_words(html):
    body = re.sub(r"<script.*?</script>|<style.*?</style>|<nav.*?</nav>|<footer.*?</footer>",
                  "", html, flags=re.S | re.I)
    return len(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)).split())


def page_facts(url, html, term=None):
    """Everything the report needs from one page, from the raw HTML."""
    title = re.findall(r"<title[^>]*>(.*?)</title>", html, re.S)
    canon = re.findall(r'rel=["\']canonical["\'][^>]*href=["\']([^"\']+)', html)
    robots = re.findall(r'<meta[^>]+name=["\']robots["\'][^>]+content=["\']([^"\']*)', html, re.I)
    return {
        "url": url,
        "hash": hashlib.md5(html.encode()).hexdigest(),
        # Unescaped, or "Vintage &amp; Thrift" prints literally in the report.
        "title": html_mod.unescape(re.sub(r"\s+", " ", title[0]).strip()) if title else "",
        "h1_count": len(re.findall(r"<h1[\s>]", html, re.I)),
        "h2_count": len(re.findall(r"<h2[\s>]", html, re.I)),
        "words": _text_words(html),
        "canonical": canon[0] if canon else "",
        "canonical_self": bool(canon) and canon[0].rstrip("/") == url.rstrip("/"),
        "schema_blocks": len(re.findall(r"application/ld\+json", html)),
        "schema_types": sorted(set(re.findall(r'"@type"\s*:\s*"([^"]+)"', html))),
        "has_agg_rating": "aggregateRating" in html,
        "has_address": bool(re.search(r"addressLocality", html, re.I)),
        "tel_links": len(re.findall(r'href=["\']tel:', html, re.I)),
        "wa_links": len(re.findall(r"wa\.me", html, re.I)),
        "hidden_text": bool(re.search(r"left:\s*-9999px|crawler-content", html, re.I)),
        "term_count": len(re.findall(re.escape(term), html, re.I)) if term else None,
        # Lowercased text, kept only for per-area mention counting. Stripped before
        # the JSON is written — it would multiply the file size for no benefit.
        "_haystack": re.sub(r"<[^>]+>", " ", html).lower(),
        "_haystack_raw": html,
    }


def run(base, term=None, extra_paths=(), urls=None, prefetched=None):
    """Crawl every page and evaluate the gate. Returns a dict.

    urls        the pages to evaluate. Given by the whole-site discovery (sitemap
                indexes, robots.txt sitemaps, and the site's own links); when
                absent, falls back to the flat /sitemap.xml read below.
    prefetched  {url: (status, html)} already fetched by that discovery, so no
                page is downloaded twice.
    """
    base = base.rstrip("/")
    ns, nhtml = fetch(base + NONEXISTENT)
    fallback_hash = hashlib.md5(nhtml.encode()).hexdigest() if nhtml else None

    urls = list(urls) if urls is not None else sitemap_urls(base)
    prefetched = prefetched or {}
    for p in extra_paths:
        u = base + p
        if u not in urls:
            urls.append(u)

    pages, seen = [], {}
    for u in urls:
        status, html = prefetched[u] if u in prefetched else fetch(u)
        if not html:
            continue
        f = page_facts(u, html, term)
        f["status"] = status
        f["is_shell"] = (fallback_hash is not None and f["hash"] == fallback_hash)
        pages.append(f)
        seen.setdefault(f["hash"], []).append(u)

    titles = [p["title"] for p in pages if p["title"]]
    dup_titles = sorted({t for t in titles if titles.count(t) > 1})
    dup_hashes = {h: v for h, v in seen.items() if len(v) > 1}

    checks = [
        ("unique_documents",
         "Every page is its own document",
         not dup_hashes,
         f"{len(seen)} unique of {len(pages)} pages"),
        ("unique_titles",
         "Every page has its own title",
         not dup_titles and all(p["title"] for p in pages),
         f"{len(set(titles))} unique of {len(pages)}"),
        ("real_404",
         "Unknown URLs return 404, not 200",
         ns == 404,
         f"HTTP {ns or '—'}"),
        ("no_hidden_text",
         "No hidden or off-screen text",
         not any(p["hidden_text"] for p in pages),
         "clean" if not any(p["hidden_text"] for p in pages) else "found"),
        ("one_h1",
         "Exactly one H1 per page",
         all(p["h1_count"] == 1 for p in pages),
         f"{sum(1 for p in pages if p['h1_count'] == 1)} of {len(pages)}"),
        ("self_canonical",
         "Self-referencing canonical on every page",
         all(p["canonical_self"] for p in pages),
         f"{sum(1 for p in pages if p['canonical_self'])} of {len(pages)}"),
        ("no_unearned_reviews",
         "No unearned review markup",
         not any(p["has_agg_rating"] for p in pages),
         "clean" if not any(p["has_agg_rating"] for p in pages) else "found"),
    ]

    # all() over an empty list is True, so with nothing crawled six of these
    # seven "pass" and a dead domain scores 86%. A check with nothing to check
    # has not passed — it did not run, and the score is withheld entirely.
    if not pages:
        checks = [(i, l, False, "not measured — no pages were crawled")
                  for i, l, _, _ in checks]

    return {
        "base": base,
        "measured": bool(pages),
        "fallback_hash": fallback_hash,
        "pages": pages,
        "page_count": len(pages),
        "unique_documents": len(seen),
        "duplicate_groups": {h: v for h, v in dup_hashes.items()},
        "duplicate_titles": dup_titles,
        "shell_pages": [p["url"] for p in pages if p["is_shell"]],
        "total_words": sum(p["words"] for p in pages),
        "term_total": sum((p["term_count"] or 0) for p in pages) if term else None,
        "checks": [{"id": i, "label": l, "pass": bool(ok), "detail": d}
                   for i, l, ok, d in checks],
        "passed": sum(1 for _, _, ok, _ in checks if ok),
        "total_checks": len(checks),
    }


# ── technical findings ───────────────────────────────────────────────────────

AI_CRAWLERS = ["GPTBot", "OAI-SearchBot", "ChatGPT-User", "ClaudeBot",
               "anthropic-ai", "PerplexityBot", "Google-Extended", "CCBot"]


def robots_and_ai(base):
    """robots.txt, llms.txt, and whether AI crawlers are shut out.

    A blocked AI crawler is invisible in every other check and silently removes
    the site from the assistant it belongs to. Worth its own line in the report.
    """
    _, txt = fetch(base.rstrip("/") + "/robots.txt")
    txt = txt or ""
    blocked = []
    for agent in AI_CRAWLERS:
        m = re.search(r"user-agent:\s*" + re.escape(agent) + r"\s*(.*?)(?=user-agent:|$)",
                      txt, re.I | re.S)
        if m and re.search(r"disallow:\s*/\s*$", m.group(1), re.I | re.M):
            blocked.append(agent)
    lstat, ltxt = fetch(base.rstrip("/") + "/llms.txt")
    has_llms = lstat == 200 and ltxt.strip().startswith("#")
    return {
        "robots_found": bool(txt.strip()),
        "sitemap_declared": bool(re.search(r"^\s*sitemap:", txt, re.I | re.M)),
        "ai_blocked": blocked,
        "llms_txt": has_llms,
    }


def canonical_host(base):
    """How many hops it takes to reach the canonical host. More than one is untidy."""
    host = urllib.parse.urlparse(base).netloc.replace("www.", "")
    hops, url = 0, "http://" + host + "/"
    for _ in range(6):
        req = urllib.request.Request(url, headers={"User-Agent": UA}, method="HEAD")
        try:
            class _NR(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, *a, **k):
                    return None
            urllib.request.build_opener(_NR).open(req, timeout=20)
            break
        except urllib.error.HTTPError as ex:
            loc = ex.headers.get("Location") if ex.headers else None
            if not loc or ex.code not in (301, 302, 307, 308):
                break
            url = urllib.parse.urljoin(url, loc)
            hops += 1
        except Exception:
            break
    return {"hops": hops, "final": url}


def link_graph(pages, base):
    """Inbound internal links per page, and anything nothing links to.

    Keyed on path, not full URL. The sitemap may say www and the supplied base
    may not (or the reverse), and comparing full URLs then reports every page as
    an orphan — which is wrong in the most alarming possible way.

    A page no other page links to reads as unimportant no matter how good it is.
    Clients never see this, and it is free to measure.
    """
    def path_of(u):
        pr = urllib.parse.urlparse(u)
        return (pr.path or "/").rstrip("/") or "/"

    hosts = {urllib.parse.urlparse(base).netloc.lower().replace("www.", "")}
    counts = {path_of(p["url"]): 0 for p in pages}
    anchors, by_path = {}, {path_of(p["url"]): p["url"] for p in pages}

    for p in pages:
        hay = p.get("_haystack_raw") or ""
        src = path_of(p["url"])
        body = hay[hay.find("<body"):] if "<body" in hay else hay
        for m in re.finditer(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
                             body, re.S | re.I):
            href = m.group(1)
            text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", m.group(2))).strip()
            if href.startswith(("mailto:", "tel:", "#", "javascript:")):
                continue
            if href.startswith("http"):
                host = urllib.parse.urlparse(href).netloc.lower().replace("www.", "")
                if host not in hosts:
                    continue
            elif not href.startswith("/"):
                continue
            target = path_of(href.split("#")[0].split("?")[0])
            if target in counts and target != src:
                counts[target] += 1
                if text:
                    anchors.setdefault(target, []).append(text.lower()[:40])

    orphans = [by_path[pth] for pth, n in counts.items() if n == 0 and pth != "/"]
    return {
        "inbound": {by_path[pth]: n for pth, n in counts.items()},
        "orphans": orphans,
        "anchors": {by_path[k]: v for k, v in anchors.items()},
    }


def technical(base, pages):
    """Everything measurable without account access, that a client never sees."""
    long_titles = [p["url"] for p in pages if len(p.get("title") or "") > 62]
    no_title = [p["url"] for p in pages if not (p.get("title") or "").strip()]
    thin = [(p["url"], p["words"]) for p in pages if p["words"] < 300]
    no_h1 = [p["url"] for p in pages if p["h1_count"] == 0]
    multi_h1 = [p["url"] for p in pages if p["h1_count"] > 1]
    no_schema = [p["url"] for p in pages if p["schema_blocks"] == 0]
    broken = [(p["url"], p["status"]) for p in pages if p.get("status") not in (200, None)]
    no_contact = [p["url"] for p in pages if not p["tel_links"] and not p["wa_links"]]
    return {
        "long_titles": long_titles,
        "missing_titles": no_title,
        "thin_pages": thin,
        "pages_without_h1": no_h1,
        "pages_multiple_h1": multi_h1,
        "pages_without_schema": no_schema,
        "broken_in_sitemap": broken,
        "pages_without_contact_link": no_contact,
    }
