"""Cloudflare Radar: internet outages and traffic anomalies, alongside IODA.

Two endpoints (read-only API token in config.ini, [radar] token):
- outage annotations: outages Cloudflare's team has confirmed, with a cause
  (government-directed shutdown, power cut, cable cut, military action...)
- traffic anomalies: automatic detections of a country or network's traffic
  dropping well below normal

Government shutdowns and "military action" causes score highest: a regime cutting
the internet is often the first visible sign of unrest or an operation.
"""
import json
import time
import urllib.parse
from datetime import datetime

from .. import places
from ..net import get

NAME = "radar"
API = "https://api.cloudflare.com/client/v4/radar"
CAUSE_SCORE = {"GOVERNMENT_DIRECTED": 3.5, "MILITARY_ACTION": 4.0, "CABLE_CUT": 2.5, "POWER_OUTAGE": 2.5,
               "WEATHER": 1.5, "TECHNICAL_PROBLEM": 1.5, "MAINTENANCE": 0.5}


def _ts(s):
    if not s:
        return None
    try:
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def _call(token, path, **params):
    url = f"{API}/{path}?" + urllib.parse.urlencode({"format": "json", **params})
    doc = json.loads(get(url, timeout=30, headers={"Authorization": f"Bearer {token}"}))
    if not doc.get("success", True):
        raise RuntimeError(f"Radar {path}: {doc.get('errors')}")
    return doc.get("result") or {}


def _point(iso):
    c = places.country_centre(iso) if iso else None
    return (c[0], c[1], c[2]) if c else (None, None, iso)


def outage_signals(result, now):
    out = []
    for a in result.get("annotations") or []:
        outage = a.get("outage") or {}
        cause = (outage.get("outageCause") or "").upper()
        locs = a.get("locations") or [d.get("code") for d in a.get("locationsDetails") or [] if d.get("code")]
        start = _ts(a.get("startDate")) or now
        for iso in locs or [None]:
            lat, lon, name = _point(iso)
            if lat is None:
                continue
            score = CAUSE_SCORE.get(cause, 2.0)
            if (a.get("scope") or "").lower().startswith("region") or a.get("asns"):
                score *= 0.8   # one network or region rather than the whole country
            out.append({
                "uid": f"radar:outage:{a.get('id')}:{iso}",
                "source": NAME, "kind": "internet_outage",
                "lat": lat, "lon": lon, "place": name, "country": iso,
                "title": f"Internet outage in {name}: {cause.replace('_', ' ').lower() or 'cause not stated'}"
                         + (f" ({a.get('description')[:120]})" if a.get("description") else ""),
                "url": a.get("linkedUrl") or f"https://radar.cloudflare.com/outage-center",
                "score": round(score, 2),
                "observed_at": start,
                "extra": {"cause": cause, "type": outage.get("outageType"), "ended": bool(a.get("endDate")),
                          "asns": a.get("asns"), "scope": a.get("scope")},
            })
    return out


def anomaly_signals(result, now):
    out = []
    for t in result.get("trafficAnomalies") or []:
        loc = t.get("locationDetails") or (t.get("asnDetails") or {}).get("location") or {}
        iso = loc.get("code")
        lat, lon, name = _point(iso)
        if lat is None:
            continue
        asn = t.get("asnDetails") or {}
        what = f"network {asn.get('name')} (AS{asn.get('asn')})" if asn.get("asn") else name
        out.append({
            "uid": f"radar:anomaly:{t.get('uuid')}",
            "source": NAME, "kind": "traffic_anomaly",
            "lat": lat, "lon": lon, "place": name, "country": iso,
            "title": f"Internet traffic drop: {what}, {name}",
            "url": f"https://radar.cloudflare.com/{iso.lower()}" if iso else "https://radar.cloudflare.com/",
            "score": 1.5 if asn.get("asn") else 2.5,
            "observed_at": _ts(t.get("startDate")) or now,
            "extra": {"status": t.get("status"), "type": t.get("type"), "asn": asn.get("asn"), "ended": bool(t.get("endDate"))},
        })
    return out


def fetch(token):
    now = int(time.time())
    outages = _call(token, "annotations/outages", dateRange="1d", limit=50)
    anomalies = _call(token, "traffic_anomalies", dateRange="1d", limit=50)
    raw = json.dumps({"outages": outages, "anomalies": anomalies}).encode()
    return raw, outage_signals(outages, now) + anomaly_signals(anomalies, now)
