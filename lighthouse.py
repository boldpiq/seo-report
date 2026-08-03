"""
Google Lighthouse integration — real Chrome measurements, not estimates.

Adds the half of the picture the HTML scan cannot see: how fast the page
actually loads on a mid-range phone, and how usable it is with a screen reader
or a keyboard.

Reuses the Lighthouse install already sitting in ~/boldpiq-tools/site-audit, and
degrades gracefully to nothing if Node or Lighthouse is unavailable — the report
still generates, just without these sections.

Audit explanations are Boldpiq's. Where an audit is not in our library we fall
back to Lighthouse's own description text (Apache 2.0, credited in the report).
"""

import glob
import json
import os
import re
import shutil
import subprocess
import tempfile

import runtime

HOME = os.path.expanduser("~")

CATEGORIES = ["performance", "accessibility", "best-practices", "seo",
              "agentic-browsing"]

CATEGORY_BLURB = {
    "agentic-browsing": "Whether an AI agent can read, navigate and actually operate "
                        "this site on a customer's behalf.",
    "performance": "How fast the page loads and becomes usable on a mid-range phone "
                   "over a throttled mobile connection.",
    "accessibility": "How usable the page is with a screen reader, a keyboard, or "
                     "impaired vision.",
    "best-practices": "Whether the page follows current web platform standards for "
                      "security, correctness and browser behaviour.",
    "seo": "Lighthouse's own basic crawlability checks — a sanity check alongside the "
           "deeper SEO section of this report.",
}

# Core Web Vitals and supporting metrics, in the order we present them.
METRICS = [
    ("largest-contentful-paint", "Largest Contentful Paint", "LCP",
     "How long until the main content is actually visible. This is the number "
     "visitors experience as 'the site is slow'.", "Under 2.5s", 2500, 4000),
    ("first-contentful-paint", "First Contentful Paint", "FCP",
     "How long the visitor stares at a blank screen before anything appears at all.",
     "Under 1.8s", 1800, 3000),
    ("total-blocking-time", "Total Blocking Time", "TBT",
     "How long the page is frozen and ignoring taps while JavaScript runs. High "
     "values feel like a broken site on a phone.", "Under 200ms", 200, 600),
    ("cumulative-layout-shift", "Cumulative Layout Shift", "CLS",
     "How much the page jumps around while loading. This is what causes people to "
     "tap the wrong button.", "Under 0.1", 0.1, 0.25),
    ("speed-index", "Speed Index", "SI",
     "How quickly the page visibly fills in overall.", "Under 3.4s", 3400, 5800),
    ("interactive", "Time to Interactive", "TTI",
     "How long until the page reliably responds to taps and clicks.",
     "Under 3.8s", 3800, 7300),
]

# Boldpiq explanations. (why it matters, how it gets fixed)
NOTES = {

# ── accessibility ────────────────────────────────────────────────────────────
"color-contrast": (
    "Text that does not contrast enough with its background is unreadable in "
    "sunlight, on a cheap screen, or for the roughly one in twelve men with colour "
    "vision deficiency. It is the single most common accessibility failure on the web.",
    "Darken text or lighten backgrounds until the contrast ratio is at least 4.5:1 "
    "for body text and 3:1 for large text."),
"image-alt": (
    "Images without ALT text are announced as 'image' and nothing else by a screen "
    "reader. Where the image is a button or a logo, the user is simply stuck.",
    "Add a short, literal alt attribute to every meaningful image, and alt=\"\" to "
    "purely decorative ones."),
"link-name": (
    "A link with no discernible text is read out as 'link' with no destination. Icon-only "
    "links are the usual culprit.",
    "Give every link visible text, or an aria-label describing where it goes."),
"button-name": (
    "A button with no accessible name cannot be understood or activated with "
    "confidence by anyone using a screen reader.",
    "Add visible text inside the button, or an aria-label."),
"label": (
    "Form fields without labels are the most common reason an enquiry form is "
    "unusable with assistive technology — and placeholders vanish the moment typing "
    "starts, which confuses everyone.",
    "Associate a <label for> with every input, or use aria-label."),
"heading-order": (
    "Screen reader users navigate by jumping between headings. Skipped levels break "
    "that map of the page.",
    "Use headings in sequence without skipping levels."),
"html-has-lang": (
    "Without a language attribute a screen reader may read English content using "
    "another language's pronunciation rules, which is close to unintelligible.",
    'Add lang="en-ZA" (or the correct locale) to the html element.'),
"html-lang-valid": (
    "An invalid language code is treated as no language at all.",
    "Use a valid BCP 47 code such as en-ZA."),
"valid-lang": (
    "Invalid lang attributes on individual elements cause the same mispronunciation "
    "problem in the middle of a page.",
    "Correct or remove invalid lang attributes."),
"meta-viewport": (
    "Disabling zoom locks out anyone who needs to enlarge text — a direct WCAG failure "
    "and an unnecessary one.",
    "Remove user-scalable=no and maximum-scale from the viewport meta tag."),
"target-size": (
    "Tap targets that are too small or too close together cause mis-taps, which on a "
    "contact or checkout page means lost enquiries and lost sales.",
    "Make interactive elements at least 24x24px with adequate spacing; 44x44px is "
    "the comfortable target."),
"bypass": (
    "Without a skip link, keyboard and screen reader users must tab through the entire "
    "menu on every single page before reaching the content.",
    "Add a 'skip to main content' link and use a <main> landmark."),
"document-title": (
    "The page title is the first thing announced when a page loads. Without it the "
    "user has no idea where they have landed.",
    "Add a descriptive <title> to every page."),
"duplicate-id-aria": (
    "Duplicate IDs break the references ARIA relies on, so labels and descriptions "
    "attach to the wrong elements.",
    "Make every id unique on the page."),
"frame-title": (
    "An untitled iframe is announced only as 'frame', giving no clue what it contains.",
    "Add a descriptive title attribute to every iframe."),
"input-image-alt": (
    "An image used as a submit button with no alt text leaves the user unable to tell "
    "what submitting does.",
    "Add alt text to image inputs."),
"list": ("Malformed lists are announced with the wrong item counts, which is "
    "disorienting mid-navigation.",
    "Ensure <ul> and <ol> contain only <li> elements."),
"listitem": (
    "List items outside a list container lose their list semantics entirely.",
    "Place every <li> inside a <ul> or <ol>."),
"tabindex": (
    "Positive tabindex values override the natural tab order, sending keyboard users "
    "jumping unpredictably around the page.",
    "Remove positive tabindex values and rely on document order."),
"aria-allowed-attr": (
    "Invalid ARIA attributes are ignored or misinterpreted, often making an element "
    "less accessible than if no ARIA had been used.",
    "Remove ARIA attributes not permitted for that role."),
"aria-required-attr": (
    "An ARIA role missing its required attributes leaves assistive technology unable "
    "to convey the element's state.",
    "Add the required ARIA attributes for each role in use."),
"aria-valid-attr-value": (
    "ARIA attributes pointing at non-existent IDs silently break the label or "
    "description they were meant to provide.",
    "Correct ARIA attribute values and the IDs they reference."),
"aria-hidden-focus": (
    "An element hidden from screen readers but still keyboard-focusable creates a "
    "'ghost' stop — focus lands somewhere the user cannot perceive.",
    "Remove aria-hidden, or make the element non-focusable."),
"aria-input-field-name": (
    "A custom input with no accessible name cannot be identified by a screen reader.",
    "Add an aria-label or aria-labelledby to custom input widgets."),
"select-name": (
    "A dropdown with no accessible name gives no indication of what is being chosen.",
    "Associate a label with every select element."),
"empty-heading": (
    "Empty headings appear in the screen reader's heading list as blank entries, "
    "cluttering navigation.",
    "Remove empty headings or give them text."),
"landmark-one-main": (
    "A single <main> landmark lets users jump straight to content.",
    "Wrap the primary content in exactly one <main> element."),
"object-alt": (
    "Embedded objects without alternative text are inaccessible and unidentifiable.",
    "Provide alternative text for <object> elements."),
"video-caption": (
    "Uncaptioned video excludes deaf and hard-of-hearing visitors, and everyone "
    "watching without sound — which on social is most people.",
    "Add caption tracks to video content."),
"identical-links-same-purpose": (
    "Links with identical text pointing to different places are ambiguous when read "
    "out of context in a screen reader's link list.",
    "Make link text unique, or differentiate with aria-label."),
"table-duplicate-name": (
    "Duplicate table summaries and captions cause repeated, confusing announcements.",
    "Give each table a single distinct caption."),
"td-has-header": (
    "Data cells without headers cannot be understood out of context — the user hears "
    "the value with no idea what it refers to.",
    "Associate data cells with header cells in complex tables."),
"form-field-multiple-labels": (
    "Multiple labels on one field are read inconsistently across screen readers.",
    "Use exactly one label per form field."),

# ── best practices ───────────────────────────────────────────────────────────
"is-on-https": (
    "Content served over plain HTTP is marked 'Not secure' in the browser and can be "
    "modified in transit on public Wi-Fi.",
    "Serve every resource over HTTPS."),
"uses-http2": (
    "HTTP/2 loads many small files far more efficiently than HTTP/1.1, which matters "
    "most on high-latency mobile connections.",
    "Enable HTTP/2 or HTTP/3 at the server or CDN."),
"errors-in-console": (
    "JavaScript errors in the console usually mean something on the page is genuinely "
    "broken — often a form or a tracking script that has silently stopped working.",
    "Fix the underlying errors rather than suppressing them."),
"third-party-cookies": (
    "Third-party cookies are being phased out by browsers, so anything depending on "
    "them will break. They also carry disclosure obligations under POPIA.",
    "Audit third-party cookies, remove what is not needed and disclose the rest."),
"image-size-responsive": (
    "Images served at the wrong resolution look blurry when scaled up and waste mobile "
    "data when scaled down.",
    "Serve images at appropriate dimensions using srcset."),
"image-aspect-ratio": (
    "Images displayed at the wrong aspect ratio appear stretched or squashed, which "
    "reads as an amateur site.",
    "Set correct width and height, and use object-fit where cropping is intended."),
"valid-source-maps": (
    "Missing or invalid source maps make production errors far harder to diagnose. "
    "Developer-facing only, with no visitor impact.",
    "Generate valid source maps in the build, or omit the reference entirely."),
"inspector-issues": (
    "Chrome has flagged issues with the page — typically deprecated APIs, cookie "
    "problems or content security warnings.",
    "Open Chrome DevTools' Issues panel and work through the list."),
"deprecations": (
    "Deprecated browser APIs will eventually be removed, and the feature relying on "
    "them will break without warning.",
    "Replace deprecated API calls with current equivalents."),
"csp-xss": (
    "Without an effective Content Security Policy, an injected script can read "
    "anything a visitor types into a form.",
    "Add a strict Content-Security-Policy header."),
"doctype": (
    "A missing doctype puts the browser into quirks mode, where layout behaves "
    "unpredictably.",
    "Add <!DOCTYPE html> as the first line."),
"charset": (
    "Without a declared charset, accented characters and symbols can render as boxes.",
    'Declare <meta charset="utf-8"> early in <head>.'),
"geolocation-on-start": (
    "Requesting location on page load, before any context, is almost always denied and "
    "damages trust immediately.",
    "Request location only in response to a user action."),
"notification-on-start": (
    "Unprompted notification permission requests are a common reason visitors leave a "
    "page within seconds.",
    "Request notification permission only after a relevant user action."),
"paste-preventing-inputs": (
    "Blocking paste in fields breaks password managers and makes long fields painful "
    "to complete accurately.",
    "Allow paste in all input fields."),
"no-unload-listeners": (
    "unload listeners prevent the browser's back-forward cache from working, making "
    "back navigation slow.",
    "Replace unload with pagehide or visibilitychange."),
"bf-cache": (
    "When the back-forward cache is blocked, pressing Back reloads the whole page "
    "instead of restoring it instantly.",
    "Remove the blockers Lighthouse lists — usually unload listeners or no-store headers."),

# ── performance ──────────────────────────────────────────────────────────────
"render-blocking-resources": (
    "These resources must fully download before the visitor sees anything at all. "
    "They are the direct cause of a blank screen on first load.",
    "Defer non-critical CSS and JavaScript, and inline only what is needed to render "
    "the top of the page."),
"unused-javascript": (
    "JavaScript that is downloaded, parsed and executed but never used costs the "
    "visitor both data and time, particularly on mid-range phones.",
    "Code-split by route and drop unused libraries."),
"unused-css-rules": (
    "Unused CSS still has to be downloaded and parsed before the page can render.",
    "Remove unused styles or split the stylesheet by page."),
"modern-image-formats": (
    "WebP and AVIF typically cut image weight by a third or more at identical quality.",
    "Convert images to WebP or AVIF with fallbacks."),
"uses-optimized-images": (
    "Uncompressed images are usually the single largest thing on a page and the "
    "cheapest to fix.",
    "Compress images before upload, or use an image CDN."),
"uses-responsive-images": (
    "Serving desktop-sized images to phones wastes the visitor's data and delays the "
    "content they came for.",
    "Use srcset and sizes so each device gets an appropriate image."),
"offscreen-images": (
    "Images far below the fold compete for bandwidth with the content the visitor is "
    "actually looking at.",
    'Add loading="lazy" to below-the-fold images.'),
"uses-text-compression": (
    "Compression typically reduces text transfer by around 70% for one server setting.",
    "Enable Brotli or gzip."),
"server-response-time": (
    "A slow first byte delays everything downstream — no amount of front-end tuning "
    "compensates for a slow server.",
    "Improve backend response time with caching, a faster host or a CDN."),
"redirects": (
    "Each redirect adds a full network round trip before anything starts loading, "
    "which is painful on mobile latency.",
    "Link directly to final URLs and collapse redirect chains."),
"uses-long-cache-ttl": (
    "Short cache lifetimes force returning visitors to re-download assets that have "
    "not changed.",
    "Set long max-age with immutable on fingerprinted static assets."),
"uses-rel-preconnect": (
    "Without preconnect, the browser waits until it discovers a third-party resource "
    "before starting the DNS, TCP and TLS handshakes.",
    "Add preconnect hints for critical third-party origins."),
"bootup-time": (
    "Long JavaScript execution keeps the main thread busy, so taps and scrolls feel "
    "unresponsive.",
    "Reduce and split JavaScript, and defer non-critical work."),
"mainthread-work-breakdown": (
    "Heavy main-thread work is what makes a page feel sluggish even after it looks "
    "finished loading.",
    "Reduce script parsing, layout thrashing and style recalculation."),
"legacy-javascript": (
    "Transpiled polyfills for browsers nobody uses are shipped to every visitor.",
    "Target modern browsers in the build and drop unnecessary polyfills."),
"duplicated-javascript": (
    "The same library bundled more than once doubles its download and parse cost.",
    "Deduplicate dependencies in the bundler."),
"total-byte-weight": (
    "Total page weight determines both load time and how much of the visitor's data "
    "bundle you consume — a real consideration on prepaid mobile data.",
    "Reduce image, font and script payloads."),
"dom-size": (
    "Very large DOMs slow style calculation, layout and every interaction after load.",
    "Simplify markup and avoid rendering huge lists at once."),
"font-display": (
    "Without font-display, text is invisible while fonts download, so the page appears "
    "empty for longer than it is.",
    "Set font-display: swap on web fonts."),
"third-party-summary": (
    "Third-party scripts run on your page but load from someone else's server, so "
    "their slow day becomes your slow day.",
    "Remove non-essential third-party scripts and defer the rest."),
"largest-contentful-paint-element": (
    "This is the specific element deciding your LCP score. Optimising it is usually "
    "the fastest single performance win available.",
    "Preload it, serve it in a modern format, and never lazy-load it."),
"prioritize-lcp-image": (
    "The main image loading late is one of the most common causes of a poor LCP.",
    'Preload the LCP image and set fetchpriority="high".'),
"lcp-lazy-loaded": (
    "Lazy-loading the main image delays the very thing the score measures.",
    "Never lazy-load above-the-fold imagery."),
"efficient-animated-content": (
    "Animated GIFs are enormous compared with video encoding the same animation.",
    "Replace animated GIFs with MP4 or WebM video."),
"forced-reflow-insight": (
    "JavaScript forcing the browser to recalculate layout mid-frame causes visible "
    "stutter during scroll and interaction.",
    "Batch DOM reads and writes instead of interleaving them."),
"network-dependency-tree-insight": (
    "Long chains of dependent requests mean the browser cannot start one download "
    "until a previous one finishes, serialising the load.",
    "Flatten request chains with preload hints and inlined critical resources."),
"document-latency-insight": (
    "The initial HTML document is arriving slowly, which delays absolutely everything "
    "that follows.",
    "Improve server response time, redirects and compression on the document itself."),
"render-blocking-insight": (
    "Resources in the head are blocking first paint.",
    "Defer or inline them so rendering can begin sooner."),
"cache-insight": (
    "Assets are being re-downloaded on repeat visits because cache lifetimes are short.",
    "Set long cache lifetimes on static assets."),
"max-potential-fid": (
    "This is the worst-case delay between a visitor tapping and the page responding.",
    "Break up long JavaScript tasks."),
"unminified-css": (
    "Unminified stylesheets carry comments and whitespace to every visitor.",
    "Minify CSS in the build."),
"unminified-javascript": (
    "Unminified scripts are larger to download and slower to parse.",
    "Minify JavaScript in the build."),
"unsized-images": (
    "Images without dimensions cause the page to jump as they load, which is what "
    "makes people tap the wrong thing.",
    "Set width and height on every image."),
"long-tasks": (
    "Long JavaScript tasks block the main thread, so taps are ignored while they run.",
    "Split long tasks and defer non-essential work."),
"non-composited-animations": (
    "Animations not handled by the GPU cause visible stutter on mid-range phones.",
    "Animate transform and opacity rather than layout properties."),

# ── LH 13 insight audits (renamed from the older opportunity audits) ──────────
"lcp-breakdown-insight": (
    "Breaks down exactly where the time to your largest visible element goes. This is "
    "the most direct route to fixing a poor LCP, because it names the bottleneck.",
    "Address the largest phase Lighthouse reports — usually server response or "
    "render-blocking resources ahead of the image."),
"lcp-discovery-insight": (
    "The browser cannot start downloading the main image until it discovers it. Late "
    "discovery is a common hidden cause of slow loading.",
    'Preload the LCP image, set fetchpriority="high", and never lazy-load it.'),
"image-delivery-insight": (
    "Images are being delivered larger or in older formats than necessary, which is "
    "usually the single biggest saving available on a page.",
    "Compress, resize and serve images as WebP or AVIF."),
"font-display-insight": (
    "Text is invisible while web fonts download, so the page looks empty for longer "
    "than it actually is.",
    "Set font-display: swap and preload the primary font."),
"dom-size-insight": (
    "A very large DOM slows every style calculation, layout pass and interaction.",
    "Reduce element count and avoid rendering long lists all at once."),
"duplicated-javascript-insight": (
    "The same code is being shipped more than once, doubling its download and parse cost.",
    "Deduplicate shared dependencies in the bundler."),
"legacy-javascript-insight": (
    "Polyfills and transpiled code for browsers nobody uses are downloaded by every "
    "visitor.",
    "Target modern browsers in the build output."),
"modern-http-insight": (
    "Older HTTP versions load many small files far less efficiently, which hurts most "
    "on high-latency mobile connections.",
    "Enable HTTP/2 or HTTP/3 at the server or CDN."),
"third-parties-insight": (
    "Third-party scripts run on your page from someone else's server, so their "
    "performance becomes yours.",
    "Remove non-essential third-party scripts and defer the rest."),
"cls-culprits-insight": (
    "Identifies exactly which elements are shifting the layout while the page loads.",
    "Reserve space for the named elements with explicit dimensions."),
"inp-breakdown-insight": (
    "Shows why the page is slow to respond to taps and clicks — the metric Google now "
    "uses to judge responsiveness.",
    "Reduce main-thread work and split long tasks."),
"viewport-insight": (
    "Viewport configuration problems make the mobile layout render incorrectly.",
    "Set a correct responsive viewport meta tag."),

# ── agentic browsing ─────────────────────────────────────────────────────────
"agent-accessibility-tree": (
    "AI agents read a page through its accessibility tree — the same structure a screen "
    "reader uses. A malformed tree means an agent cannot reliably identify your buttons, "
    "forms or navigation, so it cannot complete a task like requesting a quote on a "
    "customer's behalf.",
    "Fix the underlying accessibility issues: proper labels, roles, landmarks and "
    "semantic HTML."),
"llms-txt": (
    "An llms.txt file gives AI systems a curated map of your site. Still an emerging "
    "convention with no confirmed effect on Google Search, but it is cheap and it is "
    "what the agentic tooling now checks for.",
    "Publish /llms.txt listing your key pages with one-line descriptions."),
"webmcp-registered-tools": (
    "WebMCP lets a site expose actions — book, quote, search — that an AI agent can "
    "call directly rather than guessing its way through the interface. It is very early, "
    "and almost no site has it yet, which is exactly why it is worth watching.",
    "Optional and forward-looking. Consider registering WebMCP tools for your primary "
    "conversion actions once the standard settles."),
"webmcp-form-coverage": (
    "Measures how much of your form functionality is reachable by an agent through "
    "declared tools rather than by simulating clicks.",
    "Optional: expose key forms as WebMCP tools."),
"cumulative-layout-shift": (
    "Layout shifting while the page loads is disorienting for people and actively "
    "breaks AI agents, which may click an element that has since moved elsewhere.",
    "Reserve space for images, ads and embeds with explicit dimensions."),
"webmcp-schema-validity": (
    "Invalid tool schemas cannot be used by an agent, so the integration silently "
    "achieves nothing.",
    "Validate WebMCP tool schemas if they are in use."),
}

# Perf audits worth surfacing as "opportunities" even though they carry no score weight.
OPPORTUNITY_IDS = [
    # Lighthouse 13 insight audits
    "lcp-breakdown-insight", "lcp-discovery-insight", "image-delivery-insight",
    "render-blocking-insight", "document-latency-insight", "cache-insight",
    "network-dependency-tree-insight", "forced-reflow-insight", "font-display-insight",
    "dom-size-insight", "duplicated-javascript-insight", "legacy-javascript-insight",
    "modern-http-insight", "third-parties-insight", "cls-culprits-insight",
    "inp-breakdown-insight", "viewport-insight", "unminified-css",
    "unminified-javascript", "unsized-images", "long-tasks",
    "non-composited-animations", "bf-cache",
    # Lighthouse 12 names, kept so older installs still work
    "render-blocking-resources", "unused-javascript", "unused-css-rules",
    "modern-image-formats", "uses-optimized-images", "uses-responsive-images",
    "offscreen-images", "uses-text-compression", "server-response-time",
    "redirects", "uses-long-cache-ttl", "uses-rel-preconnect", "bootup-time",
    "mainthread-work-breakdown", "legacy-javascript", "duplicated-javascript",
    "total-byte-weight", "dom-size", "font-display", "third-party-summary",
    "efficient-animated-content", "prioritize-lcp-image", "lcp-lazy-loaded",
    "largest-contentful-paint-element", "document-latency-insight",
    "network-dependency-tree-insight", "forced-reflow-insight", "cache-insight",
    "render-blocking-insight",
]


def _clean(text):
    """Lighthouse descriptions are markdown with doc links. Strip to plain prose."""
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", str(text or ""))
    t = re.sub(r"`([^`]+)`", r"\1", t)
    t = re.sub(r"\s*\bLearn (more|how|why)[^.]*\.", "", t)
    return " ".join(t.split()).strip()


def _node_dir():
    """Find node, including an fnm-managed install that is not on PATH."""
    return runtime.node_dir()


def available():
    binary = runtime.lighthouse_bin()
    return (binary, _node_dir()) if binary and _node_dir() else (None, None)


def run(url, form_factor="mobile", timeout=180):
    """Run Lighthouse. Returns parsed results, or None if it cannot run."""
    binary, node_dir = available()
    if not binary:
        return None

    env = dict(os.environ)
    env["PATH"] = node_dir + os.pathsep + env.get("PATH", "")
    # Point Lighthouse at whichever Chromium we found. Without this it hunts for
    # its own, which fails in the container and picks the wrong browser on a
    # machine with both Chrome and Edge installed.
    chrome = runtime.find_chrome()
    if chrome:
        env["CHROME_PATH"] = chrome

    out = os.path.join(tempfile.mkdtemp(prefix="bp-lh-"), "lh.json")
    # A .js entrypoint (global npm install) has no shebang we can rely on.
    launcher = [os.path.join(node_dir, "node"), binary] if binary.endswith(".js") else [binary]
    cmd = launcher + [url, "--quiet", "--output=json", f"--output-path={out}",
           "--chrome-flags=" + " ".join(runtime.CHROME_FLAGS),
           "--only-categories=" + ",".join(CATEGORIES),
           f"--form-factor={form_factor}",
           "--max-wait-for-load=45000"]
    if form_factor == "desktop":
        cmd.append("--preset=desktop")
    try:
        subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None
    if not os.path.exists(out):
        return None
    try:
        with open(out) as f:
            return parse(json.load(f))
    except (ValueError, KeyError):
        return None
    finally:
        shutil.rmtree(os.path.dirname(out), ignore_errors=True)


def _explain(audit_id, audit):
    if audit_id in NOTES:
        why, fix = NOTES[audit_id]
        return why, fix, False
    return _clean(audit.get("description")), "", True


def parse(raw):
    audits = raw.get("audits", {})
    cats = raw.get("categories", {})

    scores = {}
    for key in CATEGORIES:
        s = (cats.get(key) or {}).get("score")
        scores[key] = round(s * 100) if isinstance(s, (int, float)) else None

    metrics = []
    for aid, name, abbr, meaning, target, good, poor in METRICS:
        a = audits.get(aid) or {}
        num = a.get("numericValue")
        if num is None:
            continue
        rating = "good" if num <= good else ("fair" if num <= poor else "poor")
        metrics.append({
            "id": aid, "name": name, "abbr": abbr, "meaning": meaning,
            "target": target, "value": a.get("displayValue") or str(num),
            "rating": rating, "core": abbr in ("LCP", "CLS", "TBT"),
        })

    def savings(a):
        """Best available estimate of what fixing this audit would save, in ms."""
        ms = (a.get("metricSavings") or {})
        best = max([v for v in ms.values() if isinstance(v, (int, float))], default=0)
        det = a.get("details") or {}
        return max(best, det.get("overallSavingsMs") or 0,
                   (det.get("overallSavingsBytes") or 0) / 1000.0)

    def failing(category, ids=None):
        rows, seen = [], set()
        refs = {r["id"]: r.get("weight", 0) for r in (cats.get(category) or {}).get("auditRefs", [])}
        for aid in (ids if ids is not None else refs):
            if aid in seen:
                continue
            seen.add(aid)
            a = audits.get(aid)
            if not a:
                continue
            score = a.get("score")
            if score is None or score >= 1:
                continue
            if a.get("scoreDisplayMode") in ("notApplicable", "manual", "informative") \
                    and ids is None:
                continue
            why, fix, borrowed = _explain(aid, a)
            items = ((a.get("details") or {}).get("items") or [])
            rows.append({
                "id": aid, "title": a.get("title", aid), "score": score,
                "weight": refs.get(aid, 0), "display": a.get("displayValue") or "",
                "count": len(items), "why": why, "fix": fix, "borrowed": borrowed,
                "savings": savings(a),
                "severity": "high" if score == 0 else "medium",
            })
        # Scored categories rank by weight; opportunities rank by measured saving.
        if ids is None:
            rows.sort(key=lambda r: (-r["weight"], r["score"], r["title"]))
        else:
            rows.sort(key=lambda r: (-r["savings"], r["score"], r["title"]))
        return rows

    def passing(category):
        n = 0
        for r in (cats.get(category) or {}).get("auditRefs", []):
            a = audits.get(r["id"]) or {}
            if a.get("score") == 1 and a.get("scoreDisplayMode") not in ("notApplicable", "manual"):
                n += 1
        return n

    # Agentic browsing: show every audit with its real status, including the
    # not-applicable ones — "no WebMCP tools" is itself the finding.
    agentic = []
    for r in (cats.get("agentic-browsing") or {}).get("auditRefs", []):
        a = audits.get(r["id"])
        if not a:
            continue
        score, mode = a.get("score"), a.get("scoreDisplayMode")
        status = ("na" if mode == "notApplicable" or score is None
                  else ("pass" if score >= 1 else ("partial" if score > 0 else "fail")))
        why, fix, borrowed = _explain(r["id"], a)
        agentic.append({
            "id": r["id"], "title": a.get("title", r["id"]), "status": status,
            "weight": r.get("weight", 0), "display": a.get("displayValue") or "",
            "why": why, "fix": fix, "borrowed": borrowed,
        })
    agentic.sort(key=lambda x: ({"fail": 0, "partial": 1, "pass": 2, "na": 3}[x["status"]],
                                -x["weight"]))

    return {
        "version": raw.get("lighthouseVersion"),
        "form_factor": (raw.get("configSettings") or {}).get("formFactor", "mobile"),
        "fetched_url": raw.get("finalDisplayedUrl") or raw.get("requestedUrl"),
        "scores": scores,
        "metrics": metrics,
        "accessibility_issues": failing("accessibility"),
        "best_practice_issues": failing("best-practices"),
        "opportunities": failing("performance", OPPORTUNITY_IDS),
        "agentic": agentic,
        "passing": {c: passing(c) for c in CATEGORIES},
        "borrowed_text": False,  # set by the report once it knows what it rendered
    }
