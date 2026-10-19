"""Unfiltered ADS-B from adsb.lol (community feed, readsb JSON format).

Two things for now:
- emergency squawks worldwide: 7500 hijack, 7600 radio failure, 7700 emergency
- military aircraft, counted per 5-degree area per 15 minutes, so phase 2
  can spot unusual build-ups against a baseline
"""
import json
import time
from collections import Counter

from ..net import get

NAME = "adsb"
BASE = "https://api.adsb.lol/v2"
SQUAWKS = {"7500": "hijack code", "7600": "radio failure", "7700": "general emergency"}


def parse_aircraft(payload):
    doc = json.loads(payload)
    return [a for a in doc.get("ac") or [] if a.get("lat") is not None and a.get("lon") is not None]


def squawk_signals(aircraft, code, now):
    out = []
    for a in aircraft:
        flight = (a.get("flight") or a.get("r") or a.get("hex") or "unknown").strip()
        out.append({
            "uid": f"adsb:sqk:{a.get('hex')}:{code}:{now // 1800}",
            "source": NAME, "kind": "emergency_squawk",
            "lat": a["lat"], "lon": a["lon"],
            "title": f"Squawk {code} ({SQUAWKS[code]}): {flight}",
            "url": f"https://adsb.lol/?icao={a.get('hex')}",
            "score": 3 if code == "7500" else 1.5,
            "observed_at": now,
            "extra": {"hex": a.get("hex"), "type": a.get("t"), "alt": a.get("alt_baro")},
        })
    return out


def military_signals(aircraft, now):
    bucket = now // 900
    counts, sample = Counter(), {}
    for a in aircraft:
        key = (int(a["lat"] // 5) * 5, int(a["lon"] // 5) * 5)
        counts[key] += 1
        sample.setdefault(key, []).append(a.get("t") or "?")
    out = []
    for (la, lo), n in counts.items():
        types = Counter(sample[(la, lo)]).most_common(4)
        out.append({
            "uid": f"adsb:mil:{la}:{lo}:{bucket}",
            "source": NAME, "kind": "military_air",
            "lat": la + 2.5, "lon": lo + 2.5,
            "title": f"{n} military aircraft broadcasting in this area",
            "url": f"https://adsb.lol/?lat={la + 2.5}&lon={lo + 2.5}&zoom=6",
            "score": 0.5,  # presence only; baselines decide what is unusual
            "observed_at": now,
            "extra": {"count": n, "types": types},
        })
    return out


def fetch():
    now = int(time.time())
    raws, signals = [], []
    for code in SQUAWKS:
        raw = get(f"{BASE}/sqk/{code}", timeout=30)
        raws.append(raw)
        signals += squawk_signals(parse_aircraft(raw), code, now)
    raw = get(f"{BASE}/mil", timeout=60)
    raws.append(raw)
    signals += military_signals(parse_aircraft(raw), now)
    return b"\n".join(raws), signals
