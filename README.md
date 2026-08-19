# seo-report

Client-ready **Website Visibility Report** — enter a URL, get a branded Boldpiq PDF.

Where `site-audit` measures launch readiness for sites *we* build, this is the
**sales and diagnostic asset for sites we did not build**: a prospect's existing
website, audited and explained in language a business owner can act on.

Scores the site on seven measured dimensions — three from a structural scan of the
HTML, four measured live in Chrome by Google Lighthouse:

| From the structural scan | Measured in Chrome (Lighthouse) |
|---|---|
| **SEO** — can Google find, understand and rank it | **Performance** — real load speed on a throttled mobile connection |
| **AEO** — can AI assistants read it and quote it | **Accessibility** — screen reader, keyboard and low-vision usability |
| **GEO** — will AI *recommend* it by name | **Best practices** — security and standards compliance |
| | **Agentic browsing** — can an AI agent actually *operate* the site |

Everything in the report is a real measurement of the live page. Nothing is
estimated, inferred or padded.

## Use

```bash
cd ~/boldpiq-tools/seo-report
./seo-report.sh clientdomain.co.za                      # one client
./seo-report.sh clientdomain.co.za --client "Client Name" --open
./seo-report.sh site-a.co.za site-b.co.za               # several, rate-limit safe
./seo-report.sh clientdomain.co.za --keyphrase "roof repairs cape town"
```

PDF, the raw scan JSON and the fix pack land in `reports/`:

```
reports/clientdomain-co-za-visibility-report-2026-08-03.pdf
reports/clientdomain-co-za-visibility-report-2026-08-03.json
reports/clientdomain-co-za-visibility-report-2026-08-03-fixes.json
```

Takes about 40 seconds per site, most of it Lighthouse.

## Fix list for AI

The PDF is written for the client. The **fix pack** (`-fixes.json`, and the
`/fixes/<report>.pdf` page in the web app) is the same recommendations rewritten
for a machine: one markdown block, section headings included, that pastes
straight into ChatGPT, Claude or an editor.

- **One button copies everything.** Sections are headings inside the copied text,
  not separate copies — per-section buttons are there as a convenience only.
- Platform-blocked items are copied too, tagged `⚠ Not fixable on this platform`,
  so an assistant does not invent a workaround for something Wix will never allow.
- Reports generated before this existed still get a fix list — rebuilt from the
  saved scan JSON, minus the Lighthouse sections, which that file never held.

Wording lives in `fixpack.py`, generated once server-side so the copy button, the
JSON and anything built on top of them can never drift apart.

Useful flags: `--out DIR`, `--keep-html` (debug the layout), `--open`,
`--from-json FILE` (re-render an old scan without re-scanning), `--desktop`
(Lighthouse desktop instead of mobile), `--no-lighthouse` (skip the Chrome run —
about 30s faster, drops four sections).

## What's in the report

~22 pages, in this order:

| Page | Content |
|---|---|
| Cover | Overall visibility score, the three pillar scores, the four Lighthouse scores |
| What we measured | Each pillar explained, scored and given a plain verdict |
| Where to start | Issues graded critical → low, quick wins, our recommended order of work |
| AI assistant readiness | Per-platform: ChatGPT, Perplexity, Claude, Google AI Overviews |
| Agentic browsing | Whether an AI agent can operate the site, including WebMCP |
| Speed & Core Web Vitals | LCP, FCP, TBT, CLS, SI, TTI each explained, plus ranked speed opportunities |
| Accessibility | WCAG audit with an honest note on what automated testing cannot catch, then browser best practices |
| Platform & architecture | What their stack can and **cannot** do (see below) |
| SEO / AEO / GEO Issues | **Every** issue, each with the finding, why it matters commercially, and how it gets fixed |
| What's already working | Passing checks — worth protecting in a redesign |
| Plain English glossary | Every term in the report, one line each |
| Next steps | Boldpiq CTA |

Issue counts match the scanner's own UI exactly (an issue is a failing check that
is applicable and not merely informational). Nothing is written for a technical
reader: every term is explained where it appears and again in the glossary.

## Platform & architecture constraints

The part that stops us giving advice a client cannot act on. The scan detects the
platform, and `platforms.py` knows what each one will not allow:

- **Blocked** — impossible on that stack. Telling a Wix client to "add a
  Content-Security-Policy header" is advice they can never follow. The report says
  so, and frames it as a replatforming decision rather than a task.
- **Constrained** — fixable, but shaped by the platform. A Next.js site will always
  look code-heavy to a text-to-HTML ratio check because React ships hydration data;
  the answer is more content, not less code.

Blocked items are excluded from the quick-wins list, badged on the issue card, and
summarised on their own page. Covered: Wix, Squarespace, Shopify, WordPress,
Next.js, Nuxt, Gatsby, Drupal, Magento, OpenCart, plus a generic profile when the
platform is not detected.

## How it works

```
URL ─┬→ seoscore.tools/api/scan (free, no key) ──→ structural JSON
     └→ Google Lighthouse in real Chrome ───────→ measured JSON
        → checks.py     Boldpiq's explanation for all 219 structural checks
        → lighthouse.py Boldpiq's explanation for the Lighthouse audits
        → platforms.py  what this stack can and cannot do
        → branded HTML (Geist + brand colours, both taken from boldpiq.com)
        → headless Chrome → PDF
```

**Requirements:** Python 3 (stdlib only), Google Chrome, and Node with Lighthouse
13+ — reused from `~/boldpiq-tools/site-audit/node_modules`, so there is only one
copy on the machine. Without Node the report still generates, minus the four
Lighthouse sections.

Agentic browsing needs Lighthouse 13 or newer (`npm install lighthouse@latest` in
site-audit). On older versions that section is simply absent.

The scan engine is the free public tool at seoscore.tools, credited in the report
footer. Their terms ask that automated scripts not run excessive scans, so the
tool scans one URL at a time, waits 6s between sites and backs off on rate limits —
keep it to real client work rather than bulk lists.

Priorities, explanations, platform analysis and the report itself are Boldpiq's own.

## Editing the content

- `checks.py` — the explanation library, keyed by check ID:
  `id: (Title, priority, effort, why_it_matters, how_to_fix)`. Unknown IDs fall
  back to the scanner's own wording, so a new check never leaves a blank.
- `lighthouse.py` — the Lighthouse runner plus `NOTES`, our explanations for its
  audits. Unknown audits fall back to Lighthouse's own description (Apache 2.0,
  credited in the report footer). `METRICS` holds the Core Web Vitals thresholds.
- `platforms.py` — per-platform blocked/limited findings and the narrative text.
- `seo_report.py` — layout, brand colours (`INK`, `ACCENT`), `GLOSSARY`, verdict
  wording and page structure.

Re-render without burning a scan while editing:

```bash
./seo-report.sh clientdomain.co.za --from-json reports/clientdomain-....json --keep-html
```

## Companion tools

`cf-onboarding` hardens the client's DNS and edge · `site-audit` proves a site we
built is launch-ready · **`seo-report` opens the conversation.**
