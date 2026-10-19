"""Seismic tripwire: USGS real-time event feed.

Big explosions register on seismometers (the 2020 Beirut port blast read as
roughly a magnitude 3.3 event). Two kinds of signal:

- events USGS itself classes as a blast (explosion, chemical/nuclear explosion,
  mine collapse, sonic boom...). Routine quarry blasts are ignored.
- a shallow earthquake in a place that doesn't normally shake. "Normally" comes
  from the last 30 days of magnitude 2.5+ events around the spot, refreshed daily,
  so Japan and Chile stay quiet while a jolt under a city in a calm region stands out.

Coverage note: outside the US, the USGS catalogue is mostly magnitude 4+, so this
catches large blasts and tests, not every strike.
"""
import json
import threading
import time

from ..net import get

NAME = "seismic"
FEED = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/{}.geojson"
BLAST_TYPES = {"explosion": 3.0, "chemical explosion": 3.0, "nuclear explosion": 6.0, "accidental explosion": 3.0,
               "experimental explosion": 2.5, "mine collapse": 2.5, "rock burst": 2.0, "sonic boom": 2.5,
               "industrial explosion": 3.0, "collapse": 2.5}
IGNORE_TYPES = {"quarry blast", "ice quake", "other event", "snow avalanche", "landslide"}
MIN_MAG = 2.5
MAX_DEPTH_KM = 15        # deeper quakes aren't explosions and rarely matter here
QUIET_MAX = 1            # 30-day events in the surrounding 3x3 squares for a place to count as "quiet"

_lock = threading.Lock()
_activity = {"t": 0, "cells": {}}   # 1-degree cell -> M2.5+ events in the last 30 days


def _cell(lat, lon):
    return int(lat // 1), int(lon // 1)


def parse(payload):
    """GeoJSON -> list of plain events."""
    out = []
    for f in json.loads(payload).get("features", []):
        p, g = f.get("properties") or {}, (f.get("geometry") or {}).get("coordinates") or []
        if len(g) < 2 or p.get("time") is None:
            continue
        out.append({"id": f.get("id"), "lon": g[0], "lat": g[1], "depth": g[2] if len(g) > 2 else None,
                    "mag": p.get("mag"), "type": (p.get("type") or "earthquake").lower(), "place": p.get("place"),
                    "t": int(p["time"] // 1000), "url": p.get("url"), "title": p.get("title")})
    return out


def activity(refresh_hours=24):
    """Last 30 days of M2.5+ events per cell (cached, refreshed daily)."""
    with _lock:
        if time.time() - _activity["t"] > refresh_hours * 3600 or not _activity["cells"]:
            cells = {}
            for e in parse(get(FEED.format("2.5_month"), timeout=90)):
                if e["type"] == "earthquake":
                    k = _cell(e["lat"], e["lon"])
                    cells[k] = cells.get(k, 0) + 1
            _activity.update(t=time.time(), cells=cells)
        return _activity["cells"]


def nearby_activity(cells, lat, lon):
    la, lo = _cell(lat, lon)
    return sum(cells.get((la + a, lo + b), 0) for a in (-1, 0, 1) for b in (-1, 0, 1))


def to_signals(events, cells):
    out = []
    for e in events:
        mag = e["mag"] or 0
        if e["type"] in IGNORE_TYPES:
            continue
        if e["type"] in BLAST_TYPES:
            kind, score = "seismic_blast", BLAST_TYPES[e["type"]]
            label = e["type"].capitalize()
        elif e["type"] == "earthquake":
            if mag < MIN_MAG or e["depth"] is None or e["depth"] > MAX_DEPTH_KM:
                continue
            n = nearby_activity(cells, e["lat"], e["lon"])
            # The event itself is in the 30-day count once the monthly feed catches up
            if n - 1 > QUIET_MAX:
                continue
            kind, score = "unusual_quake", 2.5 + max(0.0, mag - 3.0) * 0.5
            label = f"Shallow M{mag:.1f} event where the ground rarely shakes"
        else:
            kind, score, label = "seismic_other", 1.0, e["type"].capitalize()
        out.append({
            "uid": f"seismic:{e['id']}",
            "source": NAME, "kind": kind,
            "lat": e["lat"], "lon": e["lon"],
            "place": e["place"],
            "title": f"{label}: M{mag:.1f}, {e['depth'] if e['depth'] is not None else '?'} km deep, {e['place']}",
            "url": e["url"],
            "score": round(score, 2),
            "observed_at": e["t"],
            "extra": {"mag": mag, "depth_km": e["depth"], "usgs_type": e["type"]},
        })
    return out


def fetch():
    raw = get(FEED.format("all_hour"), timeout=30)
    return raw, to_signals(parse(raw), activity())
