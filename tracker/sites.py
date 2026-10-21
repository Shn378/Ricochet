"""Sensitive sites: places where a physical signal matters on its own.

Heat in a Madagascar field is farm burning. The same heat at a military base,
an ammunition dump or a nuclear plant is worth showing straight away, conflict
zone or not. This list comes from Wikidata (with coordinates) and refreshes
itself monthly.

The site rule only lifts physical signals (fires, Wikipedia bursts on the site's
own article) and only when they're above that spot's normal level, so refinery
gas flares that burn every day stay quiet.
"""
import json
import math
import re
import threading
import time
import urllib.parse

from . import db
from .net import get

KINDS = {
    "Q245016": ("military base", 15), "Q18691599": ("military installation", 15), "Q695850": ("air base", 10),
    "Q1324633": ("naval base", 10), "Q131263": ("barracks", 5), "Q2153495": ("ammunition dump", 10),
    "Q134447": ("nuclear power plant", 8), "Q12353044": ("oil refinery", 5), "Q15709854": ("LNG terminal", 5),
    "Q644371": ("international airport", 6), "Q7138926": ("parliament building", 2),
}   # QID -> (label, match radius in km)
REFRESH_DAYS = 30

SCHEMA = """
CREATE TABLE IF NOT EXISTS sites (
    qid TEXT PRIMARY KEY, name TEXT, kind TEXT, lat REAL, lon REAL, country TEXT, radius_km REAL
);
"""

_lock = threading.Lock()
_grid = None   # (lat_int, lon_int) -> [site]


def km(lat1, lon1, lat2, lon2):
    p = math.pi / 180
    a = 0.5 - math.cos((lat2 - lat1) * p) / 2 + math.cos(lat1 * p) * math.cos(lat2 * p) * (1 - math.cos((lon2 - lon1) * p)) / 2
    return 12742 * math.asin(math.sqrt(max(0.0, a)))


def query():
    values = " ".join(f'(wd:{q} "{k}")' for q, (k, _) in KINDS.items())
    sparql = f"""SELECT ?item ?itemLabel ?kind ?coord ?cc WHERE {{
      VALUES (?type ?kind) {{ {values} }}
      ?item wdt:P31 ?type; wdt:P625 ?coord.
      OPTIONAL {{ ?item wdt:P17 ?c. ?c wdt:P297 ?cc. }}
      SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
    }}"""
    raw = get("https://query.wikidata.org/sparql?format=json&query=" + urllib.parse.quote(sparql),
              timeout=120, headers={"Accept": "application/sparql-results+json"})
    radius = {k: r for k, r in KINDS.values()}
    out = {}
    for b in json.loads(raw)["results"]["bindings"]:
        pt = b["coord"]["value"]                       # "Point(lon lat)"
        try:
            lon, lat = map(float, pt[pt.index("(") + 1:pt.index(")")].split())
        except ValueError:
            continue
        qid = b["item"]["value"].rsplit("/", 1)[-1]
        kind = b["kind"]["value"]
        if qid in out and radius[out[qid]["kind"]] >= radius[kind]:
            continue
        out[qid] = {"qid": qid, "name": b["itemLabel"]["value"], "kind": kind, "lat": lat, "lon": lon,
                    "country": b.get("cc", {}).get("value"), "radius_km": radius[kind]}
    return list(out.values())


def refresh(con, log=print, force=False):
    con.executescript(SCHEMA)
    last = float(db.get_meta(con, "sites_refreshed", 0))
    have = con.execute("SELECT COUNT(*) FROM sites").fetchone()[0]
    if have and not force and time.time() - last < REFRESH_DAYS * 86400:
        return have
    try:
        rows = query()
    except Exception as e:
        log(f"[sites] Wikidata refresh failed, keeping {have} cached sites: {e}")
        return have
    with db._lock:
        con.execute("DELETE FROM sites")
        con.executemany("INSERT INTO sites VALUES (:qid,:name,:kind,:lat,:lon,:country,:radius_km)", rows)
        con.commit()
    db.set_meta(con, "sites_refreshed", time.time())
    global _grid
    _grid = None
    log(f"[sites] {len(rows)} sensitive sites loaded from Wikidata")
    return len(rows)


def _index(con):
    global _grid
    with _lock:
        if _grid is None:
            con.executescript(SCHEMA)
            g = {}
            for r in con.execute("SELECT * FROM sites"):
                g.setdefault((int(r["lat"] // 1), int(r["lon"] // 1)), []).append(dict(r))
            _grid = g
        return _grid


def near(con, lat, lon):
    """Closest sensitive site whose match radius covers this point, or None."""
    if lat is None or lon is None:
        return None
    g = _index(con)
    best = None
    for dla in (-1, 0, 1):
        for dlo in (-1, 0, 1):
            for s in g.get((int(lat // 1) + dla, int(lon // 1) + dlo), []):
                d = km(lat, lon, s["lat"], s["lon"])
                if d <= s["radius_km"] and (best is None or d < best[0]):
                    best = (d, s)
    if not best:
        return None
    s = best[1]
    return {"qid": s["qid"], "name": s["name"], "kind": s["kind"], "km": round(best[0], 1)}


FIRMS_MIN_DETECTIONS = 4        # anywhere
FIRMS_MIN_AT_SITE = 2            # at a sensitive site


def tag_and_filter(con, signals):
    """Mark physical signals that sit on a sensitive site; drop small fire clusters
    unless they're on one."""
    out = []
    for s in signals:
        if s["source"] in ("firms", "wiki", "seismic"):
            site = near(con, s.get("lat"), s.get("lon"))
            if site and s["source"] == "wiki":
                # A Wikipedia burst counts as "at the site" only when the article is about the
                # site itself, not the town next to it (Larissa city page next to the air base)
                # ("CFB Wainwright" counts for Wainwright Regional Training Area; plain "Larissa" doesn't for Larissa Air Base)
                from .rules import GENERIC, site_words
                words = set(re.findall(r"[a-z]+", (s.get("place") or s.get("title") or "").lower()))
                if not (site_words(site["name"]) & words and words & GENERIC):
                    site = None
            if site:
                s.setdefault("extra", {})["site"] = site
            if s["source"] == "firms":
                n = s.get("extra", {}).get("detections", 0)
                if n < (FIRMS_MIN_AT_SITE if site else FIRMS_MIN_DETECTIONS):
                    continue
                if site:
                    s["title"] = f"{s['title']} at {site['name']}"
        out.append(s)
    return out


def reset_cache():
    global _grid
    _grid = None
