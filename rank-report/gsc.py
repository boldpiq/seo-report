"""
Search Console data, from a manual export.

The API needs OAuth per client and a Cloud project. The UI needs neither: ask the
client to add you under Settings → Users and permissions, then export Performance
→ Search results. That export is the client's own Google data — the most accurate
position figures obtainable, and free.

Accepts what Search Console actually gives you:
  · the Queries CSV      Query, Clicks, Impressions, CTR, Position
  · the Pages CSV        Page,  Clicks, Impressions, CTR, Position
  · the multi-file ZIP   both of the above, unpacked automatically
  · the .xlsx workbook   the "Download Excel" option, read with the stdlib

The .xlsx matters: it is one of the three buttons Search Console offers and the
one people reach for. It is also a ZIP, so a naive zip branch opens it happily,
finds no .csv members and reports no data — a silent empty section rather than a
readable error. It is handled explicitly for that reason.

Column names change with the account language and Google's own wording, so
detection is by position and content rather than by exact header text.
"""

import csv
import io
import os
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile


def _num(v):
    if v is None:
        return None
    s = str(v).strip().replace("%", "").replace(",", "").replace(" ", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _rows(text):
    return list(csv.reader(io.StringIO(text)))


def _parse_table(rows):
    """One exported table → list of dicts. Header is whatever the first row says."""
    if not rows or len(rows) < 2:
        return []
    header = [h.strip().lower() for h in rows[0]]
    out = []
    for r in rows[1:]:
        if not any(x.strip() for x in r):
            continue
        rec = dict(zip(header, r))
        key = r[0].strip()
        if not key:
            continue
        vals = [_num(x) for x in r[1:]]
        # Clicks, Impressions, CTR, Position — in that order in every export
        # Google produces, in every language.
        clicks, impr, ctr, pos = (vals + [None] * 4)[:4]
        out.append({"key": key, "clicks": clicks, "impressions": impr,
                    "ctr": ctr, "position": pos, "_raw": rec})
    return out


_XL = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
       "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}


def _xlsx_tables(path):
    """Every sheet of a Search Console .xlsx → {sheet name: [row, ...]}.

    Parsed with the standard library rather than a spreadsheet package: this tool
    has no third-party dependencies and the file is simple — shared strings plus
    one XML sheet each. Sheets are keyed by their real names (Queries, Pages,
    Countries, Devices, Search appearance, Chart, Filters).
    """
    out = {}
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        shared = []
        if "xl/sharedStrings.xml" in names:
            shared = ["".join(t.text or "" for t in si.iter("{%s}t" % _XL["m"]))
                      for si in ET.fromstring(z.read("xl/sharedStrings.xml"))]
        rels = {r.get("Id"): r.get("Target")
                for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
        for sheet in ET.fromstring(z.read("xl/workbook.xml")).iter("{%s}sheet" % _XL["m"]):
            target = (rels.get(sheet.get("{%s}id" % _XL["r"])) or "").lstrip("/")
            if not target:
                continue
            if not target.startswith("xl/"):
                target = "xl/" + target
            if target not in names:
                continue
            rows = []
            for row in ET.fromstring(z.read(target)).iter("{%s}row" % _XL["m"]):
                cells = []
                for c in row.iter("{%s}c" % _XL["m"]):
                    v = c.find("{%s}v" % _XL["m"])
                    if v is None:
                        cells.append("")
                    elif c.get("t") == "s":
                        idx = int(v.text)
                        cells.append(shared[idx] if idx < len(shared) else "")
                    else:
                        cells.append(v.text or "")
                rows.append(cells)
            out[sheet.get("name") or target] = rows
    return out


SUMMARY_TABLES = ("chart", "dates", "countries", "devices", "search appearance",
                  "filters")


def _key(name):
    """Sheet name or file name → the same slot, whichever export format it came from."""
    return os.path.splitext(str(name).strip())[0].strip().lower()


def _true_totals(tables):
    """Clicks and impressions for the whole period, from a table that holds them all.

    NOT from the query table. Search Console withholds queries that too few
    people searched, to avoid identifying anyone — so the Queries sheet is a
    subset, and summing it understates the real total. On a small local site the
    gap is large: 66 impressions across named queries against 264 actually
    served. Reporting the smaller number to a client is simply wrong, and it is
    wrong in the direction that makes their site look worse than it is.

    The per-day series and the country breakdown both carry the true figures.
    """
    for name in ("chart", "dates", "countries", "devices"):
        rows = tables.get(name)
        if not rows:
            continue
        clicks = sum((r["clicks"] or 0) for r in rows)
        impressions = sum((r["impressions"] or 0) for r in rows)
        if impressions:
            return {"clicks": clicks, "impressions": impressions, "from": name}
    return None


def load(path):
    """Read a Search Console export. Returns {queries: [...], pages: [...]}."""
    if not path or not os.path.exists(path):
        return {"connected": False}

    tables, raw_sheets = {}, {}
    if path.lower().endswith(".xlsx"):
        # Checked before the ZIP branch: an .xlsx IS a zip, and the zip branch
        # would find no .csv members and silently report no data.
        raw_sheets = _xlsx_tables(path)
        for name, rows in raw_sheets.items():
            tables[_key(name)] = _parse_table(rows)
    elif zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                if not name.lower().endswith(".csv"):
                    continue
                text = z.read(name).decode("utf-8-sig", "replace")
                tables[_key(os.path.basename(name))] = _parse_table(_rows(text))
    else:
        with open(path, encoding="utf-8-sig", errors="replace") as fh:
            tables[_key(os.path.basename(path))] = _parse_table(_rows(fh.read()))

    queries, pages = [], []
    for name, rows in tables.items():
        if not rows:
            continue
        # The Chart sheet is a day-by-day series and the Filters sheet records
        # what was applied. Neither is a query or a page, and folding them in
        # would put dates into the query table.
        if name in SUMMARY_TABLES:
            continue
        looks_like_url = rows[0]["key"].startswith("http")
        if looks_like_url or "page" in name:
            pages += rows
        else:
            queries += rows

    # The window the export actually covers, which is not the window that was
    # asked for. A property verified five weeks ago returns five weeks of data
    # under a "Last 3 months" filter, and that difference is the whole answer to
    # "why is nothing showing yet".
    span = None
    chart = raw_sheets.get("Chart") or []
    days = [r[0] for r in chart[1:] if r and len(r[0]) == 10 and r[0][4] == "-"]
    if days:
        span = {"first": min(days), "last": max(days), "days": len(days)}
    requested = None
    for r in (raw_sheets.get("Filters") or [])[1:]:
        if len(r) >= 2 and r[0].strip().lower() == "date":
            requested = r[1].strip()

    return {
        "connected": bool(queries or pages),
        "source": os.path.basename(path),
        "span": span,
        "requested_range": requested,
        "queries": sorted(queries, key=lambda r: -(r["impressions"] or 0)),
        "pages": sorted(pages, key=lambda r: -(r["impressions"] or 0)),
        "totals": _totals(tables, queries, pages),
    }


def _totals(tables, queries, pages=()):
    named_clicks = sum((r["clicks"] or 0) for r in queries)
    named_impr = sum((r["impressions"] or 0) for r in queries)
    true = _true_totals(tables)
    if not true and not queries and pages:
        # A Pages-only export: no daily series and no query table. Page rows are
        # not anonymised the way query rows are, so their sum is the period
        # total — and printing 0 impressions above a table of pages that earned
        # hundreds would be exactly the false zero this report exists to avoid.
        true = {"clicks": sum((r["clicks"] or 0) for r in pages),
                "impressions": sum((r["impressions"] or 0) for r in pages),
                "from": "pages"}
    clicks = true["clicks"] if true else named_clicks
    impressions = true["impressions"] if true else named_impr
    return {
        "clicks": clicks or None,
        "impressions": impressions or None,
        "source": (true or {}).get("from", "queries"),
        "named_clicks": named_clicks,
        "named_impressions": named_impr,
        # What share of impressions Google will not attribute to a query. High
        # on a small site, and worth saying out loud — it is long-tail demand,
        # not missing data.
        # Only meaningful against a query table — a Pages-only export has none,
        # and "100% unattributed" would then be a false statement.
        "anonymised_impressions": max(0, impressions - named_impr) if true and queries else 0,
        "queries_with_impressions": sum(1 for r in queries if (r["impressions"] or 0) > 0),
    }


def for_targets(data, targets):
    """The client's real Google position for each target query, where it exists."""
    if not data.get("connected"):
        return []
    idx = {r["key"].strip().lower(): r for r in data.get("queries", [])}
    out = []
    for t in targets:
        r = idx.get(t.strip().lower())
        out.append({
            "query": t,
            "found": bool(r),
            "position": (round(r["position"], 1) if r and r["position"] else None),
            "impressions": (int(r["impressions"]) if r and r["impressions"] else 0),
            "clicks": (int(r["clicks"]) if r and r["clicks"] else 0),
        })
    return out


def by_page(data):
    """Search Console's Pages table keyed by path, for joining onto the crawl.

    A page missing from this map had no impressions in the exported period (or
    is not indexed) — Search Console lists only pages it showed at least once.
    """
    if not data.get("connected"):
        return {}
    out = {}
    for row in data.get("pages", []):
        out[_path(row["key"])] = {
            "impressions": int(row["impressions"] or 0),
            "clicks": int(row["clicks"] or 0),
            "position": round(row["position"], 1) if row["position"] else None}
    return out


def orphans(data, crawled_urls, measured_urls=None):
    """Pages Google ranks that the sitemap does not list.

    crawled_urls   the sitemap's pages — what "absent from your sitemap" is
                   measured against.
    measured_urls  every page the report actually measured, which since the
                   whole-site crawl includes pages found by following links. A
                   page in here but not in the sitemap is flagged `measured`, so
                   the report can say it was read, just not listed.
    """
    rows = _orphans(data, crawled_urls)
    seen = {_path(u) for u in (measured_urls or [])}
    for r in rows:
        r["measured"] = r["path"] in seen
    return rows


def _orphans(data, crawled_urls):
    """Pages Google is ranking that our crawl never saw.

    The crawl reads the sitemap. Anything not listed there is invisible to every
    other check in this report — and a page can be live, indexed and earning
    impressions while being absent from the sitemap entirely. Google found it;
    we did not. That gap is worth naming, because it usually means a page nobody
    is maintaining is representing the business in search.

    Host variants are ignored on purpose: http/https and www/non-www are the same
    page to a reader, and Search Console reports whichever URL it indexed.
    """
    if not data.get("connected"):
        return []
    seen = {_path(u) for u in (crawled_urls or [])}
    out = []
    for row in data.get("pages", []):
        key = _path(row["key"])
        if key in seen:
            continue
        out.append({"url": row["key"], "path": key,
                    "impressions": int(row["impressions"] or 0),
                    "clicks": int(row["clicks"] or 0),
                    "position": (round(row["position"], 1) if row["position"] else None)})
    return sorted(out, key=lambda r: -r["impressions"])


def _path(url):
    p = urllib.parse.urlparse(url if "://" in url else "https://" + url).path or "/"
    return p.rstrip("/") or "/"
