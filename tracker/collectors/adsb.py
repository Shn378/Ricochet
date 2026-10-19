"""Unfiltered ADS-B from adsb.lol (community feed, readsb JSON format).

- emergency squawks worldwide: 7500 hijack, 7600 radio failure, 7700 emergency
- military aircraft, counted per 5-degree area per 15 minutes, so phase 2
  can spot unusual build-ups against a baseline
- flashpoint regions, watched for two second-order echoes:
  * airspace emptying: civil traffic far below its usual level for that time of day
    (airlines reroute and authorities close airspace before the news catches up)
  * GPS jamming: a jump in the share of aircraft reporting poor position accuracy
    (navigation accuracy category below 8, the gpsjam.org method), compared with
    that region's usual share, since the Baltic and Black Sea are jammed every day
"""
import json
import statistics
import threading
import time
from collections import Counter

from .. import db
from ..net import get

REGIONS = {   # name: (lat, lon); each is a 250 nautical mile circle
    "Israel and Lebanon": (32.8, 35.3), "Tehran": (35.5, 51.2), "Persian Gulf": (26.5, 52.0),
    "Red Sea and Yemen": (15.5, 43.0), "Black Sea and Crimea": (44.6, 34.0), "Baltic and Kaliningrad": (55.3, 20.8),
    "Taiwan Strait": (24.3, 120.0), "Korean Peninsula": (37.6, 127.0), "India and Pakistan border": (31.0, 74.0),
    "Caracas": (10.5, -66.9),
}
RADIUS_NM = 250
DROP_RATIO = 0.35       # civil traffic below this share of its usual level = airspace emptying
DROP_MIN_USUAL = 15     # ignore regions that are quiet at this hour anyway
JAM_JUMP = 0.25         # poor-accuracy share this far above usual = jamming
JAM_MIN_AIRCRAFT = 8
MIN_DAYS = 2            # days of history before judging a region

SCHEMA = """
CREATE TABLE IF NOT EXISTS airspace (
    region TEXT, slot INTEGER, civil INTEGER, reporting INTEGER, poor INTEGER,
    PRIMARY KEY (region, slot)
);
"""
_con, _con_lock = None, threading.Lock()
LAST = {"failed": []}   # regions that failed on the last run, for the health panel


def _db():
    global _con
    with _con_lock:
        if _con is None:
            _con = db.connect()
            _con.executescript(SCHEMA)
        return _con

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
            # 7700 and 7600 happen dozens of times a day (medical diversions, radio faults):
            # map context only. A hijack code is a real tripwire.
            "source": NAME, "kind": "emergency_squawk" if code == "7500" else "squawk_routine",
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


def is_military(a):
    return bool((a.get("dbFlags") or 0) & 1)


def region_stats(aircraft):
    civil = [a for a in aircraft if not is_military(a)]
    reporting = [a for a in civil if a.get("nac_p") is not None]
    poor = [a for a in reporting if a["nac_p"] < 8]
    return civil, reporting, poor


def usual(con, region, slot, column, days=7):
    """Median of this region's value at the same 15-minute slot on previous days."""
    rows = con.execute(f"SELECT {column} AS v, reporting FROM airspace WHERE region=? AND slot IN ({','.join('?' * days)})",
                       (region, *[slot - 96 * d for d in range(1, days + 1)])).fetchall()
    return rows


def judge_region(name, lat, lon, aircraft, now, con):
    slot = now // 900
    civil, reporting, poor = region_stats(aircraft)
    with db._lock:
        con.execute("INSERT OR REPLACE INTO airspace VALUES (?,?,?,?,?)", (name, slot, len(civil), len(reporting), len(poor)))
        con.commit()
    out = []
    past = usual(con, name, slot, "civil")
    if len(past) >= MIN_DAYS:
        base = statistics.median(r["v"] for r in past)
        if base >= DROP_MIN_USUAL and len(civil) < DROP_RATIO * base:
            out.append({
                "uid": f"adsb:drop:{name}:{now // 3600}", "source": NAME, "kind": "airspace_drop",
                "lat": lat, "lon": lon, "place": name,
                "title": f"Airspace emptying over {name}: {len(civil)} civil aircraft, usually about {round(base)} at this time",
                "url": f"https://adsb.lol/?lat={lat}&lon={lon}&zoom=6", "score": 3.0, "observed_at": now,
                "extra": {"civil": len(civil), "usual": base, "region": name},
            })
    pastj = [r for r in usual(con, name, slot, "poor") if r["reporting"]]
    if len(pastj) >= MIN_DAYS and len(reporting) >= JAM_MIN_AIRCRAFT:
        share = len(poor) / len(reporting)
        base = statistics.median(r["v"] / r["reporting"] for r in pastj)
        if share >= base + JAM_JUMP:
            plat = sum(a["lat"] for a in poor) / len(poor)
            plon = sum(a["lon"] for a in poor) / len(poor)
            out.append({
                "uid": f"adsb:jam:{name}:{now // 3600}", "source": NAME, "kind": "gps_jamming",
                "lat": plat, "lon": plon, "place": name,
                "title": f"Possible GPS jamming near {name}: {len(poor)} of {len(reporting)} aircraft with poor position accuracy (usually {round(base * 100)}%)",
                "url": f"https://gpsjam.org/?lat={plat:.1f}&lon={plon:.1f}&z=6", "score": 2.5, "observed_at": now,
                "extra": {"poor": len(poor), "reporting": len(reporting), "usual_share": round(base, 3), "region": name},
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


def fetch_regions(deadline_s=150):
    """Flashpoint regions, run as their own job so a slow region never holds up squawks."""
    now, started = int(time.time()), time.time()
    raws, signals, failed = [], [], []
    con = _db()
    for name, (lat, lon) in REGIONS.items():
        if time.time() - started > deadline_s:
            failed.append(f"{name}: skipped, out of time")
            continue
        try:
            raw = get(f"{BASE}/point/{lat}/{lon}/{RADIUS_NM}", timeout=20)
            raws.append(raw)
            signals += judge_region(name, lat, lon, parse_aircraft(raw), now, con)
        except Exception as e:   # one region failing shouldn't lose the rest
            failed.append(f"{name}: {e}")
        time.sleep(3)   # be gentle with the community feed: it slows down rapid callers
    with db._lock:
        con.execute("DELETE FROM airspace WHERE slot < ?", (now // 900 - 96 * 14,))
        con.commit()
    LAST["failed"] = failed
    if len(failed) == len(REGIONS):
        raise RuntimeError(f"all flashpoint regions failed, e.g. {failed[0]}")
    return b"\n".join(raws), signals
