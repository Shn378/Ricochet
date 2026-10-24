"""The "at a glance" strip on a hotspot card: what is blinking, in plain words, from the
engine's own numbers. No AI, so nothing here can be made up: every line is a feed, how many
signals it has in the square and how far that is from the square's normal level.

Outage-type feeds always say "cause not confirmed" (CLAUDE.md principles).
"""
from collections import Counter

from . import rules

UNCONFIRMED = {"ioda", "radar", "infra"}

# (singular, plural) for "<n> <thing>" lines
NOUNS = {
    "news": ("news report", "news reports"),
    "gdelt": ("conflict event coded by GDELT", "conflict events coded by GDELT"),
    "firms": ("satellite heat cluster", "satellite heat clusters"),
    "wiki": ("Wikipedia edit burst", "Wikipedia edit bursts"),
    "seismic": ("seismic event", "seismic events"),
    "ioda": ("internet outage", "internet outages"),
    "radar": ("internet traffic anomaly (Cloudflare)", "internet traffic anomalies (Cloudflare)"),
    "infra": ("provider status incident", "provider status incidents"),
}
# Aircraft and shipping say what kind of oddity it is
KIND_WORDS = {
    "emergency_squawk": "hijack squawk (7500)", "airspace_drop": "airspace emptying", "gps_jamming": "GPS jamming",
    "shipping_drop": "ships holding back",
}


def _what(src, n, kinds):
    named = [KIND_WORDS[k] for k, _ in kinds.most_common() if k in KIND_WORDS]
    if named:
        text = ", ".join(dict.fromkeys(named))
        return text[0].upper() + text[1:]
    if src == "adsb":
        return "Unusual aircraft activity"
    one, many = NOUNS.get(src, (f"{src} signal", f"{src} signals"))
    text = f"{n} {one if n == 1 else many}"
    if src == "gdelt" and kinds.get("conflict_report_national"):
        text += " (some placed only at country level)"
    return text


def _against_normal(f):
    if f.get("severity"):
        return f["severity"]
    if f.get("quiet_site"):
        return "where normally nothing happens"
    if f.get("burst"):
        return f"burst: {f['burst']}"
    if not f.get("baseline"):
        return "no normal level learnt yet"
    if f.get("ratio"):
        return f"{f['ratio']}× normal"
    return "not usually seen here"


def build(feeds, items, site=None, expected_event=None):
    """feeds: the hotspot's per-feed judgement (rules.evaluate); items: its signals.
    Returns {"lines": [{"feed", "state", "text", "note"}], "active": n, "site": ..., "expected": ...}.
    state: "spike" (well above normal), "above" (above its normal), "normal" (at its usual level)."""
    kinds = {}
    for s in items:
        kinds.setdefault(s["source"], Counter())[s["kind"]] += 1
    lines = []
    for src, f in feeds.items():
        state = "spike" if f.get("elevated") else "above" if rules.above_normal(f) else "normal"
        note = _against_normal(f) if state != "normal" else "at its usual level"
        if src in UNCONFIRMED:
            note += " · cause not confirmed"
        lines.append({"feed": src, "state": state, "text": _what(src, f.get("n", 0), kinds.get(src, Counter())),
                      "note": note, "z": f.get("z", 0)})
    order = {"spike": 0, "above": 1, "normal": 2}
    lines.sort(key=lambda x: (order[x["state"]], -x["z"]))
    for x in lines:
        del x["z"]
    return {"lines": lines, "active": sum(1 for x in lines if x["state"] != "normal"),
            "site": f"{site['name']} ({site['kind']}{', %s km' % site['km'] if site.get('km') is not None else ''})" if site else None,
            "expected": expected_event["name"] if expected_event else None}
