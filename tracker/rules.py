"""The hotspot decision, in one place, used by both the live globe and the replay tool,
so a replayed incident is judged by exactly the rules that run live.
"""
from . import baseline

PHYSICAL = {"firms", "wiki"}   # signals the sensitive-site rule can lift
QUIET_SITE = 0.5    # expected score below this = "normally nothing happens here"


def conflict_zone(expected, cell, radius=2, min_reports=1.0):
    """Is there a normal level of conflict reporting in or near this square?"""
    la, lo = map(int, cell.split(":"))
    total = 0.0
    for dla in range(-radius, radius + 1):
        for dlo in range(-radius, radius + 1):
            total += expected.get((f"{la + dla}:{lo + dlo}", "gdelt"), 0.0)
    return total >= min_reports


def decide(feeds, cell, expected, sites_by_source=None):
    """feeds: {source: {"z", "elevated", "baseline", ...}}; sites_by_source: {source: site dict}.
    Returns (level or None, reason, site or None)."""
    sites_by_source = sites_by_source or {}
    # At a sensitive site where normally nothing happens, any physical signal is the anomaly
    for s, f in feeds.items():
        if s in PHYSICAL and s in sites_by_source and not f["elevated"] and f.get("baseline") \
                and f.get("expected") is not None and f["expected"] < QUIET_SITE and f.get("observed", 0.0) >= 1.0:
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
    if hot == ["firms"] and not conflict_zone(expected, cell):
        return None, "fires only, away from conflict reporting and sensitive sites", None
    if len(feeds) >= 2:
        return "reported", f"{len(feeds)} feeds active, {', '.join(hot)} above normal", None
    f = feeds[hot[0]]
    if f.get("baseline") and f["z"] < baseline.SOLO_Z.get(hot[0], 0):
        return None, f"{hot[0]} above normal but not enough on its own", None
    return "signal", f"{hot[0]} far above normal", None
