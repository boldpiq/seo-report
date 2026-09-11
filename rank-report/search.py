"""
Competitor discovery from the target queries.

WHAT THIS IS FOR, precisely.

Discovery answers "who should we measure?" — not "where does the client rank?".
Those need different sources and conflating them would put a soft claim into a
report built on measurement.

  Finding the field   Any credible engine will do. A local business with a real
                      page for the suburb ranks across engines; the overlap is
                      high. Once a URL is in hand, word count, structured data,
                      suburb mentions and published prices are measured on the
                      live page — identical regardless of which engine found it.

  Reporting position  Only Google can answer that for Google. Brave, Bing and
                      Google each run their own index, and positions differ.
                      The client's own position comes from Search Console, which
                      is free, accurate, and their own account data.

So: a non-Google engine may be used to assemble the competitive set, and its
positions are recorded as that engine's positions — never presented as Google's.

The report's central claim is that everything in it is measured. Scraping Google
would break that — results are personalised, location-dependent, not reliably
reproducible, and doing it is against Google's terms. So discovery goes through a
search API, or it does not happen and the operator supplies the URLs by hand.

Set one of these and discovery turns itself on. Listed in the order to try them:

    BOLDPIQ_BRAVE_KEY      Brave Search API  FREE tier, ~2,000 queries/month.
                           An official API with published terms — start here.
    BOLDPIQ_SERPER_KEY     serper.dev        paid, cheapest of the paid options
    BOLDPIQ_SERPAPI_KEY    serpapi.com       paid
    BOLDPIQ_BING_KEY       Bing Web Search   paid

At one report a month per client, with a handful of target queries each, the free
Brave tier covers a small agency comfortably.

With none set the report still runs — the operator pastes competitor URLs and
everything downstream behaves identically.
"""

import json
import os
import urllib.parse
import urllib.request

TIMEOUT = 25

# Hard ceiling on API calls per report run. Brave's free allowance is 1,000
# requests a month against a $5 credit; a normal report uses three or four. This
# cap exists so a misconfiguration — fifty queries pasted into the field, or a
# retry loop — cannot quietly spend a month's allowance in one run.
MAX_QUERIES_PER_RUN = int(os.environ.get("BOLDPIQ_SEARCH_MAX_QUERIES", "6"))

# Pages that rank for these queries but are not competitors in the sense that
# matters: you cannot out-content a directory, and comparing a client's service
# page to a listing page tells you nothing. Recorded, but kept out of the
# content comparison.
AGGREGATORS = (
    "procompare.co.za", "snupit.co.za", "assist247.co.za", "localpros.co.za",
    "handymannetwork.co.za", "gumtree.co.za", "yellowpages.co.za", "brabys.com",
    "cylex", "hotfrog", "yellosa", "yalwa", "ananzi", "cybo.com", "findmy.co.za",
    "facebook.com", "instagram.com", "linkedin.com", "youtube.com", "tiktok.com",
    "google.com", "bing.com", "wikipedia.org", "nichemarket.co.za", "locanto",
)


def provider():
    if os.environ.get("BOLDPIQ_BRAVE_KEY"):
        return "brave"
    if os.environ.get("BOLDPIQ_SERPER_KEY"):
        return "serper"
    if os.environ.get("BOLDPIQ_SERPAPI_KEY"):
        return "serpapi"
    if os.environ.get("BOLDPIQ_BING_KEY"):
        return "bing"
    return None


def _post_json(url, payload, headers):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={**headers, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.load(r)


def _get_json(url, headers):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.load(r)


def _serper(query, country, location, n):
    d = _post_json("https://google.serper.dev/search",
                   {"q": query, "gl": country.lower(), "num": n,
                    **({"location": location} if location else {})},
                   {"X-API-KEY": os.environ["BOLDPIQ_SERPER_KEY"]})
    return [{"url": o.get("link"), "title": o.get("title"), "position": o.get("position")}
            for o in (d.get("organic") or []) if o.get("link")]


def _serpapi(query, country, location, n):
    q = urllib.parse.urlencode({
        "q": query, "gl": country.lower(), "num": n, "engine": "google",
        "api_key": os.environ["BOLDPIQ_SERPAPI_KEY"],
        **({"location": location} if location else {})})
    d = _get_json("https://serpapi.com/search.json?" + q, {})
    return [{"url": o.get("link"), "title": o.get("title"), "position": o.get("position")}
            for o in (d.get("organic_results") or []) if o.get("link")]


def _bing(query, country, location, n):
    q = urllib.parse.urlencode({"q": query, "count": n, "mkt": f"en-{country.upper()}"})
    d = _get_json("https://api.bing.microsoft.com/v7.0/search?" + q,
                  {"Ocp-Apim-Subscription-Key": os.environ["BOLDPIQ_BING_KEY"]})
    pages = (d.get("webPages") or {}).get("value") or []
    return [{"url": p.get("url"), "title": p.get("name"), "position": i + 1}
            for i, p in enumerate(pages) if p.get("url")]


def _brave(query, country, location, n):
    """Brave Search API. Free tier, official, published terms.

    Brave has no per-suburb location parameter, so the suburb is carried in the
    query itself — which is what a searcher types anyway, and keeps the result
    reproducible rather than dependent on an inferred location.
    """
    q = urllib.parse.urlencode({"q": query, "count": min(n, 20),
                                "country": country.upper(), "search_lang": "en"})
    d = _get_json("https://api.search.brave.com/res/v1/web/search?" + q,
                  {"X-Subscription-Token": os.environ["BOLDPIQ_BRAVE_KEY"],
                   "Accept": "application/json"})
    results = (d.get("web") or {}).get("results") or []
    return [{"url": r.get("url"), "title": r.get("title"), "position": i + 1}
            for i, r in enumerate(results) if r.get("url")]


FETCHERS = {"brave": _brave, "serper": _serper, "serpapi": _serpapi, "bing": _bing}

PROVIDER_LABEL = {
    "brave": "Brave Search API",
    "serper": "Serper",
    "serpapi": "SerpAPI",
    "bing": "Bing Web Search",
}


def top_results(query, country="ZA", location=None, n=10):
    p = provider()
    if not p:
        return []
    try:
        return FETCHERS[p](query, country, location, n)
    except Exception:
        return []


def is_aggregator(url):
    host = urllib.parse.urlparse(url).netloc.lower()
    return any(a in host for a in AGGREGATORS)


def discover(queries, own_domain, country="ZA", location=None, per_query=8, limit=5):
    """Who actually ranks for the target queries, minus the client and the directories.

    Each competitor carries the query it was found on and the position it held, so
    the report can say 'ranked 2nd for tree felling northcliff' rather than simply
    asserting that a site is a competitor.
    """
    p = provider()
    if not p or not queries:
        return {"provider": p, "queries": [], "competitors": [], "aggregators": [],
                "own_positions": []}

    own = urllib.parse.urlparse(
        own_domain if own_domain.startswith("http") else "https://" + own_domain
    ).netloc.lower().replace("www.", "")

    found, aggs, own_pos, per_q = {}, {}, [], []
    skipped = queries[MAX_QUERIES_PER_RUN:]
    for q in queries[:MAX_QUERIES_PER_RUN]:
        results = top_results(q, country, location, per_query)
        per_q.append({"query": q, "results": len(results)})
        for r in results:
            host = urllib.parse.urlparse(r["url"]).netloc.lower().replace("www.", "")
            entry = {"url": r["url"], "title": r.get("title"), "host": host,
                     "query": q, "position": r.get("position")}
            if host == own:
                own_pos.append(entry)
            elif is_aggregator(r["url"]):
                aggs.setdefault(host, entry)
            elif host not in found:
                found[host] = entry

    ranked = sorted(found.values(), key=lambda x: (x["position"] or 99))
    # google_positions is the flag the report uses to decide whether a position
    # may be described as a Google ranking. Serper and SerpAPI return Google's
    # own results; Brave and Bing return their own index.
    return {
        "provider": p,
        "provider_label": PROVIDER_LABEL.get(p, p),
        "google_positions": p in ("serper", "serpapi"),
        "queries": per_q,
        "competitors": ranked[:limit],
        "aggregators": list(aggs.values())[:6],
        "own_positions": own_pos,
        "queries_skipped": skipped,
        "requests_used": len(per_q),
    }
