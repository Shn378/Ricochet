"""Unfiltered ADS-B from adsb.fi open data (community feed, readsb JSON format).

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
            if not db.get_meta(_con, "airspace_provider_v3"):
                # Regions moved to adsb.fi: start their history again on the new network
                with db._lock:
                    _con.execute("DELETE FROM airspace")
                    _con.commit()
                db.set_meta(_con, "airspace_provider_v3", int(time.time()))
        return _con

NAME = "adsb"
# adsb.fi open data: non-commercial use, 1 request a second, credit adsb.fi with a link.
# (adsb.lol rate-limited this server; airplanes.live asks to be contacted first.)
# Always the same network for regions: receiver coverage differs between networks,
# so switching would look like traffic rising and falling.
BASE = "https://opendata.adsb.fi/api"
REGION_BASE = BASE
_cooldown = {}   # provider base -> unix time it may be asked again (also kept in the database across restarts)
_pace_lock, _last_call = threading.Lock(), {}
MIN_GAP = 1.5    # seconds between any two calls to the same host, across both jobs


def polite_get(base, path, timeout):
    """GET from a community feed, backing off when it says we're asking too often."""
    import urllib.error
    host = base.split("/")[2]
    if base not in _cooldown:
        _cooldown[base] = float(db.get_meta(_db(), f"cooldown:{host}", 0))
    wait = _cooldown[base] - time.time()
    if wait > 0:
        raise RuntimeError(f"rate limited by {base.split('/')[2]}, resting {int(wait // 60) + 1} more min")
    with _pace_lock:   # squawks and regions run as separate jobs: keep them from overlapping
        gap = _last_call.get(host, 0) + MIN_GAP - time.time()
        if gap > 0:
            time.sleep(gap)
        _last_call[host] = time.time()
    try:
        return get(f"{base}{path}", timeout=timeout)
    except urllib.error.HTTPError as e:
        if e.code in (429, 403):
            try:
                rest = int(e.headers.get("Retry-After") or 0)
            except ValueError:
                rest = 0
            # 429: asking too often. 403: refused outright, so ask far less often.
            _cooldown[base] = time.time() + max(rest, 15 * 60 if e.code == 429 else 6 * 3600)
            db.set_meta(_db(), f"cooldown:{host}", _cooldown[base])
        raise
SQUAWKS = {"7500": "hijack code", "7600": "radio failure", "7700": "general emergency"}
FETCH_SQUAWKS = ["7500", "7700"]   # 7600 dropped to ease the load; 7700 kept as map context


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
            "url": f"https://globe.adsb.fi/?icao={a.get('hex')}",
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
            "url": f"https://globe.adsb.fi/?lat={la + 2.5}&lon={lo + 2.5}&zoom=6",
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
                "url": f"https://globe.adsb.fi/?lat={lat}&lon={lon}&zoom=6", "score": 3.0, "observed_at": now,
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
    for code in FETCH_SQUAWKS:
        raw = polite_get(BASE, f"/v2/sqk/{code}", 30)
        raws.append(raw)
        signals += squawk_signals(parse_aircraft(raw), code, now)
    raw = polite_get(BASE, "/v2/mil", 60)
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
            raw = polite_get(REGION_BASE, f"/v3/lat/{lat}/lon/{lon}/dist/{RADIUS_NM}", 20)
            raws.append(raw)
            signals += judge_region(name, lat, lon, parse_aircraft(raw), now, con)
        except Exception as e:   # one region failing shouldn't lose the rest
            failed.append(f"{name}: {e}")

    with db._lock:
        con.execute("DELETE FROM airspace WHERE slot < ?", (now // 900 - 96 * 14,))
        con.commit()
    LAST["failed"] = failed
    if len(failed) == len(REGIONS):
        raise RuntimeError(f"all flashpoint regions failed, e.g. {failed[0]}")
    return b"\n".join(raws), signals
