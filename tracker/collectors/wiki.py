"""Wikipedia live edit stream (Wikimedia EventStreams).

Two tripwires:
- edit bursts: a place article suddenly edited by several people at once
- new articles whose titles look like an incident ("2026 Xville explosion")

Only articles with coordinates become signals, so they can be placed on the map.
Runs as a long-lived stream rather than a poll.
"""
import json
import re
import time
import urllib.parse
from collections import defaultdict, deque

from ..net import get, USER_AGENT
import urllib.request

NAME = "wiki"
STREAM = "https://stream.wikimedia.org/v2/stream/recentchange"
WIKIS = {"enwiki", "ukwiki", "ruwiki", "arwiki", "hewiki", "fawiki", "zhwiki", "frwiki", "dewiki"}
INCIDENT = re.compile(
    r"\b(attack|attacks|bombing|explosion|airstrike|air strike|strike|strikes|shelling|shooting|massacre|"
    r"offensive|siege|clashes|ramming|hostage|drone|missile|assassination|stabbing|terror|terrorist|car attack|riots|"
    r"fire|wildfire|disaster|crash|collapse|derailment|blast|"
    r"explosión|атака|удар|обстріл|вибух|взрыв)\b", re.I)
# Edit bursts come in tiers: a big, sustained burst means more than a quick flurry.
# (edits, distinct editors, window in seconds, score)
TIERS = [(6, 3, 1800, 1.5), (15, 5, 7200, 2.5), (30, 8, 7200, 4.0)]
WINDOW = max(w for _, _, w, _ in TIERS)


class BurstTracker:
    def __init__(self):
        self.edits = defaultdict(deque)   # (wiki, title) -> deque[(ts, user)]
        self.fired = {}                    # (wiki, title, tier) -> ts last fired

    def add(self, wiki, title, user, ts):
        """Returns (edits, editors, tier, score) when a tier is crossed, else None."""
        key = (wiki, title)
        q = self.edits[key]
        q.append((ts, user))
        while q and q[0][0] < ts - WINDOW:
            q.popleft()
        hit = None
        for tier, (n, u, w, score) in enumerate(TIERS):
            recent = [x for x in q if x[0] > ts - w]
            users = {x[1] for x in recent}
            last = self.fired.get((wiki, title, tier))
            if len(recent) >= n and len(users) >= u and (last is None or ts - last > 6 * 3600):
                self.fired[(wiki, title, tier)] = ts
                hit = (len(recent), len(users), tier, score)
        return hit

    def tidy(self, now):
        for k in [k for k, q in self.edits.items() if not q or q[-1][0] < now - WINDOW]:
            del self.edits[k]


def coordinates(server, title):
    q = urllib.parse.urlencode({"action": "query", "prop": "coordinates", "titles": title, "format": "json", "formatversion": 2})
    doc = json.loads(get(f"https://{server}/w/api.php?{q}", timeout=20))
    for p in doc.get("query", {}).get("pages", []):
        for c in p.get("coordinates") or []:
            return c["lat"], c["lon"]
    return None


def signal_for(ev, kind, detail, coords, score=None, tier=0):
    server, title = ev["server_name"], ev["title"]
    return {
        "uid": f"wiki:{kind}{tier or ''}:{ev['wiki']}:{title}:{int(ev['timestamp']) // 3600}",
        "source": NAME, "kind": kind,
        "lat": coords[0], "lon": coords[1],
        "place": title,
        "title": f"{detail}: {title} ({ev['wiki']})",
        "url": f"https://{server}/wiki/{urllib.parse.quote(title.replace(' ', '_'))}",
        "score": score or (2 if kind == "new_incident_article" else 1.5),
        "observed_at": int(ev["timestamp"]),
    }


def handle(ev, tracker, lookup=coordinates):
    """Process one stream event. Returns a signal or None."""
    if ev.get("wiki") not in WIKIS or ev.get("namespace") != 0 or ev.get("bot"):
        return None
    title, ts = ev.get("title", ""), int(ev.get("timestamp") or time.time())
    if ev.get("type") == "new" and INCIDENT.search(title):
        c = lookup(ev["server_name"], title)
        # New incident articles often lack coordinates at first; keep them anyway without a point
        return signal_for(ev, "new_incident_article", "New incident article", c or (None, None))
    if ev.get("type") == "edit":
        hit = tracker.add(ev["wiki"], title, ev.get("user"), ts)
        if hit:
            c = lookup(ev["server_name"], title)
            if c:
                return signal_for(ev, "edit_burst", f"Edit burst, {hit[0]} edits by {hit[1]} editors", c, score=hit[3], tier=hit[2])
    return None


def stream(on_signal, on_error=print, on_status=None):
    """Blocks forever, reconnecting on errors. on_signal(signal, raw_line_bytes).
    on_status(message) every 15 minutes, so a quiet hour doesn't look like a dead stream."""
    tracker = BurstTracker()
    seen, last_beat = 0, time.time()
    while True:
        try:
            req = urllib.request.Request(STREAM, headers={"User-Agent": USER_AGENT, "Accept": "text/event-stream"})
            with urllib.request.urlopen(req, timeout=120) as r:
                last_tidy = time.time()
                for line in r:
                    if not line.startswith(b"data:"):
                        continue
                    try:
                        ev = json.loads(line[5:])
                    except ValueError:
                        continue
                    seen += 1
                    if on_status and time.time() - last_beat > 900:
                        on_status(f"stream live: {seen} edits checked in the last 15 min")
                        seen, last_beat = 0, time.time()
                    try:
                        s = handle(ev, tracker)
                    except Exception as e:  # a failed coordinate lookup shouldn't kill the stream
                        on_error(f"wiki lookup failed: {e}")
                        s = None
                    if s:
                        on_signal(s, line)
                    if time.time() - last_tidy > 300:
                        tracker.tidy(time.time())
                        last_tidy = time.time()
        except Exception as e:
            on_error(f"wiki stream dropped: {e}; reconnecting in 30s")
            time.sleep(30)
