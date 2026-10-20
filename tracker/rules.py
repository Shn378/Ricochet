"""The hotspot decision, in one place, used by both the live globe and the replay tool,
so a replayed incident is judged by exactly the rules that run live.
"""
import re

from . import baseline

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


def in_conflict(cell, expected, hot_countries=None):
    """Fires on their own only mean something in or beside a country with an ongoing or
    escalating situation. Raw reporting volume isn't a fair test: Arkansas and London get
    more GDELT coverage than Darfur or Donetsk. Without the situations layer (replays),
    fall back to the volume test."""
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


def decide(feeds, cell, expected, sites_by_source=None, hot_countries=None, nearby=None):
    """nearby: (source, title + url) pairs from the surrounding 3x3 squares."""
    """feeds: {source: {"z", "elevated", "baseline", ...}}; sites_by_source: {source: site dict}.
    Returns (level or None, reason, site or None)."""
    sites_by_source = sites_by_source or {}
    # At a sensitive site where normally nothing happens, any physical signal is the anomaly
    for s, f in feeds.items():
        if s in PHYSICAL and s in sites_by_source and not f["elevated"] and f.get("baseline") \
                and f.get("expected") is not None and f["expected"] < QUIET_SITE and f.get("observed", 0.0) >= QUIET_SITE_MIN.get(s, 1.0):
            f["elevated"] = True
            f["quiet_site"] = True
    hot = [s for s, f in feeds.items() if f["elevated"]]
    if not hot:
        return None, None, None
    at_site = [s for s in hot if s in PHYSICAL and s in sites_by_source]
    if at_site:
        site = sites_by_source[at_site[0]]
        # A lone fire at a base outside any conflict is usually a planned range burn (US bases
        # in autumn). Hold it as "watch" until a report nearby names the site and an incident.
        active = [k for k, f in feeds.items() if above_normal(f)]
        if len(active) == 1 and at_site[0] == "firms" and nearby is not None \
                and not corroborated(site, nearby) and not in_conflict(cell, expected, hot_countries):
            return "watch", f"fire at {site['name']} ({site['kind']}), waiting for a second source", site
        level = "reported" if len(active) >= 2 else "signal"
        return level, f"{at_site[0]} above normal at {site['name']} ({site['kind']})", site
    if hot == ["firms"] and not in_conflict(cell, expected, hot_countries):
        return None, "fires only, away from conflict zones and sensitive sites", None
    active = [k for k, f in feeds.items() if above_normal(f)]
    if len(active) >= 2:
        return "reported", f"{len(active)} feeds above their usual level, {', '.join(hot)} well above", None
    f = feeds[hot[0]]
    if f.get("baseline") and f["z"] < baseline.SOLO_Z.get(hot[0], 0):
        return None, f"{hot[0]} above normal but not enough on its own", None
    return "signal", f"{hot[0]} far above normal", None
