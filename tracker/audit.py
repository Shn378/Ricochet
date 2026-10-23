"""The deterministic half of the morning audit: feed and collector health, worked out on the
server every day before the 05:45 UK audit so that audit doesn't have to guess from the outside.

What it does:
- collectors: last good run against their schedule (stale = no good run for 3 intervals, min 1 h)
- news feeds: failing, dead, silent (no new items for 14 days), probation tallies
- safe repair, done automatically: a feed refusing us (HTTP 403 or 429) with no good fetch for
  3+ days is retired, never worked around (CLAUDE.md principle). It's listed in
  data/auto_retired.json, which load_backbone respects, and flagged in the report so Claude also
  takes it out of backbone.json in git.
Nothing else is changed: rules, thresholds, trust, keys and config stay proposals for Shane.

Results at /api/audit. Usage on the server: python3 -m tracker.audit
"""
import json
import os
import re
import threading
import time
from datetime import datetime, timezone

from . import db

RESULT = os.path.join(db.DATA, "audit.json")
RETIRED = os.path.join(db.DATA, "auto_retired.json")
RUN_AT_UTC = 4            # 04:xx UTC: before the 05:45 UK audit in both summer and winter time
REFUSED = re.compile(r"\b(403|429)\b")
RETIRE_AFTER = 3 * 86400
SILENT_AFTER = 14 * 86400
STREAMS = {"wiki", "ais"}   # runners that report every few minutes, no fixed interval


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d %H:%M") if t else None


def load_retired():
    try:
        with open(RETIRED, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save(path, data):
    os.makedirs(db.DATA, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path)


def intervals():
    """Each collector's schedule in minutes, from the config defaults."""
    try:
        from .run import load_config
        cfg = load_config()
        out = {s: cfg.getint(s, "every_minutes") for s in cfg.sections() if cfg.has_option(s, "every_minutes")}
        out["airspace"] = cfg.getint("adsb", "region_minutes", fallback=15)
        return out
    except Exception:
        return {}


def collectors(con, now, every):
    out = []
    for (src,) in con.execute("SELECT DISTINCT source FROM runs WHERE source != 'news' ORDER BY source"):
        last = con.execute("SELECT started, ok, message FROM runs WHERE source=? ORDER BY started DESC LIMIT 1", (src,)).fetchone()
        ok = con.execute("SELECT MAX(started) FROM runs WHERE source=? AND ok=1", (src,)).fetchone()[0]
        limit = 3600 if src in STREAMS else max(3 * 60 * every.get(src, 15), 3600)
        if last["started"] < now - 14 * 86400:
            continue        # a collector that's been switched off for weeks isn't news
        out.append({"source": src, "last_run": iso(last["started"]), "last_ok": iso(ok), "message": last["message"],
                    "stale": ok is None or now - ok > limit,
                    "hours_since_ok": round((now - ok) / 3600, 1) if ok else None})
    return out


def feeds(con, now):
    con.execute("CREATE TABLE IF NOT EXISTS sources (id INTEGER PRIMARY KEY, url TEXT UNIQUE NOT NULL, name TEXT, country TEXT, "
                "layer INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'active', lease_until INTEGER, lease_cell TEXT, "
                "last_checked INTEGER DEFAULT 0, last_ok INTEGER, last_new INTEGER, fails INTEGER DEFAULT 0, last_error TEXT, "
                "checks INTEGER DEFAULT 0, signals INTEGER DEFAULT 0, added INTEGER)")
    rows = [dict(r) for r in con.execute("SELECT * FROM sources WHERE status != 'retired' ORDER BY layer, name")]
    problems, silent, probation, refusing = [], [], [], []
    for r in rows:
        brief = {"name": r["name"], "url": r["url"], "status": r["status"], "error": r["last_error"],
                 "last_ok": iso(r["last_ok"]), "fails": r["fails"]}
        if r["status"] in ("failing", "dead"):
            problems.append(brief)
            since = r["last_ok"] or r["added"] or now
            if REFUSED.search(r["last_error"] or "") and now - since >= RETIRE_AFTER:
                refusing.append(r)
        elif r["last_new"] and now - r["last_new"] > SILENT_AFTER:
            silent.append(dict(brief, last_new_item=iso(r["last_new"])))
        if r["layer"] == 5:
            probation.append({"name": r["name"], "url": r["url"], "signals": r["signals"], "checks": r["checks"],
                              "added": iso(r["added"]), "days": round((now - (r["added"] or now)) / 86400, 1)})
    return rows, problems, silent, probation, refusing


def retire(con, refusing, now, log=print):
    """Safe repair: retire feeds that have refused us for 3+ days. Returns the newly retired."""
    done = load_retired()
    new = []
    for r in refusing:
        if r["url"] in done:
            continue
        done[r["url"]] = {"name": r["name"], "since": iso(now), "reason": f"{r['last_error']}; no good fetch since "
                          f"{iso(r['last_ok']) or 'it was added'}"}
        new.append({"name": r["name"], "url": r["url"], "reason": done[r["url"]]["reason"]})
        with db._lock:
            con.execute("UPDATE sources SET status='retired' WHERE url=?", (r["url"],))
            con.commit()
        log(f"[audit] retired {r['name']}: refused (403/429) for 3+ days")
    if new:
        _save(RETIRED, done)
    return new, done


def run(con=None, log=print, now=None):
    con = con or db.connect()
    now = int(now or time.time())
    every = intervals()
    rows, problems, silent, probation, refusing = feeds(con, now)
    new, all_retired = retire(con, refusing, now, log)
    cols = collectors(con, now, every)
    res = {"generated_at": iso(now),
           "collectors": cols, "stale_collectors": [c["source"] for c in cols if c["stale"]],
           "feeds": {"total": len(rows), "working": sum(1 for r in rows if r["status"] == "active" and r["last_ok"]),
                     "problems": problems, "silent_14d": silent, "probation": probation},
           "retired_today": new,
           "auto_retired": [dict(v, url=u) for u, v in sorted(all_retired.items())],
           "for_claude": [f"Remove '{x['name']}' ({x['url']}) from tracker/backbone.json: auto-retired, {x['reason']}"
                          for x in new]}
    _save(RESULT, res)
    log(f"[audit] {res['feeds']['working']}/{res['feeds']['total']} feeds working, {len(problems)} with problems, "
        f"{len(res['stale_collectors'])} stale collectors, {len(new)} retired")
    return res


def runner(log=print):
    """Daily in the engine, at 04:xx UTC; also once soon after a start if today's is missing."""
    try:
        os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 19)
    except (AttributeError, OSError):
        pass
    time.sleep(300)
    con = db.connect()
    while True:
        try:
            last = int(db.get_meta(con, "audit_at", 0) or 0)
            now = time.time()
            today_slot = int(now // 86400) * 86400 + RUN_AT_UTC * 3600
            if last < today_slot <= now or now - last > 86400:
                run(con, log)
                db.set_meta(con, "audit_at", int(time.time()))
        except Exception as e:
            log(f"[audit] failed: {e}")
            db.set_meta(con, "audit_at", int(time.time()))
        time.sleep(600)


if __name__ == "__main__":
    print(json.dumps(run(), indent=1))
