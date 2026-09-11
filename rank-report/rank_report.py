#!/usr/bin/env python3
"""
Boldpiq — Ranking Report generator (Tier A).

Where seo-report is a one-off diagnostic that wins a client, this is the
recurring artefact that keeps them: monthly proof the work is moving, in the
months before rankings visibly move.

Tier A runs on zero credentials. Everything in it is measured live:
crawl integrity, competitor comparison, proximity, and month-on-month deltas.
Sections needing client access render as "not connected" rather than failing.

    ./rank-report.sh clientdomain.co.za \
        --client "Client Name" --suburb Northcliff \
        --queries "tree felling northcliff" \
        --competitors a.co.za,b.co.za \
        --pin -26.1361531,27.9620647

Requires: Python 3 (stdlib only) + Google Chrome.
"""

import argparse
import base64
import datetime as dt
import html as html_mod
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request

# runtime.py and the Geist font come from seo-report. Sibling checkout locally;
# /app when staged inside the image.
_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (os.path.join(_HERE, "..", "seo-report"), os.path.join(_HERE, "..")):
    if os.path.exists(os.path.join(_p, "runtime.py")):
        sys.path.insert(0, _p)
        break
import runtime                                    # noqa: E402  (shared with seo-report)

import citations as cit                           # noqa: E402
import competitors as comp                        # noqa: E402
import gate as gate_mod                           # noqa: E402
import geo as geo_mod                             # noqa: E402
import history as hist                            # noqa: E402
import search as srch                             # noqa: E402
import gsc as gsc_mod                             # noqa: E402
import authority as auth                          # noqa: E402
import probes                                     # noqa: E402
import sitewide as sw                             # noqa: E402  (shared with seo-report)

# Most pages the crawl evaluates. Fetching raw HTML is cheap — no Chrome here —
# so this is well above the visibility report's cap.
SITE_MAX_PAGES = int(os.environ.get("BOLDPIQ_RANK_MAX_PAGES", "300"))
GATE_COVERED = {"duplicate_titles", "soft_404", "shell_pages", "duplicate_content"}

HERE = os.path.dirname(os.path.abspath(__file__))

COUNTRY_NAMES = {"ZA": "South Africa", "GB": "United Kingdom",
                 "UK": "United Kingdom", "IE": "Ireland",
                 "US": "United States", "AU": "Australia"}


def country_name(code):
    """Full country name for geocoding/search location, from the --country code."""
    return COUNTRY_NAMES.get((code or "").upper(), (code or "").upper())

FONT = next((f for f in (os.path.join(HERE, "..", "seo-report", "assets", "geist-latin.woff2"),
                         os.path.join(HERE, "..", "assets", "geist-latin.woff2"))
             if os.path.exists(f)), "")
REPORTS = os.environ.get("BOLDPIQ_REPORTS") or os.path.join(HERE, "reports")

INK = "#0B0F1C"
ACCENT = "#C4541A"
GOOD = "#16794A"
POOR = "#A32619"
MUTED = "#98A2B3"


def e(s):
    return html_mod.escape(str(s if s is not None else ""))


def font_face():
    if not os.path.exists(FONT):
        return ""
    b64 = base64.b64encode(open(FONT, "rb").read()).decode()
    return ("@font-face{font-family:Geist;font-style:normal;font-weight:100 900;"
            "font-display:block;src:url(data:font/woff2;base64,%s) format('woff2');}" % b64)


def ring(score, size=118):
    score = 0 if score is None else int(score)
    r = (size / 2) - 9
    circ = 2 * 3.14159265 * r
    dash = circ * (score / 100.0)
    colour = GOOD if score >= 80 else (ACCENT if score >= 50 else POOR)
    return f"""<svg class="ring" viewBox="0 0 {size} {size}" width="{size}" height="{size}">
  <circle cx="{size/2}" cy="{size/2}" r="{r}" fill="none" stroke="#E7E3DE" stroke-width="9"/>
  <circle cx="{size/2}" cy="{size/2}" r="{r}" fill="none" stroke="{colour}" stroke-width="9"
    stroke-linecap="round" stroke-dasharray="{dash:.1f} {circ:.1f}"
    transform="rotate(-90 {size/2} {size/2})"/>
  <text x="50%" y="50%" text-anchor="middle" dy=".35em" class="ring-num" fill="{INK}">{score}</text>
</svg>"""


def inf(text):
    """Mark a value as our assessment rather than a measurement."""
    return f'<span class="inf">{e(text)}<sup>i</sup></span>'


LEGEND = ('<div class="legend">'
          '<span><b>Plain</b> — measured on the live page</span>'
          '<span><b>Dotted<sup style="color:#C4541A">i</sup></b> — inferred: '
          'our assessment of a measurement</span></div>')


def rail(pr):
    """The data-collection panel: one line per source, worst first.

    Deliberately on the cover rather than buried at the back. A reader who
    knows a source was unreachable reads the rest of the report differently,
    and that is the point.
    """
    rows = sorted(pr.rows, key=lambda r: (probes.SEVERITY[r["status"]], r["label"]))
    s = pr.summary()
    bad, unc = s["broken"], s["unconnected"]
    if bad:
        head = f"{bad} source{'s' if bad != 1 else ''} could not be reached"
    elif unc:
        head = f"{unc} source{'s' if unc != 1 else ''} not yet connected"
    else:
        head = "All sources returned data"
    out = [f'<div class="rail"><h4>Data collection</h4>'
           f'<div class="hd{" bad" if bad else ""}">{e(head)}</div>']
    for r in rows:
        st = r["status"]
        out.append(
            f'<div class="chk"><span class="dot {st}"></span><span class="t">{e(r["label"])}'
            + (f'<span class="s {st}">{e(probes.LABEL[st])}</span>' if st != probes.OK else '')
            + '</span></div>')
    out.append(f'<div class="sum">{s["counts"][probes.OK]} of {s["total"]} checks '
               f'returned data. Anything marked otherwise is reported as missing, '
               f'never as zero. See "How this report was assembled".</div></div>')
    return "".join(out)


def missing(pr, key, heading=None):
    """A section whose source produced nothing says so, in the client's words.

    Returns an empty string when the source was fine, so this can be dropped in
    unconditionally above any section that depends on one.
    """
    note = pr.client_note(key)
    if not note:
        return ""
    st = pr.status(key)
    lbl = {probes.FAILED: "Not measured — source unreachable",
           probes.EMPTY: "No data held for this yet",
           probes.NOT_CONFIGURED: "Not connected for this report",
           probes.SKIPPED: "Not measured — an earlier step failed"}[st]
    h = f"<strong>{e(heading)}.</strong> " if heading else ""
    return f'<div class="nodata"><span class="lbl">{e(lbl)}</span>{h}{e(note)}</div>'


def delta_span(change):
    if change is None:
        return '<span class="d flat">—</span>'
    if change > 0:
        return f'<span class="d up">+{change}</span>'
    if change < 0:
        return f'<span class="d down">{change}</span>'
    return '<span class="d flat">no change</span>'


# ── collect ──────────────────────────────────────────────────────────────────

EMPTY_GATE = {
    "base": None, "nonexistent": None, "fallback_hash": None, "pages": [],
    "page_count": 0, "unique_documents": 0, "duplicate_groups": {},
    "duplicate_titles": {}, "shell_pages": [], "total_words": 0,
    "term_total": None, "checks": [], "passed": 0, "total_checks": 0,
    "measured": False,
}


def collect(args):
    """Gather every measurement, recording the health of each source as it goes.

    Nothing here is allowed to fail silently. A source that cannot be reached is
    recorded as unreachable and the section that depends on it says so, because
    a missing figure rendered as a zero is a false statement about the client's
    business — and the one kind of error this report cannot afford to make.
    """
    base = args.url.rstrip("/")
    term = args.suburb or None
    pr = probes.Probes()

    # Every page, not just the flat /sitemap.xml. The old read missed sitemap
    # indexes (every WordPress/Yoast site — it "crawled" the child .xml files as
    # if they were pages), sitemaps declared only in robots.txt, and any live page
    # the sitemap leaves out. Discovery is shared with the visibility report, so
    # both reports see the same site.
    print("   finding every page …", flush=True)

    def _discover():
        try:
            return sw.discover(base, limit=SITE_MAX_PAGES, keep_body=True,
                               log=lambda m: print(m, flush=True))
        except SystemExit as err:          # discover() exits on a dead homepage
            raise RuntimeError(str(err)) from None

    sd = pr.run(
        "site_discovery", "Every page found — sitemaps plus the site's own links",
        _discover,
        check=lambda r: bool(r and r.get("pages")),
        source="The site's sitemap(s), robots.txt and its own internal links",
        remedy="The homepage did not load, so no page could be found. Open the site "
               "in a browser; if it loads there, the host is blocking automated "
               "requests. The crawl falls back to reading /sitemap.xml directly.",
        client_note="We could not walk your site's pages on this run, so only the "
                    "pages listed in your sitemap were measured.",
        fallback=None)

    gate_urls, prefetched = None, None
    if sd:
        by_key = sd["pages"]
        sm_keys = {sw.key(u) for u in sd["sitemap_urls"]}
        extra = [f["url"] for f in by_key.values()
                 if sw.key(f["url"]) not in sm_keys and f["depth"] is not None
                 and f["status"] == 200 and f["html"] and not f["redirected"]]
        gate_urls = list(sd["sitemap_urls"]) + extra
        prefetched = {}
        for u in gate_urls:
            f = by_key.get(sw.key(u))
            if f is not None and "_body" in f:
                prefetched[u] = (f["status"], f["_body"])

    print("   crawl gate …", flush=True)
    g = pr.run(
        "gate", "Crawl gate — every page fetched and hashed",
        lambda: gate_mod.run(base, term=term, urls=gate_urls, prefetched=prefetched),
        check=lambda r: bool(r and r.get("pages")),
        source="Direct fetch of the site's sitemap and every URL in it",
        remedy="Nothing was crawled. Either the site is unreachable, /sitemap.xml "
               "is missing or is not XML, or the host is blocking us. Open the site "
               "and its /sitemap.xml in a browser to see which, then re-run. Nothing "
               "else in the report is trustworthy until this passes.",
        client_note="We could not read your site when this report was generated, so "
                    "no page-level finding below is based on live data. This is a "
                    "fault at our end or a block at the host, not a finding about "
                    "your site.",
        fallback=dict(EMPTY_GATE, base=base))

    site_structure, site_summary = None, None
    if sd and g.get("measured"):
        # The same gate over the sitemap pages alone — the set every report
        # before 2026-09 measured. Month-on-month movement across the change of
        # method is computed from this, so a real change on those pages still
        # shows as a change instead of disappearing into "new baseline".
        # Everything is already fetched; this costs one 404 probe.
        try:
            lfl = gate_mod.run(base, term=term, urls=sd["sitemap_urls"],
                               prefetched=prefetched)
            g["like_for_like"] = {k: lfl.get(k) for k in
                                  ("page_count", "unique_documents", "passed",
                                   "total_words", "term_total")}
        except Exception as err:                       # never lose the report over it
            print(f"   like-for-like gate not computed ({err})", flush=True)
    if sd:
        g["method"] = "sitemap+links"
        sm_keys = {sw.key(u) for u in sd["sitemap_urls"]}
        for p_ in g["pages"]:
            f = sd["pages"].get(sw.key(p_["url"])) or {}
            p_["in_sitemap"] = sw.key(p_["url"]) in sm_keys
            p_["inbound"] = f.get("inbound")
            p_["depth"] = f.get("depth")
            p_["noindex"] = f.get("noindex")
        site_summary = {
            "sitemap_files": sd["sitemap_files"],
            "sitemap_count": sd["sitemap_count"],
            "sitemap_truncated": sd["sitemap_truncated"],
            "found_by_links": sum(1 for p_ in g["pages"] if not p_["in_sitemap"]),
            "crawl_truncated": sd["crawl_truncated"],
        }
        print("   site structure …", flush=True)
        site_structure = pr.run(
            "site_structure", "Cross-page checks — links, sitemap, duplicates",
            lambda: sw.structure(sd, sw.hosts_check(sd)),
            check=lambda r: r is not None,
            source="The whole-site crawl",
            requires=["site_discovery"],
            remedy="Needs the page discovery to succeed.",
            client_note="The checks that compare pages against each other could not "
                        "run on this report.",
            fallback=None)
        for f in sd["pages"].values():       # working data — never written out
            f.pop("_body", None)

    money = None
    if args.money_page:
        money = next((p for p in g["pages"]
                      if p["url"].rstrip("/").endswith(args.money_page.rstrip("/"))), None)
        if money is None and g["pages"]:
            pr.note("money_named", "Named money page found in the crawl", probes.EMPTY,
                    source="Crawl gate",
                    remedy=f"--money-page {args.money_page} matched no crawled URL. "
                           "Check the path, or that the page is in the sitemap.",
                    client_note="The page you nominated as the main target was not "
                                "found in the crawl, so the strongest page we did "
                                "find is measured instead.")
    if money is None and g["pages"]:
        money = max(g["pages"], key=lambda p: (p.get("term_count") or 0, p["words"]))
    if money is None:
        pr.note("money", "Target page identified", probes.SKIPPED,
                source="Crawl gate", remedy="No pages were crawled — fix the gate first.",
                client_note="No page could be measured because the crawl returned nothing.")

    print("   competitors …", flush=True)
    # Discovery from the target queries, when a search API key is configured.
    # No key: the operator's own list is used and the report says so. We do not
    # scrape Google — results would be personalised, location-dependent and not
    # reproducible, which is exactly what this report claims not to be.
    provider = srch.provider()
    disc = pr.run(
        "discovery", "Competitor discovery from the target queries",
        lambda: srch.discover(args.queries, base,
                              country=args.country,
                              location=args.location or (f"{args.suburb}, {country_name(args.country)}"
                                                         if args.suburb else None)),
        check=lambda r: bool(r and r.get("competitors")),
        source=(f"{srch.PROVIDER_LABEL.get(provider, provider)} search API"
                if provider else "Search API — Brave free tier"),
        configured=bool(provider),
        remedy="No search key on this server, so the competition could not be "
               "looked up. Either paste the competitor URLs into 'Competitor pages' "
               "yourself, or have the Brave key set (free tier). If a key is already "
               "set the request was refused — check it is live and the monthly quota "
               "is not spent. CLI: BOLDPIQ_BRAVE_KEY.",
        client_note="Competitors below are the ones we supplied by hand rather than "
                    "ones we found by running your target searches.",
        fallback={"provider": provider, "queries": [], "competitors": [],
                  "aggregators": [], "own_positions": []})
    if not args.queries:
        pr.note("queries", "Target queries supplied", probes.NOT_CONFIGURED,
                remedy="Fill in 'Target queries'. Without them there is nothing to "
                       "find competitors for and no Search Console rows to match. "
                       "CLI: --queries.",
                client_note="No target searches were set for this report.")

    comp_urls = list(args.competitors)
    if disc.get("competitors"):
        comp_urls = [c["url"] for c in disc["competitors"]] + [
            u for u in comp_urls if u not in {c["url"] for c in disc["competitors"]}]

    c = pr.run(
        "competitors", "Competitor pages measured live",
        lambda: comp.compare(money or {}, comp_urls, term=term),
        check=lambda r: bool(r and r.get("competitors")),
        source="Each competitor page fetched and measured directly",
        configured=bool(comp_urls and money),
        requires=["gate"],
        remedy="No competitor URLs to measure — either the search lookup found "
               "none, or none were typed into 'Competitor pages'. Leaving that field "
               "empty is fine when the search key is working; with no key it is the "
               "only way to fill this section. CLI: --competitors.",
        client_note="We have no competitor pages to compare against, so the "
                    "comparison below is not a measure of where you stand.",
        fallback=None)
    if c and disc.get("competitors"):
        pos = {d["url"]: d for d in disc["competitors"]}
        for row in c["competitors"]:
            hit = pos.get(row["url"])
            if hit:
                row["found_for"] = hit["query"]
                row["position"] = hit["position"]

    print("   proximity …", flush=True)
    pin = None
    pin_src = ""
    if args.pin:
        try:
            lat, lon = [float(x) for x in args.pin.split(",")]
            pin = (lat, lon)
            pin_src = "map pin supplied directly"
        except ValueError:
            pr.note("pin", "Map pin read", probes.FAILED, source="--pin",
                    detail=f"could not parse {args.pin!r}",
                    remedy="'Map pin' takes lat,lon as two decimals, e.g. "
                           "-26.13,27.96. Easier: paste the Google Maps link into "
                           "'Google Maps link' instead and the pin is read from it. "
                           "CLI: --pin.",
                    client_note="The map pin could not be read, so distances to your "
                                "target areas were not measured.")
    elif args.gbp_url:
        pin = pr.run("pin", "Map pin read from the Google Maps link",
                     lambda: geo_mod.pin_from_maps_url(args.gbp_url),
                     check=lambda r: bool(r),
                     source="Google Maps share link",
                     remedy="That Maps link carries no coordinates. Open the "
                            "profile in Google Maps, use Share → Copy link, and "
                            "paste that. Failing which, read the lat,lon off the "
                            "full Maps URL into 'Map pin'. CLI: --gbp-url / --pin.",
                     client_note="We could not read the coordinates from the profile "
                                 "link, so distances to your target areas were not "
                                 "measured.")
        pin_src = "Google Maps share link"
    else:
        pr.note("pin", "Map pin read", probes.NOT_CONFIGURED,
                remedy="Open 'Proximity and page targeting' and paste the client's "
                       "Google Maps link (or their map pin as lat,lon). Without it "
                       "no distance to any target area can be measured, and the "
                       "whole area-strategy section is absent. CLI: --gbp-url / --pin.",
                client_note="No business location was supplied, so we could not "
                            "measure how close you are to each target area.")

    prox = pr.run(
        "proximity", "Distance from the pin to each target area",
        lambda: geo_mod.measure(pin, args.areas, country_name(args.country)),
        check=lambda r: bool(r and r.get("areas")),
        source="OpenStreetMap Nominatim geocoding, straight-line distance",
        configured=bool(pin and args.areas),
        requires=["pin"] if (args.pin or args.gbp_url) else None,
        remedy="Needs both a map pin and a list of areas. If both were given, the "
               "OpenStreetMap lookup refused the request or could not place the area "
               "names — use full names in 'Service areas', e.g. 'Northcliff, "
               "Johannesburg'. CLI: --areas.",
        client_note="We could not measure the distance from your location to each "
                    "target area for this report.",
        fallback={"pin": pin, "areas": []})

    areas = pr.run(
        "area_strategy", "Target areas cross-referenced against your pages",
        lambda: geo_mod.area_strategy(prox, g["pages"]),
        check=lambda r: bool(r),
        configured=bool(prox.get("areas")),
        requires=["proximity", "gate"],
        source="Proximity readings matched against the crawled pages",
        remedy="Needs a measured distance and a successful crawl. Fix the map pin "
               "first — this section is built on top of it.",
        client_note="Without distances we cannot tell you which areas are realistic "
                    "to target and which are not.",
        fallback=[])

    print("   search console …", flush=True)
    sc = pr.run(
        "gsc", "Search Console export",
        lambda: gsc_mod.load(args.gsc),
        check=lambda r: bool(r and r.get("connected")),
        source="Your own Search Console export",
        configured=bool(args.gsc),
        remedy="No export attached. Open 'Search Console export' and attach the "
               "file: in Search Console go to Performance → Search results, switch "
               "ON all four metric cards (Average CTR and Average position are off "
               "by default), then Export. .xlsx, .csv and .zip all work. "
               "CLI: --gsc.",
        client_note="Your Search Console data was not included in this report, so "
                    "your actual Google positions and impressions are not shown. "
                    "This is the only accurate source for them.",
        fallback={"connected": False})
    sc_targets = []
    if sc.get("connected"):
        sc_targets = pr.run(
            "gsc_targets", "Target queries matched in Search Console",
            lambda: gsc_mod.for_targets(sc, args.queries),
            check=lambda r: bool(r),
            source="Your own Search Console export",
            requires=["gsc"],
            remedy="The export loaded but none of the target queries appear in it.",
            client_note="None of your target searches appear in Search Console yet. "
                        "That means Google has recorded no impressions for them — "
                        "normal for a new or recently changed site.",
            fallback=[])
    sc_orphans = []
    if sc.get("connected"):
        # Pages Google ranks that the sitemap never listed. Only findable by
        # holding the crawl and the export side by side, which is the one thing
        # neither a crawler nor Search Console can do alone.
        # "Absent from your sitemap" is measured against the sitemap, while
        # `measured` records whether the link crawl reached the page anyway.
        in_sm = [p_["url"] for p_ in g["pages"] if p_.get("in_sitemap", True)]
        sc_orphans = gsc_mod.orphans(sc, in_sm, [p_["url"] for p_ in g["pages"]])

    print("   accumulated signals …", flush=True)
    comp_domains = list(comp_urls or [])
    authority = pr.run(
        "authority", "Domain age and index footprint",
        lambda: auth.compare(base, comp_domains, srch),
        check=lambda r: bool(r and r.get("client")),
        source="RDAP registration records and the Internet Archive",
        configured=bool(comp_domains),
        remedy="Needs a competitor set before there is anything to compare against "
               "— fix competitor discovery first. Note RDAP does not publish "
               ".co.za registration dates, so age falls back to first-seen in the "
               "Internet Archive.",
        client_note="We could not establish how long these domains have been "
                    "running, so the age comparison is not shown.",
        fallback=None)

    if authority:
        cc_errors = []
        opr = pr.run(
            "link_graph", "Referring domains from the open web graph",
            lambda: auth.open_page_rank([base] + comp_domains, errors=cc_errors),
            check=lambda r: bool(r),
            source="Common Crawl host graph",
            remedy="The Common Crawl graph endpoint did not answer. It is a "
                   "community-run service. Re-run later; if it stays down, the "
                   "column is simply absent and no other section is affected.",
            client_note="We could not reach the open web link graph when this "
                        "report ran, so referring-domain counts are missing rather "
                        "than zero. Do not read a blank here as 'no links'.",
            fallback={})
        if cc_errors:
            pr.note("link_graph_partial", "Link graph — per-domain lookups",
                    probes.FAILED, source="Common Crawl host graph",
                    detail="; ".join(cc_errors[:3]),
                    remedy="Some domains answered and some did not. Counts shown are "
                           "real; blanks are unknown, not zero.",
                    client_note="Some domains could not be looked up in the link "
                                "graph. A blank means unknown, not zero.")
        if opr:
            authority["client"]["strength"] = opr.get(authority["client"]["domain"])
            for o in authority["competitors"]:
                o["strength"] = opr.get(o["domain"])

    # Top few per query only. A broad query like "handyman {suburb}" returns
    # thirty-odd businesses; the map pack shows three, and a table of thirty is
    # noise the client cannot act on.
    def _pack():
        out = []
        for q in args.queries[:2]:
            out += auth.local_pack(q, srch, country=args.country)[:5]
        return out

    pack = pr.run(
        "local_pack", "Map pack field for the target queries",
        _pack,
        check=lambda r: bool(r),
        source=(f"{srch.PROVIDER_LABEL.get(provider, provider)} local results"
                if provider else "Search API local results — Brave free tier"),
        configured=bool(provider and args.queries),
        remedy="The map-pack lookup needs the search key and at least one target "
               "query. If both are present, the local endpoint returned nothing — "
               "check the quota, and that 'Target suburb' names a place that can be "
               "resolved.",
        client_note="We could not retrieve the map pack for your target searches, so "
                    "the businesses you are competing against on the map are not "
                    "listed here.",
        fallback=[])

    rev = auth.parse_review_counts(args.competitor_reviews)
    if args.competitor_reviews and not rev:
        pr.note("competitor_reviews", "Competitor review counts parsed", probes.FAILED,
                source="Operator-supplied counts",
                detail="nothing parsed from the supplied string",
                remedy="Nothing could be read from what was typed into 'Review "
                       "counts'. Expected 'Name:41, Name:18' — a colon or an equals "
                       "sign between name and number, commas between entries.",
                client_note="Competitor review counts were not read for this report.")
    elif not args.competitor_reviews:
        pr.note("competitor_reviews", "Competitor review counts", probes.NOT_CONFIGURED,
                source="Counted by hand from the map pack",
                remedy="Open 'Review counts' and type them in as "
                       "'Name:41, Name:18' — read straight off the map pack in "
                       "Google Maps, about two minutes. No free API supplies these, "
                       "and review count is one of the strongest map-pack signals, "
                       "so the gap is worth making exact. CLI: --competitor-reviews.",
                client_note="Review counts for the businesses above the fold were "
                            "not collected for this report. Review count is one of "
                            "the strongest map-pack signals, so this is worth adding.")
    for p_ in pack:
        p_["reviews"] = rev.get((p_["name"] or "").strip().lower())

    print("   technical …", flush=True)
    robots = pr.run(
        "robots", "robots.txt and AI-crawler access",
        lambda: gate_mod.robots_and_ai(base),
        check=lambda r: r is not None,
        source="Direct fetch of /robots.txt and /llms.txt",
        remedy="Could not fetch robots.txt. Confirm the host answers on that path.",
        client_note="We could not check whether search and AI crawlers are allowed "
                    "on your site.",
        fallback=None)
    host = pr.run(
        "canonical_host", "Canonical host and redirect chain",
        lambda: gate_mod.canonical_host(base),
        check=lambda r: r is not None,
        source="Following redirects from all four host forms",
        remedy="Could not resolve one or more host variants — check DNS and the "
               "www/non-www redirect.",
        client_note="We could not confirm which version of your domain is the "
                    "canonical one.",
        fallback=None)
    links = pr.run(
        "links", "Internal link graph",
        lambda: gate_mod.link_graph(g["pages"], base),
        check=lambda r: r is not None,
        source="Links parsed from the crawled pages",
        requires=["gate"],
        remedy="Needs a successful crawl.",
        client_note="We could not map how your pages link to each other.",
        fallback=None)
    tech = pr.run(
        "technical", "Technical findings",
        lambda: gate_mod.technical(base, g["pages"]),
        check=lambda r: r is not None,
        source="Measured from the crawled pages and response headers",
        requires=["gate"],
        remedy="Needs a successful crawl.",
        client_note="Technical checks could not run without a successful crawl.",
        fallback=None)

    for _p in g["pages"]:            # working data only — never written to JSON
        _p.pop("_haystack", None)
        _p.pop("_haystack_raw", None)

    s = pr.summary()
    if s["broken"]:
        print(f"   ⚠ {s['broken']} source(s) failed — see the fix list", flush=True)
    if s["unconnected"]:
        print(f"   · {s['unconnected']} source(s) not connected for this run", flush=True)

    return {
        "generated": dt.datetime.now().isoformat(timespec="seconds"),
        "period": dt.date.today().strftime("%Y-%m"),
        "client": args.client or urllib.parse.urlparse(base).netloc,
        "url": base,
        "suburb": args.suburb,
        "queries": args.queries,
        "gate": g,
        "money_page": money,
        "competitors": c,
        "discovery": disc,
        "proximity": prox,
        "area_strategy": areas,
        "robots": robots,
        "canonical_host": host,
        "links": links,
        "technical": tech,
        "profile": {"connected": False, "reviews": None},
        "search_console": {**sc, "targets": sc_targets, "orphans": sc_orphans,
                           "by_page": gsc_mod.by_page(sc)},
        "site_discovery": site_summary,
        "site_structure": site_structure,
        "authority": authority,
        "local_pack": pack,
        "own_reviews": (int(args.reviews) if str(args.reviews).isdigit() else None),
        "tier": "A",
        "probes": pr.as_dict(),
        "_probes": pr,               # live object for the HTML build; stripped before JSON
    }


# ── html ─────────────────────────────────────────────────────────────────────

CSS = """
*{box-sizing:border-box}
body{margin:0;font-family:Geist,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
 color:%(ink)s;font-size:10.5pt;line-height:1.55;-webkit-print-color-adjust:exact;print-color-adjust:exact}
.page{padding:16mm 15mm;page-break-after:always}
.page:last-child{page-break-after:auto}
h1{font-size:30pt;font-weight:800;letter-spacing:-.03em;margin:0 0 6pt;line-height:1.05}
h2{font-size:16pt;font-weight:700;letter-spacing:-.02em;margin:0 0 4pt}
h3{font-size:11.5pt;font-weight:700;margin:14pt 0 4pt}
p{margin:0 0 7pt;max-width:74ch}
ul,ol{margin:0 0 8pt;padding-left:14pt}li{margin-bottom:3pt}
.eyebrow{font-size:7.5pt;letter-spacing:.18em;text-transform:uppercase;color:%(accent)s;font-weight:700;margin-bottom:8pt}
.lede{font-size:12pt;color:#3C4450;max-width:64ch}
.rule{height:2px;background:%(ink)s;margin:10pt 0 14pt}
table{border-collapse:collapse;width:100%%;font-size:9pt;margin:0 0 10pt}
th{text-align:left;font-size:7pt;letter-spacing:.1em;text-transform:uppercase;color:#6B7480;
 font-weight:700;padding:5pt 6pt;border-bottom:1.5px solid %(ink)s;background:#F5F3F0}
td{padding:5pt 6pt;border-bottom:.6px solid #E4E0DB;vertical-align:top}
td.n{font-variant-numeric:tabular-nums;white-space:nowrap}
.card{border:.8px solid #E4E0DB;border-radius:4px;padding:9pt 11pt;margin:0 0 8pt;background:#fff}
.card{page-break-inside:avoid;break-inside:avoid}
.card.good{border-left:3px solid %(good)s}
.card.bad{border-left:3px solid %(poor)s}
.card.warn{border-left:3px solid %(accent)s}
.tag{display:inline-block;font-size:7pt;font-weight:700;letter-spacing:.08em;text-transform:uppercase;
 padding:2pt 5pt;border-radius:2px;margin-right:4pt}
.tag.pass{background:#DFEEE7;color:%(good)s}
.tag.fail{background:#F7E4E1;color:%(poor)s}
.tag.na{background:#EEF0F2;color:#6B7480}
.grid{display:flex;gap:8pt;flex-wrap:wrap;margin-bottom:10pt}
.stat{flex:1 1 130pt;border:.8px solid #E4E0DB;border-radius:4px;padding:8pt 10pt;background:#fff}
.stat .k{font-size:7pt;letter-spacing:.1em;text-transform:uppercase;color:#6B7480;font-weight:700}
.stat .v{font-size:19pt;font-weight:800;letter-spacing:-.02em;line-height:1.1;margin-top:2pt}
.d{font-size:8pt;font-weight:700}
.d.up{color:%(good)s}.d.down{color:%(poor)s}.d.flat{color:#98A2B3}
.ring-num{font-size:26px;font-weight:800}
.src{font-size:7.5pt;color:#6B7480}
.note{background:#F5F3F0;border-left:3px solid %(accent)s;padding:8pt 10pt;margin:8pt 0;font-size:9.5pt}
.foot{position:running(f)}
.you{background:#FFF6F0;font-weight:700}
/* Inferred values carry a dotted underline and a marker so a reader can see at
   a glance which figures are readings and which are judgements. */
.inf{border-bottom:1px dotted #9AA4B0}
.inf sup{color:#C4541A;font-weight:700;font-size:7pt;margin-left:1pt}
.legend{display:flex;gap:16pt;flex-wrap:wrap;font-size:8pt;color:#6B7480;margin:4pt 0 10pt}
.legend b{color:#0B0F1C}
/* Data-collection rail. Sits down the right of the cover so the health of every
   source is the first thing seen, before any figure is read. */
.withrail{display:flex;gap:11mm;align-items:flex-start}
.withrail .main{flex:1 1 auto;min-width:0}
.rail{flex:0 0 52mm;border:.8px solid #E4E0DB;border-radius:4px;background:#FBFAF9;padding:9pt 10pt}
.rail h4{font-size:7.5pt;letter-spacing:.14em;text-transform:uppercase;color:#6B7480;
 font-weight:700;margin:0 0 2pt}
.rail .hd{font-size:9.5pt;font-weight:800;letter-spacing:-.01em;margin:0 0 7pt;line-height:1.25}
.rail .hd.bad{color:%(poor)s}
.chk{display:flex;gap:5pt;align-items:flex-start;font-size:7.6pt;line-height:1.35;
 padding:3.2pt 0;border-bottom:.5px solid #EDE9E4}
.chk:last-child{border-bottom:none}
.chk .dot{flex:0 0 6px;height:6px;border-radius:50%%;margin-top:3.4pt}
.chk .dot.ok{background:%(good)s}
.chk .dot.empty{background:#C9CDD3}
.chk .dot.not_conf{background:#D8A23C}
.chk .dot.skipped{background:%(poor)s}
.chk .dot.failed{background:%(poor)s}
.chk .t{flex:1 1 auto}
.chk .s{display:block;color:#8A929C;font-size:6.9pt;letter-spacing:.05em;text-transform:uppercase;font-weight:700}
.chk .s.failed{color:%(poor)s}
.chk .s.not_conf{color:#B0812A}
.chk .s.skipped{color:%(poor)s}
.rail .sum{font-size:7.4pt;color:#6B7480;margin-top:7pt;padding-top:6pt;border-top:.8px solid #E4E0DB}
/* A figure we could not obtain. Never rendered as a zero. */
.nodata{border:.8px dashed #C9A34E;border-radius:4px;background:#FDFBF4;
 padding:8pt 10pt;margin:0 0 9pt;font-size:9pt}
.nodata .lbl{font-size:7pt;letter-spacing:.12em;text-transform:uppercase;
 font-weight:700;color:#9A7420;display:block;margin-bottom:2pt}
.fixrow td{font-size:8.6pt}
/* Every-page table: up to ten columns on A4, so it runs a size smaller. */
table.pgt{font-size:7.9pt}
table.pgt th{font-size:6.4pt;padding:4pt 4pt}
table.pgt td{padding:3.4pt 4pt}
table.pgt thead{display:table-header-group}
table.pgt tr{page-break-inside:avoid}
.fixrow .why{color:#6B7480}
""" % {"ink": INK, "accent": ACCENT, "good": GOOD, "poor": POOR}


def coverage_line(d):
    """How the pages were found — one line under the cover figures."""
    sd = d.get("site_discovery")
    g = d["gate"]
    if not g.get("measured"):
        return ""
    if not sd:
        return ('<p class="src">Pages read from the sitemap only — the full-site walk '
                'did not run on this report.</p>')
    sm = (f"{sd['sitemap_count']} in the sitemap" if sd["sitemap_files"]
          else "no sitemap was found")
    extra = sd["found_by_links"]
    return (f'<p class="src">Every page on the site was measured: {sm}, and '
            f'{extra} more found only by following the site\'s own links'
            + (" (the walk stopped at its page limit)" if sd["crawl_truncated"] else "")
            + '.</p>')


def gsc_cell(v, fmt="{:,}"):
    return f'<td class="n">{fmt.format(v)}</td>' if v is not None else '<td class="n src">—</td>'


def build_html(d):
    g = d["gate"]
    used_keys = []
    pr = d.get("_probes") or probes.Probes()
    ps = pr.summary()
    gen = dt.datetime.fromisoformat(d["generated"]).strftime("%d %B %Y")
    score = round(100 * g["passed"] / max(1, g["total_checks"]))

    # The claim on the cover has to match what actually happened. If a source
    # was unreachable, saying "everything here is measured" is still true of
    # what is shown, but the reader is told what is absent in the same breath.
    # Three provenances, and the report never blurs them: a direct measurement,
    # the client's own account data, or a figure read by hand and labelled as
    # read by hand. Anything that is our judgement rather than a reading carries
    # a dotted underline and a marker. Nothing is estimated.
    base = ("Every figure below is one of three things: a measurement we took of "
            "the live page, data from the client's own Google account, or a count "
            "read by hand and labelled as such. Where a figure is our assessment "
            "rather than a reading it is underlined and marked ⁱ — nothing "
            "anywhere in this report is estimated or modelled.")
    if ps["broken"]:
        honesty = base + (" Sources we could not reach on this run are named on the "
                          "right and marked where they fall: a figure we could not "
                          "obtain is shown as missing, never as zero.")
    elif ps["unconnected"]:
        honesty = base + (" The panel on the right lists what was measured and what "
                          "is not yet connected, so the shape of what is missing is "
                          "as visible as what is here.")
    else:
        honesty = base + " Every source answered on this run."

    # ── cover + headline
    parts = [f"""<!doctype html><html><head><meta charset="utf-8">
<style>@page{{size:A4;margin:0}}{font_face()}{CSS}</style></head><body>
<div class="page withrail"><div class="main">
  <div class="eyebrow">Boldpiq · Ranking Report · {e(d['period'])}</div>
  <h1>{e(d['client'])}</h1>
  <p class="lede">Where this site stands in search, measured live on {e(gen)}.
     {e(honesty)}</p>
  <div class="rule"></div>
  <div class="grid">
    <div class="stat"><div class="k">Pages live</div><div class="v">{
      g['page_count'] if g.get('measured') else '—'}</div></div>
    <div class="stat"><div class="k">Unique documents</div><div class="v">{
      g['unique_documents'] if g.get('measured') else '—'}</div></div>
    <div class="stat"><div class="k">Total words</div><div class="v">{
      format(g['total_words'], ',') if g.get('measured') else '—'}</div></div>
    <div class="stat"><div class="k">Gate checks</div><div class="v">{
      f"{g['passed']}/{g['total_checks']}" if g.get('measured') else '—'}</div></div>
  </div>
  {coverage_line(d)}
  <p><strong>Target queries:</strong> {e(', '.join(d['queries']) or '—')}</p>"""]

    # ── movement
    prev = d.get("_previous")
    rows = hist.deltas(d, prev)
    if prev:
        parts.append('<h3>Movement since the last report</h3><table><thead><tr>'
                     '<th>Measure</th><th>Last</th><th>Now</th><th>Change</th></tr></thead><tbody>')
        for r in rows:
            chg = ('<span class="d flat">new baseline</span>' if r.get("not_comparable")
                   else delta_span(r["change"]))
            full = (f' <span class="src">({r["full"]:,} whole site)</span>'
                    if isinstance(r.get("full"), (int, float)) else "")
            parts.append(f'<tr><td>{e(r["label"])}</td><td class="n">{e(r["was"])}</td>'
                         f'<td class="n">{e(r["now"])}{full}</td><td class="n">{chg}</td></tr>')
        parts.append('</tbody></table>')
        if hist.method_changed(d, prev) and any(r.get("full") is not None for r in rows):
            parts.append('<p class="src">This is the first report that measures every '
                         'page on the site rather than only the pages in the sitemap. '
                         'So the movement above compares the same sitemap pages as last '
                         'time — a real change on those pages still shows — and the '
                         'whole-site figure is given in brackets.</p>')
        parts.append(f'<div class="note">{e(hist.summarise(rows))}</div>')
    else:
        parts.append('<div class="note">First report for this client — this becomes '
                     'the baseline. From next period every measure is shown with its '
                     'change alongside.</div>')
    parts.append("</div>" + rail(pr) + "</div>")

    # ── the four factors
    prox = d.get("proximity") or {}
    nearest = (prox.get("areas") or [{}])[0]
    parts.append(f"""<div class="page">
  <div class="eyebrow">01</div><h2>Where you stand</h2>
  <p>Four factors decide the local pack. They are not equal, and three of them
     have nothing to do with the website. {e(cit.cite('local_ranking_three'))}</p>
  <table><thead><tr><th>Factor</th><th>Status</th><th>Detail</th></tr></thead><tbody>
    <tr><td><strong>Proximity</strong></td><td>{e(nearest.get('band') or 'Not measured').title()}</td>
        <td>{(f"{e(nearest.get('km'))} km to {e(nearest.get('area'))} — fixed, sets the ceiling")
             if nearest.get('km') is not None
             else 'No verified map pin was available for this report'}</td></tr>
    <tr><td><strong>Reviews</strong></td><td>Not connected</td>
        <td>Largest changeable factor. {e(cit.cite('reviews_ranking_factor'))}</td></tr>
    <tr><td><strong>Category</strong></td><td>Not connected</td>
        <td>Grant profile access to include</td></tr>
    <tr><td><strong>Prominence</strong></td><td>{
        f"{g['passed']}/{g['total_checks']} checks" if g.get('measured') else 'Not measured'}</td>
        <td>{'Site, citations and business name' if g.get('measured')
             else 'The site could not be read on this run, so its contribution here is unknown'}</td></tr>
  </tbody></table>""")
    used_keys += ["local_ranking_three", "reviews_ranking_factor"]
    parts.append(missing(pr, "pin", "Proximity"))
    parts.append(missing(pr, "proximity", "Proximity"))
    parts.append("</div>")

    areas = d.get("area_strategy") or []
    if not areas and (pr.client_note("proximity") or pr.client_note("area_strategy")):
        # The section that would have gone here. Silently dropping it is how a
        # client ends up believing every area they named is winnable.
        parts.append('<div class="page"><div class="eyebrow">01b</div>'
                     '<h2>Every area you serve, and what is realistic in each</h2>'
                     + missing(pr, "proximity", "Distance to each area")
                     + missing(pr, "area_strategy", "Area-by-area verdict")
                     + '<p>This section compares how far you are from each area you '
                       'want to serve against whether you have a page targeting it. '
                       'It is the section that tells you which areas are realistic. '
                       'It could not be produced for this report.</p></div>')
    if areas:
        addr = (prox.get("address") or {})
        where = ", ".join(x for x in (addr.get("suburb") or addr.get("neighbourhood"),
                                      addr.get("city") or addr.get("town")) if x)
        parts.append(f"""<div class="page"><div class="eyebrow">01b</div>
  <h2>Every area you serve, and what is realistic in each</h2>
  <p>Two different things decide whether you appear for "service + area", and they
     work independently. Understanding the difference is what stops a target area
     being promised when it was never winnable.</p>

  <div class="card"><strong>Your address on Google Maps decides the map pack</strong>
    <p>Google ranks local results on relevance, distance and prominence.
       {e(cit.cite('local_ranking_three'))} <em>Distance</em> is measured from your
       verified map pin to wherever the person searching happens to be — not from
       the areas you list as served. Your pin sits in
       <strong>{e(where or 'the recorded location')}</strong>, and that is fixed.
       It sets a hard ceiling on how far out you can realistically appear in the
       three map results.</p>
    <p><strong>Listing an area as "served" does not create proximity to it.</strong>
       Service areas govern what displays on the profile and which searches you are
       eligible for. They do not move the pin.</p></div>

  <div class="card"><strong>Your website decides the organic result</strong>
    <p>The blue links below the map are a different contest, and distance plays no
       part in it. What matters there is whether a real page exists for that area —
       one that could only be about that place. That is entirely within your control,
       which is why a further-out area can still be won on the website even when the
       map pack is out of reach.</p></div>

  <table><thead><tr><th>Area</th><th>km from pin</th><th>Map pack</th>
  <th>Page on site</th><th>What is realistic</th></tr></thead><tbody>""")
        used_keys.append("local_ranking_three")
        for a in areas:
            page = (a["page"].replace(d["url"], "") if a["page"] else "—")
            words = f' <span class="src">{a["page_words"]:,}w</span>' if a["page_words"] else ""
            parts.append(f'<tr><td><strong>{e(a["area"])}</strong></td>'
                         f'<td class="n">{e(a["km"])}</td><td>{inf(a["map_pack"])}</td>'
                         f'<td>{e(page)}{words}</td><td>{inf(a["verdict"])}</td></tr>')
        parts.append("</tbody></table>" + LEGEND)

        gaps = [a for a in areas if not a["page"] and a["map_pack"] in ("Strong", "Good")]
        far = [a for a in areas if a["map_pack"] == "Weak"]
        if gaps:
            parts.append('<div class="card warn"><strong>Close enough to win, no page for it</strong>'
                         '<p>Proximity already supports these areas — the website does not yet. '
                         'A page for each is the cheapest ranking gain available.</p><ul>'
                         + "".join(f'<li>{e(a["area"])} — {e(a["km"])} km</li>' for a in gaps)
                         + "</ul></div>")
        if far:
            parts.append('<div class="card bad"><strong>Too far for the map pack</strong>'
                         '<p>These can only realistically be won on the website, and only with a '
                         'genuine page for each. They should not be promised as map results.</p><ul>'
                         + "".join(f'<li>{e(a["area"])} — {e(a["km"])} km</li>' for a in far)
                         + "</ul></div>")

        parts.append('<div class="note"><strong>The rule underneath all of this.</strong> '
                     'The pin decides where you can appear on the map and cannot be changed. '
                     'The website decides where you can appear in the links and can. '
                     'A page for an area you are nowhere near will not put you in the map pack, '
                     'and being close to an area you have no page for will not win you the '
                     'organic result.</div>')
        parts.append('<p class="src">Distances measured from the verified map pin to the centre '
                     'of each area. Page detection is by URL, title and body mentions on the '
                     'live site.</p></div>')

    # ── crawl gate
    if not g.get("measured"):
        parts.append('<div class="page"><div class="eyebrow">02</div>'
                     '<h2>Can Google read the site</h2>'
                     + missing(pr, "gate", "The crawl")
                     + '<p>This section normally fetches every page on the site and '
                       'checks that each returns its own document. No score is shown '
                       'because nothing was measured — a check with nothing to check '
                       'has not passed.</p></div>')
    parts.append(f"""<div class="page" style="{'display:none' if not g.get('measured') else ''}">
  <div class="eyebrow">02</div><h2>Can Google read the site</h2>
  <p>The check no rendering-based audit performs: does every page return its own
     document? A site can score well elsewhere while being structurally incapable
     of ranking a single page. {e(cit.cite('crawl_then_index'))}</p>
  <div style="display:flex;gap:14pt;align-items:center;margin:10pt 0">{ring(score)}
    <div><strong>{g['passed']} of {g['total_checks']} checks passing</strong>
    <p style="margin:2pt 0 0">{'All structural checks pass. Every page is eligible to rank.'
      if g['passed'] == g['total_checks'] else
      'Failures below are blocking — content work cannot compensate for them.'}</p></div>
  </div>
  <table><thead><tr><th>Check</th><th>Result</th><th>Detail</th></tr></thead><tbody>""")
    used_keys.append("crawl_then_index")
    for c in g["checks"]:
        tag = '<span class="tag pass">Pass</span>' if c["pass"] else '<span class="tag fail">Fail</span>'
        parts.append(f'<tr><td>{e(c["label"])}</td><td>{tag}</td><td class="n">{e(c["detail"])}</td></tr>')
    parts.append("</tbody></table>")

    if g["shell_pages"]:
        parts.append('<div class="card bad"><strong>Pages with no document behind them</strong>'
                     '<p>These URLs return the same content as an address that does not exist, '
                     'which means there is no page there to rank. Creating them as real pages '
                     'is always the fix.</p><ul>')
        for u in g["shell_pages"][:12]:
            parts.append(f"<li>{e(u)}</li>")
        parts.append("</ul></div>")
    if g["duplicate_titles"]:
        parts.append('<div class="card warn"><strong>Repeated titles</strong>'
                     '<p>Pages sharing a title compete with each other. Each needs its own.</p><ul>'
                     + "".join(f"<li>{e(t)}</li>" for t in g["duplicate_titles"][:8]) + "</ul></div>")
    parts.append("</div>")

    # ── pages
    # Every page the site publishes, with Google's own figures for it when a
    # Search Console export is attached. A page absent from the export had no
    # impressions in the period — shown as 0 only because Search Console lists
    # every page it showed at least once, so absence IS the measurement.
    scd = d.get("search_console") or {}
    bp = scd.get("by_page") or {}
    with_gsc = bool(scd.get("connected") and scd.get("pages"))
    live_pages = [p for p in g["pages"] if (p.get("status") or 200) == 200]

    def gpath(u):
        return gsc_mod._path(u)

    def sort_key(p):
        row = bp.get(gpath(p["url"])) or {}
        return (-(row.get("impressions") or 0), -p["words"]) if with_gsc else (-p["words"],)

    head = ('<th>Page</th><th>Words</th>'
            f'<th>"{e(d["suburb"] or "term")}"</th><th>H1</th><th>Schema</th><th>Canonical</th>'
            + ('<th>Linked from</th>' if d.get("site_discovery") else '')
            + ('<th>Impr.</th><th>Clicks</th><th>Pos.</th>' if with_gsc else ''))
    intro = ('<p>Every live page on the site, measured on the live page'
             + ('; impressions, clicks and average position are Google\'s own figures '
                'from Search Console for the period exported' if with_gsc else '')
             + '. <em>Linked from</em> is how many other pages on the site link to '
               'it. Pages the sitemap does not list are marked — they were found by '
               'following the site\'s own links.</p>')
    parts.append('<div class="page"><div class="eyebrow">03</div><h2>Every page, measured</h2>'
                 + intro + f'<table class="pgt"><thead><tr>{head}</tr></thead><tbody>')
    for p in sorted(live_pages, key=sort_key):
        path = p["url"].replace(d["url"], "") or "/"
        canon = "self" if p["canonical_self"] else ("missing" if not p["canonical"] else "other")
        flags = []
        if p.get("in_sitemap") is False:
            flags.append("not in sitemap")
        if p.get("noindex"):
            flags.append("noindex")
        flag = f' <span class="src">({e(", ".join(flags))})</span>' if flags else ""
        row = (f'<tr><td>{e(path)}{flag}</td><td class="n">{p["words"]:,}</td>'
               f'<td class="n">{e(p.get("term_count"))}</td><td class="n">{p["h1_count"]}</td>'
               f'<td class="n">{p["schema_blocks"]}</td><td class="n">{canon}</td>')
        if d.get("site_discovery"):
            row += gsc_cell(p.get("inbound"))
        if with_gsc:
            gr = bp.get(gpath(p["url"])) or {"impressions": 0, "clicks": 0, "position": None}
            row += (gsc_cell(gr["impressions"]) + gsc_cell(gr["clicks"])
                    + gsc_cell(gr["position"], "{}"))
        parts.append(row + "</tr>")
    parts.append("</tbody></table>")

    if with_gsc:
        silent = [p for p in live_pages if not p.get("noindex")
                  and not (bp.get(gpath(p["url"])) or {}).get("impressions")]
        if silent:
            shown = ", ".join(e(p["url"].replace(d["url"], "") or "/") for p in silent[:12])
            more = f" and {len(silent) - 12} more" if len(silent) > 12 else ""
            parts.append(
                f'<div class="card warn"><strong>{len(silent)} of {len(live_pages)} live '
                f'pages had no impressions on Google in this period</strong>'
                '<p>Google did not show these pages for any search in the period '
                'exported — either they are not indexed yet, or they are indexed and '
                'not ranking for anything anyone searched. Each is a page doing no '
                'work in search. Check its indexing state under Search Console → URL '
                'inspection, and give it a clear subject and internal links.</p>'
                f'<p class="src">{shown}{more}</p></div>')
    parts.append("</div>")

    # ── competitors
    c = d.get("competitors")
    if not c:
        parts.append('<div class="page"><div class="eyebrow">04</div>'
                     '<h2>Your competition, measured</h2>'
                     + missing(pr, "discovery", "Finding the field")
                     + missing(pr, "competitors", "Measuring the field")
                     + '<p>Without a competitive set we cannot tell you what it takes '
                       'to outrank the sites currently above you. Nothing in this '
                       'report should be read as a comparison until this section '
                       'returns.</p></div>')
    if c:
        disc = d.get("discovery") or {}
        if disc.get("provider"):
            qs = ", ".join(q["query"] for q in disc.get("queries", []))
            label = disc.get("provider_label", "a search API")
            is_google = disc.get("google_positions")
            prov_note = (
                '<div class="card"><strong>How this list was chosen</strong>'
                f'<p>These are the sites ranking for {e(qs)}, retrieved from '
                f'{e(label)} — an API with published terms, not scraped, and not '
                f'our opinion of who the competition is.</p>'
                + ('' if is_google else
                   f'<p><strong>{e(label)} runs its own index, so the order differs '
                   f'from Google\'s.</strong> It is used here to assemble the field, '
                   f'which overlaps closely across engines — a local business with a '
                   f'real page for the area ranks on all of them. Every figure in the '
                   f'table below is then measured directly on the live page, so it is '
                   f'the same number whichever engine surfaced the URL. Positions '
                   f'shown are that engine\'s, and are never presented as Google '
                   f'rankings — the client\'s own Google position comes from Search '
                   f'Console.</p>')
                + '<p>Directories and social profiles are listed separately below, '
                  'because a service page cannot be compared like-for-like with a '
                  'listing page.</p></div>')
        else:
            prov_note = (
                '<div class="card warn"><strong>How this list was chosen</strong>'
                '<p>Supplied manually for this report. Configure a search API key '
                'and the competition is discovered from the target queries instead, '
                'with the position each site held recorded alongside it.</p></div>')
        parts.append(f"""<div class="page"><div class="eyebrow">04</div>
  <h2>Your competition, measured</h2>
  <p>Every figure below was measured by us on the live competitor page. Nothing is
     claimed or estimated.</p>
  {prov_note}
  <table><thead><tr><th>Site</th><th>Words</th><th>"{e(d['suburb'] or 'term')}"</th>
  <th>Schema</th><th>Prices</th></tr></thead><tbody>""")
        cl = c["client"]
        parts.append(f'<tr class="you"><td>You — {e(cl.get("url","").replace(d["url"],"") or "/")}</td>'
                     f'<td class="n">{cl.get("words",0):,}</td><td class="n">{e(cl.get("term_count"))}</td>'
                     f'<td class="n">{cl.get("schema_blocks",0)}</td>'
                     f'<td class="n">{"yes" if cl.get("schema_blocks") else "—"}</td></tr>')
        for r in c["competitors"]:
            _d = d.get("discovery") or {}
            _eng = "" if _d.get("google_positions") else f' on {e(_d.get("provider_label",""))}'
            pos = (f'<br><span class="src">#{r["position"]} for “{e(r["found_for"])}”{_eng}</span>'
                   if r.get("position") else "")
            parts.append(f'<tr><td>{e(urllib.parse.urlparse(r["url"]).netloc)}{pos}</td>'
                         f'<td class="n">{r["words"]:,}</td><td class="n">{e(r.get("term_count"))}</td>'
                         f'<td class="n">{r["schema_blocks"]}</td>'
                         f'<td class="n">{"yes" if r["publishes_prices"] else "no"}</td></tr>')
        parts.append("</tbody></table>")
        aggs = (d.get("discovery") or {}).get("aggregators") or []
        if aggs:
            parts.append('<div class="card"><strong>Directories also ranking for these '
                         'queries</strong><p>Not competitors to out-write — but pages the '
                         'client should be listed on, because they occupy results the '
                         'client cannot otherwise reach, and AI assistants read them.</p><ul>'
                         + "".join(f'<li>{e(a["host"])} — #{e(a["position"])} for '
                                   f'“{e(a["query"])}”</li>' for a in aggs)
                         + "</ul></div>")
        own = (d.get("discovery") or {}).get("own_positions") or []
        if own:
            parts.append('<div class="card good"><strong>Where you already appear</strong><ul>'
                         + "".join(f'<li>#{e(o["position"])} for “{e(o["query"])}” — '
                                   f'{e(o["url"].replace(d["url"], "") or "/")}</li>'
                                   for o in own[:6]) + "</ul></div>")
        leads = c["leads_on"]
        parts.append(f'<div class="card {"good" if leads else "warn"}">'
                     f'<strong>{"You lead on: " + ", ".join(leads) if leads else "You do not yet lead on any measured factor."}</strong>'
                     '<p>' + inf(
                         f'Ranked {c["ranks"]["words"]} of {c["field_size"]} on content '
                         f'depth, {c["ranks"]["term_count"]} on suburb relevance, '
                         f'{c["ranks"]["schema_blocks"]} on structured data'
                     ) + '.</p></div>' + LEGEND)
        parts.append('<div class="note"><strong>What this does and does not mean.</strong> '
                     'These are the factors the website controls, and they decide the organic '
                     'result. They do not decide the map pack, which is set by proximity, '
                     'category and reviews. Leading here makes you eligible to win; it does '
                     'not by itself make you chosen.</div>')
        parts.append("</div>")

    # ── the map pack, and accumulated signals
    a = d.get("authority")
    pack = d.get("local_pack") or []
    if a or pack:
        parts.append('<div class="page"><div class="eyebrow">04d</div>'
                     '<h2>What you are actually up against</h2>')
        parts.append(missing(pr, "local_pack", "The map pack field"))
        parts.append(missing(pr, "link_graph", "Referring domains"))
        parts.append(missing(pr, "link_graph_partial", "Referring domains"))
        parts.append(missing(pr, "competitor_reviews", "Competitor review counts"))
    elif pr.client_note("local_pack") or pr.client_note("authority"):
        parts.append('<div class="page"><div class="eyebrow">04d</div>'
                     '<h2>What you are actually up against</h2>'
                     + missing(pr, "local_pack", "The map pack field")
                     + missing(pr, "authority", "Domain age and footprint")
                     + missing(pr, "link_graph", "Referring domains")
                     + '</div>')
    if pack:
        seen_names, uniq = set(), []
        for p_ in pack:
            k = (p_["name"] or "").lower()
            if k and k not in seen_names:
                seen_names.add(k)
                uniq.append(p_)
        own = d.get("own_reviews")
        parts.append('<p>The businesses occupying the local results — a different set '
                     'from the sites ranking in the blue links, and usually a surprise. '
                     'These are who the client competes with for the phone calls.</p>'
                     '<table><thead><tr><th>Business</th><th>Where</th>'
                     '<th>Google reviews</th></tr></thead><tbody>')
        for p_ in uniq[:8]:
            rv = (f'<strong>{p_["reviews"]}</strong>' if p_.get("reviews") is not None
                  else '<span class="src">not looked up</span>')
            parts.append(f'<tr><td>{e(p_["name"])}</td>'
                         f'<td class="src">{e((p_["address"] or "")[:52])}</td>'
                         f'<td class="n">{rv}</td></tr>')
        if own is not None:
            parts.append(f'<tr class="you"><td>You</td><td class="src">—</td>'
                         f'<td class="n">{own}</td></tr>')
        parts.append("</tbody></table>")
        counts = [p_["reviews"] for p_ in uniq if p_.get("reviews") is not None]
        if counts or own is not None:
            parts.append('<p class="src">Review counts are read by hand from Google '
                         f'Maps and are correct as at {e(gen)}. No API supplies them '
                         'free, so they are a point-in-time reading rather than a '
                         'measurement this tool took — and they move. The business '
                         'names, addresses and positions above are retrieved from the '
                         'search API.</p>')
        if counts and own is not None:
            ahead = sum(1 for c in counts if c > own)
            parts.append(f'<div class="card {"bad" if ahead else "good"}">'
                         f'<strong>{ahead} of {len(counts)} have more reviews than you</strong>'
                         f'<p>Reviews are among the factors Google names for local results. '
                         f'{e(cit.cite("reviews_ranking_factor"))} Closing this gap is the '
                         f'single largest change available, it costs nothing, and it is the '
                         f'one part of local ranking a website cannot substitute for.</p></div>')
        elif not counts:
            parts.append('<div class="card warn"><strong>Review counts not supplied</strong>'
                         '<p>Two minutes on Google Maps against the list above turns this into '
                         'an exact figure. It is not obtainable free through any API, and an '
                         'estimate would be the only unmeasured number in this report.</p></div>')

    if a:
        c_ = a["client"]
        parts.append('<h3>Why a thinner page can still be ahead</h3>'
                     '<p>Content is measurable in minutes. What usually beats it is what a '
                     'domain has accumulated over years — age, history, links. None of that '
                     'shows up in a word count, which is why the comparison on the previous '
                     'page can look inexplicable.</p>'
                     '<table><thead><tr><th>Domain</th><th>Age (years)</th>'
                     '<th>Since</th><th>Strength</th></tr></thead><tbody>')
        def _row(o, you=False):
            st = (o.get("strength") or {}).get("rank")
            # Built outside the f-string: Python 3.11 rejects a backslash inside
            # an f-string expression, and the container runs 3.11.
            cls = ' class="you"' if you else ''
            return (f'<tr{cls}><td>{e(o["domain"])}</td>'
                    f'<td class="n">{e(o["age_years"] if o["age_years"] is not None else "—")}</td>'
                    f'<td class="n src">{e(o.get("registered") or o.get("first_archived") or "—")}</td>'
                    f'<td class="n">{e(st if st is not None else "—")}</td></tr>')
        parts.append(_row(c_, you=True))
        for o in a["competitors"]:
            parts.append(_row(o))
        parts.append("</tbody></table>")
        parts.append('<p class="src">Age from the domain registry where it publishes '
                     'one, otherwise the first snapshot in the Internet Archive — the ZA '
                     'registry does not publish creation dates, so a .co.za age is a '
                     '"seen no later than" and may understate. Domain age is not itself a '
                     'ranking factor; what correlates with it is everything accumulated '
                     'during it.</p>'
                     '<p class="src"><strong>Strength</strong> is an authority score '
                     'computed from the open Common Crawl web graph — higher means more, '
                     'and better-connected, referring domains. It is a third-party '
                     'calculation over a public dataset, not a Google metric, and it is '
                     'not the same number as Ahrefs DR or Moz DA. Use it to compare the '
                     'sites in this table against each other; do not read it as a score '
                     'out of a hundred. A blank means the domain was not found in the '
                     'graph, which usually means few or no inbound links.</p>')
        # Who actually links to each domain — the answer to "where do the
        # backlinks come from", at domain level, from the open Common Crawl graph.
        linked = [(o["domain"], (o.get("strength") or {}).get("linking_from") or [],
                   (o.get("strength") or {}).get("referring_domains"))
                  for o in ([c_] + a["competitors"])]
        linked = [(d, rows, n) for d, rows, n in linked if rows]
        if linked:
            parts.append('<h3>Where their links actually come from</h3>'
                         '<p>Referring domains for each site, from the open web graph. '
                         'Worth reading rather than counting — a handful of unrelated '
                         'foreign domains is not authority, and a competitor with six of '
                         'those is more beatable than the number suggests.</p>')
            for dom, rows, n in linked[:6]:
                srcs = ", ".join(f'{e(r["domain"])}'
                                 + (f' <span class="src">({r["authority"]})</span>'
                                    if r.get("authority") is not None else "")
                                 for r in rows[:8])
                parts.append(f'<div class="card"><strong>{e(dom)}</strong> — '
                             f'{e(n if n is not None else "0")} referring domain'
                             f'{"" if n == 1 else "s"}<p class="src">{srcs}</p></div>')

        parts.append('<div class="card"><strong>How to see the client\'s own links in full</strong>'
                     '<p>The table above is domain-level and comes from a public sample of '
                     'the web. For the client\'s own site there are two free sources that are '
                     'exact and page-level: <strong>Search Console → Links</strong> shows the '
                     'linking sites and the specific pages being linked to, from Google\'s own '
                     'index; and <strong>Ahrefs Webmaster Tools</strong> is free on any site you '
                     'verify, giving the full source-to-target list from their crawl. Neither is '
                     'available for competitors, which is why their side stops at domain level.</p>'
                     '</div>')
        parts.append('<div class="card"><strong>What is deliberately not in this table</strong><ul>'
                     + "".join(f"<li>{e(x)}</li>" for x in a["not_measured"]) + "</ul></div>")
    if a or pack:
        parts.append("</div>")

    # ── technical + AI access
    r = d.get("robots") or {}
    lk = d.get("links") or {}
    t = d.get("technical") or {}
    hostinfo = d.get("canonical_host") or {}
    parts.append(f"""<div class="page"><div class="eyebrow">04b</div>
  <h2>Findings you would not otherwise see</h2>
  <p>Measured on the live site. None of this is visible in a browser, and none of
     it appears in a standard audit score.</p>

  <h3>Can AI assistants read the site</h3>
  <table><thead><tr><th>Check</th><th>Result</th><th>Why it matters</th></tr></thead><tbody>
    <tr><td>AI crawlers allowed</td>
        <td>{'<span class="tag fail">Blocked: ' + e(", ".join(r.get("ai_blocked", []))) + '</span>'
            if r.get("ai_blocked") else '<span class="tag pass">All allowed</span>'}</td>
        <td>A blocked crawler removes the site from that assistant entirely, and
            nothing else in this report would reveal it</td></tr>
    <tr><td>robots.txt present</td>
        <td>{'<span class="tag pass">Yes</span>' if r.get("robots_found") else '<span class="tag na">No</span>'}</td>
        <td>Declares the sitemap and governs crawler access</td></tr>
    <tr><td>Sitemap declared in robots.txt</td>
        <td>{'<span class="tag pass">Yes</span>' if r.get("sitemap_declared") else '<span class="tag na">No</span>'}</td>
        <td>How a crawler finds every page without following links</td></tr>
    <tr><td>llms.txt present</td>
        <td>{'<span class="tag pass">Yes</span>' if r.get("llms_txt") else '<span class="tag na">No</span>'}</td>
        <td>A summary written for AI assistants. Few sites have one</td></tr>
    <tr><td>Canonical host</td>
        <td class="n">{hostinfo.get("hops", "—")} redirect{'' if hostinfo.get("hops") == 1 else 's'}</td>
        <td>More than one hop wastes crawl budget on every visit</td></tr>
  </tbody></table>"""
)
    used_keys += ["crawl_budget_demand", "js_second_pass"]

    # Cross-page checks from the whole-site crawl — the same set, same wording,
    # as the visibility report, so the two documents never disagree about a site.
    # The crawl gate (section 02) already scores unique titles, real 404s, shell
    # pages and duplicate documents, and its score is tracked month on month — so
    # those four are shown there only, never twice in one report.
    ss = [f for f in (d.get("site_structure") or []) if f["id"] not in GATE_COVERED]
    if ss:
        parts.append("<h3>Across the whole site</h3>"
                     "<p>Checks that only exist when every page is read together: "
                     "links that lead nowhere, a sitemap that disagrees with its "
                     "pages, pages that compete with each other.</p>"
                     "<table><thead><tr><th>Check</th><th>Result</th><th>Detail</th>"
                     "</tr></thead><tbody>")
        for f in ss:
            tag = ('<span class="tag pass">Pass</span>' if f["pass"] else
                   '<span class="tag na">Not measured</span>' if not f["measured"] else
                   '<span class="tag fail">Fail</span>')
            parts.append(f'<tr><td>{e(f["title"])}</td><td>{tag}</td>'
                         f'<td>{e(f["label"])}</td></tr>')
        parts.append("</tbody></table>")
        for f in ss:
            if f["pass"] or not f["measured"]:
                continue
            items = ", ".join(e(p.get("show") or sw.path_of(p["url"]))
                              + (f' — {e(p["note"])}' if p.get("note") else "")
                              for p in f["pages"][:6])
            more = f" and {len(f['pages']) - 6} more" if len(f["pages"]) > 6 else ""
            parts.append(f'<div class="card {"bad" if f["priority"] in ("critical", "high") else "warn"}">'
                         f'<strong>{e(f["title"])} — {len(f["pages"])}</strong>'
                         f'<p>{e(f["why"])}</p><p><em>Fix:</em> {e(f["fix"])}</p>'
                         f'<p class="src">{items}{more}</p></div>')
    elif pr.client_note("site_structure"):
        parts.append(missing(pr, "site_structure", "Across the whole site"))

    orphans = lk.get("orphans") or []
    inbound = lk.get("inbound") or {}
    if inbound:
        top = sorted(inbound.items(), key=lambda kv: -kv[1])[:6]
        parts.append("<h3>Which pages the site itself points at</h3>"
                     "<p>Internal links tell a search engine which pages you consider "
                     "important. A page nothing links to reads as unimportant, however "
                     "good it is.</p><table><thead><tr><th>Page</th>"
                     "<th>Links pointing to it</th></tr></thead><tbody>")
        for u, n in top:
            parts.append(f'<tr><td>{e(u.replace(d["url"], "") or "/")}</td>'
                         f'<td class="n">{n}</td></tr>')
        parts.append("</tbody></table>")
        # The whole-site checks above already list orphans, counting links that
        # arrive through a redirect — so this older card only shows without them.
        if orphans and not ss:
            parts.append('<div class="card bad"><strong>Pages nothing links to</strong>'
                         "<p>These are in the sitemap but no other page points at them. "
                         "Linking them from the navigation or a related page is a "
                         "five-minute fix.</p><ul>"
                         + "".join(f'<li>{e(u.replace(d["url"], "") or "/")}</li>'
                                   for u in orphans[:10]) + "</ul></div>")

    flags = [
        ("Pages with no page-specific structured data", t.get("pages_without_schema"),
         "Structured data is how a search engine understands what the page is about"),
        ("Pages under 300 words", [f"{u} ({w}w)" for u, w in (t.get("thin_pages") or [])],
         "Thin pages rarely rank and can dilute the pages that would"),
        ("Titles longer than 62 characters", t.get("long_titles"),
         "Google truncates them, so the end of the message is lost"),
        ("Pages with no H1", t.get("pages_without_h1"), "The page's main heading is missing"),
        ("Pages with more than one H1", t.get("pages_multiple_h1"),
         "Competing headings blur what the page is about"),
        ("Pages with no phone or WhatsApp link", t.get("pages_without_contact_link"),
         "A visitor ready to call should never have to hunt for the number"),
        ("Sitemap URLs not returning 200", [f"{u} ({s})" for u, s in (t.get("broken_in_sitemap") or [])],
         "A sitemap should only list pages that work"),
    ]
    if ss:
        # Listed with the whole-site checks already (as "Sitemap lists only live
        # pages", with the redirect target named) — once is enough.
        flags = [f_ for f_ in flags if not f_[0].startswith("Sitemap URLs")]
    live = [(lbl, items, why) for lbl, items, why in flags if items]
    if live:
        parts.append("<h3>Worth fixing</h3>")
        for lbl, items, why in live:
            shown = ", ".join(e(str(x).replace(d["url"], "")) for x in items[:5])
            more = f" and {len(items) - 5} more" if len(items) > 5 else ""
            parts.append(f'<div class="card warn"><strong>{e(lbl)} — {len(items)}</strong>'
                         f"<p>{e(why)}.</p><p class=\"src\">{shown}{more}</p></div>")
    else:
        parts.append('<div class="card good"><strong>Nothing outstanding</strong>'
                     "<p>Every page has its own structured data, a single heading, a "
                     "workable title and a way to make contact.</p></div>")
    parts.append("</div>")

    sc = d.get("search_console") or {}
    if not sc.get("connected"):
        parts.append('<div class="page"><div class="eyebrow">04c</div>'
                     '<h2>Where you actually rank on Google</h2>'
                     + missing(pr, "gsc", "Search Console")
                     + '<p>Search Console is Google\'s own record of this site: the '
                       'exact queries it appeared for, the average position, and how '
                       'many people saw and clicked it. It is free, it is the client\'s '
                       'own account, and it is the only accurate source of Google '
                       'position data that exists. No third-party tool substitutes for '
                       'it, and this report will not pretend otherwise.</p>'
                       '<div class="card"><strong>To include it next time</strong>'
                       '<p>In Search Console: Performance → Search results → set the '
                       'date range → Export → CSV. Send us the file and every figure '
                       'in this section becomes real.</p></div></div>')
    if sc.get("connected"):
        t = sc.get("totals") or {}
        # A "Last 3 months" filter over a property verified five weeks ago
        # returns five weeks. Saying so is the difference between a client
        # reading "barely any traffic in three months" and reading the truth.
        sp, req = sc.get("span"), sc.get("requested_range")
        span_note = ""
        if sp:
            short = req and sp["days"] < 80 and "3 month" in req.lower()
            span_note = (
                f'<div class="{"note" if short else "card"}">'
                f'<strong>The data covers {sp["days"]} days — '
                f'{e(sp["first"])} to {e(sp["last"])}.</strong>'
                + (f' You asked for "{e(req)}", and Search Console returned '
                   f'{sp["days"]} days, because that is all it holds. It records '
                   f'nothing from before the property was verified, so an empty '
                   f'earlier period is a gap in the record rather than a gap in '
                   f'performance.' if short else
                   (f' Requested range: {e(req)}.' if req else ''))
                + '</div>')
        # Google withholds queries too few people searched. The table below is
        # therefore a subset, and saying by how much stops a reader adding the
        # column up and concluding the report contradicts itself.
        anon = int(t.get("anonymised_impressions") or 0)
        anon_note = ""
        if anon:
            pct = round(100 * anon / max(1, int(t.get("impressions") or 1)))
            anon_note = (
                f'<div class="card"><strong>{anon:,} of those impressions '
                f'({pct}%) are not attributed to any query below.</strong>'
                '<p>Google withholds search terms that too few people used, so that '
                'no individual can be identified from them. They are real '
                'impressions on real searches — they are simply unnamed. The query '
                'table adds up to less than the total for that reason, not because '
                'anything is missing from this report.</p>'
                '<p>A high share here is normal for a small local business and is '
                'worth reading as a signal: most of the demand is long-tail, '
                'phrased differently by every person searching.</p></div>')
        parts.append(f"""<div class="page"><div class="eyebrow">04c</div>
  <h2>Where you actually rank on Google</h2>
  <p>From the client's own Search Console account — exported {e(sc.get('source',''))}.
     This is Google's own measurement of this site, not an estimate and not another
     engine's index. It is the most accurate position data that exists.</p>
  {span_note}
  <div class="grid">
    <div class="stat"><div class="k">Impressions</div><div class="v">{int(t.get('impressions') or 0):,}</div></div>
    <div class="stat"><div class="k">Clicks</div><div class="v">{int(t.get('clicks') or 0):,}</div></div>
    <div class="stat"><div class="k">Queries appearing</div><div class="v">{(t.get('queries_with_impressions') or 0) if sc.get('queries') else '—'}</div></div>
  </div>
  {anon_note}
  <h3>Your target queries</h3>
  <table><thead><tr><th>Query</th><th>Average position</th><th>Impressions</th>
  <th>Clicks</th></tr></thead><tbody>""")
        for r in sc.get("targets", []):
            if r["found"]:
                parts.append(f'<tr><td>{e(r["query"])}</td><td class="n">{r["position"]}</td>'
                             f'<td class="n">{r["impressions"]:,}</td>'
                             f'<td class="n">{r["clicks"]:,}</td></tr>')
            else:
                parts.append(f'<tr><td>{e(r["query"])}</td>'
                             f'<td colspan="3" class="src">Not yet appearing for this query '
                             f'in the period exported</td></tr>')
        parts.append("</tbody></table>")
        parts.append('<p class="src">Average position is exactly that — an average across '
                     'every impression in the period, and across every location the search '
                     'came from. A local result seen from inside the suburb will normally '
                     'sit higher than this figure, and one seen from across the city lower.</p>')

        top = [r for r in sc.get("queries", [])[:12]]
        if top:
            parts.append("<h3>What the site is already being found for</h3>"
                         "<table><thead><tr><th>Query</th><th>Position</th>"
                         "<th>Impressions</th><th>Clicks</th></tr></thead><tbody>")
            for r in top:
                parts.append(f'<tr><td>{e(r["key"])}</td>'
                             f'<td class="n">{round(r["position"],1) if r["position"] else "—"}</td>'
                             f'<td class="n">{int(r["impressions"] or 0):,}</td>'
                             f'<td class="n">{int(r["clicks"] or 0):,}</td></tr>')
            parts.append("</tbody></table>"
                         '<p class="src">Queries already producing impressions are the '
                         'cheapest wins available — the site is being shown for them and '
                         'is not yet being clicked.</p>')

        orph = sc.get("orphans") or []
        if orph:
            tot = sum(o["impressions"] for o in orph)
            n_in = sum(1 for o in orph if o.get("measured"))
            n_out = len(orph) - n_in
            parts.append(
                '<div class="card bad"><strong>Pages Google ranks that your sitemap '
                'does not list</strong>'
                f'<p>{len(orph)} page{"s" if len(orph) != 1 else ""} earning '
                f'{tot:,} impression{"s" if tot != 1 else ""} between them are live '
                'and indexed, but absent from your sitemap. '
                + (f'{n_in} of them {"was" if n_in == 1 else "were"} still '
                   'reached by following the site\'s own links, so '
                   f'{"it is" if n_in == 1 else "they are"} measured '
                   'in "Every page, measured" above; ' if n_in else '')
                + (f'{n_out} {"was" if n_out == 1 else "were"} not reached '
                   'by any link on the site either, so nothing here has measured '
                   f'{"its" if n_out == 1 else "their"} content, headings or '
                   'structured data — Google found '
                   f'{"it" if n_out == 1 else "them"}; we did not.' if n_out
                   else 'none is hidden from this report, but the sitemap should still '
                        'list them.')
                + '</p>'
                '<p>This matters because a page nobody is maintaining is still '
                'representing the business in search. Decide for each one: keep it '
                'and add it to the sitemap so it is measured and improved, or '
                'redirect it to the page that replaced it. Leaving it in this state '
                'is the only option that is certainly wrong.</p></div>'
                '<table><thead><tr><th>Page</th><th>Impressions</th><th>Clicks</th>'
                '<th>Position</th></tr></thead><tbody>')
            for o in orph:
                parts.append(f'<tr><td>{e(o["path"])}</td>'
                             f'<td class="n">{o["impressions"]:,}</td>'
                             f'<td class="n">{o["clicks"]:,}</td>'
                             f'<td class="n">{o["position"] if o["position"] else "—"}</td></tr>')
            parts.append('</tbody></table>')
        parts.append("</div>")

    # ── not connected
    parts.append("""<div class="page"><div class="eyebrow">05</div>
  <h2>Not yet connected</h2>
  <p>These sections need one-off access from the client. Each adds data that
     cannot be measured from outside.</p>
  <div class="card"><strong>Google Search Console</strong>
    <p>Adds: per-page indexing state, last crawl date, Google-selected canonical,
       impressions, clicks and average position for your target queries. This is
       the honest answer to "has Google seen my site yet" — and it is the client's
       own data, not our estimate.</p></div>
  <div class="card"><strong>Bing Webmaster Tools</strong>
    <p>Adds the same for Bing, which is a separate index and feeds Copilot and
       parts of ChatGPT search.</p></div>
  <div class="card"><strong>Google Business Profile</strong>
    <p>Adds review count and velocity, category, service areas, photo recency and
       unanswered reviews. Reviews are among the strongest factors in local
       ranking, so this is the most valuable of the three.</p></div>
  <p class="src">Granting access takes a few minutes and only has to be done once.</p>
</div>""")

    # ── how this report was assembled
    # Every source, every run, whether it worked or not. A client who can see
    # which sources answered can judge how much weight to put on each section —
    # and can see that a blank is a blank rather than a bad result.
    rows = sorted(pr.rows, key=lambda r: (probes.SEVERITY[r["status"]], r["label"]))
    if rows:
        parts.append('<div class="page"><div class="eyebrow">05b</div>'
                     '<h2>How this report was assembled</h2>'
                     '<p>Every source this report draws on, and whether it returned '
                     'data on this run. Nothing here is filler: a source that did not '
                     'answer leaves a gap in the report, and the gap is marked where '
                     'it falls rather than filled with a zero.</p>'
                     '<table><thead><tr><th>What we checked</th><th>Where it comes from</th>'
                     '<th>Result</th></tr></thead><tbody>')
        verdict = {probes.OK: '<span class="tag pass">Data returned</span>',
                   probes.EMPTY: '<span class="tag na">No data held yet</span>',
                   probes.NOT_CONFIGURED: '<span class="tag na">Not connected</span>',
                   probes.SKIPPED: '<span class="tag fail">Not run</span>',
                   probes.FAILED: '<span class="tag fail">Unreachable</span>'}
        for r in rows:
            why = "" if r["status"] == probes.OK else \
                f'<div class="why">{e(pr.client_note(r["key"]) or "")}</div>'
            parts.append(f'<tr class="fixrow"><td>{e(r["label"])}{why}</td>'
                         f'<td>{e(r["source"] or "—")}</td>'
                         f'<td>{verdict[r["status"]]}</td></tr>')
        parts.append('</tbody></table>')
        if ps["degraded"]:
            parts.append('<div class="note">A source marked unreachable is a fault at '
                         'our end or a service that did not answer — not a finding '
                         'about this business. Where one occurs, the affected figure '
                         'is absent from this report rather than guessed at, and it '
                         'will be filled in on the next run.</div>')
        parts.append('</div>')

    # ── internal fix list (staff builds only)
    if d.get("internal"):
        fails = pr.attention()
        if fails:
            parts.append('<div class="page"><div class="eyebrow">Internal</div>'
                         '<h2>Fix list</h2><p>Not for the client. One row per source '
                         'that did not return data, with what to do about it.</p>'
                         '<table><thead><tr><th>Check</th><th>Status</th>'
                         '<th>What to do</th></tr></thead><tbody>')
            for r in fails:
                det = f'<div class="why">{e(r["detail"])}</div>' if r["detail"] else ""
                parts.append(f'<tr class="fixrow"><td>{e(r["label"])}{det}</td>'
                             f'<td>{e(probes.LABEL[r["status"]])}</td>'
                             f'<td>{e(r["remedy"] or "—")}</td></tr>')
            parts.append('</tbody></table></div>')

    # ── sources
    src = cit.used(used_keys + ["sab_hide_address", "no_self_serving_reviews",
                                "doorway_pages", "hidden_text", "crawl_budget_demand",
                                "js_second_pass", "crawl_takes_weeks",
                                "sitemap_no_guarantee"])
    parts.append('<div class="page"><div class="eyebrow">06</div><h2>Sources</h2>'
                 '<p>Every external claim in this report, with where it comes from. '
                 'Figures about this site and its competitors were measured directly '
                 'and are not listed here.</p><table><thead><tr>'
                 '<th>Claim</th><th>Source</th><th>Checked</th></tr></thead><tbody>')
    for s in src:
        parts.append(f'<tr><td>{e(s["claim"])}</td>'
                     f'<td>{e(s["source"])}<br><span class="src">{e(s["url"])}</span></td>'
                     f'<td class="n">{e(s["checked"])}</td></tr>')
    parts.append("</tbody></table>")
    parts.append(f'<h3>Measured, and inferred</h3><p>{e(cit.INFERRED_NOTE)}</p>'
                 '<table><thead><tr><th style="width:26%">Inferred figure</th>'
                 '<th>How it is derived, and what it does not account for</th>'
                 '</tr></thead><tbody>')
    for label, basis in cit.INFERRED_BASIS.values():
        parts.append(f'<tr><td><strong>{e(label)}</strong></td><td>{e(basis)}</td></tr>')
    parts.append('</tbody></table>'
                 '<p class="src">Everything not listed above is a direct measurement of '
                 'the live page, repeatable by re-running this report.</p>'
                 f'<p class="src" style="margin-top:12pt">Generated {e(gen)} · Boldpiq · '
                 f'Tier A (measured without client account access)</p></div>')

    parts.append("</body></html>")
    return "".join(parts)


# ── render ───────────────────────────────────────────────────────────────────

def render_pdf(html_path, pdf_path):
    chrome = runtime.find_chrome()
    if not chrome:
        raise SystemExit("No Chromium-family browser found. Install Google Chrome, "
                         "or set BOLDPIQ_CHROME.")
    cmd = [chrome] + runtime.CHROME_FLAGS + [
        "--no-pdf-header-footer", "--run-all-compositor-stages-before-draw",
        "--virtual-time-budget=" + os.environ.get("BOLDPIQ_VTB", "30000"),
        f"--print-to-pdf={pdf_path}",
        urllib.parse.urljoin("file:", urllib.request.pathname2url(html_path))]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       timeout=int(os.environ.get("BOLDPIQ_PDF_TIMEOUT", "180")))
    if not os.path.exists(pdf_path):
        raise SystemExit(f"Chrome failed to produce a PDF:\n{r.stderr[-600:]}")


def slug(url):
    host = urllib.parse.urlparse(url).netloc or url
    return re.sub(r"[^a-z0-9]+", "-", host.lower().replace("www.", "")).strip("-")


def normalise(url):
    return url if re.match(r"^https?://", url, re.I) else "https://" + url


def csv(v):
    return [x.strip() for x in (v or "").split(",") if x.strip()]


def main():
    ap = argparse.ArgumentParser(description="Boldpiq Ranking Report (Tier A).")
    ap.add_argument("url")
    ap.add_argument("--client", default="")
    ap.add_argument("--suburb", default="", help="target suburb, counted across the site")
    ap.add_argument("--queries", default="", help="comma-separated target queries")
    ap.add_argument("--competitors", default="", help="comma-separated competitor URLs")
    ap.add_argument("--areas", default="", help="comma-separated service areas for proximity")
    ap.add_argument("--pin", default="", help="lat,lon of the verified map pin")
    ap.add_argument("--gbp-url", default="", help="Google Maps place URL (pin is read from it)")
    ap.add_argument("--money-page", default="", help="path of the primary target page")
    ap.add_argument("--compare", default="", help="previous report JSON for deltas")
    ap.add_argument("--country", default="ZA", help="two-letter market for search results")
    ap.add_argument("--location", default="", help="search location, e.g. 'Johannesburg, South Africa'")
    ap.add_argument("--competitor-reviews", default="",
                    help='Google review counts you looked up, e.g. "Big J\'s=41,SAM\'S=7". '
                         "Two minutes on Maps and the map-pack gap becomes exact.")
    ap.add_argument("--reviews", default="",
                    help="the client's own current Google review count")
    ap.add_argument("--gsc", default="",
                    help="Search Console Performance export (CSV or ZIP) — the "
                         "client's own Google position data, no API needed")
    ap.add_argument("--internal", action="store_true",
                    help="add a staff fix-list page naming every source that did "
                         "not return data and what to do about it")
    ap.add_argument("--open", action="store_true")
    a = ap.parse_args()

    a.url = normalise(a.url)
    a.queries = csv(a.queries)
    a.competitors = [normalise(u) for u in csv(a.competitors)]
    a.areas = csv(a.areas) or ([a.suburb] if a.suburb else [])

    os.makedirs(REPORTS, exist_ok=True)
    s = slug(a.url)
    stamp = dt.date.today().strftime("%Y-%m")
    base = os.path.join(REPORTS, f"{s}-ranking-report-{stamp}")

    print(f"→ {a.url}")
    data = collect(a)

    prev = hist.load(a.compare) if a.compare else hist.latest_for(REPORTS, s, base + ".json")
    data["_previous"] = prev
    data["internal"] = a.internal

    # Underscore keys are working objects — the live Probes instance and the
    # previous run — and must not reach the JSON.
    with open(base + ".json", "w", encoding="utf-8") as fh:
        json.dump({k: v for k, v in data.items() if not k.startswith("_")}, fh, indent=1)

    html_path = base + ".html"
    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(build_html(data))
    print("   rendering PDF …", flush=True)
    render_pdf(html_path, base + ".pdf")
    os.remove(html_path)

    g = data["gate"]
    c = data.get("competitors") or {}
    if g.get("measured"):
        print(f"   overall {g['passed']}/{g['total_checks']} gate checks · "
              f"{g['page_count']} pages · {g['unique_documents']} unique · "
              f"{g['total_words']:,} words")
    else:
        print("   overall gate not measured — no pages were crawled")
    if c.get("leads_on"):
        print(f"   leads on {', '.join(c['leads_on'])} "
              f"against {len(c.get('competitors', []))} competitors")

    # Named on stdout so a failure is visible without opening the PDF, and so
    # the web app can lift the same list into its panel.
    pr_ = data["_probes"]
    ps_ = pr_.summary()
    print(f"   CHECKS: {ps_['counts'][probes.OK]}/{ps_['total']} ok · "
          f"{ps_['broken']} failed · {ps_['unconnected']} not connected")
    # Every check, not only the problems. A panel showing five amber rows and no
    # green ones reads as a broken run when ten things actually worked.
    for r in sorted(pr_.rows, key=lambda r: (probes.SEVERITY[r["status"]], r["label"])):
        # A healthy check needs no remedy, and "needs <other check>" is already
        # said by the remedy — neither belongs on the line a person reads.
        det = "" if (not r["detail"] or r["detail"].startswith("needs ")) \
            else f" — {r['detail']}"
        fix = "" if r["status"] == probes.OK else r["remedy"]
        print(f"   CHECK {r['status']}\t{r['label']}{det}\t{fix}")
    if a.open:
        runtime.open_files([base + ".pdf"])
    # Bare path, last line, no prefix — the web app finds the finished report by
    # taking the last line that ends in .pdf and stat-ing it. A tick in front of
    # the path makes the file "not exist" and the job reports as failed.
    print(f"   {base}.pdf")


if __name__ == "__main__":
    main()
