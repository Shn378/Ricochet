"""NASA FIRMS active fire detections (VIIRS), needs a free MAP_KEY.

Individual detections are noisy (crop burning, gas flares, wildfires), so we
group them into half-degree cells and only raise a signal for tight,
high-confidence clusters. Phase 2 adds per-cell baselines to filter the
seasonal stuff properly.
"""
import csv
import io
from collections import defaultdict
from datetime import datetime, timezone

from ..net import get

NAME = "firms"
API = "https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/{source}/world/1{date}"
SOURCES = ["VIIRS_NOAA20_NRT", "VIIRS_SNPP_NRT"]
MIN_DETECTIONS = 2   # final cut (4, or 2 at a sensitive site) happens in sites.tag_and_filter


def parse(csv_bytes, sensor=""):
    cells = defaultdict(list)
    for row in csv.DictReader(io.StringIO(csv_bytes.decode("utf-8", "replace"))):
        conf = (row.get("confidence") or "").lower()
        if conf not in ("h", "high", "n", "nominal"):
            continue
        try:
            lat, lon = float(row["latitude"]), float(row["longitude"])
            frp = float(row.get("frp") or 0)
            t = datetime.strptime(f"{row['acq_date']} {int(row['acq_time']):04d}", "%Y-%m-%d %H%M").replace(tzinfo=timezone.utc)
        except (KeyError, ValueError):
            continue
        # Cluster per half-degree area per satellite pass (hour), so re-downloading
        # the same day never counts the same fires twice
        cells[(round(lat * 2) / 2, round(lon * 2) / 2, t.strftime("%Y%m%d%H"))].append((lat, lon, frp, t, conf))

    out = []
    for (clat, clon, passhour), pts in cells.items():
        high = sum(1 for p in pts if p[4] in ("h", "high"))
        if len(pts) < MIN_DETECTIONS:
            continue
        lat = sum(p[0] for p in pts) / len(pts)
        lon = sum(p[1] for p in pts) / len(pts)
        latest = max(p[3] for p in pts)
        frp = sum(p[2] for p in pts)
        out.append({
            "uid": f"firms:{sensor}:{clat}:{clon}:{passhour}",
            "source": NAME,
            "kind": "heat_cluster",
            "lat": lat, "lon": lon,
            "title": f"Heat cluster: {len(pts)} detections ({high} high confidence)" if high else f"Heat cluster: {len(pts)} detections (nominal confidence)",
            "url": f"https://firms.modaps.eosdis.nasa.gov/map/#d:24hrs;@{lon:.2f},{lat:.2f},9.0z",
            # grass fires burn cooler and are often only "nominal" confidence: they count, at a lower score
            "score": round(min(5.0, 1 + len(pts) / 5 + frp / 500) * (1.0 if high else 0.6), 2),
            "observed_at": int(latest.timestamp()),
            "extra": {"detections": len(pts), "high": high, "frp_total": round(frp, 1), "sensor": sensor},
        })
    return out


def fetch_day(key, day):
    """All clusters for one past UTC day (YYYY-MM-DD), for backfilling baselines."""
    out = []
    for src in SOURCES:
        out += parse(get(API.format(key=key, source=src, date=f"/{day}"), timeout=180), src)
    return out


def fetch(key):
    if not key:
        raise RuntimeError("No FIRMS map key set in config.ini (free from firms.modaps.eosdis.nasa.gov/api/map_key)")
    raws, signals = [], []
    for src in SOURCES:
        raw = get(API.format(key=key, source=src, date=""), timeout=120)
        raws.append(raw)
        signals += parse(raw, src)
    return b"\n".join(raws), signals
