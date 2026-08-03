#!/usr/bin/env python3
"""
Boldpiq — Website Visibility Report generator.

Feed it a client's URL, get back a branded PDF explaining every SEO, AEO and
GEO issue on their site: what it is, why it costs them, and how it gets fixed.

    ./seo-report.sh https://clientdomain.co.za

Scan data comes from the free scanner at seoscore.tools. Explanations,
prioritisation and the report itself are Boldpiq's.

Requires: Python 3 (stdlib only) + Google Chrome. No installs.
"""

import argparse
import base64
import datetime as dt
import html
import json
import os
import re
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from checks import CHECKS
import platforms as plat
import lighthouse as lh

HERE = os.path.dirname(os.path.abspath(__file__))
FONT = os.path.join(HERE, "assets", "geist-latin.woff2")
REPORTS = os.path.join(HERE, "reports")
API = "https://seoscore.tools/api/scan"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

# Boldpiq brand
INK = "#0B0F1C"        # near-black navy
ACCENT = "#C4541A"     # burnt orange
PAPER = "#FFFFFF"

PRIORITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
PRIORITY_LABEL = {
    "critical": "Critical",
    "high": "High",
    "medium": "Medium",
    "low": "Low",
}
EFFORT_LABEL = {"low": "Quick fix", "medium": "Moderate", "high": "Larger project"}

PILLARS = {
    "seo": ("SEO", "Search Engine Optimisation",
            "How well Google and Bing can find, understand and rank this site."),
    "aeo": ("AEO", "Answer Engine Optimisation",
            "How easily AI assistants can read this site and quote it in an answer."),
    "geo": ("GEO", "Generative Engine Optimisation",
            "How likely this site is to be recommended by ChatGPT, Perplexity, "
            "Claude and Google's AI Overviews."),
}

PLATFORM_NAMES = {
    "chatgpt": "ChatGPT",
    "perplexity": "Perplexity",
    "google_ai_overview": "Google AI Overviews",
    "claude": "Claude",
    "gemini": "Gemini",
    "copilot": "Microsoft Copilot",
}


# ── scan ─────────────────────────────────────────────────────────────────────

def scan(url, keyphrase="", tries=3):
    """POST the URL to the scanner; back off politely on rate limits."""
    payload = json.dumps({"url": url, "keyphrase": keyphrase}).encode()
    req = urllib.request.Request(
        API, data=payload, method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Origin": "https://seoscore.tools",
            "Referer": "https://seoscore.tools/",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
        })
    ctx = ssl.create_default_context()
    for attempt in range(1, tries + 1):
        try:
            with urllib.request.urlopen(req, timeout=90, context=ctx) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")[:200]
            if e.code == 429 and attempt < tries:
                wait = 15 * attempt
                print(f"   rate limited — waiting {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            raise SystemExit(f"Scan failed ({e.code}): {body}")
        except urllib.error.URLError as e:
            if attempt < tries:
                time.sleep(5 * attempt)
                continue
            raise SystemExit(f"Could not reach the scanner: {e.reason}")
    raise SystemExit("Scan failed after retries.")


# ── analysis ─────────────────────────────────────────────────────────────────

def meta(check):
    """Boldpiq explanation for a check, with a graceful fallback."""
    if check["id"] in CHECKS:
        title, priority, effort, why, fix = CHECKS[check["id"]]
        return {"title": title, "priority": priority, "effort": effort,
                "why": why, "fix": fix}
    title = check["id"].replace("aeo_", "").replace("geo_", "").replace("_", " ").capitalize()
    label = check.get("label", "")
    fix = label.split("—", 1)[1].strip() if "—" in label else "Review this item with your developer."
    return {"title": title, "priority": "medium", "effort": "medium",
            "why": "This affects how clearly search engines and AI assistants can "
                   "read and trust this page.", "fix": fix}


def is_issue(check):
    """Same rule the scanner's own UI uses to count issues."""
    return (not check.get("pass")
            and check.get("applicable") is not False
            and check.get("severity") != "info")


def analyse(data):
    prof, known = plat.profile((data.get("siteType") or {}).get("detectedCms"))
    out = {"pillars": {}, "issues": [], "passes": [],
           "platform": prof, "platform_known": known}
    for key in ("seo", "aeo", "geo"):
        block = data.get(key) or {}
        checks = block.get("checks", [])
        issues, passes = [], []
        for c in checks:
            row = dict(c)
            row.update(meta(c))
            row["pillar"] = key
            con = plat.constraint(prof, c["id"])
            row["limit_status"], row["limit_reason"] = con if con else (None, None)
            if is_issue(c):
                issues.append(row)
            elif c.get("pass") and c.get("applicable") is not False:
                passes.append(row)
        issues.sort(key=lambda r: (PRIORITY_ORDER[r["priority"]], r["title"]))
        out["pillars"][key] = {
            "score": block.get("score"),
            "passed": len(passes),
            "total": len(passes) + len(issues),
            "issues": issues,
            "passes": passes,
            "platforms": block.get("platforms", []),
        }
        out["issues"] += issues
        out["passes"] += passes
    out["issues"].sort(key=lambda r: (PRIORITY_ORDER[r["priority"]], r["pillar"]))
    out["counts"] = {p: sum(1 for i in out["issues"] if i["priority"] == p)
                     for p in PRIORITY_ORDER}
    out["quick_wins"] = [i for i in out["issues"]
                         if i["priority"] in ("critical", "high")
                         and i["effort"] == "low"
                         and i["limit_status"] != plat.BLOCKED][:6]
    out["blocked"] = [i for i in out["issues"] if i["limit_status"] == plat.BLOCKED]
    out["limited"] = [i for i in out["issues"] if i["limit_status"] == plat.LIMITED]
    scores = [out["pillars"][k]["score"] for k in ("seo", "aeo", "geo")
              if isinstance(out["pillars"][k]["score"], (int, float))]
    out["overall"] = round(sum(scores) / len(scores)) if scores else None
    return out


def band(score):
    if score is None:
        return "na"
    return "good" if score >= 80 else ("fair" if score >= 50 else "poor")


VERDICTS = {
    "performance": [
        (90, "Excellent — genuinely fast, even on a mid-range phone."),
        (75, "Good — quick enough that speed is not costing you visitors."),
        (50, "Needs work — noticeably slow on mobile data, which is how most local "
             "customers arrive."),
        (0,  "Poor — slow enough that a meaningful share of visitors leave before the "
             "page finishes appearing."),
    ],
    "accessibility": [
        (95, "Excellent — no machine-detectable barriers, which is rarer than it should be."),
        (85, "Good — mostly accessible, with a small number of fixable barriers."),
        (65, "Needs work — real barriers here will be excluding real customers."),
        (0,  "Poor — significant barriers. Parts of this site are unusable with a "
             "screen reader or a keyboard."),
    ],
    "agentic": [
        (90, "Excellent — an AI agent can navigate and operate this site."),
        (70, "Good — mostly operable by an agent, with gaps worth closing."),
        (50, "Needs work — an agent would struggle to complete a task here."),
        (0,  "Poor — an AI agent cannot reliably use this site on a customer's behalf."),
    ],
}


def verdict_for(kind, score):
    if score is None:
        return "Not measured"
    for floor, text in VERDICTS[kind]:
        if score >= floor:
            return text
    return "Not measured"


def verdict(score):
    if score is None:
        return "Not measured"
    if score >= 90:
        return "Excellent — a strong, well-built site with only fine-tuning left."
    if score >= 80:
        return "Good — the fundamentals are in place, with clear room to pull ahead."
    if score >= 65:
        return "Fair — working, but leaving meaningful visibility on the table."
    if score >= 50:
        return "Weak — competitors with better-built sites will outrank this one."
    return "Poor — this site is close to invisible to search and AI assistants."


# ── html ─────────────────────────────────────────────────────────────────────

def e(s):
    return html.escape(str(s if s is not None else ""))


def clean_label(label):
    """Scanner labels put the finding first and advice after an em dash."""
    txt = html.unescape(str(label or ""))
    return txt.split("—", 1)[0].strip() if "—" in txt else txt.strip()


def font_face():
    if not os.path.exists(FONT):
        return ""
    b64 = base64.b64encode(open(FONT, "rb").read()).decode()
    return ("@font-face{font-family:Geist;font-style:normal;font-weight:100 900;"
            "font-display:block;src:url(data:font/woff2;base64,%s) format('woff2');}" % b64)


def ring(score, size=118):
    """SVG donut for a score."""
    if score is None:
        score, dash = 0, 0
    r = (size / 2) - 9
    circ = 2 * 3.14159265 * r
    dash = circ * (score / 100.0)
    colour = {"good": "#16794A", "fair": ACCENT, "poor": "#A32619", "na": "#98A2B3"}[band(score)]
    return f"""<svg class="ring" viewBox="0 0 {size} {size}" width="{size}" height="{size}">
  <circle cx="{size/2}" cy="{size/2}" r="{r}" fill="none" stroke="#E7E3DE" stroke-width="9"/>
  <circle cx="{size/2}" cy="{size/2}" r="{r}" fill="none" stroke="{colour}" stroke-width="9"
    stroke-linecap="round" stroke-dasharray="{dash:.1f} {circ:.1f}"
    transform="rotate(-90 {size/2} {size/2})"/>
  <text x="50%" y="50%" text-anchor="middle" dy=".35em" class="ring-num" fill="{INK}">{score}</text>
</svg>"""


def bar(pct, colour=ACCENT):
    pct = max(0, min(100, pct or 0))
    return (f'<div class="bar"><span style="width:{pct}%;background:{colour}"></span></div>')


def issue_card(i, n, platform_name):
    limit = ""
    extra_tag = ""
    if i["limit_status"] == plat.BLOCKED:
        extra_tag = '<span class="tag blocked">Not fixable on this stack</span>'
        limit = f"""<div class="limit blocked">
          <span class="lbl">Platform limitation — {e(platform_name)}</span>
          <p>{e(i['limit_reason'])} The fix above is the correct one in principle, but it
             cannot be carried out while the site stays on this platform. Treat this as a
             reason to replatform, or as an accepted risk — not as a task.</p></div>"""
    elif i["limit_status"] == plat.LIMITED:
        extra_tag = '<span class="tag limited">Platform constrained</span>'
        limit = f"""<div class="limit">
          <span class="lbl">Platform limitation — {e(platform_name)}</span>
          <p>{e(i['limit_reason'])}</p></div>"""
    return f"""<article class="issue p-{i['priority']}">
  <header>
    <span class="num">{n}</span>
    <div class="ih">
      <h4>{e(i['title'])}</h4>
      <p class="finding">{e(clean_label(i.get('label')))}</p>
    </div>
    <div class="tags">
      <span class="tag pri">{PRIORITY_LABEL[i['priority']]}</span>
      <span class="tag eff">{EFFORT_LABEL[i['effort']]}</span>
      {extra_tag}
    </div>
  </header>
  <div class="body">
    <div class="blk"><span class="lbl">Why it matters</span><p>{e(i['why'])}</p></div>
    <div class="blk"><span class="lbl">How it gets fixed</span><p>{e(i['fix'])}</p></div>
  </div>
  {limit}
</article>"""


LH_LABEL = {"performance": "Performance", "accessibility": "Accessibility",
            "best-practices": "Best practices", "seo": "Lighthouse SEO",
            "agentic-browsing": "Agentic browsing"}

GLOSSARY = [
    ("SEO", "Search Engine Optimisation — making a site easy for Google to find, "
            "understand and rank."),
    ("AEO", "Answer Engine Optimisation — making a site easy for an AI assistant to "
            "read and quote."),
    ("GEO", "Generative Engine Optimisation — making a site likely to be recommended "
            "by name in an AI answer."),
    ("Agentic browsing", "Whether an AI agent can actually operate the site — click, "
                         "fill in and complete a task — not just read it."),
    ("Crawler / bot", "An automated program that reads websites. Google uses one to "
                      "build search results; ChatGPT and Claude use their own."),
    ("Indexing", "Being stored in a search engine's library. If a page is not indexed, "
                 "it cannot appear in results at all."),
    ("Title tag", "The blue clickable headline in Google results and the text on the "
                  "browser tab."),
    ("Meta description", "The two grey lines under your Google listing. Your free "
                         "advert."),
    ("H1 / H2 / H3", "The headline and subheadings on a page. They tell readers and "
                     "machines how the page is organised."),
    ("ALT text", "A written description of an image, read aloud to blind visitors and "
                 "used by search engines to understand the picture."),
    ("Schema / structured data", "A hidden, machine-readable fact sheet on the page: "
                                 "your address, hours, services and prices."),
    ("JSON-LD", "The standard format that structured data is written in."),
    ("Canonical tag", "A note telling Google which version of a page is the real one, "
                      "so duplicates do not compete with each other."),
    ("robots.txt", "A file at the root of your site telling crawlers which pages they "
                   "may read. One wrong line here can hide an entire website."),
    ("Sitemap", "A list of your pages submitted to search engines so nothing is missed."),
    ("Open Graph", "The tags controlling the image and headline shown when your link is "
                   "shared on WhatsApp, Facebook or LinkedIn."),
    ("HTTPS", "The padlock in the address bar. Without it browsers warn visitors that "
              "the site is not secure."),
    ("Core Web Vitals", "Google's three official speed and stability measurements: LCP, "
                        "CLS and responsiveness."),
    ("LCP", "Largest Contentful Paint — how long until the main content is visible. "
            "Should be under 2.5 seconds."),
    ("CLS", "Cumulative Layout Shift — how much the page jumps around while loading. "
            "It is why people tap the wrong button."),
    ("TBT", "Total Blocking Time — how long the page ignores taps because it is busy "
            "running code."),
    ("Render-blocking", "A file the browser must finish downloading before it can show "
                        "anything at all — the cause of a blank screen."),
    ("Lazy loading", "Delaying images below the fold so the visible part of the page "
                     "loads first."),
    ("WCAG", "The international accessibility guidelines: can the site be used with a "
             "screen reader, a keyboard, or poor eyesight."),
    ("ARIA", "Extra markup that tells screen readers what a control is and does."),
    ("Screen reader", "Software that reads a page aloud, used by blind and partially "
                      "sighted visitors."),
    ("E-E-A-T", "Experience, Expertise, Authority, Trust — what Google and AI systems "
                "look for before relying on your content."),
    ("Featured snippet", "The answer box quoted at the top of Google results, above the "
                         "normal listings."),
    ("AI Overview", "Google's AI-written answer at the top of the results page."),
    ("Hydration", "The extra data a modern JavaScript website ships so the page becomes "
                  "interactive. It makes pages look code-heavy to automated checks."),
    ("llms.txt", "An emerging file that gives AI systems a summary map of your site."),
    ("WebMCP", "A very new standard letting a website publish actions — book, quote, "
               "search — that an AI agent can call directly."),
]


def glossary_section():
    rows = "".join(f'<div class="grow"><dt>{e(t)}</dt><dd>{e(d)}</dd></div>'
                   for t, d in GLOSSARY)
    return f"""<section class="page">
  <h2 class="sec">Plain English glossary</h2>
  <p class="lead">Every term used in this report, in one line each. Nothing here needs
    to be memorised — it is here so no part of the report is a black box.</p>
  <dl class="gloss">{rows}</dl>
</section>"""


def lh_card(i, n):
    fix = (f'<div class="blk"><span class="lbl">How it gets fixed</span>'
           f'<p>{e(i["fix"])}</p></div>') if i["fix"] else ""
    found = (f'<p class="finding">{e(i["display"])}</p>' if i["display"]
             else (f'<p class="finding">{i["count"]} element(s) affected</p>'
                   if i["count"] else ""))
    sev = "high" if i["severity"] == "high" else "medium"
    return f"""<article class="issue p-{sev}">
  <header><span class="num">{n}</span>
    <div class="ih"><h4>{e(i['title'])}</h4>{found}</div>
    <div class="tags"><span class="tag pri">{'Fails' if i['score'] == 0 else 'Partial'}</span></div>
  </header>
  <div class="body">
    <div class="blk"><span class="lbl">Why it matters</span><p>{e(i['why'])}</p></div>
    {fix}
  </div>
</article>"""


def lh_sections(l):
    """The two Lighthouse pages: speed, then accessibility and best practices."""
    if not l:
        return ""
    ff = "a simulated mid-range Android phone on a throttled 4G connection" \
        if l["form_factor"] == "mobile" else "a desktop browser on a fast connection"
    colours = {"good": "#16794A", "fair": ACCENT, "poor": "#A32619"}

    mrows = ""
    for m in l["metrics"]:
        col = colours[m["rating"]]
        core = '<span class="cwv">Core Web Vital</span>' if m["core"] else ""
        mrows += f"""<div class="mtr">
          <div class="mtl"><strong>{e(m['name'])}</strong> <span class="ab">{m['abbr']}</span>{core}
            <p>{e(m['meaning'])}</p></div>
          <div class="mtv"><span class="mv" style="color:{col}">{e(m['value'])}</span>
            <span class="mt">target {e(m['target'])}</span></div>
        </div>"""

    opps = l["opportunities"]
    orows = "".join(
        f'<li><strong>{e(o["title"])}</strong>'
        f'{" — " + e(o["display"]) if o["display"] else ""}'
        f'<br><span class="why">{e(o["why"])}</span>'
        f'{"<br><span class=why><em>Fix:</em> " + e(o["fix"]) + "</span>" if o["fix"] else ""}</li>'
        for o in opps[:10])
    more = (f'<li class="more">…and {len(opps) - 10} further opportunities in the raw '
            f'Lighthouse data.</li>' if len(opps) > 10 else "")

    perf = l["scores"]["performance"]
    a11y = l["scores"]["accessibility"]
    bp = l["scores"]["best-practices"]

    a11y_cards = "".join(lh_card(i, n) for n, i in enumerate(l["accessibility_issues"], 1))
    bp_cards = "".join(lh_card(i, n) for n, i in enumerate(l["best_practice_issues"], 1))

    speed = f"""<section class="page">
  <h2 class="sec">Speed &amp; Core Web Vitals</h2>
  <p class="lead">Measured by Google Lighthouse in a real Chrome browser on {ff} —
    the same engine and thresholds Google uses to judge page experience. These are
    measurements, not estimates.</p>

  <div class="lhhead">{ring(perf, 104)}
    <div><h3>Performance {perf}/100</h3>
      <p>{e(verdict_for("performance", perf))} Speed is not a vanity metric: it decides how many people
      stay long enough to see the offer. Google's own research puts the risk of a
      visitor leaving at more than 100% higher when load time goes from one second
      to six.</p>
      <p class="small">{l['passing']['performance']} performance audits passed.</p></div>
  </div>

  <h3 style="margin:6mm 0 1mm">What each measurement means</h3>
  {mrows}

  {'<h3 style="margin:7mm 0 1mm">Biggest speed opportunities</h3>'
   '<p class="lead">Ordered by the impact Lighthouse measured on this page.</p>'
   f'<ul class="climit">{orows}{more}</ul>' if opps else
   '<div class="box" style="margin-top:6mm"><h3>No significant speed opportunities</h3>'
   '<p style="font-size:9.5pt;color:#4A5261">Lighthouse found nothing material to '
   'improve on this page.</p></div>'}
</section>"""

    access = f"""<section class="page">
  <h2 class="sec">Accessibility</h2>
  <p class="lead">How usable this site is with a screen reader, a keyboard, or
    impaired vision — tested against the WCAG guidelines by Lighthouse's automated
    audit.</p>

  <div class="lhhead">{ring(a11y, 104)}
    <div><h3>Accessibility {a11y}/100</h3>
      <p>{e(verdict_for("accessibility", a11y))} Roughly one in six people has a disability affecting how
      they use the web, and accessibility fixes overwhelmingly improve the experience
      for everyone else too — bigger tap targets and better contrast help every
      customer on a phone in the sun.</p>
      <p class="small">{l['passing']['accessibility']} accessibility audits passed ·
      {len(l['accessibility_issues'])} failing.</p></div>
  </div>

  <div class="box"><h3>An honest caveat</h3>
    <p style="font-size:9.5pt;color:#4A5261">Automated testing catches roughly a
      third of real accessibility barriers. A clean score here means no machine-detectable
      failures — it does not prove the site is usable with a screen reader. Anything
      customer-critical, such as an enquiry or checkout flow, deserves a manual test
      with real assistive technology.</p></div>

  {'<h3 style="margin:7mm 0 3mm">Accessibility issues</h3>' + a11y_cards if a11y_cards else
   '<div class="box dark"><h3>No automated failures</h3><p>Lighthouse found no '
   'machine-detectable accessibility failures on this page. Worth protecting through '
   'any redesign — accessibility regressions are easy to introduce and easy to miss.</p></div>'}

  {'<h2 class="sec" style="margin-top:9mm">Browser best practices <span class="secn">('
   + str(len(l['best_practice_issues'])) + ')</span></h2>'
   f'<p class="lead">Standards compliance, security and correctness, scored '
   f'{bp}/100 by Lighthouse.</p>' + bp_cards if bp_cards else
   f'<h2 class="sec" style="margin-top:9mm">Browser best practices</h2>'
   f'<p class="lead">Scored {bp}/100 with no failing audits.</p>'}
</section>"""

    return agentic_section(l) + speed + access


AGENTIC_STATUS = {
    "pass": ("Pass", "#16794A"),
    "partial": ("Partial", "#B8860B"),
    "fail": ("Fails", "#A32619"),
    "na": ("Not present", "#6B7280"),
}


def agentic_section(l):
    """Lighthouse's Agentic Browsing category — can an AI agent actually use this site."""
    rows = l.get("agentic") or []
    if not rows:
        return ""
    score = l["scores"].get("agentic-browsing")
    body = ""
    for a in rows:
        label, colour = AGENTIC_STATUS[a["status"]]
        fix = (f'<p class="afix"><em>What to do:</em> {e(a["fix"])}</p>'
               if a["fix"] and a["status"] != "pass" else "")
        body += f"""<div class="arow">
          <div class="ast" style="background:{colour}">{label}</div>
          <div class="atx"><h4>{e(a['title'])}
            {'<span class="adisp">' + e(a['display']) + '</span>' if a['display'] else ''}</h4>
            <p>{e(a['why'])}</p>{fix}</div>
        </div>"""

    na = [a for a in rows if a["status"] == "na"]
    na_note = ""
    if na:
        na_note = f"""<div class="box"><h3>"Not present" is not a failure — yet</h3>
        <p style="font-size:9.5pt;color:#4A5261">{len(na)} of these checks look for
          WebMCP, a very new standard that lets a website publish actions an AI agent
          can call directly — "get a quote", "book a slot" — instead of the agent
          guessing its way through the interface. Almost no site has this yet, so it
          does not count against the score. We flag it because the businesses that
          adopt it early will be the ones agents can actually transact with.</p></div>"""

    return f"""<section class="page">
  <h2 class="sec">Agentic browsing</h2>
  <p class="lead">Lighthouse's newest category, and the one most people have not heard
    of. It does not ask whether an AI can <em>read</em> the site — that is the AEO and
    GEO work earlier in this report — but whether an AI agent can <em>operate</em> it:
    navigate the pages, understand the controls and complete a task on a customer's
    behalf.</p>

  <div class="lhhead">{ring(score, 104)}
    <div><h3>Agentic browsing {score if score is not None else '—'}/100</h3>
      <p>{e(verdict_for("agentic", score))} Assistants are moving from answering questions to
      completing tasks. A site an agent cannot operate gets skipped in favour of a
      competitor's that it can — and the customer never learns you were an option.</p>
      <p class="small">Measured by Lighthouse {e(l['version'])} in Chrome.</p></div>
  </div>

  {na_note}
  <h3 style="margin:6mm 0 3mm">What was checked</h3>
  {body}
</section>"""


def build_html(data, an, client, generated):
    site = urllib.parse.urlparse(data.get("url", "")).netloc or data.get("url", "")
    display = client or site.replace("www.", "")
    st = data.get("siteType") or {}
    cms = st.get("detectedCms")
    kind = (st.get("siteType") or "").replace("_", " ")

    # cover scores
    cards = ""
    for k, (abbr, full, blurb) in PILLARS.items():
        p = an["pillars"][k]
        cards += f"""<div class="scard">
          {ring(p['score'])}
          <h3>{abbr}</h3>
          <p class="full">{full}</p>
          <p class="cnt">{p['passed']} of {p['total']} checks passed</p>
          <p class="iss">{len(p['issues'])} issue{'s' if len(p['issues'])!=1 else ''} found</p>
        </div>"""

    # what we measured
    measured = ""
    for k, (abbr, full, blurb) in PILLARS.items():
        p = an["pillars"][k]
        measured += f"""<div class="mrow">
          <div class="mk"><span class="abbr">{abbr}</span><span class="mfull">{full}</span></div>
          <div class="mtxt"><p>{blurb}</p>
            <div class="mscore">{bar(p['score'] or 0, {"good":"#16794A","fair":ACCENT,"poor":"#A32619","na":"#98A2B3"}[band(p['score'])])}
            <span class="msnum">{p['score'] if p['score'] is not None else '—'}<small>/100</small></span></div>
            <p class="verdict">{e(verdict(p['score']))}</p>
          </div>
        </div>"""

    # priority summary
    c = an["counts"]
    prio = ""
    for key, colour, desc in (
        ("critical", "#A32619", "Fix immediately — actively costing traffic or trust"),
        ("high", ACCENT, "Schedule this month — meaningful visibility gains"),
        ("medium", "#B8860B", "Worth doing — incremental improvements"),
        ("low", "#6B7280", "Nice to have — minimal business impact"),
    ):
        prio += f"""<div class="pcell">
          <span class="pnum" style="color:{colour}">{c[key]}</span>
          <span class="plab">{PRIORITY_LABEL[key]}</span>
          <span class="pdesc">{desc}</span></div>"""

    # AI platforms
    plats = an["pillars"]["geo"].get("platforms") or []
    prows = ""
    for p in plats:
        name = PLATFORM_NAMES.get(p.get("platform"), str(p.get("platform", "")).title())
        sc = p.get("score", 0)
        col = {"good": "#16794A", "fair": ACCENT, "poor": "#A32619", "na": "#98A2B3"}[band(sc)]
        prows += f"""<div class="prow">
          <span class="pname">{e(name)}</span>{bar(sc, col)}
          <span class="pscore">{sc}<small>/100</small></span>
          <span class="ppass">{p.get('passed',0)}/{p.get('total',0)} checks</span></div>"""

    # quick wins
    qw = ""
    for i in an["quick_wins"]:
        qw += (f'<li><strong>{e(i["title"])}</strong> — {e(i["fix"])}</li>')
    qw = qw or "<li>No quick wins outstanding — the low-effort fundamentals are already in place.</li>"

    # Lighthouse strip on the cover
    l = an.get("lh")
    health_strip = ""
    if l:
        chips = ""
        for key in ("performance", "accessibility", "best-practices", "agentic-browsing"):
            sc = l["scores"].get(key)
            if sc is None:
                continue
            col = {"good": "#3FA776", "fair": "#E0762F", "poor": "#D4553F",
                   "na": "#8B93A5"}[band(sc)]
            chips += (f'<div class="hchip"><span class="hs" style="color:{col}">{sc}</span>'
                      f'<span class="hl">{LH_LABEL.get(key, key)}</span></div>')
        ff = "mobile" if l["form_factor"] == "mobile" else "desktop"
        health_strip = (f'<div class="hrow">{chips}</div>'
                        f'<p class="hnote">Measured in Chrome by Google Lighthouse '
                        f'{e(l["version"])} · {ff} test</p>')
    lh_credit = (f"Performance, accessibility, best-practice and agentic-browsing "
                 f"figures are real measurements taken in Chrome by Google Lighthouse "
                 f"{e(l['version'])} ({e(l['form_factor'])} test with standard "
                 f"throttling); scores vary between runs and network conditions. "
                 if l else "")

    # platform constraints
    pf = an["platform"]
    blocked_rows = "".join(
        f'<li><strong>{e(i["title"])}</strong> <span class="pill">{PRIORITY_LABEL[i["priority"]]}</span>'
        f'<br><span class="why">{e(i["limit_reason"])}</span></li>'
        for i in an["blocked"])
    limited_rows = "".join(
        f'<li><strong>{e(i["title"])}</strong><br><span class="why">{e(i["limit_reason"])}</span></li>'
        for i in an["limited"][:10])

    if an["blocked"]:
        blocked_block = f"""<h3 style="margin:6mm 0 2mm">Cannot be fixed on {e(pf['name'])}
          <span class="cnt2">{len(an['blocked'])} of {len(an['issues'])} issues</span></h3>
        <p class="lead">These findings are real, but the platform does not expose the
          setting involved. No amount of development work resolves them while the site
          stays where it is.</p>
        <ul class="climit blocked">{blocked_rows}</ul>"""
    else:
        blocked_block = f"""<div class="box" style="margin-top:6mm">
          <h3>Nothing is hard-blocked</h3>
          <p style="font-size:9.5pt;color:#4A5261">Every issue in this report can be
            resolved on {e(pf['name'])}. What varies is cost and effort, not whether it
            is possible.</p></div>"""

    limited_block = ""
    if an["limited"]:
        more = (f'<li class="more">…and {len(an["limited"]) - 10} more marked '
                f'"Platform constrained" in the sections that follow.</li>'
                if len(an["limited"]) > 10 else "")
        limited_block = f"""<h3 style="margin:6mm 0 2mm">Constrained by the platform
          <span class="cnt2">{len(an['limited'])} issues</span></h3>
        <p class="lead">Fixable, but partly determined by how {e(pf['name'])} builds pages.
          Expect improvement rather than a perfect score on these.</p>
        <ul class="climit">{limited_rows}{more}</ul>"""

    detected_line = (f"Detected platform: <strong>{e(pf['name'])}</strong>"
                     if an["platform_known"] else
                     "We could not identify the platform from the page signature.")

    platform_section = f"""<section class="page">
  <h2 class="sec">Platform &amp; architecture</h2>
  <p class="lead">{detected_line} — {e(pf['kind'])}. Level of control:
    {e(pf['control'])}</p>

  <div class="ptwo">
    <div class="pgood"><span class="lbl">What this platform does well</span>
      <p>{e(pf['good'])}</p></div>
    <div class="pwall"><span class="lbl">Where it stops</span>
      <p>{e(pf['wall'])}</p></div>
  </div>

  <div class="box dark" style="margin-top:5mm">
    <h3>Our read</h3><p>{e(pf['verdict'])}</p></div>

  {blocked_block}
  {limited_block}
</section>"""

    # issue sections
    sections = ""
    for k, (abbr, full, blurb) in PILLARS.items():
        p = an["pillars"][k]
        if not p["issues"]:
            sections += f"""<section class="page"><h2 class="sec">{abbr} Issues (0)</h2>
              <p class="lead">No {abbr} issues found. {e(blurb)}</p></section>"""
            continue
        cardsx = "".join(issue_card(i, n, pf["name"])
                         for n, i in enumerate(p["issues"], 1))
        sections += f"""<section class="page">
          <h2 class="sec">{abbr} Issues <span class="secn">({len(p['issues'])})</span></h2>
          <p class="lead">{e(blurb)} Every issue below is listed with what we found,
             why it matters to the business, and what fixing it involves.</p>
          {cardsx}
        </section>"""

    # what's working
    working = ""
    bycat = {}
    for pz in an["passes"]:
        bycat.setdefault(pz["pillar"], []).append(pz)
    for k, (abbr, full, blurb) in PILLARS.items():
        items = bycat.get(k, [])
        if not items:
            continue
        lis = "".join(f"<li>{e(x['title'])}</li>" for x in sorted(items, key=lambda x: x["title"]))
        working += (f'<div class="wblk"><h4>{abbr} <span>{len(items)} passing</span></h4>'
                    f'<ul class="wlist">{lis}</ul></div>')

    dtl = f"{kind} site" if kind else "site"
    cmsl = (f" built on <strong>{e(an['platform']['name'])}</strong>"
            if an["platform_known"] else "")

    return f"""<style>
{font_face()}
*{{box-sizing:border-box;margin:0;padding:0}}
html{{-webkit-print-color-adjust:exact;print-color-adjust:exact}}
body{{font-family:Geist,-apple-system,'Helvetica Neue',sans-serif;color:{INK};
  font-size:10.5pt;line-height:1.55;background:{PAPER}}}
@page{{size:A4;margin:14mm 13mm 14mm}}
@page:first{{margin:0}}
.page{{page-break-before:always;padding-top:2mm}}
h2.sec{{font-size:20pt;font-weight:700;letter-spacing:-.02em;margin-bottom:3mm;
  padding-bottom:2.5mm;border-bottom:2.5px solid {ACCENT}}}
h2.sec .secn{{color:{ACCENT};font-weight:600}}
h3{{font-size:13pt;font-weight:650;letter-spacing:-.01em}}
.lead{{color:#4A5261;font-size:10pt;margin-bottom:5mm;max-width:165mm}}

/* cover */
.cover{{background:{INK};color:#fff;padding:20mm 16mm 14mm;min-height:297mm;
  display:flex;flex-direction:column}}
.brand{{display:flex;align-items:center;gap:3mm;margin-bottom:auto}}
.mark{{width:9mm;height:9mm;background:{ACCENT};border-radius:2mm;position:relative}}
.mark::after{{content:'';position:absolute;inset:2.6mm;border:1.4mm solid #fff;border-radius:.8mm}}
.bname{{font-size:15pt;font-weight:700;letter-spacing:-.02em}}
.btag{{font-size:8pt;color:#8B93A5;letter-spacing:.14em;text-transform:uppercase;margin-top:.6mm}}
.ctitle{{font-size:38pt;font-weight:700;line-height:1.03;letter-spacing:-.035em;margin:10mm 0 5mm}}
.ctitle em{{color:{ACCENT};font-style:normal}}
.csub{{font-size:12pt;color:#B7BECD;max-width:130mm;margin-bottom:9mm}}
.csite{{font-size:19pt;font-weight:600;color:#fff;padding:4mm 0 3mm;
  border-top:1px solid #262C3D;border-bottom:1px solid #262C3D;margin-bottom:9mm}}
.csite small{{display:block;font-size:8.5pt;color:#8B93A5;font-weight:400;
  letter-spacing:.12em;text-transform:uppercase;margin-bottom:1.5mm}}
.overall{{display:flex;align-items:center;gap:6mm;margin-bottom:9mm}}
.obig{{font-size:52pt;font-weight:700;letter-spacing:-.04em;color:{ACCENT};line-height:1}}
.obig small{{font-size:16pt;color:#8B93A5}}
.overall p{{color:#B7BECD;font-size:10pt;max-width:105mm}}
.overall .olab{{color:#fff;display:block;font-size:11pt;margin-bottom:1mm;font-weight:650}}
.overall p strong{{color:#fff}}
.scards{{display:flex;gap:4mm}}
.scard{{flex:1;background:#fff;border-radius:3mm;padding:6mm 4mm;text-align:center;color:{INK}}}
.scard h3{{margin-top:2mm;font-size:15pt;letter-spacing:.02em}}
.scard .full{{font-size:7.5pt;color:#6B7280;margin-bottom:2.5mm}}
.scard .cnt{{font-size:8.5pt;color:#4A5261}}
.scard .iss{{font-size:8.5pt;color:{ACCENT};font-weight:600;margin-top:.5mm}}
.ring-num{{font-size:27px;font-weight:700}}
.cfoot{{margin-top:auto;padding-top:8mm;border-top:1px solid #262C3D;
  display:flex;justify-content:space-between;font-size:8.5pt;color:#8B93A5}}

/* what we measured */
.mrow{{display:flex;gap:5mm;padding:4.5mm 0;border-bottom:1px solid #EAE7E2;
  page-break-inside:avoid}}
.mk{{width:38mm;flex-shrink:0}}
.abbr{{display:block;font-size:17pt;font-weight:700;color:{ACCENT};letter-spacing:-.02em}}
.mfull{{display:block;font-size:8pt;color:#6B7280;line-height:1.3}}
.mtxt{{flex:1}}
.mtxt p{{font-size:9.5pt;color:#3A4150}}
.mscore{{display:flex;align-items:center;gap:3mm;margin:2.5mm 0 1.5mm}}
.msnum{{font-size:13pt;font-weight:700;width:20mm;text-align:right}}
.msnum small{{font-size:8pt;color:#98A2B3;font-weight:400}}
.verdict{{font-size:9pt;color:{INK};font-weight:550}}
.bar{{flex:1;height:7px;background:#EDEAE5;border-radius:4px;overflow:hidden}}
.bar span{{display:block;height:100%;border-radius:4px}}

/* priority grid */
.pgrid{{display:flex;gap:3mm;margin:5mm 0 7mm}}
.pcell{{flex:1;border:1px solid #E4E0DA;border-top:3px solid {ACCENT};border-radius:2mm;
  padding:4mm 3.5mm}}
.pcell:nth-child(1){{border-top-color:#A32619}}
.pcell:nth-child(3){{border-top-color:#B8860B}}
.pcell:nth-child(4){{border-top-color:#6B7280}}
.pnum{{display:block;font-size:26pt;font-weight:700;line-height:1;letter-spacing:-.03em}}
.plab{{display:block;font-size:10pt;font-weight:650;margin:1mm 0 1mm}}
.pdesc{{display:block;font-size:8pt;color:#6B7280;line-height:1.4}}

/* boxes */
.box{{background:#FAF8F5;border-left:3px solid {ACCENT};border-radius:0 2mm 2mm 0;
  padding:5mm 5.5mm;margin:5mm 0;page-break-inside:avoid}}
.box h3{{margin-bottom:2.5mm}}
.box ul{{margin-left:4.5mm;font-size:9.5pt;color:#3A4150}}
.box li{{margin-bottom:1.8mm}}
.dark{{background:{INK};color:#fff;border-radius:3mm;padding:7mm;border:0}}
.dark h3{{color:#fff}}.dark p{{color:#B7BECD;font-size:9.5pt}}
.dark strong{{color:{ACCENT}}}

/* platforms */
.prow{{display:flex;align-items:center;gap:4mm;padding:3.2mm 0;
  border-bottom:1px solid #EFECE7}}
.pname{{width:44mm;font-weight:600;font-size:10pt}}
.pscore{{width:16mm;text-align:right;font-weight:700;font-size:11pt}}
.pscore small{{font-size:7.5pt;color:#98A2B3;font-weight:400}}
.ppass{{width:24mm;text-align:right;font-size:8.5pt;color:#6B7280}}

/* issues */
.issue{{border:1px solid #E4E0DA;border-radius:2.5mm;margin-bottom:4mm;
  page-break-inside:avoid;overflow:hidden}}
.issue header{{display:flex;gap:3.5mm;align-items:flex-start;padding:4mm 4.5mm 3mm;
  border-left:3.5px solid #98A2B3;background:#FCFBF9}}
.p-critical header{{border-left-color:#A32619}}
.p-high header{{border-left-color:{ACCENT}}}
.p-medium header{{border-left-color:#B8860B}}
.num{{font-size:11pt;font-weight:700;color:#B4BAC6;width:7mm;flex-shrink:0;padding-top:.4mm}}
.ih{{flex:1}}
.ih h4{{font-size:11.5pt;font-weight:650;letter-spacing:-.01em}}
.finding{{font-size:9pt;color:#5A616F;margin-top:.8mm}}
.tags{{display:flex;flex-direction:column;gap:1.2mm;align-items:flex-end;flex-shrink:0}}
.tag{{font-size:7pt;font-weight:650;letter-spacing:.05em;text-transform:uppercase;
  padding:1mm 2.2mm;border-radius:1mm;white-space:nowrap}}
.pri{{background:#98A2B3;color:#fff}}
.p-critical .pri{{background:#A32619}}
.p-high .pri{{background:{ACCENT}}}
.p-medium .pri{{background:#B8860B}}
.eff{{background:#F0EDE8;color:#5A616F}}
.body{{display:flex;gap:5mm;padding:3.5mm 4.5mm 4mm 12mm}}
.blk{{flex:1}}
.lbl{{display:block;font-size:7.5pt;font-weight:700;letter-spacing:.1em;
  text-transform:uppercase;color:{ACCENT};margin-bottom:1.2mm}}
.blk p{{font-size:9pt;color:#3A4150;line-height:1.5}}

/* lighthouse */
.lhhead{{display:flex;gap:6mm;align-items:center;margin:4mm 0 2mm;
  padding-bottom:4mm;border-bottom:1px solid #EAE7E2}}
.lhhead h3{{margin-bottom:1.5mm}}
.lhhead p{{font-size:9.5pt;color:#3A4150;line-height:1.5}}
.lhhead .small{{font-size:8.5pt;color:#6B7280;margin-top:1.5mm}}
.mtr{{display:flex;gap:5mm;align-items:center;padding:3mm 0;
  border-bottom:1px solid #EFECE7;page-break-inside:avoid}}
.mtl{{flex:1}}
.mtl strong{{font-size:10pt}}
.ab{{font-size:8pt;color:#6B7280;background:#F2EFEA;padding:.4mm 1.6mm;
  border-radius:1mm;margin-left:1.5mm}}
.cwv{{font-size:7pt;font-weight:650;letter-spacing:.06em;text-transform:uppercase;
  color:#fff;background:{ACCENT};padding:.6mm 1.8mm;border-radius:1mm;margin-left:1.5mm}}
.mtl p{{font-size:8.5pt;color:#5A616F;margin-top:.8mm;line-height:1.45}}
.mtv{{width:30mm;text-align:right}}
.mv{{display:block;font-size:14pt;font-weight:700;letter-spacing:-.02em}}
.mt{{display:block;font-size:7.5pt;color:#98A2B3}}
.arow{{display:flex;gap:4mm;padding:3.5mm 0;border-bottom:1px solid #EFECE7;
  page-break-inside:avoid}}
.ast{{width:22mm;flex-shrink:0;color:#fff;font-size:7.5pt;font-weight:650;
  letter-spacing:.05em;text-transform:uppercase;text-align:center;
  padding:1.4mm 0;border-radius:1.2mm;height:fit-content}}
.atx{{flex:1}}
.atx h4{{font-size:10.5pt;font-weight:650}}
.adisp{{font-size:8.5pt;color:{ACCENT};font-weight:600;margin-left:2mm}}
.atx p{{font-size:9pt;color:#3A4150;margin-top:1mm;line-height:1.5}}
.afix{{font-size:8.5pt;color:#5A616F;margin-top:1.2mm}}
.afix em{{color:{ACCENT};font-style:normal;font-weight:650}}
.hrow{{display:flex;gap:3mm;margin-top:4mm}}
.hchip{{flex:1;background:#141A29;border-radius:2mm;padding:3.5mm 3mm;text-align:center}}
.hchip .hs{{display:block;font-size:19pt;font-weight:700;letter-spacing:-.03em;line-height:1}}
.hchip .hl{{display:block;font-size:7.5pt;color:#8B93A5;margin-top:1.2mm;
  letter-spacing:.06em;text-transform:uppercase}}
.hnote{{font-size:7.5pt;color:#8B93A5;margin-top:2.5mm;text-align:center}}

/* platform */
.ptwo{{display:flex;gap:4mm;margin-top:4mm}}
.pgood,.pwall{{flex:1;border:1px solid #E4E0DA;border-radius:2.5mm;padding:5mm}}
.pgood{{border-left:3px solid #16794A}}
.pwall{{border-left:3px solid #A32619}}
.ptwo p{{font-size:9pt;color:#3A4150;line-height:1.5}}
.cnt2{{font-size:9pt;font-weight:600;color:{ACCENT};margin-left:2mm}}
.climit{{list-style:none;margin:3mm 0 0}}
.climit li{{padding:2.8mm 0 2.8mm 6mm;border-bottom:1px solid #EFECE7;position:relative;
  font-size:9.5pt;page-break-inside:avoid}}
.climit li::before{{content:'';position:absolute;left:0;top:4.2mm;width:2.6mm;height:2.6mm;
  border-radius:.6mm;background:#B8860B}}
.climit.blocked li::before{{background:#A32619}}
.climit .why{{font-size:8.5pt;color:#5A616F;line-height:1.45}}
.climit .more{{color:#6B7280;font-size:8.5pt;font-style:italic}}
.climit .more::before{{display:none}}
.pill{{font-size:7pt;font-weight:650;letter-spacing:.05em;text-transform:uppercase;
  background:#F0EDE8;color:#5A616F;padding:.6mm 1.8mm;border-radius:1mm;vertical-align:1px}}
.limit{{margin:0 4.5mm 4mm 12mm;padding:3mm 3.5mm;background:#FBF6F0;
  border-left:2.5px solid #B8860B;border-radius:0 1.5mm 1.5mm 0}}
.limit.blocked{{background:#FBF1EF;border-left-color:#A32619}}
.limit p{{font-size:8.5pt;color:#3A4150;line-height:1.5}}
.limit .lbl{{color:#8A6A1F}}
.limit.blocked .lbl{{color:#A32619}}
.blocked.tag,.tag.blocked{{background:#A32619;color:#fff}}
.tag.limited{{background:#B8860B;color:#fff}}

/* working */
.wblk{{margin-bottom:5mm;page-break-inside:avoid}}
.wblk h4{{font-size:11pt;font-weight:650;padding-bottom:1.5mm;
  border-bottom:1px solid #E4E0DA;margin-bottom:2.5mm}}
.wblk h4 span{{float:right;font-size:8.5pt;color:#16794A;font-weight:600}}
.wlist{{columns:3;column-gap:6mm;list-style:none;font-size:8.5pt;color:#4A5261}}
.wlist li{{padding-left:4mm;position:relative;margin-bottom:1.1mm;
  break-inside:avoid}}
.wlist li::before{{content:'';position:absolute;left:0;top:1.6mm;width:2.2mm;height:2.2mm;
  border-radius:50%;background:#16794A}}

/* glossary */
.gloss{{columns:2;column-gap:7mm}}
.grow{{break-inside:avoid;padding:.8mm 0;border-bottom:1px solid #F2EFEA}}
.gloss dt{{font-size:8.5pt;font-weight:650;color:{INK}}}
.gloss dd{{font-size:8pt;color:#5A616F;line-height:1.34;margin-top:.2mm}}

/* cta */
.cta{{background:{INK};color:#fff;border-radius:3mm;padding:9mm;margin-top:6mm}}
.cta h2{{font-size:22pt;font-weight:700;letter-spacing:-.03em;margin-bottom:3mm}}
.cta h2 em{{color:{ACCENT};font-style:normal}}
.cta p{{color:#B7BECD;font-size:10pt;max-width:140mm;margin-bottom:4mm}}
.steps{{display:flex;gap:4mm;margin:6mm 0}}
.step{{flex:1;background:#151B2B;border-radius:2mm;padding:4.5mm}}
.step span{{display:block;font-size:9pt;font-weight:700;color:{ACCENT};margin-bottom:1.5mm}}
.step p{{font-size:8.5pt;color:#B7BECD;margin:0}}
.contact{{border-top:1px solid #262C3D;padding-top:5mm;display:flex;
  justify-content:space-between;align-items:flex-end}}
.contact .who{{font-size:12pt;font-weight:650}}
.contact .where{{font-size:9.5pt;color:{ACCENT}}}
.disclaimer{{font-size:7.5pt;color:#8A8F9B;margin-top:6mm;line-height:1.5}}
</style>

<section class="cover">
  <div class="brand"><div class="mark"></div>
    <div><div class="bname">Boldpiq</div>
    <div class="btag">Websites &middot; AI &middot; Lead Systems</div></div></div>

  <h1 class="ctitle">Website<br>Visibility <em>Report</em></h1>
  <p class="csub">An independent audit of how this website performs in Google search
    and in AI assistants — and exactly what to fix first.</p>

  <div class="csite"><small>Prepared for</small>{e(display)}</div>

  <div class="overall">
    <div class="obig">{an['overall'] if an['overall'] is not None else '—'}<small>/100</small></div>
    <div><span class="olab">Overall visibility score</span>
      <p>{e(verdict(an['overall']))} We found <strong>{len(an['issues'])} issues</strong>
      across {len(an['issues']) + len(an['passes'])} checks, of which
      {an['counts']['critical'] + an['counts']['high']} are high impact.</p></div>
  </div>

  <div class="scards">{cards}</div>
  {health_strip}

  <div class="cfoot"><span>Prepared by Boldpiq &middot; boldpiq.com</span>
    <span>{e(generated.strftime('%d %B %Y'))}</span></div>
</section>

<section class="page">
  <h2 class="sec">What we measured</h2>
  <p class="lead">Being found online no longer means only Google. Buyers now ask
    ChatGPT, Claude, Perplexity and Google's own AI for recommendations, and those
    systems read a website very differently to a traditional search crawler. We
    scored this {e(dtl)}{cmsl} on all three.</p>
  {measured}

  <div class="box" style="margin-top:7mm"><h3>How to read this report</h3>
    <p style="font-size:9.5pt;color:#4A5261">Each issue is listed with three things:
      what we found on the page, why it matters commercially, and what fixing it
      involves. Nothing here is a guess — every item is a specific, verifiable
      finding on this website, and every one of them can be re-tested once the work
      is done.</p></div>
</section>

<section class="page">
  <h2 class="sec">Where to start</h2>
  <p class="lead">Every issue in this report is graded by business impact, not
    technical severity. A missing meta tag that costs enquiries outranks a
    technically interesting problem that costs nothing.</p>
  <div class="pgrid">{prio}</div>

  <div class="box"><h3>Start here — quick wins</h3>
    <p style="font-size:9.5pt;color:#4A5261;margin-bottom:2.5mm">High-impact fixes
      that take little effort. These move the score fastest, and none of them are
      blocked by the platform this site is built on.</p>
    <ul>{qw}</ul></div>

  <div class="box dark"><h3>The order we would work in</h3>
    <p><strong>1. Quick wins above</strong> — days, not weeks, and they lift the score
      immediately.<br>
    <strong>2. Remaining critical and high items</strong> — {an['counts']['critical'] + an['counts']['high']}
      in total, these are where traffic and enquiries are actually being lost.<br>
    <strong>3. Structural and content work</strong> — depth, answer formatting and
      citable content. Slower, but it is what gets a business named by an AI assistant
      rather than skipped.<br>
    <strong>4. Medium and low items</strong> — worth doing once the above is done.</p></div>
</section>

<section class="page">
  <h2 class="sec">AI assistant readiness</h2>
  <p class="lead">How this site scores against the specific things each AI platform
    looks for when deciding which businesses to name in an answer. A low score here
    means the assistant is recommending competitors instead.</p>
  {prows or '<p class="lead">No platform breakdown returned for this scan.</p>'}

  <div class="box dark" style="margin-top:7mm">
    <h3>Why this matters now</h3>
    <p>When someone asks an AI assistant to recommend a supplier, it answers from a
    handful of sources it can read, trust and quote. There is no page two — either
    you are in the answer or you are invisible. The checks in this report are the
    difference between <strong>being cited</strong> and being skipped.</p>
  </div>
</section>

{lh_sections(l)}

{platform_section}

{sections}

<section class="page">
  <h2 class="sec">What's already working</h2>
  <p class="lead">Not everything needs fixing. These {len(an['passes'])} checks passed
    and are worth protecting during any future redesign or migration.</p>
  {working}
</section>

{glossary_section()}

<section class="page">
  <div class="cta">
    <h2>Want this <em>fixed</em>?</h2>
    <p>Boldpiq builds and maintains websites that are engineered to be found — by
      Google and by the AI assistants your customers now ask first. We can work
      through this report with you, or handle it end to end.</p>
    <div class="steps">
      <div class="step"><span>01 &nbsp;Triage</span>
        <p>We fix the critical and high-priority items first — the ones actively
          costing enquiries.</p></div>
      <div class="step"><span>02 &nbsp;Rebuild</span>
        <p>Structure, speed, schema and content reshaped so both search engines and
          AI can read the site properly.</p></div>
      <div class="step"><span>03 &nbsp;Re-scan</span>
        <p>We re-run this exact audit so you can see the score move, in writing.</p></div>
    </div>
    <div class="contact">
      <div><div class="who">Boldpiq</div>
        <div style="font-size:9pt;color:#8B93A5">Websites, AI assistants &amp; lead systems</div></div>
      <div class="where">boldpiq.com</div>
    </div>
  </div>
  <p class="disclaimer">Report generated {e(generated.strftime('%d %B %Y at %H:%M'))} for
    {e(data.get('url',''))}. Scores reflect the page as published at the time of
    scanning and will change as the site changes. Automated checks cover technical and
    structural factors; they do not replace a manual review of content quality,
    commercial positioning or legal compliance. {lh_credit}Structural scan engine:
    seoscore.tools. Analysis, prioritisation and recommendations: Boldpiq.</p>
</section>"""


# ── render ───────────────────────────────────────────────────────────────────

def render_pdf(html_path, pdf_path):
    if not os.path.exists(CHROME):
        raise SystemExit(f"Google Chrome not found at {CHROME}")
    cmd = [CHROME, "--headless", "--disable-gpu", "--no-sandbox",
           "--no-pdf-header-footer", "--run-all-compositor-stages-before-draw",
           "--virtual-time-budget=10000",
           f"--print-to-pdf={pdf_path}", f"file://{html_path}"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if not os.path.exists(pdf_path):
        raise SystemExit(f"Chrome failed to produce a PDF:\n{r.stderr[-600:]}")


def slug(url):
    host = urllib.parse.urlparse(url).netloc or url
    return re.sub(r"[^a-z0-9]+", "-", host.lower().replace("www.", "")).strip("-")


def normalise(url):
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    return url


# ── cli ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="Generate a branded Boldpiq website visibility PDF report.")
    ap.add_argument("urls", nargs="+", help="client website URL(s)")
    ap.add_argument("--client", help="client name for the cover (default: domain)")
    ap.add_argument("--keyphrase", default="", help="target keyphrase to score against")
    ap.add_argument("--out", default=REPORTS, help="output directory")
    ap.add_argument("--keep-html", action="store_true", help="keep the intermediate HTML")
    ap.add_argument("--open", dest="open_pdf", action="store_true", help="open the PDF when done")
    ap.add_argument("--from-json", help="render from a saved scan JSON instead of scanning")
    ap.add_argument("--no-lighthouse", action="store_true",
                    help="skip the Chrome Lighthouse run (faster, less complete)")
    ap.add_argument("--desktop", action="store_true",
                    help="run Lighthouse as desktop instead of mobile")
    a = ap.parse_args()

    if not a.no_lighthouse and not lh.available()[0]:
        print("   note: Lighthouse not found — continuing without speed, accessibility "
              "and agentic sections", file=sys.stderr)

    os.makedirs(a.out, exist_ok=True)
    made = []

    for n, raw in enumerate(a.urls):
        url = normalise(raw)
        print(f"→ {url}")

        if a.from_json:
            data = json.load(open(a.from_json))
        else:
            if n:
                time.sleep(6)   # stay well inside the scanner's fair-use limits
            print("   scanning …")
            data = scan(url, a.keyphrase)

        an = analyse(data)

        an["lh"] = None
        if not a.no_lighthouse:
            print("   measuring in Chrome (Lighthouse) …")
            an["lh"] = lh.run(url, "desktop" if a.desktop else "mobile")
            if an["lh"] is None:
                print("   Lighthouse did not complete — continuing without it",
                      file=sys.stderr)

        generated = dt.datetime.now()
        stamp = generated.strftime("%Y-%m-%d")
        base = os.path.join(a.out, f"{slug(url)}-visibility-report-{stamp}")

        with open(base + ".json", "w") as f:
            json.dump(data, f, indent=1)

        html_path = base + ".html"
        with open(html_path, "w") as f:
            f.write(build_html(data, an, a.client, generated))

        pdf_path = base + ".pdf"
        print("   rendering PDF …")
        render_pdf(html_path, pdf_path)
        if not a.keep_html:
            os.remove(html_path)

        c = an["counts"]
        print(f"   overall {an['overall']}/100 · "
              f"SEO {an['pillars']['seo']['score']} · "
              f"AEO {an['pillars']['aeo']['score']} · "
              f"GEO {an['pillars']['geo']['score']}")
        print(f"   {len(an['issues'])} issues "
              f"({c['critical']} critical, {c['high']} high, "
              f"{c['medium']} medium, {c['low']} low)")
        if an["lh"]:
            s = an["lh"]["scores"]
            print(f"   lighthouse · perf {s.get('performance')} · "
                  f"a11y {s.get('accessibility')} · "
                  f"best-practice {s.get('best-practices')} · "
                  f"agentic {s.get('agentic-browsing')}")
        print(f"   {pdf_path}")
        made.append(pdf_path)

    if a.open_pdf and made:
        subprocess.run(["open"] + made)


if __name__ == "__main__":
    main()
