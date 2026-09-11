"""
Accumulated signals — the reasons a thin page outranks a better one.

Content is measurable in minutes. What is usually beating you is everything a
domain has accrued over years: age, history, links, index footprint. None of that
shows up in a word count, which is why a 1,000-word page can sit above a
3,000-word one and the comparison looks inexplicable.

This module measures the parts obtainable for free and states plainly which parts
are not. Guessing at the rest would put the one soft number into a report built
on measurement.

  Measurable free   domain age (RDAP), first archived snapshot (Wayback),
                    index footprint (site: via the configured search API),
                    HTTPS, and the client's own backlinks (Search Console export)
  Not free          competitors' backlink profiles. Ahrefs, Majestic and Moz all
                    charge. We do not estimate them.
"""

import datetime as dt
import json
import re
import urllib.parse
import urllib.request

UA = "boldpiq-rank-report/1.0 (reports@boldpiq.com)"
TIMEOUT = 20


def _json(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.load(r)


def _host(url):
    h = urllib.parse.urlparse(url if url.startswith("http") else "https://" + url).netloc
    return h.lower().replace("www.", "")


def registered(domain):
    """Domain registration date via RDAP — the free, official successor to WHOIS.

    Domain age is not itself a ranking factor; Google has said so. What correlates
    with age is everything accumulated during it. An old domain with nothing
    pointing at it ranks like a new one — so this is context, not a score.
    """
    d = _host(domain)
    for base in ("https://rdap.org/domain/", "https://rdap.iana.org/domain/"):
        try:
            data = _json(base + d)
            for ev in data.get("events", []):
                if ev.get("eventAction") in ("registration", "created"):
                    return ev.get("eventDate", "")[:10]
        except Exception:
            continue
    return None


def first_seen(domain):
    """Earliest snapshot in the Internet Archive.

    This is the load-bearing source for South African clients: the ZA registry
    does not publish creation dates over RDAP, so .co.za domains return nothing
    from the registry route. The archive does.

    Uses the CDX index rather than the /available endpoint — the latter returns
    the snapshot *closest to* a given timestamp, which is not the same as the
    first one and quietly returns nothing for some domains.
    """
    d = _host(domain)
    url = ("https://web.archive.org/cdx/search/cdx?url=" + urllib.parse.quote(d)
           + "&output=json&fl=timestamp&filter=statuscode:200&limit=1&collapse=timestamp:8")
    try:
        rows = _json(url)
    except Exception:
        return None
    # First row is the header ["timestamp"]
    if not rows or len(rows) < 2:
        return None
    ts = str(rows[1][0])
    return f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}" if len(ts) >= 8 else None


def years_since(iso):
    if not iso:
        return None
    try:
        y, m, dd = (int(x) for x in iso.split("-")[:3])
        delta = dt.date.today() - dt.date(y, m, dd)
        return round(delta.days / 365.25, 1)
    except Exception:
        return None


def index_footprint(domain, search_mod):
    """How many pages the search index holds for this domain.

    A rough proxy for how substantial a site is. Uses the configured search API,
    so it costs one request and is skipped entirely when no key is set.
    """
    if not search_mod.provider():
        return None
    try:
        res = search_mod.top_results(f"site:{_host(domain)}", n=20)
        return len(res)
    except Exception:
        return None


def profile(domain, search_mod=None):
    reg = registered(domain)
    seen = first_seen(domain)
    age_iso = reg or seen
    return {
        "domain": _host(domain),
        "registered": reg,
        "first_archived": seen,
        "age_years": years_since(age_iso),
        "age_basis": "registration" if reg else ("first archived snapshot" if seen else None),
        "indexed_sample": (index_footprint(domain, search_mod) if search_mod else None),
    }


def compare(client_url, competitor_urls, search_mod=None):
    """Client against the field on accumulated signals."""
    you = profile(client_url, search_mod)
    others = [profile(u, search_mod) for u in competitor_urls]
    ages = [o["age_years"] for o in others if o["age_years"] is not None]
    older = sum(1 for a in ages if you["age_years"] is not None and a > you["age_years"])
    return {
        "client": you,
        "competitors": others,
        "older_than_you": older,
        "field_size": len(others) + 1,
        "median_age": (sorted(ages)[len(ages) // 2] if ages else None),
        "not_measured": [
            "Competitors' backlink profiles — Ahrefs, Majestic and Moz all charge, "
            "and an estimate would be the only unmeasured number in this report.",
            "Competitors' review counts — visible on Google Maps but not reliably "
            "readable without scraping. Two minutes of looking gives an exact figure; "
            "see the map-pack section.",
            "Click-through and engagement history — Google's, and not published.",
        ],
    }


# ── domain strength proxy ────────────────────────────────────────────────────

# Common Crawl's host-level web graph, exposed as a free API. 121M domains,
# 3.9B domain-to-domain links, no key and no signup.
#
# Chosen over the paid authority metrics because it is free, and over Open
# PageRank because OPR moved behind a Keywords Everywhere subscription in 2026
# (cheapest plan $84/year — the old free tier is gone).
#
# Caveats worth knowing, and stated in the report:
#   · Common Crawl is a sample of the web, not all of it. A domain absent from
#     the graph has few or no inbound links, but "none found" is not proof of
#     "none exist".
#   · This is a community-run service, not an institutional API. Failure is
#     handled by showing nothing rather than by guessing.
CC_GRAPH = "https://cc-backlink-checker.metehan777.workers.dev/api/report"


def link_graph_profile(domain, raise_on_error=False):
    """Referring domains and an authority score, from the open Common Crawl graph."""
    d = _host(domain)
    try:
        data = _json(f"{CC_GRAPH}?domain={urllib.parse.quote(d)}")
    except Exception:
        if raise_on_error:
            raise
        return None
    st = data.get("stats") or {}
    if not st.get("found"):
        return {"domain": d, "found": False, "referring_domains": 0,
                "authority": None, "note": "not present in the open web graph — "
                                           "few or no inbound links found"}
    return {
        "domain": d,
        "found": True,
        "authority": st.get("authorityScore"),
        "citation": st.get("citationScore"),
        "referring_domains": st.get("referringDomains"),
        "top_pct": st.get("harmonicTopPct"),
        "graph_size": st.get("totalDomains"),
        "linking_from": [
            {"domain": r.get("domain"), "authority": r.get("authorityScore")}
            for r in (data.get("rows") or [])[:8]
        ],
    }


def open_page_rank(domains, errors=None):
    """Kept as the name the report calls; now backed by the Common Crawl graph.

    Open PageRank itself moved behind a paid Keywords Everywhere plan, so the
    free path is the underlying data both were always derived from.
    """
    out = {}
    for d in domains or []:
        try:
            p = link_graph_profile(d, raise_on_error=True)
        except Exception as exc:                      # noqa: BLE001
            # Recorded rather than swallowed. A domain we could not look up is
            # unknown, and the report must not print it as zero links.
            if errors is not None:
                errors.append(f"{_host(d)}: {type(exc).__name__}")
            continue
        if p:
            out[p["domain"]] = {"rank": p.get("authority"),
                                "referring_domains": p.get("referring_domains"),
                                "found": p.get("found"),
                                "linking_from": p.get("linking_from", [])}
    return out


# ── map pack field ───────────────────────────────────────────────────────────

def local_pack(query, search_mod, country="ZA"):
    """Who actually occupies the local results for this query.

    A different set from the organic results and frequently a surprise: the
    businesses in the map pack are often not the ones with the best websites.
    Brave returns names, addresses, phones and coordinates for free. It does not
    return Google review counts — those are supplied manually, because two
    minutes of looking gives an exact figure and an estimate would be the one
    unmeasured number in the report.
    """
    import json as _json_mod
    import os
    key = os.environ.get("BOLDPIQ_BRAVE_KEY")
    if not key:
        return []
    q = urllib.parse.urlencode({"q": query, "country": country, "count": 10,
                                "result_filter": "locations"})
    try:
        req = urllib.request.Request(
            "https://api.search.brave.com/res/v1/web/search?" + q,
            headers={"X-Subscription-Token": key, "Accept": "application/json",
                     "User-Agent": UA})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            d = _json_mod.load(r)
    except Exception:
        return []
    out = []
    for i, r in enumerate(d.get("locations", {}).get("results", [])):
        out.append({
            "position": i + 1,
            "name": r.get("title"),
            "address": (r.get("postal_address") or {}).get("displayAddress"),
            "phone": (r.get("contact") or {}).get("telephone"),
            "coordinates": r.get("coordinates"),
            "query": query,
        })
    return out


def parse_review_counts(raw):
    """--competitor-reviews "Big J's=41, SAM'S:7" → {name: count}.

    Accepts either separator. Anyone typing these is reading them off a map pack
    and will reach for whichever they think of first; rejecting one of the two
    silently produced an empty table with no visible cause.
    """
    out = {}
    for pair in re.split(r"[,\n;]", raw or ""):
        pair = pair.strip()
        if not pair:
            continue
        m = re.match(r"^(.*?)\s*[=:]\s*(\d+)$", pair)
        if not m:
            continue
        out[m.group(1).strip().lower()] = int(m.group(2))
    return out
