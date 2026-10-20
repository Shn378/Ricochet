"""The attention budget: a fixed number of feed checks per minute, shared out by heat.

Adding feeds never makes Ricochet slower or heavier. It only changes where the
checks go. Quiet countries' feeds are looked at every half hour; countries with
an open hotspot or an escalating situation every couple of minutes; leased
incident sources (layer 3, added later by the scout) fastest of all.

Feeds also look after themselves:
- "failing": errors on the last few checks (shown on the health panel)
- "dead": failing for 3 days, no longer checked until it recovers or is replaced
- "silent": nothing new published for 14 days
"""
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import db, places, situations
from .collectors import news

BACKBONE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backbone.json")

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id          INTEGER PRIMARY KEY,
    url         TEXT UNIQUE NOT NULL,
    name        TEXT,
    country     TEXT,              -- ISO2, or NULL for world desks
    layer       INTEGER NOT NULL,  -- 2 backbone, 3 leased incident source, 4 earned, 5 probation
    status      TEXT NOT NULL DEFAULT 'active',   -- active, failing, dead, retired
    lease_until INTEGER,           -- layer 3 only
    lease_cell  TEXT,
    last_checked INTEGER DEFAULT 0,
    last_ok     INTEGER,
    last_new    INTEGER,           -- newest item publish time seen
    fails       INTEGER DEFAULT 0,
    last_error  TEXT,
    checks      INTEGER DEFAULT 0,
    signals     INTEGER DEFAULT 0,
    added       INTEGER
);
"""


class Budget:
    def __init__(self, cfg, log=print):
        self.per_min = cfg.getint("news", "budget_per_min", fallback=30)
        self.quiet = cfg.getint("news", "quiet_minutes", fallback=30) * 60
        self.hot = cfg.getint("news", "hot_minutes", fallback=2) * 60
        self.world = cfg.getint("news", "world_desk_minutes", fallback=5) * 60
        self.lease = 60
        self.log = log
        self.con = db.connect()
        self.con.executescript(SCHEMA)
        self.hot_countries = set()
        self.tokens = 0.0
        self.pool = ThreadPoolExecutor(max_workers=4)
        self.inflight = set()
        self.lock = threading.Lock()
        self.load_backbone()
        self.migrate()

    def migrate(self):
        """Placement rules improved: clear news signals once so feeds re-read them with the new rules."""
        if db.get_meta(self.con, "news_rules_v2"):
            return
        with db._lock:
            self.con.execute("DELETE FROM signals WHERE source='news'")
            self.con.execute("DELETE FROM buckets WHERE source='news'")
            self.con.execute("UPDATE sources SET last_checked=0, signals=0")
            self.con.commit()
        db.set_meta(self.con, "news_rules_v2", int(time.time()))

    def load_backbone(self):
        with open(BACKBONE, encoding="utf-8") as f:
            feeds = json.load(f)["feeds"]
        now = int(time.time())
        with db._lock:
            for fd in feeds:
                layer = 5 if fd.get("probation") else 2
                self.con.execute("""INSERT INTO sources (url, name, country, layer, added) VALUES (?,?,?,?,?)
                                    ON CONFLICT(url) DO UPDATE SET name=excluded.name, country=excluded.country, layer=excluded.layer""",
                                 (fd["url"], fd["name"], fd.get("country"), layer, now))
            # Feeds taken out of backbone.json are retired, not deleted, so their history stays
            urls = [fd["url"] for fd in feeds]
            self.con.execute(f"UPDATE sources SET status='retired' WHERE layer IN (2, 5) AND url NOT IN ({','.join('?' * len(urls))})", urls)
            self.con.execute(f"UPDATE sources SET status='active', fails=0 WHERE layer IN (2, 5) AND status='retired' AND url IN ({','.join('?' * len(urls))})", urls)
            self.con.commit()

    # --- heat -------------------------------------------------------------
    def refresh_heat(self):
        """Countries that deserve fast checks: escalating/ongoing situations and open hotspots."""
        hot = set()
        try:
            for c in situations.compute(self.con)["countries"]:
                if c["ongoing"]:
                    iso = places.FIPS_TO_ISO.get(c["country"])
                    if iso:
                        hot.add(iso)
        except Exception as e:
            self.log(f"[attention] situations unavailable: {e}")
        try:
            from .server import hotspots
            for h in hotspots(self.con, 6):
                cc = places.nearest_country(h["lat"], h["lon"])
                if cc:
                    hot.add(cc)
        except Exception as e:
            self.log(f"[attention] hotspots unavailable: {e}")
        self.hot_countries = hot

    def interval(self, s):
        if s["layer"] == 3:
            return self.lease
        if s["country"] is None:
            return self.world
        return self.hot if s["country"] in self.hot_countries else self.quiet

    # --- checking ---------------------------------------------------------
    def due(self, now):
        rows = [dict(r) for r in self.con.execute(
            "SELECT * FROM sources WHERE status IN ('active', 'failing') AND (lease_until IS NULL OR lease_until > ?)", (now,))]
        out = []
        for s in rows:
            if s["id"] in self.inflight:
                continue
            iv = self.interval(s)
            if s["fails"]:  # back off failing feeds: 2, 4, 8... times the interval, up to 6 hours
                iv = min(iv * 2 ** min(s["fails"], 6), 6 * 3600)
            late = (now - (s["last_checked"] or 0)) / iv
            if late >= 1:
                out.append((late, s))
        out.sort(key=lambda x: -x[0])
        return [s for _, s in out]

    def check(self, s):
        now = int(time.time())
        feed = {"name": s["name"], "country": s["country"], "layer": s["layer"]}
        try:
            raw, items, sigs, newest = news.fetch(dict(feed, url=s["url"]), now)
            if s["layer"] == 5:
                # Probation: shown on cards and the map, tallied, but never counted towards a hotspot
                for sg in sigs:
                    sg["kind"] = "probation_report"
                    sg.setdefault("extra", {})["probation"] = True
            snap, sha = db.save_snapshot("news", raw)
            added = db.add_signals(self.con, sigs, snap, sha)
            with db._lock:
                self.con.execute("""UPDATE sources SET last_checked=?, last_ok=?, fails=0, last_error=NULL, status='active',
                                    checks=checks+1, signals=signals+?, last_new=MAX(COALESCE(last_new,0), ?) WHERE id=?""",
                                 (now, now, added, newest or 0, s["id"]))
                self.con.commit()
            if added:
                self.log(f"[news] {s['name']}: {added} new incident report(s)")
        except Exception as e:
            dead = s["last_ok"] is None and s["checks"] >= 5 or (s["last_ok"] and now - s["last_ok"] > 3 * 86400)
            with db._lock:
                self.con.execute("UPDATE sources SET last_checked=?, fails=fails+1, last_error=?, checks=checks+1, status=? WHERE id=?",
                                 (now, str(e)[:200], "dead" if dead else "failing", s["id"]))
                self.con.commit()
            if s["fails"] == 0:
                self.log(f"[news] {s['name']} failing: {e}")
        finally:
            with self.lock:
                self.inflight.discard(s["id"])

    def run(self):
        last_heat, last_tick = 0, time.time()
        self.tokens = self.per_min / 6    # small head start
        while True:
            try:
                now = time.time()
                if now - last_heat > 120:
                    self.refresh_heat()
                    last_heat = now
                self.tokens = min(self.per_min, self.tokens + (now - last_tick) * self.per_min / 60)
                last_tick = now
                for s in self.due(int(now)):
                    if self.tokens < 1:
                        break
                    self.tokens -= 1
                    with self.lock:
                        self.inflight.add(s["id"])
                    self.pool.submit(self.check, s)
            except Exception as e:
                self.log(f"[attention] loop error: {e}")
            time.sleep(5)


def status(con):
    con.executescript(SCHEMA)
    now = int(time.time())
    rows = [dict(r) for r in con.execute("SELECT * FROM sources ORDER BY layer, country, name")]
    for r in rows:
        r["silent"] = bool(r["last_new"]) and now - r["last_new"] > 14 * 86400
    return rows
