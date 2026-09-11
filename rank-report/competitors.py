"""
Competitor measurement.

Everything here is measured by us on the live page. Nothing is claimed, inferred
or taken from a third-party estimate — a client can dispute an opinion but not a
word count we can show them.
"""

import re

import gate


def measure(url, term=None):
    status, html = gate.fetch(url)
    if not html:
        return {"url": url, "reachable": False}
    f = gate.page_facts(url, html, term)
    prices = re.findall(r"R\s?\d[\d\s,]{2,}", html)
    return {
        "url": url,
        "reachable": True,
        "status": status,
        "title": f["title"],
        "words": f["words"],
        "term_count": f["term_count"],
        "schema_blocks": f["schema_blocks"],
        "schema_types": f["schema_types"],
        "h1_count": f["h1_count"],
        "h2_count": f["h2_count"],
        "publishes_prices": len(prices) >= 3,
        "price_mentions": len(prices),
    }


def compare(client_page, competitor_urls, term=None):
    """Client's money page against the field, ranked on each measured factor."""
    rows = [dict(measure(u, term), who="competitor") for u in competitor_urls]
    rows = [r for r in rows if r.get("reachable")]
    client = dict(client_page, who="client")
    field = [client] + rows

    def rank_of(key, higher_is_better=True):
        vals = sorted((r.get(key) or 0 for r in field), reverse=higher_is_better)
        return vals.index(client.get(key) or 0) + 1

    return {
        "client": client,
        "competitors": rows,
        "field_size": len(field),
        "ranks": {
            "words": rank_of("words"),
            "term_count": rank_of("term_count"),
            "schema_blocks": rank_of("schema_blocks"),
        },
        "leads_on": [k for k, v in {
            "content depth": rank_of("words"),
            "suburb relevance": rank_of("term_count"),
            "structured data": rank_of("schema_blocks"),
        }.items() if v == 1],
        "best_competitor_words": max((r["words"] for r in rows), default=0),
        "best_competitor_term": max((r.get("term_count") or 0 for r in rows), default=0),
        "prices_published_by": sum(1 for r in rows if r.get("publishes_prices")),
    }
