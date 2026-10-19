"""The hotspot decision, in one place, used by both the live globe and the replay tool,
so a replayed incident is judged by exactly the rules that run live.
"""
from . import baseline

PHYSICAL = {"firms", "wiki"}   # signals the sensitive-site rule can lift
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


def decide(feeds, cell, expected, sites_by_source=None, hot_countries=None):
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
        level = "reported" if len(feeds) >= 2 else "signal"
        return level, f"{at_site[0]} above normal at {site['name']} ({site['kind']})", site
    if hot == ["firms"] and not in_conflict(cell, expected, hot_countries):
        return None, "fires only, away from conflict zones and sensitive sites", None
    if len(feeds) >= 2:
        return "reported", f"{len(feeds)} feeds active, {', '.join(hot)} above normal", None
    f = feeds[hot[0]]
    if f.get("baseline") and f["z"] < baseline.SOLO_Z.get(hot[0], 0):
        return None, f"{hot[0]} above normal but not enough on its own", None
    return "signal", f"{hot[0]} far above normal", None
