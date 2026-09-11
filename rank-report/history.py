"""
Month-on-month history.

Each run writes a JSON that becomes the next run's baseline. Month two produces
deltas; month six produces a trend. Nothing predicts client retention like a
line going up, even when the ranking itself has not moved yet.
"""

import glob
import json
import os


def latest_for(reports_dir, slug, exclude=None):
    """Most recent prior report JSON for this client, or None."""
    pat = os.path.join(reports_dir, f"{slug}-ranking-report-*.json")
    files = sorted(f for f in glob.glob(pat) if f != exclude)
    if not files:
        return None
    try:
        with open(files[-1], encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def _dig(d, path, default=None):
    cur = d
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


TRACKED = [
    ("Pages live",        ["gate", "page_count"]),
    ("Unique documents",  ["gate", "unique_documents"]),
    ("Gate checks passed", ["gate", "passed"]),
    ("Total words",       ["gate", "total_words"]),
    ("Target term total", ["gate", "term_total"]),
    ("Reviews",           ["profile", "reviews"]),
]


def method_changed(current, previous):
    """True when the two runs counted pages differently.

    Reports before 2026-09 crawled the sitemap only; from then on the crawl also
    follows the site's own links (gate.method == "sitemap+links"). Across that
    boundary a rise in pages or words is the counting, not the client's site, and
    must not be presented as movement.
    """
    if not previous:
        return False
    return (_dig(current, ["gate", "method"], "sitemap")
            != _dig(previous, ["gate", "method"], "sitemap"))


def deltas(current, previous):
    """Tracked metrics with last period's value alongside."""
    rows = []
    recount = method_changed(current, previous)
    # Across the change, compare the same pages last period measured (the
    # sitemap set), recorded by this run as gate.like_for_like. The whole-site
    # figure rides along as `full` so the report can show both.
    lfl = _dig(current, ["gate", "like_for_like"]) if recount else None
    for label, path in TRACKED:
        now = _dig(current, path)
        was = _dig(previous or {}, path)
        full = None
        if recount and path[0] == "gate" and lfl is not None:
            full, now = now, lfl.get(path[1])
        if now is None and was is None:
            continue
        change = None
        if isinstance(now, (int, float)) and isinstance(was, (int, float)):
            change = now - was
        # No like-for-like figure: crawl-derived counts are not comparable.
        not_comparable = recount and path[0] == "gate" and lfl is None
        if not_comparable:
            change = None
        rows.append({"label": label, "now": now, "was": was, "change": change,
                     "full": full if full != now else None,
                     "not_comparable": not_comparable})
    return rows


def summarise(rows):
    """One plain-English line for the top of the report."""
    moved = [r for r in rows if r["change"]]
    if not moved and any(r.get("not_comparable") for r in rows):
        return ("This period's crawl covers every page on the site, not only the "
                "sitemap, so page and word counts are a new baseline rather than a "
                "change. They are compared like for like again from next report.")
    if not moved:
        return ("No measurable change since the last report. Everything built is "
                "in place and waiting on indexing and reviews.")
    ups = [r for r in moved if r["change"] > 0]
    if ups:
        best = max(ups, key=lambda r: r["change"])
        return f"{best['label']} moved from {best['was']} to {best['now']} this period."
    return "Some measures moved down since the last report — see the detail below."
