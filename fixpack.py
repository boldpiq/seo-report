#!/usr/bin/env python3
"""
Fix pack — the report's recommendations, restructured for pasting into an AI.

The PDF is written for the client: prose, context, reassurance. This is the
opposite audience. It is the same findings compressed into per-section markdown
that someone can copy straight into ChatGPT/Claude/Cursor and say "apply these to
my site", with the platform constraints kept in so the assistant is never told to
do something the stack cannot do.

Built alongside the PDF (seo_report.py writes `<base>-fixes.json`) and rendered
by the web app at /fixes/<report>.

One source of truth on wording: the markdown strings are generated here, not in
the browser, so the copy button and any future export always agree.
"""

import datetime as dt
import urllib.parse

import platforms as plat

PRIORITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
PRIORITY_LABEL = {"critical": "Critical", "high": "High",
                  "medium": "Medium", "low": "Low"}
EFFORT_LABEL = {"low": "Quick fix", "medium": "Moderate", "high": "Larger project"}

# How many items each section carries into the markdown. Beyond this the tail is
# noise for an assistant and it stops acting on any of it.
SECTION_CAP = 25
HIGHLIGHT_CAP = 3


def _sentence(text):
    """One tidy sentence-ish line: no newlines, no double spaces, ends cleanly."""
    txt = " ".join(str(text or "").split())
    return txt


def _finding(label):
    """Scanner labels put the finding first and advice after an em dash."""
    txt = _sentence(label)
    return txt.split("—", 1)[0].strip() if "—" in txt else txt


def _item(title, fix, priority="medium", effort=None, finding="", why="",
          note="", section=""):
    return {"title": _sentence(title), "fix": _sentence(fix),
            "priority": priority, "effort": effort,
            "finding": _sentence(finding), "why": _sentence(why),
            "note": _sentence(note), "section": section}


def _from_issue(i):
    note = ""
    if i.get("limit_status") == plat.BLOCKED:
        note = (f"Not fixable on this platform — {i.get('limit_reason', '')} "
                "Do not attempt; flag it as a replatforming reason instead.")
    elif i.get("limit_status") == plat.LIMITED:
        note = f"Platform-constrained — {i.get('limit_reason', '')}"
    return _item(i["title"], i["fix"], i["priority"], i.get("effort"),
                 _finding(i.get("label")), i.get("why", ""), note)


def _md_item(it, n):
    """One markdown bullet. Dense on purpose — an assistant reads structure, not prose."""
    tags = [PRIORITY_LABEL.get(it["priority"], it["priority"])]
    if it["effort"]:
        tags.append(EFFORT_LABEL.get(it["effort"], it["effort"]))
    line = f"{n}. **{it['title']}** _({' · '.join(tags)})_"
    if it["finding"]:
        line += f"\n   - Found: {it['finding']}"
    if it["fix"]:
        line += f"\n   - Fix: {it['fix']}"
    if it["note"]:
        line += f"\n   - ⚠ {it['note']}"
    return line


def _section(key, title, blurb, items, extra_note=""):
    items = list(items)
    shown = items[:SECTION_CAP]
    counts = {}
    for it in items:
        counts[it["priority"]] = counts.get(it["priority"], 0) + 1
    highlights = [it["title"] for it in shown[:HIGHLIGHT_CAP]]
    body = "\n".join(_md_item(it, n) for n, it in enumerate(shown, 1))
    if len(items) > len(shown):
        body += f"\n\n_({len(items) - len(shown)} further lower-priority items omitted.)_"
    return {"key": key, "title": title, "blurb": blurb,
            "count": len(items), "counts": counts,
            "highlights": highlights, "items": shown,
            "note": extra_note, "body": body}


def _priority_line(counts):
    parts = [f"{counts[p]} {PRIORITY_LABEL[p].lower()}"
             for p in ("critical", "high", "medium", "low") if counts.get(p)]
    return ", ".join(parts)


def build(data, an, client=None, generated=None):
    """Structured fix pack from a scan + its analysis. `an['lh']` may be None."""
    url = data.get("url", "")
    site = urllib.parse.urlparse(url).netloc or url
    generated = generated or dt.datetime.now()
    lhr = an.get("lh")
    prof = an.get("platform") or {}

    # Pillar scores first. Lighthouse ALSO has a category called "seo" — merging its
    # score dict in flat used to overwrite the structural SEO pillar with Lighthouse's
    # (nearly always 100), so the pack disagreed with the PDF and with its own overall.
    # Lighthouse SEO is a different measurement: keep it, under its own key.
    scores = {"overall": an.get("overall")}
    for key in ("seo", "aeo", "geo"):
        scores[key] = (an["pillars"].get(key) or {}).get("score")
    if lhr:
        for key, val in (lhr.get("scores") or {}).items():
            scores["lighthouse-seo" if key == "seo" else key] = val

    sections = []

    # Quick wins first: this is the section most people will actually paste.
    sections.append(_section(
        "quick-wins", "Quick wins",
        "High-impact items that are cheap to do. Start here — each also appears "
        "in its own section below, so fix it once.",
        [_from_issue(i) for i in an.get("quick_wins", [])]))

    titles = {"seo": ("SEO", "Findability in Google and Bing."),
              "aeo": ("AEO", "How readable and quotable the page is to AI assistants."),
              "geo": ("GEO", "How likely an AI assistant is to recommend this business by name.")}
    for key in ("seo", "aeo", "geo"):
        pillar = an["pillars"].get(key) or {}
        name, blurb = titles[key]
        sections.append(_section(
            key, name, blurb,
            [_from_issue(i) for i in pillar.get("issues", [])]))

    if lhr:
        opps = lhr.get("opportunities") or []
        sections.append(_section(
            "performance", "Speed",
            f"Measured by Lighthouse {lhr.get('version') or ''} in Chrome as "
            f"{'a mid-range phone on 4G' if lhr.get('form_factor') == 'mobile' else 'a desktop browser'}. "
            "Ordered by the saving Lighthouse actually measured.",
            [_item(o["title"], o.get("fix") or o.get("why", ""),
                   "high" if o.get("severity") == "high" else "medium",
                   finding=o.get("display", ""), why=o.get("why", ""))
             for o in opps]))

        sections.append(_section(
            "accessibility", "Accessibility",
            "Machine-detectable WCAG failures. Automated testing catches roughly a "
            "third of real barriers, so treat a clean list as a floor, not a pass.",
            [_item(a["title"], a.get("fix") or a.get("why", ""),
                   "high" if a.get("severity") == "high" else "medium",
                   finding=a.get("display") or (f"{a['count']} element(s) affected"
                                                if a.get("count") else ""),
                   why=a.get("why", ""))
             for a in lhr.get("accessibility_issues") or []]))

        sections.append(_section(
            "best-practices", "Browser best practices",
            "Standards, security and correctness as Chrome sees them.",
            [_item(b["title"], b.get("fix") or b.get("why", ""),
                   "high" if b.get("severity") == "high" else "medium",
                   finding=b.get("display", ""), why=b.get("why", ""))
             for b in lhr.get("best_practice_issues") or []]))

        agentic = [a for a in (lhr.get("agentic") or [])
                   if a["status"] in ("fail", "partial") and a.get("fix")]
        sections.append(_section(
            "agentic", "Agentic browsing",
            "Whether an AI agent can operate the site — navigate it and complete a "
            "task — not merely read it.",
            [_item(a["title"], a.get("fix", ""),
                   "high" if a["status"] == "fail" else "medium",
                   finding=a.get("display", ""), why=a.get("why", ""))
             for a in agentic]))

    blocked = an.get("blocked") or []
    if blocked:
        sections.append(_section(
            "blocked", "Do not attempt on this platform",
            f"These are real problems, but {prof.get('name', 'this platform')} does not "
            "expose the controls to fix them. Included so an assistant does not waste "
            "effort — or invent a workaround that breaks something.",
            [_from_issue(i) for i in blocked]))

    sections = [s for s in sections if s["count"]]

    pack = {
        "site": site,
        "url": url,
        "client": client or site.replace("www.", ""),
        "generated": generated.strftime("%Y-%m-%d %H:%M"),
        "scanned": data.get("scannedAt", ""),
        "platform": {"name": prof.get("name", ""), "known": an.get("platform_known", False),
                     "kind": prof.get("kind", "")},
        "scores": scores,
        "lighthouse": bool(lhr),
        "total_issues": len(an.get("issues", [])),
        "counts": an.get("counts", {}),
        "sections": sections,
    }
    pack["preamble"] = _preamble(pack)
    for s in pack["sections"]:
        s["markdown"] = _section_markdown(pack, s)
    pack["markdown"] = _full_markdown(pack)
    return pack


def _preamble(pack):
    sc = pack["scores"]
    bits = [f"{k.upper()} {sc[k]}/100" for k in ("seo", "aeo", "geo") if sc.get(k) is not None]
    if pack["lighthouse"]:
        for key, label in (("performance", "Speed"), ("accessibility", "Accessibility"),
                           ("best-practices", "Best practices"),
                           ("agentic-browsing", "Agentic"),
                           ("lighthouse-seo", "Lighthouse SEO")):
            if sc.get(key) is not None:
                bits.append(f"{label} {sc[key]}/100")
    lines = [
        f"Site: {pack['url']}",
        f"Audited: {pack['generated']} (Boldpiq website audit)",
        f"Scores: " + " · ".join(bits) if bits else "",
        f"Platform: {pack['platform']['name']}" +
        ("" if pack["platform"]["known"] else " (not detected — assume full control unless told otherwise)"),
        f"Issues: {pack['total_issues']} total"
        + (f" ({_priority_line(pack['counts'])})" if pack.get("counts") else ""),
    ]
    return "\n".join(l for l in lines if l)


INSTRUCTION = (
    "You are fixing a website. Below are audit findings with the recommended fix for "
    "each. Work top-down: apply what you can directly in the codebase, and list "
    "anything that needs a decision, a credential or a design change. Do not act on "
    "items marked ⚠ as not fixable on the platform. Where a fix is ambiguous, say so "
    "rather than guessing."
)


def _section_markdown(pack, s):
    head = [f"# {s['title']} fixes — {pack['site']}", "", pack["preamble"], ""]
    if s["blurb"]:
        head += [f"_{s['blurb']}_", ""]
    if s["note"]:
        head += [s["note"], ""]
    head += [INSTRUCTION, "", f"## {s['title']} ({s['count']})", "", s["body"], ""]
    return "\n".join(head)


def _full_markdown(pack):
    out = [f"# Website audit fixes — {pack['site']}", "", pack["preamble"], "",
           INSTRUCTION, ""]
    for s in pack["sections"]:
        out += [f"## {s['title']} ({s['count']})"]
        if s["blurb"]:
            out += [f"_{s['blurb']}_"]
        if s["note"]:
            out += [s["note"]]
        out += ["", s["body"], ""]
    if not pack["lighthouse"]:
        out += ["_No Chrome measurement in this pack: speed, accessibility, best "
                "practice and agentic findings are not included._", ""]
    return "\n".join(out)
