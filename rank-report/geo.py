"""
Proximity.

Distance from the business's verified map pin to the centre of each target
suburb. This is the one map-pack factor that cannot be changed, so it sets the
ceiling on what is achievable — measure it before promising anything.

Geocoding uses OpenStreetMap Nominatim, which requires a real User-Agent and
no more than one request per second. Both are honoured here.
"""

import json
import math
import re
import time
import urllib.parse
import urllib.request

UA = "boldpiq-rank-report/1.0 (reports@boldpiq.com)"
_LAST = [0.0]


def _get(url):
    wait = 1.1 - (time.time() - _LAST[0])
    if wait > 0:
        time.sleep(wait)
    _LAST[0] = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


SHORT_HOSTS = ("maps.app.goo.gl", "goo.gl", "g.co")


def expand(url):
    """Follow a Maps share link to the full URL that carries the coordinates.

    The link Google's share button produces is a short one and holds no
    coordinates at all — they only appear after the redirect. Reading the short
    form directly finds nothing, which looks identical to a business having no
    pin, so the redirect is followed rather than guessed at.
    """
    if not url or not any(h in url for h in SHORT_HOSTS):
        return url
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.geturl()


def _pin_in(url):
    m = re.search(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)", url or "")
    if m:
        return float(m.group(1)), float(m.group(2))
    m = re.search(r"@(-?\d+\.\d+),(-?\d+\.\d+)", url or "")
    return (float(m.group(1)), float(m.group(2))) if m else None


def pin_from_maps_url(url):
    """Extract (lat, lon) from a Google Maps place URL. Returns None if absent.

    Prefers the !3d/!4d pair, which is the business's own pin. The @lat,lon in
    the path is the map viewport centre — close, but not the same point, and
    using it would put a small error into every distance in the report.
    """
    hit = _pin_in(url)
    if hit:
        return hit
    return _pin_in(expand(url))


def haversine(a, b):
    r = 6371.0088
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp = p2 - p1
    dl = math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def geocode(place, tries=2):
    q = urllib.parse.quote(place)
    for _ in range(tries):
        try:
            res = _get(f"https://nominatim.openstreetmap.org/search?format=jsonv2&limit=1&q={q}")
            if res:
                return float(res[0]["lat"]), float(res[0]["lon"])
        except Exception:
            continue
    return None


def reverse(pin):
    try:
        d = _get("https://nominatim.openstreetmap.org/reverse?format=jsonv2"
                 f"&zoom=18&lat={pin[0]}&lon={pin[1]}")
        return d.get("address", {})
    except Exception:
        return {}


def band(km):
    if km is None:
        return "na", "Not measured"
    if km <= 2:
        return "strong", "Strong — the pin does the work here"
    if km <= 5:
        return "good", "Good — genuinely close"
    if km <= 8:
        return "moderate", "Moderate — reachable, secondary target"
    return "weak", "Weak on proximity — win this on the website, not the profile"


def measure(pin, areas, country="South Africa"):
    """Distance from the pin to each named area, nearest first."""
    if not pin:
        return {"pin": None, "areas": []}
    out = []
    for a in areas:
        c = geocode(f"{a}, {country}")
        if not c:
            out.append({"area": a, "km": None, "band": "na",
                        "note": "Could not geocode"})
            continue
        km = haversine(pin, c)
        b, note = band(km)
        out.append({"area": a, "km": round(km, 2), "band": b, "note": note})
    out.sort(key=lambda x: (x["km"] is None, x["km"] or 0))
    return {"pin": list(pin), "address": reverse(pin), "areas": out}


# ── area strategy ────────────────────────────────────────────────────────────

def _page_for(area, pages):
    """The page targeting this area, if one exists. URL and title beat body text."""
    a = area.lower()
    slug = a.replace(" ", "-")
    best, best_score = None, 0
    for p in pages:
        url = p["url"].lower()
        title = (p.get("title") or "").lower()
        score = 0
        if slug in url:
            score += 100
        if a in title:
            score += 50
        body = len(re.findall(re.escape(a), (p.get("_haystack") or ""), re.I)) \
            if p.get("_haystack") else 0
        score += min(body, 40)
        if score > best_score:
            best, best_score = p, score
    return best if best_score >= 50 else None


def area_strategy(prox, pages):
    """Cross the map pin against the website, per area.

    The correlation clients most often miss: the verified pin decides which areas
    are realistically winnable in the map pack, and it cannot be changed. The
    website decides which areas are winnable organically, and it can. Listing an
    area as 'served' creates neither.
    """
    rows = []
    for a in prox.get("areas", []):
        km, band_ = a.get("km"), a.get("band")
        page = _page_for(a["area"], pages)
        if km is None:
            pack = "Not measured"
        elif km <= 2:
            pack = "Strong"
        elif km <= 5:
            pack = "Good"
        elif km <= 8:
            pack = "Moderate"
        else:
            pack = "Weak"

        if pack in ("Strong", "Good") and page:
            verdict = "Primary target — winnable in both the map pack and organic"
        elif pack in ("Strong", "Good") and not page:
            verdict = "Add a page — proximity is there, the website is not"
        elif page:
            verdict = "Organic only — too far for the map pack, but the page can rank"
        elif pack == "Moderate":
            verdict = "Needs a page to be worth anything here"
        else:
            verdict = "Not realistic on proximity — do not promise this area"

        rows.append({
            "area": a["area"], "km": km, "band": band_,
            "map_pack": pack,
            "page": (page["url"] if page else None),
            "page_words": (page["words"] if page else None),
            "page_mentions": (page.get("term_count") if page else None),
            "verdict": verdict,
        })
    return rows
