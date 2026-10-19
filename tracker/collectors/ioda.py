"""IODA internet outage detection (Georgia Tech).

Pulls country-level outage events for the last few hours. A country losing
connectivity is one of the earliest signs of strikes on infrastructure or a
government shutdown. Placed at the country's centre point.
"""
import json
import math
import os
import time

from ..net import get

NAME = "ioda"
API = "https://api.ioda.inetintel.cc.gatech.edu/v2/outages/events"
_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(_HERE, "countries.json"), encoding="utf-8") as f:
    COUNTRIES = json.load(f)


def _code_and_name(ev):
    """IODA has used a few shapes over time; accept the common ones."""
    loc = ev.get("location") or ""
    if isinstance(loc, str) and "/" in loc:
        etype, code = loc.split("/", 1)
        return etype, code.upper(), ev.get("location_name") or ev.get("locationName")
    ent = ev.get("entity") or {}
    if ent:
        return ent.get("type"), (ent.get("code") or "").upper(), ent.get("name")
    return ev.get("entityType"), (ev.get("entityCode") or "").upper(), ev.get("entityName")


def outage_score(ioda_score):
    """IODA scores run from ~100 (a noticeable dip) into the millions (a national blackout)."""
    if not ioda_score or ioda_score <= 0:
        return 2.0
    return round(1 + min(4.0, math.log10(ioda_score) / 1.5), 2)


def parse(payload):
    doc = json.loads(payload)
    items = doc.get("data") if isinstance(doc, dict) else doc
    if isinstance(items, dict):  # sometimes nested one level
        items = items.get("events") or items.get("data") or []
    out = []
    for ev in items or []:
        etype, code, name = _code_and_name(ev)
        if etype != "country" or code not in COUNTRIES:
            continue
        c = COUNTRIES[code]
        start = int(ev.get("start") or ev.get("from") or time.time())
        score = float(ev.get("score") or 0)
        out.append({
            "uid": f"ioda:{code}:{start}",
            "source": NAME,
            "kind": "internet_outage",
            "lat": c["ll"][0], "lon": c["ll"][1],
            "place": name or c["n"],
            "country": code,
            "title": f"Internet outage detected: {name or c['n']}",
            "url": f"https://ioda.inetintel.cc.gatech.edu/country/{code}",
            "score": outage_score(score),
            "observed_at": start,
            "extra": {"duration": ev.get("duration"), "ioda_score": score},
        })
    return out


def fetch(hours=6):
    now = int(time.time())
    url = f"{API}?from={now - hours * 3600}&until={now}&entityType=country&includeAlerts=false&limit=200"
    raw = get(url, timeout=60)
    return raw, parse(raw)
