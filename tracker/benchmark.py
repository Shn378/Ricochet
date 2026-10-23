"""Benchmark: replay every audited incident, and recent everyday traffic, through the
hotspot rules with each proposed change switched on or off.

For each incident in docs/benchmark.json the evidence Ricochet actually held at the
time (signals, normal levels, ongoing countries) is frozen once into
data/benchmark/<id>.json, so it outlives the 14-day signal pruning and the result
is "knowable then": a signal only counts from the moment it was fetched.

Each variant (current rules, each switch in rules.FLAGS alone, all switches) is scored:
  hits      incidents that opened a Signal or Reported hotspot nearby, and how early
  openings  hotspots opened over the last 3 days of live data; extra ones compared with
            the current rules are listed as possible false alarms for a person to judge

Runs on the server after each deploy and nightly; results at /api/benchmark.
Usage on the server: python3 -m tracker.benchmark
"""
import json
import os
import statistics
import time
from collections import defaultdict
from datetime import datetime, timezone

from . import baseline, db, places, rules, situations

ROOT = db.ROOT
INCIDENTS = os.path.join(ROOT, "docs", "benchmark.json")
FROZEN = os.path.join(db.DATA, "benchmark")
RESULT = os.path.join(db.DATA, "benchmark.json")
WINDOW = 6 * 3600          # the live rules look at the last 6 hours
LATE = 12 * 3600           # a hotspot up to 12 h after the incident window still counts
STEP = 900
NOISE_DAYS = 3


def ts(s):
    return int(datetime.strptime(s, "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc).timestamp()) if s else None


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d %H:%M") if t else None


def load_incidents():
    with open(INCIDENTS, encoding="utf-8") as f:
        return json.load(f)["incidents"]


def around(cell, r=1):
    la, lo = map(int, cell.split(":"))
    return [f"{la + a}:{lo + b}" for a in range(-r, r + 1) for b in range(-r, r + 1)]


def _hot_cc(con, t):
    try:
        return sorted(places.FIPS_TO_ISO[c["country"]] for c in situations.compute(con, now=t)["countries"]
                      if c["ongoing"] and c["country"] in places.FIPS_TO_ISO)
    except Exception:
        return None


def _expect(con, t, cells):
    out = {}
    for name, med in (("mean", False), ("median", True)):
        exp, hist = baseline.expectations(con, 6, now=t, cells=cells, median=med)
        out[name] = {f"{c}|{s}": round(v, 3) for (c, s), v in exp.items() if c in cells}
    out["history"] = hist
    return out


def freeze(con, inc):
    """Capture what Ricochet held around an incident. None if the window isn't over yet
    or the signals have already been pruned."""
    start, end = ts(inc["start"]), ts(inc["end"])
    if time.time() < end + LATE:
        return None
    judge = sorted({c for p in inc["points"] for c in around(db.cell_of(*p))})
    wide = sorted({c for j in judge for c in around(j)})          # for "nearby" corroboration
    q = f"SELECT source, kind, cell, score, title, url, extra, observed_at, fetched_at, country FROM signals " \
        f"WHERE cell IN ({','.join('?' * len(wide))}) AND observed_at > ? AND observed_at <= ? ORDER BY observed_at"
    sigs = []
    for r in con.execute(q, (*wide, start - WINDOW, end + LATE)):
        s = dict(r)
        if s["kind"] in db.CONTEXT_KINDS:
            continue
        site = rules._extra(s).get("site")
        s["extra"] = {"site": site} if site else {}
        sigs.append(s)
    oldest = con.execute("SELECT MIN(observed_at) FROM signals").fetchone()[0] or time.time()
    if oldest > start - WINDOW:
        return {"id": inc["id"], "status": "no data (signals already pruned or before launch)"}
    hours = sorted({(t // 3600) * 3600 for t in range(start, end + LATE + 1, STEP)})
    expect = {str(h): _expect(con, h, judge) for h in hours}
    days = sorted({h // 86400 for h in hours})
    hot = {str(d): _hot_cc(con, d * 86400 + 43200) for d in days}
    return {"id": inc["id"], "status": "ok", "frozen_at": int(time.time()), "judge_cells": judge,
            "signals": sigs, "expect": expect, "hot_countries": hot}


def frozen(con, inc, log=print):
    path = os.path.join(FROZEN, f"{inc['id']}.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    snap = freeze(con, inc)
    if snap and snap.get("status") == "ok":
        os.makedirs(FROZEN, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(snap, f)
        log(f"[benchmark] froze {inc['id']}: {len(snap['signals'])} signals")
    return snap


def _decide_at(sigs_by_cell, cell, t, expected, history, hot, flags):
    items = [s for s in sigs_by_cell.get(cell, ()) if s["fetched_at"] <= t and t - WINDOW < s["observed_at"] <= t]
    if not items:
        return None
    nearby = [(s["source"], f"{s.get('title') or ''} {s.get('url') or ''}") for n in around(cell)
              for s in sigs_by_cell.get(n, ()) if s["fetched_at"] <= t and t - WINDOW < s["observed_at"] <= t]
    return rules.evaluate(cell, items, expected, history, hot, nearby, now=t, flags=flags)


def score_incident(inc, snap, flags):
    """First Signal/Reported hotspot near the incident under these rules."""
    if not snap or snap.get("status") != "ok":
        return {"status": (snap or {}).get("status", "waiting for the window to finish")}
    start, end = ts(inc["start"]), ts(inc["end"])
    by_cell = defaultdict(list)
    for s in snap["signals"]:
        by_cell[s["cell"]].append(s)
    kind = "median" if flags.get("median_baseline") else "mean"
    best = {}
    for t in range(start, end + LATE + 1, STEP):
        e = snap["expect"].get(str((t // 3600) * 3600)) or {}
        expected = {tuple(k.split("|")): v for k, v in e.get(kind, {}).items()}
        hot = snap["hot_countries"].get(str(t // 86400))
        hot = set(hot) if hot is not None else None
        for cell in snap["judge_cells"]:
            ev = _decide_at(by_cell, cell, t, expected, e.get("history", {}), hot, flags)
            if ev and ev["level"] in ("signal", "reported"):
                lvl = ev["level"]
                if lvl not in best:
                    best[lvl] = {"t": t, "cell": cell, "reason": ev["reason"]}
        if "reported" in best:
            break
    if not best:
        return {"status": "ok", "level": None}
    first = min(best.values(), key=lambda b: b["t"])
    level = "reported" if "reported" in best else "signal"
    fr = ts(inc.get("first_report"))
    return {"status": "ok", "level": level, "first": iso(first["t"]), "cell": first["cell"], "reason": first["reason"],
            "lead_min": round((fr - first["t"]) / 60) if fr else None}


def noise(con, variants, log=print):
    """Hotspots each variant would have opened over the last few days, hour by hour."""
    now = (int(time.time()) // 3600) * 3600
    since = now - NOISE_DAYS * 86400
    rows = [dict(r) for r in con.execute(
        "SELECT source, kind, cell, score, title, url, extra, observed_at, fetched_at, country, place FROM signals "
        "WHERE observed_at > ? AND cell IS NOT NULL", (since - WINDOW,))]
    rows = [r for r in rows if r["kind"] not in db.CONTEXT_KINDS]
    opened = {v: {} for v in variants}
    hot_by_day = {}
    for t in range(since, now + 1, 3600):
        live = [r for r in rows if r["fetched_at"] <= t and t - WINDOW < r["observed_at"] <= t]
        if not live:
            continue
        by_cell = defaultdict(list)
        for r in live:
            by_cell[r["cell"]].append(r)
        cells = sorted(by_cell)
        exps = {m: baseline.expectations(con, 6, now=t, cells=cells, median=m) for m in (False, True)}
        day = t // 86400
        if day not in hot_by_day:
            h = _hot_cc(con, t)
            hot_by_day[day] = set(h) if h is not None else None
        texts = defaultdict(list)
        for r in live:
            texts[r["cell"]].append((r["source"], f"{r.get('title') or ''} {r.get('url') or ''}"))
        for v, flags in variants.items():
            expected, history = exps[bool(flags.get("median_baseline"))]
            for cell, items in by_cell.items():
                ev = rules.evaluate(cell, items, expected, history, hot_by_day[day],
                                    [x for n in around(cell) for x in texts.get(n, ())], now=t, flags=flags)
                if ev["level"] in ("signal", "reported") and (cell, day) not in opened[v]:
                    best = max(items, key=lambda s: s["score"] or 1)
                    opened[v][(cell, day)] = {"cell": cell, "day": iso(day * 86400)[:10], "level": ev["level"],
                                              "place": next((s["place"] for s in items if s.get("place")), None),
                                              "headline": (best.get("title") or "")[:140], "reason": ev["reason"]}
    return opened


def variants():
    base = dict(rules.FLAGS)
    out = {"current": base}
    for k in rules.FLAGS:
        if not base[k]:
            out[f"+{k}"] = dict(base, **{k: True})
    out["all"] = {k: True for k in rules.FLAGS}
    return out


def commit():
    """Short hash of the deployed commit (reads .git directly; no git needed)."""
    def read(*p):
        with open(os.path.join(ROOT, ".git", *p), encoding="utf-8") as f:
            return f.read().strip()
    try:
        head = read("HEAD")
        if not head.startswith("ref: "):
            return head[:7]
        ref = head[5:]
        try:
            return read(*ref.split("/"))[:7]
        except OSError:
            for line in read("packed-refs").splitlines():
                if line.endswith(" " + ref):
                    return line[:7]
    except OSError:
        pass
    return None


def run(con=None, log=print):
    con = con or db.connect()
    started = time.time()
    incs = load_incidents()
    snaps = {i["id"]: frozen(con, i, log) for i in incs}
    vs = variants()
    detail = {i["id"]: {"name": i["name"], "first_report": i.get("first_report")} for i in incs}
    for v, flags in vs.items():
        for i in incs:
            detail[i["id"]][v] = score_incident(i, snaps[i["id"]], flags)
    opened = noise(con, vs, log)
    cur_open = set(opened["current"])
    summary = []
    for v, flags in vs.items():
        scored = [d[v] for d in detail.values() if d[v].get("status") == "ok"]
        hits = [i for i, d in detail.items() if d[v].get("level")]
        leads = [d[v]["lead_min"] for d in detail.values() if d[v].get("level") and d[v].get("lead_min") is not None]
        cur_hits = {i for i, d in detail.items() if d["current"].get("level")}
        extra = [opened[v][k] for k in opened[v] if k not in cur_open]
        summary.append({
            "variant": v, "flags": {k: x for k, x in flags.items() if x},
            "scored": len(scored), "hits": len(hits),
            "reported": sum(1 for d in detail.values() if d[v].get("level") == "reported"),
            "median_lead_min": statistics.median(leads) if leads else None,
            "hits_gained": sorted(set(hits) - cur_hits), "hits_lost": sorted(cur_hits - set(hits)),
            "openings": len(opened[v]), "extra_openings": len(extra),
            "fewer_openings": len(cur_open - set(opened[v])),
            "extra_examples": extra[:25],
        })
    res = {"run_at": iso(int(time.time())), "seconds": round(time.time() - started), "commit": commit(),
           "noise_days": NOISE_DAYS, "incidents": len(incs),
           "unscored": {i: s.get("status") for i, s in ((k, detail[k]["current"]) for k in detail) if s.get("status") != "ok"},
           "summary": summary, "detail": detail}
    os.makedirs(db.DATA, exist_ok=True)
    with open(RESULT, "w", encoding="utf-8") as f:
        json.dump(res, f, indent=1)
    log(f"[benchmark] done in {res['seconds']} s: current {summary[0]['hits']}/{summary[0]['scored']} hits")
    return res


def due(con):
    """After a deploy (new commit) or once a day."""
    last = db.get_meta(con, "benchmark_commit")
    when = int(db.get_meta(con, "benchmark_at", 0) or 0)
    return last != commit() or time.time() - when > 86400


def runner(log=print, delay=600):
    """Background loop in the engine: wait for things to settle after a start, then run when due."""
    time.sleep(delay)
    con = db.connect()
    while True:
        try:
            if due(con):
                run(con, log)
                db.set_meta(con, "benchmark_commit", commit())
                db.set_meta(con, "benchmark_at", int(time.time()))
        except Exception as e:
            log(f"[benchmark] failed: {e}")
            db.set_meta(con, "benchmark_at", int(time.time()))   # don't retry in a tight loop
        time.sleep(1800)


if __name__ == "__main__":
    run()
