"""
The sourced-claims library.

Nothing in the report may assert an external fact that is not in this file.
Every entry carries its source, URL and the date it was checked.

Four tiers of evidence. Every figure in the report belongs to exactly one, and
the report says which — a client must never mistake a judgement for a reading.

  measured  we measured it on the live page or from the client's own account.
            Word counts, distances, link counts, schema blocks, redirect hops.
            Repeatable: run it again and you get the same number.
  inferred  our assessment applied to a measurement. Bands, verdicts,
            projections, "what is realistic". Defensible, but a judgement —
            and a client quoting it back in month four is entitled to know that.
  platform  the platform's own published documentation — not our opinion
  research  named third-party research, with a year

Review quarterly. Drop anything that has gone stale. The cost of not doing this
is proven: published "free SA directory" lists were years out of date and
produced four dead ends in a single afternoon.
"""

CITATIONS = {
    "reviews_ranking_factor": {
        "tier": "platform",
        "claim": "Review count and score are among the factors Google uses to "
                 "rank local business results.",
        "source": "Google — Improve your local ranking on Google",
        "url": "https://support.google.com/business/answer/7091",
        "checked": "2026-08-21",
    },
    "local_ranking_three": {
        "tier": "platform",
        "claim": "Google ranks local results on relevance, distance and "
                 "prominence.",
        "source": "Google — Improve your local ranking on Google",
        "url": "https://support.google.com/business/answer/7091",
        "checked": "2026-08-21",
    },
    "sab_hide_address": {
        "tier": "platform",
        "claim": "A business that does not serve customers at its address must "
                 "hide the address and set a service area.",
        "source": "Google — Business Profile guidelines",
        "url": "https://support.google.com/business/answer/3038177",
        "checked": "2026-08-21",
    },
    "no_self_serving_reviews": {
        "tier": "platform",
        "claim": "Review snippet markup may not be self-serving — reviews about "
                 "the business itself, written or hosted by that business, are "
                 "not eligible.",
        "source": "Google — Review snippet structured data",
        "url": "https://developers.google.com/search/docs/appearance/structured-data/review-snippet",
        "checked": "2026-08-21",
    },
    "crawl_then_index": {
        "tier": "platform",
        "claim": "Crawling and indexing are separate stages. A page that has "
                 "been crawled has not necessarily been indexed.",
        "source": "Google — How Search works: crawling and indexing",
        "url": "https://developers.google.com/search/docs/fundamentals/how-search-works",
        "checked": "2026-08-21",
    },
    "js_second_pass": {
        "tier": "platform",
        "claim": "Pages requiring JavaScript are queued for rendering in a "
                 "second pass, which can lag the initial crawl.",
        "source": "Google — Understand JavaScript SEO basics",
        "url": "https://developers.google.com/search/docs/crawling-indexing/javascript/javascript-seo-basics",
        "checked": "2026-08-21",
    },
    "crawl_budget_demand": {
        "tier": "platform",
        "claim": "Crawl rate is governed by capacity and demand. Sites Google "
                 "considers more important, and pages that change more often, "
                 "are crawled more frequently.",
        "source": "Google — Large site owner's guide to managing crawl budget",
        "url": "https://developers.google.com/search/docs/crawling-indexing/large-site-managing-crawl-budget",
        "checked": "2026-08-21",
    },
    # Replaced the former "no_sandbox" entry on 2026-08-21. It asserted that
    # "Google has stated it does not operate a sandbox" and cited a page that
    # does not mention one — an attribution a client could check and find
    # wanting, which is the precise failure its own note warned about. Google
    # publishes no statement either way. What it does publish is below, verbatim
    # and verifiable, and it answers the client's actual question better.
    "crawl_takes_weeks": {
        "tier": "platform",
        "claim": "Crawling can take anywhere from a few days to a few weeks, and "
                 "requesting a crawl does not guarantee that inclusion in search "
                 "results will happen instantly or even at all.",
        "source": "Google Search Central — Ask Google to recrawl your URLs",
        "url": "https://developers.google.com/search/docs/crawling-indexing/"
               "ask-google-to-recrawl",
        "checked": "2026-08-21",
        "note": "Quoted almost verbatim from the page. Use this rather than any "
                "claim about a 'sandbox' — Google publishes no statement either "
                "way on that, so it cannot be cited.",
    },
    "sitemap_no_guarantee": {
        "tier": "platform",
        "claim": "A sitemap helps search engines discover URLs, but does not "
                 "guarantee that every item in it will be crawled and indexed.",
        "source": "Google Search Central — Sitemaps overview",
        "url": "https://developers.google.com/search/docs/crawling-indexing/"
               "sitemaps/overview",
        "checked": "2026-08-21",
        "note": "The answer to 'I submitted the sitemap, why is nothing showing'.",
    },
    "doorway_pages": {
        "tier": "platform",
        "claim": "Multiple pages targeting location variants without "
                 "substantially unique value are doorway pages and are treated "
                 "as spam.",
        "source": "Google — Spam policies for Google web search",
        "url": "https://developers.google.com/search/docs/essentials/spam-policies",
        "checked": "2026-08-21",
    },
    "hidden_text": {
        "tier": "platform",
        "claim": "Text positioned off-screen or otherwise hidden from users but "
                 "served to crawlers is a spam policy violation.",
        "source": "Google — Spam policies for Google web search",
        "url": "https://developers.google.com/search/docs/essentials/spam-policies",
        "checked": "2026-08-21",
    },
    "duplicate_listings": {
        "tier": "platform",
        "claim": "A business may have only one profile per location. Duplicate "
                 "listings are merged or removed.",
        "source": "Google — Business Profile guidelines",
        "url": "https://support.google.com/business/answer/3038177",
        "checked": "2026-08-21",
    },
    "core_web_vitals": {
        "tier": "platform",
        "claim": "Page experience signals, including Core Web Vitals, are used "
                 "in ranking systems.",
        "source": "Google — Understanding page experience in Google Search",
        "url": "https://developers.google.com/search/docs/appearance/page-experience",
        "checked": "2026-08-21",
    },
}


# Rendered next to any figure that is our assessment rather than a reading.
INFERRED_NOTE = (
    "Assessment, not a measurement — our judgement applied to the measured "
    "figure alongside it. The number it is based on is repeatable; the "
    "conclusion drawn from it is ours."
)

INFERRED_BASIS = {
    "map_pack_band": (
        "Map-pack strength band",
        "Derived from the measured distance between the verified map pin and the "
        "centre of the area: 2 km or less strong, 5 km good, 8 km moderate, "
        "beyond that weak. Distance is one of the three factors Google names for "
        "local results, but it is not the only one — a band is an expectation, "
        "not a prediction."),
    "area_verdict": (
        "What is realistic per area",
        "Derived from the map-pack band together with whether a page targeting "
        "that area exists on the site. It assumes reviews and category are "
        "handled; it does not model competitor strength in that specific area."),
    "competitive_rank": (
        "Rank against competitors",
        "A ranking of measured on-page factors only — word count, area mentions "
        "and structured data. It describes the website, not the search result. "
        "It excludes reviews, proximity and the business name, all of which can "
        "outweigh everything on this list."),
    "page_detection": (
        "Whether a page targets an area",
        "Detected from the URL, the title and body mentions. A page can target "
        "an area without naming it the way we look for, and a passing mention "
        "can look like targeting."),
}


def cite(key):
    """Inline citation marker for the report body."""
    c = CITATIONS.get(key)
    return f"[{c['source']}]" if c else ""


def used(keys):
    """Ordered, de-duplicated citation records for the Sources page."""
    out, seen = [], set()
    for k in keys:
        if k in CITATIONS and k not in seen:
            seen.add(k)
            out.append(dict(CITATIONS[k], key=k))
    return out
