"""The hotspot decision, in one place, used by both the live globe and the replay tool,
so a replayed incident is judged by exactly the rules that run live.
"""
import json
import re
import time
from collections import defaultdict

from . import baseline, known_events

# Proposed rule changes, each behind a switch so the benchmark can replay old vs new.
# A switch flips to True once the benchmark shows hits gained with no extra false alarms.
FLAGS = {
    "pair_signal": False,       # two feeds each moderately above normal open a Signal together
    "national_no_open": True,   # live 24 Oct (Shane): 0 hits lost, 3 fewer openings. GDELT reports pinned to a country's centre point don't count
    "quake_strict": True,       # live 24 Oct (Shane): 0 hits lost, 3 fewer openings. The "5+ might be a nuclear test" exception needs one big event, not a swarm
    "severity": True,           # live 24 Oct (Shane): +2 hits, +21 openings. A report of 10+ killed counts whatever the baseline
    "listed_conflicts": False,  # hand-kept list of wars (conflicts.py) counts as "in conflict"
    "median_baseline": False,   # normal level = median day over 4 weeks, not the average
    "na_admin_no_open": True,   # live 24 Oct (Shane, audit proposal 1): 16 fewer openings in 3 days, 0 hits lost. US/Canada GDELT reports placed only at a state/province or county can't open a hotspot alone
}

PHYSICAL = {"firms", "wiki"}   # signals the sensitive-site rule can lift
# Feeds that can corroborate a lone fire: someone, somewhere, is talking about it
CORROBORATE = {"gdelt", "news", "wiki"}
QUIET_SITE = 0.5    # expected score below this = "normally nothing happens here"
# The smallest signal the quiet-site rule will lift. A 2-3 detection fire 10 km from a
# range is usually a controlled burn; Wainwright was 8 detections (score 1.6).
QUIET_SITE_MIN = {"firms": 1.5, "wiki": 1.0}


def conflict_zone(expected, cell, radius=2, min_reports=1.0):
    """Is there a normal level of conflict reporting in or near this square?"""
    la, lo = map(int, cell.split(":"))
    total = 0.0
    for dla in range(-radius, radius + 1):
        for dlo in range(-radius, radius + 1):
            total += expected.get((f"{la + dla}:{lo + dlo}", "gdelt"), 0.0)
    return total >= min_reports


_near_cache = {}


def countries_near(cell, radius=2):
    """ISO codes of countries within about two degrees of this square (cached; squares don't move)."""
    hit = _near_cache.get(cell)
    if hit is None:
        from .places import nearest_country
        la, lo = map(int, cell.split(":"))
        hit = set()
        for dla in range(-radius, radius + 1):
            for dlo in range(-radius, radius + 1):
                cc = nearest_country(la + dla + 0.5, lo + dlo + 0.5)
                if cc:
                    hit.add(cc)
        _near_cache[cell] = hit
    return hit


def in_conflict(cell, expected, hot_countries=None, flags=None):
    """Fires on their own only mean something in or beside a country with an ongoing or
    escalating situation. Raw reporting volume isn't a fair test: Arkansas and London get
    more GDELT coverage than Darfur or Donetsk. Without the situations layer,
    fall back to the volume test."""
    if (flags or FLAGS).get("listed_conflicts"):
        from .conflicts import listed_near
        if listed_near(cell):
            return True
    if hot_countries is None:
        return conflict_zone(expected, cell)
    return bool(countries_near(cell) & hot_countries)


GENERIC = set("""fort base army air force regional training area plant ammunition cfb raf naval
station camp international airport military refinery nuclear power the of and joint field range
depot barracks installation terminal lng parliament building national centre center county""".split())
INCIDENT = re.compile(r"fire|blaze|explo|blast|smoke|burn|incident|attack|drone|strike|evacuat|emergenc|casualt|dead|killed|injur", re.I)


def site_words(name):
    """Distinctive words of a site's name: 'Fort Campbell' -> {'campbell'}."""
    return {w for w in re.findall(r"[a-z]+", (name or "").lower()) if len(w) >= 4 and w not in GENERIC}


def corroborated(site, nearby):
    """Does any nearby report name this site and talk about an incident?
    Plain news volume isn't enough: Fort Campbell sits next to Nashville."""
    words = site_words(site.get("name"))
    for src, text in nearby or ():
        if src in CORROBORATE and text:
            t = text.lower().replace("-", " ").replace("_", " ")
            if any(w in t for w in words) and INCIDENT.search(t):
                return True
    return False


def above_normal(f):
    """Does this feed add anything here? A feed at its everyday level (London's
    usual GDELT crime coverage) is background, not a second source."""
    if f.get("elevated") or not f.get("baseline"):
        return True
    exp = f.get("expected") or 0.0
    return f.get("z", 0) >= 1.0 and (exp == 0 or f.get("observed", 0) >= 1.5 * exp)


def neighbours(cell, radius=1):
    la, lo = map(int, cell.split(":"))
    return [f"{la + a}:{lo + b}" for a in range(-radius, radius + 1) for b in range(-radius, radius + 1)]


def moderately_above(f):
    """Clearly above its own normal, but not by enough to stand alone."""
    exp = f.get("expected") or 0.0
    return bool(f.get("baseline")) and f.get("z", 0) >= 1.5 and f.get("observed", 0) >= 2 * max(exp, 0.5)


def decide(feeds, cell, expected, sites_by_source=None, hot_countries=None, nearby=None, flags=None):
    """feeds: {source: {"z", "elevated", "baseline", ...}}; sites_by_source: {source: site dict};
    nearby: (source, title + url) pairs from the surrounding 3x3 squares.
    Returns (level or None, reason, site or None)."""
    flags = flags or FLAGS
    sites_by_source = sites_by_source or {}
    conflict = lambda: in_conflict(cell, expected, hot_countries, flags)
    # At a sensitive site where normally nothing happens, any physical signal is the anomaly
    for s, f in feeds.items():
        if s in PHYSICAL and s in sites_by_source and not f["elevated"] and f.get("baseline") \
                and f.get("expected") is not None and f["expected"] < QUIET_SITE and f.get("observed", 0.0) >= QUIET_SITE_MIN.get(s, 1.0):
            f["elevated"] = True
            f["quiet_site"] = True
    hot = [s for s, f in feeds.items() if f["elevated"]]
    if not hot:
        if flags.get("pair_signal"):
            pair = [s for s, f in feeds.items() if moderately_above(f) and s in ("gdelt", "news", "wiki", "ioda", "radar", "firms", "adsb")]
            if len(pair) >= 2:
                return "signal", f"{' and '.join(pair)} both above normal together", None
        return None, None, None
    at_site = [s for s in hot if s in PHYSICAL and s in sites_by_source]
    if at_site:
        site = sites_by_source[at_site[0]]
        # A lone fire at a base outside any conflict is usually a planned range burn (US bases
        # in autumn). Hold it as "watch" until a report nearby names the site and an incident.
        active = [k for k, f in feeds.items() if above_normal(f)]
        if len(active) == 1 and at_site[0] == "firms" and nearby is not None \
                and not corroborated(site, nearby) and not conflict():
            return "watch", f"fire at {site['name']} ({site['kind']}), waiting for a second source", site
        level = "reported" if len(active) >= 2 else "signal"
        return level, f"{at_site[0]} above normal at {site['name']} ({site['kind']})", site
    # Lone seismic events (natural quakes in odd places, mislabelled blasts) stay as map dots
    # unless they're near a conflict or a sensitive site, or big enough to be a nuclear test
    if hot == ["seismic"] and "seismic" not in sites_by_source and not conflict():
        sq = feeds["seismic"]
        big = sq.get("observed", 0) >= 5
        if flags.get("quake_strict"):
            # A test is one big event; several quakes together is a natural swarm (ridges, volcanoes)
            big = sq.get("max", sq.get("observed", 0)) >= 5 and sq.get("n", 1) < 3
        if not big:
            return None, "seismic event alone, away from conflict zones and sensitive sites", None
    if hot == ["firms"] and not conflict():
        return None, "fires only, away from conflict zones and sensitive sites", None
    active = [k for k, f in feeds.items() if above_normal(f)]
    if len(active) >= 2:
        learning = [k for k in active if not feeds[k].get("baseline")]
        note = f" ({', '.join(learning)} still learning its normal level)" if learning else ""
        return "reported", f"{', '.join(hot)} well above normal, {len(active) - 1} other feed(s) active{note}", None
    f = feeds[hot[0]]
    if f.get("severity"):
        return "signal", f"{hot[0]}: {f['severity']}", None
    if f.get("baseline") and f["z"] < baseline.SOLO_Z.get(hot[0], 0):
        return None, f"{hot[0]} above normal but not enough on its own", None
    return "signal", f"{hot[0]} far above normal", None


# "73 killed", "dozens dead", "death toll rises to 153"
_TOLL = re.compile(r"\b(\d{1,4}|dozens|scores|hundreds)\s+(?:people\s+|civilians\s+|were\s+|are\s+|others\s+)?(?:killed|dead|died|deaths|massacred)\b"
                   r"|(?<!injury )(?<!injured )\b(?:death toll|toll)\b[a-z ,]{0,20}?(\d{1,4})\b"
                   r"|\bkill(?:s|ed|ing)\s+(?:at least\s+|more than\s+|over\s+)?(\d{1,4}|dozens|scores|hundreds)\b", re.I)
SEVERITY_MIN = 10


def death_toll(text):
    """Largest casualty figure stated in a headline or address, or None."""
    best = None
    for m in _TOLL.finditer((text or "").replace("-", " ")):
        word = (m.group(1) or m.group(2) or m.group(3) or "").lower()
        n = {"dozens": 24, "scores": 40, "hundreds": 200}.get(word) or (int(word) if word.isdigit() else 0)
        if 1900 <= n <= 2100:   # a year, not a toll
            continue
        if n and (best is None or n > best):
            best = n
    return best


_country_cache = {}


def country_of(cell, items):
    """Country (ISO2) for a square: from its signals if they say, else the nearest town."""
    for s in items:
        if s.get("country") and len(s["country"]) == 2 and s["source"] != "gdelt":
            return s["country"]
    if cell not in _country_cache:
        from .places import nearest_country
        la, lo = map(int, cell.split(":"))
        _country_cache[cell] = nearest_country(la + 0.5, lo + 0.5)
    return _country_cache[cell]


def _extra(s):
    e = s.get("extra")
    if isinstance(e, str):
        try:
            return json.loads(e or "{}")
        except ValueError:
            return {}
    return e or {}


# GDELT places a story it can only pin to a US state, Canadian province or US county at that area's
# centre ("Ohio, United States", "Clark County, Nevada, United States"). These are mostly local crime
# coded as fighting: 12 of 24 noise hotspots in the 24 Oct audit.
_COUNTY = re.compile(r"\b(county|parish|borough)\b", re.I)


def na_admin(s):
    """True for a GDELT report placed only at a US/Canada state, province or county."""
    if s.get("source") != "gdelt" or s.get("country") not in ("US", "CA"):
        return False
    parts = [p.strip() for p in (s.get("place") or "").split(",") if p.strip()]
    if len(parts) == 2:                       # "Ohio, United States", "British Columbia, Canada"
        return True
    return len(parts) == 3 and bool(_COUNTY.search(parts[0]))


# Official red alerts open a hotspot on their own (Shane, 24 Oct): their own "Natural hazard"
# level in lime, never mixed with security. Not part of evaluate(): the benchmark is security incidents.
# Droughts are left out: GDACS keeps them red for months, which would be a permanent hotspot.
OFFICIAL_ALERT_KINDS = {"gdacs_red", "tsunami_warning", "tsunami_threat"}
ALERT_HOURS = 24      # an alert stays on the tour for a day after its latest update


def official_alert(s):
    """Is this hazard signal an official red-level alert?"""
    e = _extra(s)
    if s.get("kind") == "met_warning":
        return e.get("alert") == "red"
    return s.get("kind") in OFFICIAL_ALERT_KINDS and e.get("hazard") != "drought"


def evaluate(cell, items, expected, history, hot_countries=None, nearby=None, now=None, flags=None):
    """The whole hotspot decision for one square, shared by the live globe, replay and the
    benchmark. items: that square's signals in the window (context kinds already removed).
    Returns {"level", "reason", "feeds", "hot", "site", "expected_event"}."""
    flags = {**FLAGS, **(flags or {})}
    now = now or time.time()
    if flags["na_admin_no_open"] and any(na_admin(s) for s in items):
        # They can back up a hotspot something else opened, never open one themselves
        inner = dict(flags, na_admin_no_open=False)
        full = evaluate(cell, items, expected, history, hot_countries, nearby, now, inner)
        if full["level"] in ("signal", "reported"):
            rest = [s for s in items if not na_admin(s)]
            alone = evaluate(cell, rest, expected, history, hot_countries, nearby, now, inner) if rest else None
            if not alone or alone["level"] not in ("signal", "reported"):
                return dict(full, level=None, reason="only US/Canada state- or county-level reports")
        return full
    if flags["national_no_open"]:
        items = [s for s in items if s.get("kind") != "conflict_report_national"]
    by_src, n_src, max_src = defaultdict(float), defaultdict(int), defaultdict(float)
    toll = {}
    for s in items:
        sc = s.get("score") or 1
        by_src[s["source"]] += sc
        n_src[s["source"]] += 1
        max_src[s["source"]] = max(max_src[s["source"]], sc)
        if flags["severity"] and s["source"] in ("gdelt", "news"):
            n = death_toll(f"{s.get('title') or ''} {s.get('url') or ''}")
            if n and n >= SEVERITY_MIN and n > toll.get(s["source"], 0):
                toll[s["source"]] = n
    feeds = {}
    for src, obs in by_src.items():
        exp = expected.get((cell, src), 0.0)
        z, ratio, elevated = baseline.judge(obs, exp, src, history.get(src, 0))
        feeds[src] = {"observed": round(obs, 1), "expected": round(exp, 2), "z": round(z, 1),
                      "ratio": round(ratio, 1) if ratio else None, "elevated": elevated,
                      "baseline": history.get(src, 0) >= baseline.MIN_HISTORY_HOURS,
                      "n": n_src[src], "max": round(max_src[src], 2)}
        if src in toll and not elevated:
            feeds[src]["elevated"] = True
            feeds[src]["severity"] = f"report of {toll[src]} killed"
    site_by = {}
    for s in items:
        site = _extra(s).get("site")
        if site and s["source"] not in site_by:
            site_by[s["source"]] = site
    level, reason, site = decide(feeds, cell, expected, site_by, hot_countries, nearby, flags) if feeds else (None, None, None)
    hot = [s for s, f in feeds.items() if f["elevated"]]   # after decide: the site rule can lift a feed
    expected_event = None
    if level in ("signal", "reported"):
        active = [s for s, f in feeds.items() if above_normal(f)] or hot
        # The note only applies when the feed driving the hotspot is one the event explains:
        # bonfire season explains fires, not a Google Cloud outage in London.
        lead = max(hot or active, key=lambda s: feeds[s].get("z", 0))
        expected_event = known_events.match(now, country_of(cell, items), [lead])
        if expected_event:
            unexplained = [s for s in active if s not in expected_event["feeds"]]
            if level == "reported" and not unexplained:
                level = "signal"   # held until a feed the event doesn't explain backs it up
            reason = f"{reason} · expected: {expected_event['name']}"
    return {"level": level, "reason": reason, "feeds": feeds, "hot": hot, "site": site, "expected_event": expected_event}
