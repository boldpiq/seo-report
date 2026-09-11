import html
import os
import re
import sys
import urllib.request
#!/usr/bin/env python3
"""Verify every citation still says what we attribute to it.

The report's whole claim is that it is accurate and sourced. A citation whose
page no longer contains the statement — or never did — is worse than no
citation, because a client who checks one and finds it wanting will re-examine
every other claim in the document.

This caught a real one: an entry asserted "Google has stated it does not operate
a sandbox" and cited a page that does not mention a sandbox. Google publishes no
statement either way, so the claim was unsupportable and was replaced with two
that are quoted verbatim from the docs.

Run before shipping, and whenever Google reorganises Search Central:

    python3 citecheck.py            # exits non-zero if any citation fails

Note the probe is a keyword, not the full sentence — Google rewords pages often
and an exact-sentence match would cry wolf. A miss means "go and read that page",
not necessarily "this is wrong".
"""


sys.path.insert(0, __import__("os").path.dirname(os.path.abspath(__file__)))
import citations as cit

lib = next(v for v in vars(cit).values()
           if isinstance(v, dict) and v and all(isinstance(x, dict) for x in v.values()))

# A word that must appear on the cited page for the citation to be plausible.
PROBE = {
    "reviews_ranking_factor": "review",
    "local_ranking_three": "distance",
    "sab_hide_address": "service area",
    "no_self_serving_reviews": "self-serving",
    "crawl_then_index": "indexing",
    "js_second_pass": "javascript",
    "crawl_budget_demand": "crawl demand",
    "crawl_takes_weeks": "few days to a few weeks",
    "sitemap_no_guarantee": "doesn't guarantee",
    "doorway_pages": "doorway",
    "hidden_text": "hidden text",
    "duplicate_listings": "duplicate",
    "core_web_vitals": "core web vitals",
}

bad = []
for k, v in lib.items():
    url = v.get("url")
    probe = PROBE.get(k, "")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        body = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
        text = html.unescape(re.sub(r"<[^>]+>", " ", body)).lower()
        found = probe in text if probe else None
        if probe and not found:
            bad.append(k)
        print("%-26s HTTP 200  probe %-24r %s"
              % (k, probe, "ok" if found else "*** NOT FOUND — go and read it ***"))
    except Exception as e:
        print("%-26s FAILED %s" % (k, type(e).__name__))
        bad.append(k)

print()
if bad:
    print("%d citation(s) need attention: %s" % (len(bad), ", ".join(bad)))
    sys.exit(1)
print("all %d citations verified" % len(lib))
