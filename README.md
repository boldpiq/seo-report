# seo-report

Client-ready **Website Visibility Report** — enter a URL, get a branded Boldpiq PDF.

Where `site-audit` measures launch readiness for sites *we* build, this is the
**sales and diagnostic asset for sites we did not build**: a prospect's existing
website, audited and explained in language a business owner can act on.

Scores the site on three pillars:

- **SEO** — how well Google and Bing can find, understand and rank it
- **AEO** — how easily AI assistants can read it and quote it in an answer
- **GEO** — how likely it is to be *recommended* by ChatGPT, Perplexity, Claude
  and Google's AI Overviews

## Use

```bash
cd ~/boldpiq-tools/seo-report
./seo-report.sh clientdomain.co.za                      # one client
./seo-report.sh clientdomain.co.za --client "Client Name" --open
./seo-report.sh site-a.co.za site-b.co.za               # several, rate-limit safe
./seo-report.sh clientdomain.co.za --keyphrase "roof repairs cape town"
```

PDF and the raw scan JSON land in `reports/`:

```
reports/clientdomain-co-za-visibility-report-2026-08-03.pdf
reports/clientdomain-co-za-visibility-report-2026-08-03.json
```

Useful flags: `--out DIR`, `--keep-html` (debug the layout), `--open`,
`--from-json FILE` (re-render an old scan without re-scanning).

## What's in the report

| Page | Content |
|---|---|
| Cover | Overall score, the three pillar scores, issue count |
| What we measured | Each pillar explained, scored and given a plain verdict |
| Where to start | Issues graded critical → low, quick wins, our recommended order |
| AI assistant readiness | Per-platform scores: ChatGPT, Perplexity, Claude, Google AI Overviews |
| Platform & architecture | What their stack can and **cannot** do (see below) |
| SEO / AEO / GEO Issues | **Every** issue, each with the finding, why it matters commercially, and how it gets fixed |
| What's already working | Passing checks — worth protecting in a redesign |
| Next steps | Boldpiq CTA |

Issue counts match the scanner's own UI exactly (an issue is a failing check that
is applicable and not merely informational).

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
URL → seoscore.tools/api/scan (free, no key) → JSON
    → checks.py    Boldpiq's explanation for all 219 checks (why + fix + priority)
    → platforms.py what this stack can and cannot do
    → branded HTML (Geist, brand colours pulled from boldpiq.com)
    → headless Chrome → PDF
```

**Requirements:** Python 3 (stdlib only) and Google Chrome. Nothing to install.

The scan engine is the free public tool at seoscore.tools, credited in the report
footer. Their terms ask that automated scripts not run excessive scans, so the
tool scans one URL at a time, waits 6s between sites and backs off on rate limits —
keep it to real client work rather than bulk lists.

Priorities, explanations, platform analysis and the report itself are Boldpiq's own.

## Editing the content

- `checks.py` — the explanation library, keyed by check ID:
  `id: (Title, priority, effort, why_it_matters, how_to_fix)`. Unknown IDs fall
  back to the scanner's own wording, so a new check never leaves a blank.
- `platforms.py` — per-platform blocked/limited findings and the narrative text.
- `seo_report.py` — layout, brand colours (`INK`, `ACCENT`) and page structure.

Re-render without burning a scan while editing:

```bash
./seo-report.sh clientdomain.co.za --from-json reports/clientdomain-....json --keep-html
```

## Companion tools

`cf-onboarding` hardens the client's DNS and edge · `site-audit` proves a site we
built is launch-ready · **`seo-report` opens the conversation.**
