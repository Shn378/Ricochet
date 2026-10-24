"""Local web server: the globe page plus a small JSON API.

/api/hotspots?hours=6   cells where signals stack up, strongest first
/api/signals?hours=24   raw signals (optionally &cell=lat:lon)
/api/health             last run of each collector
"""
import json
import os
import threading
import time
from collections import defaultdict
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import baseline, db, known_events, rules, situations

WEB = os.path.join(db.ROOT, "web")
SOURCES = ["news", "gdelt", "ioda", "radar", "infra", "wiki", "firms", "adsb", "ais", "seismic", "hazard"]
PRESENCE_ONLY = db.CONTEXT_KINDS   # context layer, never makes a hotspot


def rows(con, sql, args=()):
    return [dict(r) for r in con.execute(sql, args).fetchall()]


_cache = {}


conflict_zone = rules.conflict_zone


_threat = {}


def threat_level(iso):
    """National threat level for a country card, from the hand-kept table (or None)."""
    if not _threat:
        try:
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "threat_levels.json"), encoding="utf-8") as f:
                _threat.update(json.load(f)["levels"])
        except (OSError, ValueError, KeyError):
            _threat["_"] = None
    return _threat.get(iso) if iso else None


def hotspots(con, hours=6, limit=40, watch=False):
    """Cached for a minute: the baseline maths is the heavy part on a small machine.
    "watch" items (lone fires at bases waiting for a second source) are left off the globe."""
    key = ("hotspots", hours)
    hit = _cache.get(key)
    if not hit or time.time() - hit[0] >= 60:
        hit = (time.time(), _hotspots(con, hours, MAX_HOTSPOTS))   # one list for every caller, sliced
        _cache[key] = hit
    return [h for h in hit[1] if watch or h["level"] != "watch"][:limit]


MAX_HOTSPOTS = 240
MAX_HOURS = 168
# Seconds a polled API answer is shared between visitors. /api/near is a full uncached pass.
SHARED_CACHE = {"/api/health": 20, "/api/signals": 20, "/api/situations": 20, "/api/sources": 30, "/api/near": 60}
_responses = {}


def warm(log=print):
    """Keep the globe's answers ready: work out hotspots and situations every 50 s in the
    background, so a visitor never waits for the baseline maths (slow on the e2-micro)."""
    con = db.connect()
    while True:
        try:
            # compute, then swap in: visitors keep getting the previous answer meanwhile
            if time.time() - _cache.get("situations", (0,))[0] > 240:
                _cache["situations"] = (time.time(), situations.compute(con))
            _cache[("hotspots", 6)] = (time.time(), _hotspots(con, 6, MAX_HOTSPOTS))
        except Exception as e:
            log(f"[warm] failed: {e}")
        time.sleep(50)


def _country_of(cell, items):
    return rules.country_of(cell, items)


def confidence(level, feeds, site, expected_event):
    """0-100: how much a person should care, used to order the tour.
    Level sets the floor; independent feeds above normal, how far above, a
    sensitive site and corroboration raise it; an expected event lowers it."""
    if level == "watch":
        return 5
    active = [f for f in feeds.values() if rules.above_normal(f)]
    zmax = max((f.get("z", 0) for f in feeds.values()), default=0)
    c = 40 if level == "reported" else 15
    c += 12 * max(0, len(active) - 1)              # each extra independent feed
    c += min(25, 4 * max(0.0, zmax - 3))           # how far above normal
    c += 8 if site else 0
    c -= 20 if expected_event else 0
    return int(max(1, min(100, c)))


def _situations(con):
    hit = _cache.get("situations")
    if not hit or time.time() - hit[0] > 300:
        hit = (time.time(), situations.compute(con))
        _cache["situations"] = hit
    return hit[1]


def hot_countries(con):
    """ISO codes of countries with an ongoing or escalating situation."""
    from .places import FIPS_TO_ISO
    try:
        return {FIPS_TO_ISO[c["country"]] for c in _situations(con)["countries"] if c["ongoing"] and c["country"] in FIPS_TO_ISO}
    except Exception:
        return None


def _hotspots(con, hours=6, limit=40, debug=None):
    """Areas running above their normal level.

    Reported: two or more feeds active in the area and at least one well above normal.
    Signal:   one feed far above normal on its own (fires need a very big jump,
              because seasonal burning varies a lot).
    """
    since = int(time.time()) - hours * 3600
    sigs = rows(con, "SELECT * FROM signals WHERE observed_at >= ? AND cell IS NOT NULL ORDER BY observed_at", (since,))
    cells = defaultdict(list)
    for s in sigs:
        if s["kind"] not in PRESENCE_ONLY:
            cells[s["cell"]].append(s)
    # Only the squares with signals (and their surroundings): reading every bucket for 4 weeks was the slow part
    expected, history = baseline.expectations(con, hours, cells=list(cells), median=rules.FLAGS["median_baseline"])
    hot_cc = hot_countries(con)
    texts = defaultdict(list)
    for c, its in cells.items():
        for s in its:
            texts[c].append((s["source"], f"{s.get('title') or ''} {s.get('url') or ''}"))
    out = []
    for cell, items in cells.items():
        nearby = [x for n in rules.neighbours(cell) for x in texts.get(n, ())]
        ev = rules.evaluate(cell, items, expected, history, hot_cc, nearby)
        level, reason, site, feeds, hot, expected_event = ev["level"], ev["reason"], ev["site"], ev["feeds"], ev["hot"], ev["expected_event"]
        if debug is not None:
            debug.append({"cell": cell, "feeds": feeds, "lat": items[0]["lat"], "lon": items[0]["lon"], "decision": level, "reason": reason,
                          "place": next((s["place"] for s in items if s.get("place")), None)})
        if not level:
            continue
        best = max(items, key=lambda s: s["score"] or 1)
        place = next((s["place"] for s in reversed(items) if s.get("place")), None)
        out.append({
            "cell": cell,
            "lat": sum(s["lat"] for s in items) / len(items),
            "lon": sum(s["lon"] for s in items) / len(items),
            "level": level,
            "sources": sorted(feeds),
            "elevated": sorted(hot),
            "feeds": feeds,
            "score": round(max(f["z"] for f in feeds.values()), 1),
            "confidence": confidence(level, feeds, site, expected_event),
            "count": len(items),
            "place": place,
            "headline": best["title"],
            "reason": reason,
            "expected": {"name": expected_event["name"], "note": expected_event["note"]} if expected_event else None,
            "site": site,
            "country": (cc := rules.country_of(cell, items)),
            "threat_level": threat_level(cc),
            "first": items[0]["observed_at"],
            "last": items[-1]["observed_at"],
        })
    out.extend(_alert_hotspots(con, {h["cell"] for h in out}))
    out.sort(key=lambda h: h["confidence"], reverse=True)
    return out[:limit]


ALERT_CONFIDENCE = 60   # an official red alert: high on the tour, below a well-backed security incident


def _alert_hotspots(con, taken):
    """Official red alerts (GDACS red, tsunami warnings, Met Office red) as their own
    "hazard" level. A square that already has a security hotspot keeps it."""
    since = int(time.time()) - rules.ALERT_HOURS * 3600
    by_cell = defaultdict(list)
    for s in rows(con, "SELECT * FROM signals WHERE source='hazard' AND observed_at >= ? AND cell IS NOT NULL ORDER BY observed_at", (since,)):
        if s["cell"] not in taken and rules.official_alert(s):
            by_cell[s["cell"]].append(s)
    out = []
    for cell, items in by_cell.items():
        last = items[-1]
        credit = rules._extra(last).get("credit") or "official source"
        out.append({
            "cell": cell, "lat": last["lat"], "lon": last["lon"], "level": "hazard",
            "sources": ["hazard"], "elevated": ["hazard"],
            "feeds": {"hazard": {"observed": len(items), "n": len(items), "elevated": True, "baseline": False, "z": 0}},
            "score": 0, "confidence": ALERT_CONFIDENCE, "count": len(items),
            "place": last["place"], "headline": last["title"],
            "reason": f"official red-level alert from {credit} (natural hazard, not a security incident)",
            "expected": None, "site": None,
            "country": last["country"] or rules.country_of(cell, items), "threat_level": None,   # a terror threat level says nothing about a storm
            "first": items[0]["observed_at"], "last": last["observed_at"],
        })
    return out


def health(con):
    out = {}
    try:
        from . import attention
        feeds = [f for f in attention.status(con) if f["status"] != "retired"]
        ok = [f for f in feeds if f["status"] == "active" and f["last_ok"]]
        bad = [f for f in feeds if f["status"] in ("failing", "dead")]
        n24 = con.execute("SELECT COUNT(*) FROM signals WHERE source='news' AND fetched_at >= ?", (int(time.time()) - 86400,)).fetchone()[0]
        out["news"] = {"last_run": max((f["last_checked"] or 0 for f in feeds), default=None) or None, "ok": bool(ok) if feeds else None,
                       "message": f"{len(ok)} of {len(feeds)} feeds working" + (f", {len(bad)} failing: " + ", ".join(f["name"] for f in bad[:6]) if bad else ""),
                       "last_ok": max((f["last_ok"] or 0 for f in feeds), default=None) or None, "signals_24h": n24,
                       "feeds": len(feeds), "feeds_ok": len(ok)}
    except Exception as e:
        out["news"] = {"last_run": None, "ok": False, "message": str(e), "last_ok": None, "signals_24h": 0}
    for src in SOURCES + ["airspace"]:
        if src == "news":
            continue
        last = con.execute("SELECT * FROM runs WHERE source=? ORDER BY started DESC LIMIT 1", (src,)).fetchone()
        last_ok = con.execute("SELECT started FROM runs WHERE source=? AND ok=1 ORDER BY started DESC LIMIT 1", (src,)).fetchone()
        n24 = con.execute("SELECT COUNT(*) FROM signals WHERE source=? AND fetched_at >= ?", (src, int(time.time()) - 86400)).fetchone()[0]
        out[src] = {
            "last_run": last["started"] if last else None,
            "ok": bool(last["ok"]) if last else None,
            "message": last["message"] if last else "never run",
            "last_ok": last_ok["started"] if last_ok else None,
            "signals_24h": n24,
        }
    return out


class Handler(SimpleHTTPRequestHandler):
    con = None

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=WEB, **kw)

    def log_message(self, *a):
        pass

    def _local(self):
        """True only for a request made on this machine. Requests relayed by Cloudflare's tunnel
        also arrive from 127.0.0.1, so any proxy header means it came from outside."""
        if self.client_address[0] not in ("127.0.0.1", "::1"):
            return False
        return not any(self.headers.get(h) for h in ("Cf-Connecting-Ip", "X-Forwarded-For", "Cf-Ray", "X-Real-Ip"))

    def _json(self, obj):
        body = json.dumps(obj).encode()
        if getattr(self, "_key", None):
            if len(_responses) > 500:   # odd query strings can't grow it without limit
                _responses.clear()
            _responses[self._key] = (time.time(), body)
        self._send(body)

    def _send(self, body):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items() if k != "v"}   # ?v= is only a cache-buster
        if "hours" in q:   # a week at most: longer windows are slow scans on the e2-micro
            q["hours"] = str(max(1, min(int(q["hours"]) if q["hours"].isdigit() else 6, MAX_HOURS)))
        self._key = None
        if u.path in SHARED_CACHE:
            # Every open page polls these: answer from one copy for a few seconds, not one query per visitor
            self._key = (u.path, tuple(sorted(q.items())))
            hit = _responses.get(self._key)
            if hit and time.time() - hit[0] < SHARED_CACHE[u.path]:
                return self._send(hit[1])
        try:
            if u.path == "/api/hotspots":
                return self._json({"now": int(time.time()), "hotspots": hotspots(self.con, int(q.get("hours", 6)), min(int(q.get("limit", 40)), 200), watch=bool(q.get("watch")))})
            if u.path == "/api/situations":   # all=1 for the full country table (tuning)
                res = dict(_situations(self.con))
                if not q.get("all"):
                    res["countries"] = [c for c in res["countries"] if c["ongoing"]]
                from . import conflicts
                res["listed"] = conflicts.for_display()   # hand-kept wars, display only
                return self._json(res)
            if u.path == "/api/replay" and self._local():
                import importlib
                from . import replay
                importlib.reload(rules)      # pick up rule changes without restarting the engine
                importlib.reload(replay)
                args = (float(q["lat"]), float(q["lon"]), replay.parse_time(q["start"]), replay.parse_time(q["end"]),
                        [w for w in q.get("wiki", "").split("|") if w], [k for k in q.get("keywords", "").split(",") if k])
                from .run import load_config
                key = load_config().get("firms", "map_key").strip() or None
                threading.Thread(target=replay.run, args=args, kwargs={"firms_key": key}, daemon=True, name="replay").start()
                return self._json({"ok": True, "message": "Replay started; results in data/replay.json"})
            if u.path == "/api/sources":
                from . import attention
                return self._json({"now": int(time.time()), "sources": attention.status(self.con)})
            if u.path == "/api/near":   # tuning view: the areas closest to tripping
                dbg = []
                _hotspots(self.con, int(q.get("hours", 6)), 40, dbg)
                for d in dbg:
                    d["max_z"] = max(f["z"] for f in d["feeds"].values())
                dbg.sort(key=lambda d: d["max_z"], reverse=True)
                return self._json({"now": int(time.time()), "areas": dbg[:30]})
            if u.path == "/api/signals":
                since = int(time.time()) - int(q.get("hours", 24)) * 3600
                if q.get("cell"):
                    data = rows(self.con, "SELECT * FROM signals WHERE cell=? AND observed_at>=? ORDER BY observed_at", (q["cell"], since))
                elif q.get("feed") or q.get("source"):   # latest signals from one feed, for the health panel
                    # ("feed" as well as "source": some fetch tools strip ?source= as a tracking parameter)
                    data = rows(self.con, "SELECT * FROM signals WHERE source=? AND observed_at>=? ORDER BY observed_at DESC LIMIT ?",
                                (q.get("feed") or q["source"], since, min(int(q.get("limit", 10)), 50)))
                elif q.get("country"):
                    data = rows(self.con, "SELECT * FROM signals WHERE country=? AND source='gdelt' AND observed_at>=? ORDER BY observed_at DESC LIMIT 200", (q["country"], since))
                else:
                    if q.get("lite"):   # just what the globe draws: a much smaller download for phones
                        data = rows(self.con, "SELECT source, kind, lat, lon, title, url, observed_at, '{}' AS extra FROM signals "
                                              "WHERE observed_at>=? AND lat IS NOT NULL ORDER BY observed_at DESC LIMIT 3000", (since,))
                    else:
                        data = rows(self.con, "SELECT * FROM signals WHERE observed_at>=? AND lat IS NOT NULL ORDER BY observed_at DESC LIMIT 3000", (since,))
                for d in data:
                    d["extra"] = json.loads(d["extra"] or "{}")
                return self._json({"now": int(time.time()), "signals": data})
            if u.path == "/api/benchmark":   # rule-change scoreboard (tracker/benchmark.py)
                from . import benchmark
                try:
                    with open(benchmark.RESULT, encoding="utf-8") as f:
                        res = json.load(f)
                except OSError:
                    return self._json({"ok": False, "message": "No benchmark run yet"})
                if not q.get("detail"):
                    res.pop("detail", None)
                return self._json(res)
            if u.path == "/api/audit":   # deterministic audit: feed and collector health (tracker/audit.py)
                from . import audit
                try:
                    with open(audit.RESULT, encoding="utf-8") as f:
                        return self._json(json.load(f))
                except OSError:
                    return self._json({"ok": False, "message": "No audit run yet"})
            if u.path == "/api/health":
                return self._json({"now": int(time.time()), "sources": health(self.con), "baseline": baseline.status(self.con)})
        except Exception as e:
            self.send_error(500, str(e))
            return
        return super().do_GET()


def serve(port=8765, host="127.0.0.1", background=False):
    """Binds straight away (so a busy port fails loudly), then answers requests. In the background
    it returns the thread, letting the engine open the site before the slow start-up work."""
    Handler.con = db.connect()
    httpd = ThreadingHTTPServer((host, port), Handler)
    if not background:
        return httpd.serve_forever()
    t = threading.Thread(target=httpd.serve_forever, daemon=True, name="web")
    t.start()
    return t
