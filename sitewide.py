#!/usr/bin/env python3
"""
Whole-site audit — find every page, audit each one, roll the results up.

The single-page report measures the URL it is given. A client's site is not one
page: the homepage can be immaculate while every room page is missing its
description, and a report that only read the homepage would call that site
healthy. This module turns the report into a measurement of the whole site.

  discover()    every page the site publishes: its sitemap(s) first, then a
                crawl of its own links from the homepage to catch what the
                sitemap missed. Also the raw facts the cross-page checks need.
  structure()   checks that only exist ACROSS pages — broken internal links,
                duplicate titles, orphans, sitemap entries that redirect or are
                noindexed. A one-page scan cannot see any of these.
  aggregate()   per-page scan results folded into the same shape the
                single-page report already renders, with every issue carrying
                the pages it affects.
  aggregate_lh() the same for Lighthouse.

The rule carried over from the rest of the tool: a page that could not be
measured is listed as not measured — never dropped, never counted as a zero.

Stdlib only.
"""

import gzip
import hashlib
import re
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from collections import deque

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126 Safari/537.36")
PROBE_PATH = "/boldpiq-audit-page-that-does-not-exist-7c1e"
MAX_BYTES = 3 * 1024 * 1024
FETCH_GAP = 0.2           # between crawl fetches — this is a client's server
SITEMAP_CAP = 500         # sitemap URLs checked for status/indexability
SITEMAP_FILES_CAP = 25    # child sitemaps followed from a sitemap index
SKIP_EXT = re.compile(
    r"\.(pdf|jpe?g|png|gif|webp|avif|svg|ico|bmp|tiff?|mp4|mov|webm|mp3|wav|"
    r"zip|rar|7z|gz|xml|txt|json|csv|css|js|mjs|map|woff2?|ttf|eot|"
    r"docx?|xlsx?|pptx?|ics|vcf)$", re.I)

# seoscore checks that describe ONE file or ONE server setting for the whole
# site. When one of these fails on every page, "missing on 38 of 38 pages" is
# misleading — it is one robots.txt, one fix — so the report says "site-wide".
SITE_LEVEL = {
    "robots_txt", "robots_txt_sitemap", "ai_crawlers_blocked", "sitemap_xml",
    "sitemap_urls", "hsts", "csp", "x_content_type", "referrer_policy",
    "permissions_policy", "compression", "https", "favicon", "apple_touch_icon",
    "web_manifest", "rss_feed", "indexnow", "trust_about", "trust_contact",
    "trust_privacy", "trust_terms", "aeo_llms_txt", "aeo_ai_crawlers",
    "aeo_ai_robots", "geo_robots_ai", "geo_bot_access", "geo_ai_discovery",
    "geo_https", "org_logo", "aeo_org_schema", "aeo_sameas_links",
}

PRIORITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


# ── fetching ─────────────────────────────────────────────────────────────────

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def fetch(url, timeout=25, max_hops=6):
    """Follow redirects by hand so the chain is recorded. Never raises.

    Returns {url, final, status, hops, ctype, headers, body, error}. status 0
    means the request itself failed (DNS, TLS, timeout) — which is a different
    statement from any HTTP status, and is reported as such.
    """
    cur, hops = url, []
    for _ in range(max_hops + 1):
        req = urllib.request.Request(cur, headers={
            "User-Agent": UA, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            "Cache-Control": "no-cache"})
        try:
            r = _OPENER.open(req, timeout=timeout)
            status, headers, raw = r.status, r.headers, r.read(MAX_BYTES)
        except urllib.error.HTTPError as ex:
            status, headers = ex.code, ex.headers
            if status in (301, 302, 303, 307, 308) and headers and headers.get("Location"):
                hops.append({"url": cur, "status": status})
                cur = urllib.parse.urljoin(cur, headers.get("Location"))
                continue
            try:
                raw = ex.read(MAX_BYTES)
            except Exception:
                raw = b""
        except Exception as ex:
            return {"url": url, "final": cur, "status": 0, "hops": hops, "ctype": "",
                    "headers": {}, "body": "", "error": str(getattr(ex, "reason", ex))[:160]}
        if raw[:2] == b"\x1f\x8b":
            try:
                raw = gzip.decompress(raw)
            except OSError:
                pass
        ctype = (headers.get("Content-Type") or "") if headers else ""
        m = re.search(r"charset=([\w-]+)", ctype, re.I)
        try:
            body = raw.decode(m.group(1) if m else "utf-8", "replace")
        except LookupError:
            body = raw.decode("utf-8", "replace")
        return {"url": url, "final": cur, "status": status, "hops": hops, "ctype": ctype,
                "headers": {k.lower(): v for k, v in (headers.items() if headers else [])},
                "body": body, "error": None}
    return {"url": url, "final": cur, "status": 0, "hops": hops, "ctype": "", "headers": {},
            "body": "", "error": "too many redirects"}


# ── urls ─────────────────────────────────────────────────────────────────────

def bare_host(u):
    h = (urllib.parse.urlparse(u).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


def key(u):
    """Identity of a page: host without www, path without trailing slash, query kept.

    The sitemap may say www and the links may not, or the reverse. Keying on the
    full URL would then call every page an orphan — wrong in the most alarming
    possible way (the same lesson as rank-report's link graph).
    """
    p = urllib.parse.urlparse(u)
    path = re.sub(r"/{2,}", "/", p.path or "/")
    if path != "/":
        path = path.rstrip("/")
    return bare_host(u) + path + ("?" + p.query if p.query else "")


def path_of(u):
    p = urllib.parse.urlparse(u)
    return (p.path or "/") + ("?" + p.query if p.query else "")


def _clean_url(u):
    return urllib.parse.urldefrag(u.strip())[0]


# ── page facts ───────────────────────────────────────────────────────────────

def _one(pattern, html, flags=re.I | re.S):
    m = re.search(pattern, html, flags)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


def _meta(html, name):
    for tag in re.findall(r"<meta\b[^>]*>", html, re.I):
        if re.search(r'(name|property)\s*=\s*["\']' + re.escape(name) + r'["\']', tag, re.I):
            m = re.search(r'content\s*=\s*["\']([^"\']*)', tag, re.I)
            if m:
                return m.group(1).strip()
    return ""


def _canonical(html):
    for tag in re.findall(r"<link\b[^>]*>", html, re.I):
        if re.search(r'rel\s*=\s*["\']canonical["\']', tag, re.I):
            m = re.search(r'href\s*=\s*["\']([^"\']+)', tag, re.I)
            if m:
                return m.group(1).strip()
    return ""


def _words(html):
    body = re.sub(r"<(script|style|noscript|svg)\b.*?</\1>", " ", html, flags=re.S | re.I)
    return len(re.sub(r"<[^>]+>", " ", body).split())


def _links(html, base, root):
    """Same-site links on the page, resolved and de-fragmented."""
    out = []
    body = html[html.lower().find("<body"):] if "<body" in html.lower() else html
    for href in re.findall(r'<a\b[^>]*?href\s*=\s*["\']([^"\']+)["\']', body, re.I):
        href = href.strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#", "data:", "sms:", "whatsapp:")):
            continue
        u = _clean_url(urllib.parse.urljoin(base, href))
        if not u.startswith(("http://", "https://")) or bare_host(u) != root:
            continue
        out.append(u)
    return out


def facts(resp, root):
    html = resp["body"] if "html" in (resp["ctype"] or "").lower() or \
        resp["body"][:500].lstrip().lower().startswith(("<!doctype html", "<html")) else ""
    robots_meta = _meta(html, "robots").lower() if html else ""
    x_robots = (resp["headers"].get("x-robots-tag") or "").lower()
    canon = _canonical(html) if html else ""
    return {
        "url": resp["url"],
        "final": resp["final"],
        "status": resp["status"],
        "error": resp["error"],
        "redirected": bool(resp["hops"]),
        "hops": resp["hops"],
        "html": bool(html),
        "title": _one(r"<title[^>]*>(.*?)</title>", html) if html else "",
        "description": _meta(html, "description") if html else "",
        "h1": len(re.findall(r"<h1[\s>]", html, re.I)) if html else 0,
        "canonical": urllib.parse.urljoin(resp["final"], canon) if canon else "",
        "noindex": "noindex" in robots_meta or "noindex" in x_robots,
        "words": _words(html) if html else 0,
        "hash": hashlib.md5(html.encode()).hexdigest() if html else None,
        "links": _links(html, resp["final"], root) if html else [],
    }


# ── sitemaps ─────────────────────────────────────────────────────────────────

def _read_sitemaps(candidates, log):
    """Every <loc> across the sitemap(s), following sitemap indexes one level down."""
    urls, files, seen = [], [], set()
    todo = deque(candidates)
    while todo and len(files) < SITEMAP_FILES_CAP:
        sm = todo.popleft()
        if sm in seen:
            continue
        seen.add(sm)
        r = fetch(sm)
        if r["status"] != 200 or "<loc" not in r["body"]:
            continue
        files.append(r["final"])
        locs = [_clean_url(u.replace("&amp;", "&"))
                for u in re.findall(r"<loc>\s*([^<]+?)\s*</loc>", r["body"], re.I)]
        if re.search(r"<sitemapindex", r["body"], re.I):
            todo.extend(locs)
        else:
            urls.extend(locs)
        time.sleep(FETCH_GAP)
    out, keys = [], set()
    for u in urls:
        k = key(u)
        if k not in keys:
            keys.add(k)
            out.append(u)
    if files:
        log(f"   sitemap: {len(out)} URLs in {len(files)} file(s)")
    return out, files


# ── discovery ────────────────────────────────────────────────────────────────

def discover(start, limit=100, log=print):
    """Find every page and gather what the cross-page checks need.

    Sitemap URLs are always checked (up to SITEMAP_CAP) because the sitemap is
    the site's own claim about what exists. Link crawling adds what the sitemap
    left out, within a budget, and records click depth from the homepage.
    """
    home = fetch(start)
    if home["status"] != 200 or not home["body"]:
        raise SystemExit(f"The homepage could not be loaded "
                         f"({home['error'] or 'HTTP ' + str(home['status'])}).")
    origin = "{0.scheme}://{0.netloc}".format(urllib.parse.urlparse(home["final"]))
    root = bare_host(origin)

    rob = fetch(origin + "/robots.txt")
    robots_txt = rob["body"] if rob["status"] == 200 else ""
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(robots_txt.splitlines())

    declared = [u.strip() for u in re.findall(r"(?im)^\s*sitemap:\s*(\S+)", robots_txt)]
    sm_urls, sm_files = _read_sitemaps(
        declared or [origin + "/sitemap.xml", origin + "/sitemap_index.xml",
                     origin + "/wp-sitemap.xml"], log)
    sm_urls = [u for u in sm_urls if bare_host(u) == root]
    sm_truncated = len(sm_urls) > SITEMAP_CAP
    sm_keys = {key(u) for u in sm_urls[:SITEMAP_CAP]}

    probe = fetch(origin + PROBE_PATH)
    probe_hash = hashlib.md5(probe["body"].encode()).hexdigest() if probe["body"] else None

    crawl_budget = min(max(limit * 3, 150), 400)
    pages = {}                 # key -> facts
    depth = {key(home["final"]): 0}
    inbound = {}               # key -> set of source keys
    link_sources = {}          # target key -> set of source urls (for broken-link notes)
    robots_skipped = []
    crawled_extra = 0
    crawl_truncated = False

    # The homepage is recorded under its final address: https://example.com that
    # redirects to https://www.example.com is still the homepage, not a redirect
    # to be excluded from its own audit.
    pages[key(home["final"])] = dict(facts(home, root), url=home["final"],
                                     redirected=False, hops=[], depth=0)
    queue = deque()

    def enqueue_links(src_facts):
        sk = key(src_facts["final"])
        for u in src_facts["links"]:
            k = key(u)
            if k == sk:
                continue
            inbound.setdefault(k, set()).add(sk)
            link_sources.setdefault(k, set()).add(src_facts["final"])
            if k not in depth:
                depth[k] = depth.get(sk, 0) + 1
                queue.append(u)

    enqueue_links(pages[key(home["final"])])
    log(f"   crawling {origin} …")

    def visit(u):
        r = fetch(u)
        f = facts(r, root)
        pages[key(u)] = f
        fk = key(f["final"])
        if f["redirected"] and fk != key(u) and bare_host(f["final"]) == root \
                and fk not in pages and f["status"] == 200:
            # The redirect target is a real page in its own right.
            pages[fk] = dict(f, url=f["final"], redirected=False, hops=[])
            depth.setdefault(fk, depth.get(key(u)))
        if f["status"] == 200 and f["html"]:
            enqueue_links(f)
        time.sleep(FETCH_GAP)

    while queue:
        u = queue.popleft()
        k = key(u)
        if k in pages:
            continue
        if SKIP_EXT.search(urllib.parse.urlparse(u).path):
            continue
        if urllib.parse.urlparse(u).query and k not in sm_keys:
            continue           # filters, tracking and pagination variants
        if not rp.can_fetch("*", u):
            robots_skipped.append(u)
            continue
        if k not in sm_keys:
            if crawled_extra >= crawl_budget:
                crawl_truncated = True
                continue
            crawled_extra += 1
        visit(u)
        if len(pages) % 25 == 0:
            log(f"   crawled {len(pages)} pages …")

    # Sitemap URLs no link reached.
    for u in sm_urls[:SITEMAP_CAP]:
        k = key(u)
        if k in pages or not rp.can_fetch("*", u):
            continue
        visit(u)

    # A link to /about-us that 301s to /about is still a link to /about.
    for k, f in list(pages.items()):
        if f["redirected"] and f["status"] == 200:
            fk = key(f["final"])
            if fk != k:
                inbound.setdefault(fk, set()).update(inbound.get(k, ()))

    for k, f in pages.items():
        f["depth"] = depth.get(k)
        f["in_sitemap"] = k in sm_keys
        f["inbound"] = len(inbound.get(k, ()))
        f["shell"] = bool(probe_hash and f["hash"] == probe_hash)
        f["found_by"] = ("sitemap" if f["depth"] is None else
                         "both" if f["in_sitemap"] else "links")

    # What gets audited: live, indexable-or-not HTML pages, one per final URL.
    home_key = key(home["final"])
    cands, taken = [], set()
    for k, f in pages.items():
        if f["status"] != 200 or not f["html"] or f["redirected"] or f["shell"]:
            continue
        fk = key(f["final"])
        if fk in taken:
            continue
        taken.add(fk)
        cands.append(f)

    def rank(f):
        return (key(f["final"]) != home_key, f["noindex"],
                f["depth"] if f["depth"] is not None else 99,
                -f["inbound"], f["final"])
    cands.sort(key=rank)
    audit = [f["final"] for f in cands[:limit]]
    not_audited = [f["final"] for f in cands[limit:]]

    log(f"   found {len(cands)} live pages "
        f"({len(sm_keys)} in the sitemap, {crawled_extra} more by following links) — "
        f"auditing {len(audit)}")

    return {
        "start": start,
        "origin": origin,
        "root": root,
        "home": home["final"],
        "robots_found": bool(robots_txt.strip()),
        "robots_disallow": [u for u in sm_urls[:SITEMAP_CAP] if not rp.can_fetch("*", u)],
        "robots_skipped": robots_skipped[:50],
        "sitemap_files": sm_files,
        "sitemap_count": len(sm_urls),
        "sitemap_truncated": sm_truncated,
        "sitemap_urls": sm_urls[:SITEMAP_CAP],
        "crawl_truncated": crawl_truncated,
        "probe_status": probe["status"],
        "pages": pages,
        "link_sources": {k: sorted(v)[:10] for k, v in link_sources.items()},
        "live_count": len(cands),
        "audit": audit,
        "not_audited": not_audited,
        "limit": limit,
    }


def hosts_check(disc):
    """Every spelling of the domain should land on the one real address, in one hop."""
    origin, root = disc["origin"], disc["root"]
    rows = []
    for variant in (f"http://{root}/", f"http://www.{root}/",
                    f"https://{root}/", f"https://www.{root}/"):
        if variant.rstrip("/") == origin:
            continue
        r = fetch(variant)
        final = "{0.scheme}://{0.netloc}".format(urllib.parse.urlparse(r["final"]))
        rows.append({"variant": variant.rstrip("/"), "status": r["status"],
                     "final": final, "hops": len(r["hops"]),
                     "ok": r["status"] == 200 and final == origin,
                     "error": r["error"]})
    return rows


# ── cross-page checks ────────────────────────────────────────────────────────

def _finding(fid, title, priority, effort, why, fix, pages, detail_ok, detail_bad,
             measured=True, unmeasured=""):
    """One cross-page check. A check that could not run is `measured: False` —
    it has not passed, and it is not shown as a failure either."""
    return {"id": fid, "title": title, "priority": priority, "effort": effort,
            "why": why, "fix": fix, "pages": pages if measured else [],
            "pass": measured and not pages, "measured": measured,
            "label": ((detail_bad if pages else detail_ok) if measured
                      else "Not measured — " + unmeasured)}


def structure(disc, hosts=None):
    """The findings only a whole-site crawl can make. Returns a list of findings."""
    pages = disc["pages"]
    live = [f for f in pages.values()
            if f["status"] == 200 and f["html"] and not f["redirected"]]
    indexable = [f for f in live if not f["noindex"] and not f["shell"]]
    src = disc["link_sources"]

    def srcs(k):
        s = src.get(k) or []
        if not s:
            return ""
        shown = ", ".join(path_of(u) for u in s[:3])
        more = f" +{len(s) - 3} more" if len(s) > 3 else ""
        return f"linked from {shown}{more}"

    out = []

    # 1. Broken internal links
    broken = [f for k, f in pages.items()
              if (f["status"] >= 400 or f["status"] == 0) and k in src]
    out.append(_finding(
        "broken_links", "Broken internal links", "critical", "low",
        "A link on your own site that leads to an error page loses the visitor at "
        "the exact moment they were interested, and tells Google the site is not "
        "maintained. Every one of these is a dead end you built yourself.",
        "Update each link to point at the page that replaced it, or restore the page. "
        "If the page is gone for good, add a 301 redirect to its closest equivalent.",
        [{"url": f["url"],
          "note": (f"HTTP {f['status']}" if f["status"] else f"did not load ({f['error']})")
          + (" — " + srcs(key(f["url"])) if srcs(key(f["url"])) else "")}
         for f in broken],
        "Every internal link that was followed loads a real page.",
        f"{len(broken)} internal link target{'s' if len(broken) != 1 else ''} "
        f"return an error."))

    # 2. Unknown URLs must 404
    ps = disc["probe_status"]
    out.append(_finding(
        "soft_404", "Pages that don't exist return \"not found\"", "high", "medium",
        "When an address that does not exist still returns a normal page, Google can "
        "index endless empty duplicates of your site, and a mistyped link never shows "
        "up as broken in any report — including this one.",
        "Configure the site so unknown addresses return a real 404 status with a "
        "helpful 'page not found' page.",
        [] if ps == 404 or ps == 410 else
        [{"url": disc["origin"] + PROBE_PATH, "note": f"returned HTTP {ps or 'nothing'}"}],
        "A made-up address correctly returns 404.",
        f"A made-up address returned HTTP {ps or '—'} instead of 404."))

    # 3. Shell pages — routes serving the app's fallback, not their own content
    shells = [f for f in live if f["shell"]]
    out.append(_finding(
        "shell_pages", "Every page is its own document", "critical", "high",
        "These addresses return exactly the same code as a page that does not exist. "
        "To Google they are empty: the content only appears after JavaScript runs, so "
        "there is nothing to rank. This is the most serious structural fault a site "
        "can have, and it is invisible to a visitor.",
        "Render each page on the server (or pre-render it at build time) so the HTML "
        "for each address contains its own content.",
        [{"url": f["url"], "note": "identical to the site's not-found response"}
         for f in shells],
        "Every page returns its own HTML.",
        f"{len(shells)} page{'s' if len(shells) != 1 else ''} return the site's "
        f"fallback shell."))

    # 4. Duplicate content
    groups = {}
    for f in indexable:
        if f["hash"]:
            groups.setdefault(f["hash"], []).append(f)
    dups = [g for g in groups.values() if len(g) > 1]
    out.append(_finding(
        "duplicate_content", "No two pages are identical", "high", "medium",
        "When two addresses serve the same page, Google has to guess which one to "
        "show, and the two split whatever authority the page has earned.",
        "Keep one address per page. 301-redirect the others to it, or point their "
        "canonical tag at the one you want ranked.",
        [{"url": g[0]["url"], "note": "identical to " + ", ".join(path_of(x["url"]) for x in g[1:4])}
         for g in dups],
        "No duplicate pages found.",
        f"{sum(len(g) for g in dups)} pages are exact duplicates of another page."))

    # 5 & 6. Duplicate titles / descriptions
    def dup_field(field):
        by = {}
        for f in indexable:
            v = (f[field] or "").strip().lower()
            if v:
                by.setdefault(v, []).append(f)
        return [g for g in by.values() if len(g) > 1]

    dt = dup_field("title")
    out.append(_finding(
        "duplicate_titles", "Every page has its own title", "high", "low",
        "The title is the headline Google shows for a page. When several pages share "
        "one, Google cannot tell them apart, often shows the wrong one, and a searcher "
        "sees the same headline twice and trusts neither.",
        "Write a unique title for each page that says what that page offers and "
        "where — for example 'Family Suite with Mountain View | Lodge Name, Clarens'.",
        [{"url": g[0]["url"],
          "note": f"“{g[0]['title'][:70]}” — also on "
                  + ", ".join(path_of(x["url"]) for x in g[1:4])
                  + (f" +{len(g) - 4} more" if len(g) > 4 else "")}
         for g in dt],
        "Every page has a distinct title.",
        f"{sum(len(g) for g in dt)} pages share a title with another page."))

    dd = dup_field("description")
    out.append(_finding(
        "duplicate_descriptions", "Every page has its own description", "medium", "low",
        "The description is the two lines under your Google listing. A copied one "
        "means Google usually writes its own from random page text instead.",
        "Give each page a unique 140–160 character description that sells that "
        "specific page.",
        [{"url": g[0]["url"],
          "note": "shared with " + ", ".join(path_of(x["url"]) for x in g[1:4])
                  + (f" +{len(g) - 4} more" if len(g) > 4 else "")}
         for g in dd],
        "Every page with a description has its own.",
        f"{sum(len(g) for g in dd)} pages share a description with another page."))

    has_sitemap = bool(disc["sitemap_files"])
    sm_pages = [pages[key(u)] for u in disc["sitemap_urls"] if key(u) in pages]

    # 7. Sitemap entries that are not live pages
    bad_sm = [f for f in sm_pages if f["status"] != 200 or f["redirected"]]
    out.append(_finding(
        "sitemap_errors", "Sitemap lists only live pages", "high", "low",
        "The sitemap is your list of pages for Google. Every entry that redirects or "
        "errors wastes a crawl and makes the whole list less trusted.",
        "Remove dead and redirecting addresses from the sitemap and list the final "
        "address instead. Most platforms rebuild the sitemap automatically once the "
        "page itself is fixed or deleted.",
        [{"url": f["url"],
          "note": (f"redirects to {path_of(f['final'])}" if f["redirected"] and f["status"] == 200
                   else f"HTTP {f['status']}" if f["status"] else f"did not load ({f['error']})")}
         for f in bad_sm],
        "Every sitemap entry loads directly.",
        f"{len(bad_sm)} sitemap entr{'ies' if len(bad_sm) != 1 else 'y'} redirect or fail.",
        measured=has_sitemap, unmeasured="no sitemap was found."))

    # 8. Sitemap entries Google is told not to index
    conflict = []
    for f in sm_pages:
        if f["status"] != 200 or f["redirected"]:
            continue
        if f["noindex"]:
            conflict.append({"url": f["url"], "note": "marked noindex"})
        elif f["canonical"] and key(f["canonical"]) != key(f["final"]):
            conflict.append({"url": f["url"],
                             "note": f"canonical points to {path_of(f['canonical'])}"})
    for u in disc["robots_disallow"]:
        conflict.append({"url": u, "note": "blocked by robots.txt"})
    out.append(_finding(
        "sitemap_conflicts", "Sitemap and page signals agree", "high", "low",
        "These pages are in the sitemap — 'please index this' — while the page itself "
        "says 'don't index me' or 'index another page instead'. Mixed signals make "
        "Google trust both less, and the page usually ends up out of the results.",
        "Decide for each page: if it should rank, remove the noindex / fix the "
        "canonical / unblock it; if it should not, take it out of the sitemap.",
        conflict,
        "Every sitemap page is indexable and canonical to itself.",
        f"{len(conflict)} sitemap page{'s' if len(conflict) != 1 else ''} send "
        f"contradicting signals.",
        measured=has_sitemap, unmeasured="no sitemap was found."))

    # 9. Live pages missing from the sitemap
    missing = [f for f in indexable if not f["in_sitemap"] and f["depth"] is not None]
    out.append(_finding(
        "missing_from_sitemap", "Every page is in the sitemap", "medium", "low",
        "Google still finds pages by following links, but a page left out of the "
        "sitemap is discovered later and re-checked less often.",
        "Add these pages to the sitemap. On most platforms that means making sure "
        "the page is published and not excluded in the SEO settings.",
        [{"url": f["url"], "note": "linked from the site but not in the sitemap"}
         for f in missing],
        "Every page found by following links is in the sitemap.",
        f"{len(missing)} live page{'s' if len(missing) != 1 else ''} missing from "
        f"the sitemap.",
        measured=has_sitemap, unmeasured="no sitemap was found."))

    # 10. Orphans
    orphans = [f for f in indexable
               if f["in_sitemap"] and f["inbound"] == 0 and key(f["final"]) != key(disc["home"])]
    out.append(_finding(
        "orphan_pages", "Every page is linked from somewhere", "medium", "low",
        "A page nothing on your site links to looks unimportant to Google no matter "
        "how good it is, and visitors can only reach it by typing the address.",
        "Link to each of these from a relevant page — the menu, a related room or "
        "service page, or the footer.",
        [{"url": f["url"], "note": "no internal links point here"} for f in orphans],
        "Every sitemap page is linked from at least one other page.",
        f"{len(orphans)} page{'s' if len(orphans) != 1 else ''} with no internal links.",
        measured=has_sitemap and not disc["crawl_truncated"],
        unmeasured=("no sitemap was found, so there is no list to compare links against."
                    if not has_sitemap else
                    "the crawl stopped at its page budget, so link counts are incomplete.")))

    # 11. Click depth
    deep = [f for f in indexable if f["depth"] is not None and f["depth"] > 3]
    out.append(_finding(
        "click_depth", "Every page is within three clicks of the homepage", "medium", "medium",
        "Pages buried deep in a site are crawled less often and treated as less "
        "important. Visitors rarely dig that far either.",
        "Bring these pages closer to the homepage: link them from the main menu, a "
        "hub page or the footer.",
        [{"url": f["url"], "note": f"{f['depth']} clicks from the homepage"} for f in deep],
        "Every page is three clicks or fewer from the homepage.",
        f"{len(deep)} page{'s' if len(deep) != 1 else ''} deeper than three clicks."))

    # 12. Internal links through redirects
    via = [f for k, f in pages.items() if f["redirected"] and f["status"] == 200 and k in src]
    out.append(_finding(
        "redirect_links", "Internal links go straight to the page", "low", "low",
        "A link that goes through a redirect still works, but each hop slows the "
        "visitor down and leaks a little of the link's value.",
        "Change each link to point directly at the final address.",
        [{"url": f["url"], "note": f"redirects to {path_of(f['final'])}"
          + (" — " + srcs(key(f["url"])) if srcs(key(f["url"])) else "")}
         for f in via],
        "No internal link goes through a redirect.",
        f"{len(via)} internal link target{'s' if len(via) != 1 else ''} redirect."))

    # 13. One address for the site
    if hosts is not None:
        badh = [h for h in hosts if not h["ok"] or h["hops"] > 1]
        out.append(_finding(
            "host_variants", "Every version of the address lands on one site", "medium", "low",
            "People type the address with and without www, and old links use http. "
            "Each version should land on the one real site in a single step — anything "
            "else splits your authority or shows visitors a security warning.",
            "Set a single permanent (301) redirect from every variant straight to "
            f"{disc['origin']}.",
            # "show": the whole point is which spelling of the domain misbehaves,
            # so the report prints the full address rather than just its path.
            [{"url": h["variant"], "show": h["variant"],
              "note": (f"did not load ({h['error']})" if not h["status"] else
                       f"ends at {h['final']}" if h["final"] != disc["origin"] else
                       f"takes {h['hops']} redirects")}
             for h in badh],
            f"Every variant redirects to {disc['origin']} in one step.",
            f"{len(badh)} address variant{'s do' if len(badh) != 1 else ' does'} not "
            f"land cleanly on {disc['origin']}."))

    return out


# ── roll-up of per-page scans ────────────────────────────────────────────────

def _mean(vals):
    vals = [v for v in vals if isinstance(v, (int, float))]
    return round(sum(vals) / len(vals)) if vals else None


def aggregate(results, analyse, clean_label):
    """Fold per-page seoscore results into the single-page `an` shape.

    results: [{"url", "scan", ...}] — pages whose scan failed have scan=None and
    are left out of the averages (and listed as not measured by the report).
    analyse / clean_label are passed in from seo_report so there is exactly one
    definition of what counts as an issue.
    """
    scanned = [(r, analyse(r["scan"])) for r in results if r.get("scan")]
    if not scanned:
        return None
    first_an = scanned[0][1]
    n = len(scanned)

    by_id = {}
    order = []
    for r, an in scanned:
        for row in an["issues"]:
            e = by_id.get(row["id"])
            if e is None:
                e = by_id[row["id"]] = {"row": row, "fail": [], "pass": []}
                order.append(row["id"])
            e["fail"].append({"url": r["url"], "note": clean_label(row.get("label"))})
        for row in an["passes"]:
            e = by_id.get(row["id"])
            if e is None:
                e = by_id[row["id"]] = {"row": row, "fail": [], "pass": []}
                order.append(row["id"])
            e["pass"].append(r["url"])

    pillars = {k: {"issues": [], "passes": []} for k in ("seo", "aeo", "geo")}
    for cid in order:
        e = by_id[cid]
        row = dict(e["row"])
        applicable = len(e["fail"]) + len(e["pass"])
        if e["fail"]:
            notes = {p["note"] for p in e["fail"]}
            row["pages"] = e["fail"]
            row["affected"] = len(e["fail"])
            row["applicable"] = applicable
            row["sitewide"] = cid in SITE_LEVEL and not e["pass"]
            row["same_note"] = len(notes) == 1
            if row["sitewide"]:
                row["label"] = e["fail"][0]["note"]
            else:
                row["label"] = (f"Found on every page checked ({applicable})"
                                if len(e["fail"]) == applicable else
                                f"Found on {len(e['fail'])} of {applicable} pages")
                if row["same_note"] and e["fail"][0]["note"]:
                    row["label"] += " — " + e["fail"][0]["note"]
            pillars[row["pillar"]]["issues"].append(row)
        else:
            row["applicable"] = applicable
            pillars[row["pillar"]]["passes"].append(row)

    def sort_key(r):
        return (PRIORITY_ORDER[r["priority"]], -r.get("affected", 0), r["title"])

    out = {"pillars": {}, "issues": [], "passes": [],
           "platform": first_an["platform"], "platform_known": first_an["platform_known"],
           "site": True, "pages_scanned": n}
    for k in ("seo", "aeo", "geo"):
        p = pillars[k]
        p["issues"].sort(key=sort_key)
        scores = [a["pillars"][k]["score"] for _, a in scanned]
        # AI-platform readiness, averaged per platform across pages.
        plat_rows = {}
        for _, a in scanned:
            for pl in a["pillars"][k].get("platforms") or []:
                plat_rows.setdefault(pl.get("platform"), []).append(pl)
        platforms = [{"platform": name,
                      "score": _mean([x.get("score") for x in rows]),
                      "passed": _mean([x.get("passed") for x in rows]) or 0,
                      "total": _mean([x.get("total") for x in rows]) or 0}
                     for name, rows in plat_rows.items()]
        valid = [s for s in scores if isinstance(s, (int, float))]
        out["pillars"][k] = {
            "score": _mean(scores),
            "min": min(valid) if valid else None,
            "passed": len(p["passes"]),
            "total": len(p["passes"]) + len(p["issues"]),
            "issues": p["issues"],
            "passes": p["passes"],
            "platforms": platforms,
        }
        out["issues"] += p["issues"]
        out["passes"] += p["passes"]
    out["issues"].sort(key=sort_key)
    s = [out["pillars"][k]["score"] for k in ("seo", "aeo", "geo")
         if isinstance(out["pillars"][k]["score"], (int, float))]
    out["overall"] = round(sum(s) / len(s)) if s else None
    out["per_page"] = {r["url"]: {"seo": a["pillars"]["seo"]["score"],
                                  "aeo": a["pillars"]["aeo"]["score"],
                                  "geo": a["pillars"]["geo"]["score"],
                                  "overall": a["overall"],
                                  "issues": len(a["issues"])}
                       for r, a in scanned}
    return out


def finish(an, struct, blocked_status, limited_status):
    """Merge cross-page findings into the roll-up and compute the headline lists."""
    site_issues = []
    for f in struct:
        if f["pass"] or not f["measured"]:
            continue
        site_issues.append({
            "id": f["id"], "title": f["title"], "priority": f["priority"],
            "effort": f["effort"], "why": f["why"], "fix": f["fix"],
            "label": f["label"], "pillar": "site", "pages": f["pages"],
            "affected": len(f["pages"]), "applicable": None, "sitewide": False,
            "same_note": False, "limit_status": None, "limit_reason": None,
        })
    an["structure"] = struct
    an["structure_issues"] = site_issues
    an["issues"] = sorted(an["issues"] + site_issues,
                          key=lambda r: (PRIORITY_ORDER[r["priority"]],
                                         -r.get("affected", 0), r["title"]))
    an["counts"] = {p: sum(1 for i in an["issues"] if i["priority"] == p)
                    for p in PRIORITY_ORDER}
    an["quick_wins"] = [i for i in an["issues"]
                        if i["priority"] in ("critical", "high")
                        and i["effort"] == "low"
                        and i["limit_status"] != blocked_status][:6]
    an["blocked"] = [i for i in an["issues"] if i["limit_status"] == blocked_status]
    an["limited"] = [i for i in an["issues"] if i["limit_status"] == limited_status]
    return an


# ── roll-up of per-page Lighthouse runs ──────────────────────────────────────

def _fmt_metric(mid, v):
    if mid == "cumulative-layout-shift":
        return f"{v:.3f}".rstrip("0").rstrip(".") if v else "0"
    if mid == "total-blocking-time":
        return f"{round(v / 10) * 10:,.0f} ms"
    return f"{v / 1000:.1f} s"


def _group(runs, field):
    """Group one list-of-failures field across pages by audit id."""
    by, order = {}, []
    for url, l in runs:
        for it in l.get(field) or []:
            e = by.get(it["id"])
            if e is None:
                e = by[it["id"]] = dict(it, pages=[], savings_total=0, count_total=0)
                order.append(it["id"])
            e["pages"].append({"url": url, "note": it.get("display") or
                               (f"{it['count']} element(s)" if it.get("count") else "")})
            e["savings_total"] += it.get("savings") or 0
            e["count_total"] += it.get("count") or 0
            if it.get("score") == 0:
                e["score"], e["severity"] = 0, "high"
    rows = [by[i] for i in order]
    for r in rows:
        r["affected"] = len(r["pages"])
        r["count"] = r["count_total"]
        r["display"] = ""
    return rows


def aggregate_lh(results):
    runs = [(r["url"], r["lh"]) for r in results if r.get("lh")]
    if not runs:
        return None
    first = runs[0][1]
    n = len(runs)
    cats = list(first["scores"].keys())
    scores = {c: _mean([l["scores"].get(c) for _, l in runs]) for c in cats}

    metrics = []
    for m in first["metrics"]:
        vals = [x["numeric"] for _, l in runs for x in l["metrics"]
                if x["id"] == m["id"] and isinstance(x.get("numeric"), (int, float))]
        if not vals:
            continue
        med = statistics.median(vals)
        good, poor = m.get("good"), m.get("poor")
        rating = m["rating"] if good is None else (
            "good" if med <= good else ("fair" if med <= poor else "poor"))
        over = sum(1 for v in vals if good is not None and v > good)
        metrics.append(dict(m, numeric=med, value=_fmt_metric(m["id"], med),
                            rating=rating, pages_over=over, pages=len(vals)))

    opps = _group(runs, "opportunities")
    opps.sort(key=lambda r: (-r["affected"], -r["savings_total"], r["title"]))
    a11y = _group(runs, "accessibility_issues")
    a11y.sort(key=lambda r: (-r.get("weight", 0), -r["affected"], r["title"]))
    bp = _group(runs, "best_practice_issues")
    bp.sort(key=lambda r: (-r.get("weight", 0), -r["affected"], r["title"]))
    for rows in (opps, a11y, bp):
        for r in rows:
            r["display"] = f"on {r['affected']} of {n} pages"
            r["applicable"] = n

    # Agentic: worst status across pages, with how many pages pass.
    ag, order = {}, []
    for _, l in runs:
        for a in l.get("agentic") or []:
            if a["id"] not in ag:
                ag[a["id"]] = dict(a, statuses=[])
                order.append(a["id"])
            ag[a["id"]]["statuses"].append(a["status"])
    agentic = []
    for i in order:
        a = ag[i]
        st = a["statuses"]
        worst = ("fail" if "fail" in st else "partial" if "partial" in st
                 else "pass" if "pass" in st else "na")
        npass = st.count("pass")
        mixed = len(set(st)) > 1
        a = dict(a, status=worst,
                 display=(f"passes on {npass} of {len(st)} pages" if mixed else a.get("display", "")))
        a.pop("statuses", None)
        agentic.append(a)
    agentic.sort(key=lambda x: ({"fail": 0, "partial": 1, "pass": 2, "na": 3}[x["status"]],
                                -x.get("weight", 0)))

    per_page = []
    for url, l in runs:
        mm = {x["abbr"]: x for x in l["metrics"]}
        per_page.append({"url": url, "scores": l["scores"],
                         "lcp": mm.get("LCP"), "cls": mm.get("CLS"), "tbt": mm.get("TBT")})

    return {
        "version": first.get("version"),
        "form_factor": first.get("form_factor"),
        "fetched_url": None,
        "scores": scores,
        "metrics": metrics,
        "opportunities": opps,
        "accessibility_issues": a11y,
        "best_practice_issues": bp,
        "agentic": agentic,
        "passing": {c: _mean([l["passing"].get(c) for _, l in runs]) or 0 for c in cats},
        "borrowed_text": False,
        "site": True,
        "pages_measured": n,
        "per_page": per_page,
    }
