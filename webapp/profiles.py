"""Remembered inputs, one profile per client.

Most of what the ranking report needs does not change between months. A map pin
never moves. Target queries, service areas and the target suburb are decided
once at the start of an engagement. Competitor review counts drift slowly.

Re-typing all of it every month is the kind of admin that quietly stops a
monthly report being monthly, so it is stored per client and offered back.

Not stored: the Search Console export. It is a different file every month, and
prefilling last month's would put stale figures under a heading that says they
are current — the one failure this whole tool exists to prevent.
"""

import json
import os
import re
import threading
import time
import urllib.parse

_lock = threading.Lock()

# Everything the form collects that is stable between runs.
FIELDS = ("client", "suburb", "queries", "competitors", "areas", "pin",
          "gbp_url", "money_page", "reviews", "competitor_reviews",
          "location", "country")


def _store(root):
    return os.path.join(root, "client-profiles.json")


def slug(url):
    host = urllib.parse.urlparse(
        url if "://" in url else "https://" + url).netloc.lower()
    host = re.sub(r"^www\.", "", host)
    return re.sub(r"[^a-z0-9]+", "-", host).strip("-")


def _read(root):
    try:
        with open(_store(root), encoding="utf-8") as fh:
            d = json.load(fh)
            return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(root, data):
    os.makedirs(root, exist_ok=True)
    tmp = _store(root) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, sort_keys=True)
    os.replace(tmp, _store(root))       # atomic: never a half-written store


def save(root, url, opts):
    """Record what was used for this run, keyed by the client's domain."""
    key = slug(url)
    if not key:
        return
    kept = {f: str(opts.get(f) or "").strip() for f in FIELDS}
    kept = {k: v for k, v in kept.items() if v}
    with _lock:
        data = _read(root)
        prev = data.get(key) or {}
        # Merge rather than replace: a run that omitted the pin should not
        # erase the pin someone entered last month.
        merged = {**(prev.get("fields") or {}), **kept}
        data[key] = {"url": url, "fields": merged,
                     "saved": int(time.time()),
                     "runs": int(prev.get("runs") or 0) + 1}
        _write(root, data)


def load(root, url):
    with _lock:
        return (_read(root).get(slug(url)) or {})


def listing(root):
    """Every saved client, most recently used first."""
    with _lock:
        data = _read(root)
    out = []
    for key, rec in data.items():
        f = rec.get("fields") or {}
        out.append({"slug": key, "url": rec.get("url"),
                    "client": f.get("client") or key,
                    "saved": rec.get("saved"), "runs": rec.get("runs", 0),
                    "fields": f})
    return sorted(out, key=lambda r: -(r["saved"] or 0))


def forget(root, key):
    with _lock:
        data = _read(root)
        existed = data.pop(key, None) is not None
        if existed:
            _write(root, data)
    return existed
