"""Shipping tripwire: AIS ship positions at the world's chokepoints (aisstream.io).

Counts the distinct ships seen in each chokepoint every 15 minutes and compares
with the same 15 minutes on previous days. A sharp drop means ships are holding
back or rerouting, which usually happens before the reason is in the news
(Houthi attacks in Bab el-Mandeb, closures of the Kerch Strait or Hormuz).

If every chokepoint drops at once, it's the feed, not the world: no signals.
Needs a free aisstream.io API key in config.ini ([ais] key).
"""
import json
import statistics
import threading
import time

from .. import db
from ..ws import WebSocket

NAME = "ais"
STREAM = "wss://stream.aisstream.io/v0/stream"
CHOKEPOINTS = {   # name: ((lat_min, lon_min), (lat_max, lon_max))
    "Bab el-Mandeb": ((12.0, 42.8), (13.4, 44.0)),
    "Red Sea off Hodeidah": ((14.5, 41.5), (16.0, 42.8)),
    "Strait of Hormuz": ((25.8, 55.8), (27.0, 57.2)),
    "Suez Canal": ((29.8, 32.2), (31.4, 32.7)),
    "Bosporus": ((40.95, 28.9), (41.3, 29.2)),
    "Kerch Strait": ((45.0, 36.3), (45.5, 36.8)),
    "Odesa approaches": ((45.8, 30.3), (46.6, 31.2)),
    "Gulf of Finland": ((59.5, 24.5), (60.3, 27.5)),
    "Taiwan Strait": ((23.5, 119.0), (25.5, 120.5)),
    "Singapore Strait": ((1.0, 103.4), (1.5, 104.2)),
}
SLOT = 900
DROP_RATIO = 0.4        # ships below this share of the usual count = traffic holding back
MIN_USUAL = 6           # ignore chokepoints that are quiet at this hour anyway
FEED_FLOOR = 0.3        # all chokepoints together below this share = a feed problem
MIN_DAYS = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS shipping (
    point TEXT, slot INTEGER, ships INTEGER, PRIMARY KEY (point, slot)
);
"""


def where(lat, lon):
    for name, ((a, b), (c, d)) in CHOKEPOINTS.items():
        if a <= lat <= c and b <= lon <= d:
            return name
    return None


def centre(name):
    (a, b), (c, d) = CHOKEPOINTS[name]
    return (a + c) / 2, (b + d) / 2


def usual(con, point, slot, days=7):
    rows = con.execute(f"SELECT ships FROM shipping WHERE point=? AND slot IN ({','.join('?' * days)})",
                       (point, *[slot - 96 * d for d in range(1, days + 1)])).fetchall()
    return [r[0] for r in rows]


def judge_slot(con, slot, counts):
    """counts: {chokepoint: distinct ships} for a finished slot. Stores them, returns signals."""
    with db._lock:
        con.executemany("INSERT OR REPLACE INTO shipping VALUES (?,?,?)", [(p, slot, counts.get(p, 0)) for p in CHOKEPOINTS])
        con.commit()
    bases = {p: usual(con, p, slot) for p in CHOKEPOINTS}
    have = {p: statistics.median(v) for p, v in bases.items() if len(v) >= MIN_DAYS}
    if not have:
        return []
    total_now = sum(counts.get(p, 0) for p in have)
    total_usual = sum(have.values())
    if total_usual and total_now < FEED_FLOOR * total_usual:
        return []   # everything dropped at once: the feed, not the world
    out, now = [], (slot + 1) * SLOT
    for p, base in have.items():
        n = counts.get(p, 0)
        if base >= MIN_USUAL and n < DROP_RATIO * base:
            lat, lon = centre(p)
            out.append({
                "uid": f"ais:drop:{p}:{now // 3600}", "source": NAME, "kind": "shipping_drop",
                "lat": lat, "lon": lon, "place": p,
                "title": f"Shipping holding back at {p}: {n} ships in 15 minutes, usually about {round(base)}",
                "url": f"https://www.marinetraffic.com/en/ais/home/centerx:{lon:.1f}/centery:{lat:.1f}/zoom:9",
                "score": 3.0, "observed_at": now,
                "extra": {"ships": n, "usual": base, "chokepoint": p},
            })
    return out


class Counter:
    def __init__(self):
        self.slot = int(time.time()) // SLOT
        self.seen = {p: set() for p in CHOKEPOINTS}
        self.messages = 0

    def add(self, mmsi, lat, lon, ts=None):
        """Returns (finished_slot, counts) when a slot rolls over, else None."""
        slot = int(ts or time.time()) // SLOT
        done = None
        if slot != self.slot:
            done = (self.slot, {p: len(s) for p, s in self.seen.items()})
            self.slot, self.seen = slot, {p: set() for p in CHOKEPOINTS}
        p = where(lat, lon)
        if p and mmsi:
            self.seen[p].add(mmsi)
        self.messages += 1
        return done


def subscription(key):
    return json.dumps({"APIKey": key,
                       "BoundingBoxes": [[[a, b], [c, d]] for (a, b), (c, d) in CHOKEPOINTS.values()],
                       "FilterMessageTypes": ["PositionReport", "StandardClassBPositionReport"]})


def stream(key, on_signals, on_status, on_error=print):
    """Runs forever. on_signals(list, raw_bytes); on_status(message) once per slot."""
    con = db.connect()
    con.executescript(SCHEMA)
    counter, backoff = Counter(), 30
    while True:
        ws = None
        try:
            ws = WebSocket(STREAM, timeout=120)
            ws.send(subscription(key))
            backoff = 30
            while True:
                msg = json.loads(ws.recv())
                if msg.get("error"):
                    raise RuntimeError(msg["error"])
                meta = msg.get("MetaData") or {}
                lat, lon = meta.get("latitude"), meta.get("longitude")
                if lat is None or lon is None:
                    continue
                done = counter.add(meta.get("MMSI"), lat, lon)
                if done:
                    slot, counts = done
                    sigs = judge_slot(con, slot, counts)
                    on_signals(sigs, json.dumps({"slot": slot, "counts": counts}).encode())
                    on_status(f"{sum(counts.values())} ships across {sum(1 for v in counts.values() if v)}/{len(CHOKEPOINTS)} chokepoints in 15 min"
                              + (f", {len(sigs)} drop(s)" if sigs else ""))
        except Exception as e:
            on_error(f"ais stream dropped: {e}; reconnecting in {backoff}s")
            time.sleep(backoff)
            backoff = min(backoff * 2, 900)
        finally:
            if ws:
                ws.close()
