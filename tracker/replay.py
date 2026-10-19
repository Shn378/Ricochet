"""Replay: how fast could Ricochet have shown a past incident?

Given a place and a time window, re-downloads what the feeds published, slot by
slot, and reports:
  - when each feed first carried something about that place
  - when Ricochet's own rules would have opened a hotspot there

Usage (on the engine machine):
  python -m tracker.replay --lat -36.36 --lon 146.31 --start 2026-10-19T04:00 --end 2026-10-19T12:00 --wiki "Helen Haines" --keywords wangaratta,haines
or from the page: /api/replay?... (writes data/replay.json when done)
"""
import argparse
import json
import math
import os
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

from . import baseline, db, rules, sites
from .collectors import firms, gdelt, wiki as wikic
from .net import get

RADIUS_KM = 60


def km(lat1, lon1, lat2, lon2):
    p = math.pi / 180
    a = 0.5 - math.cos((lat2 - lat1) * p) / 2 + math.cos(lat1 * p) * math.cos(lat2 * p) * (1 - math.cos((lon2 - lon1) * p)) / 2
    return 12742 * math.asin(math.sqrt(a))


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def wiki_revisions(title, start, end, server="en.wikipedia.org"):
    q = urllib.parse.urlencode({"action": "query", "prop": "revisions", "titles": title, "rvlimit": 500, "rvdir": "newer",
                                "rvstart": datetime.fromtimestamp(start, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                "rvend": datetime.fromtimestamp(end, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                "rvprop": "timestamp|user|comment", "format": "json", "formatversion": 2})
    doc = json.loads(get(f"https://{server}/w/api.php?{q}", timeout=30))
    revs = []
    for p in doc.get("query", {}).get("pages", []):
        for r in p.get("revisions", []):
            t = int(datetime.strptime(r["timestamp"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())
            revs.append({"t": t, "user": r.get("user"), "comment": (r.get("comment") or "")[:120]})
    return revs


def wiki_new_pages(keywords, start, end, server="en.wikipedia.org"):
    """Articles created in the window whose titles mention a keyword."""
    out, cont = [], None
    for _ in range(20):
        params = {"action": "query", "list": "recentchanges", "rctype": "new", "rcnamespace": 0, "rclimit": 500, "rcdir": "newer",
                  "rcstart": datetime.fromtimestamp(start, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                  "rcend": datetime.fromtimestamp(end, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                  "rcprop": "title|timestamp|user", "format": "json", "formatversion": 2}
        if cont:
            params["rccontinue"] = cont
        doc = json.loads(get(f"https://{server}/w/api.php?{urllib.parse.urlencode(params)}", timeout=30))
        for rc in doc.get("query", {}).get("recentchanges", []):
            if any(k in rc["title"].lower() for k in keywords):
                t = int(datetime.strptime(rc["timestamp"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())
                out.append({"t": t, "title": rc["title"], "user": rc.get("user")})
        cont = doc.get("continue", {}).get("rccontinue")
        if not cont:
            break
    return out


def firms_area(key, lat, lon, start, end, pad=1.0, notes=None):
    """Fire detections in a box around the point, for each UTC day in the window.
    Records raw row counts per request in `notes`, so "no fires" and "request failed" can be told apart."""
    import csv, io
    box = f"{lon - pad:.2f},{lat - pad:.2f},{lon + pad:.2f},{lat + pad:.2f}"
    out, raw_points = [], []
    day = datetime.fromtimestamp(start, timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    while day.timestamp() <= end:
        for src in firms.SOURCES + ["VIIRS_NOAA21_NRT", "MODIS_NRT"]:
            url = f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/{src}/{box}/1/{day:%Y-%m-%d}"
            try:
                body = get(url, timeout=120)
                rows = list(csv.DictReader(io.StringIO(body.decode("utf-8", "replace"))))
                if notes is not None:
                    notes.append(f"FIRMS {src} {day:%m-%d}: {len(rows)} raw detections" + ("" if rows or body[:9] == b"latitude," else f" (reply: {body[:80]!r})"))
                for r in rows:
                    raw_points.append({"t": f"{r.get('acq_date')} {r.get('acq_time')}", "lat": r.get("latitude"), "lon": r.get("longitude"),
                                       "conf": r.get("confidence"), "frp": r.get("frp"), "sensor": src})
                out += firms.parse(body, src)
            except Exception as e:
                if notes is not None:
                    notes.append(f"FIRMS {src} {day:%m-%d} failed: {e}")
        day += timedelta(days=1)
    firms_area.raw = raw_points
    return [s for s in out if start <= s["observed_at"] <= end]


def simulate(series, cell, expected, history, steps, con=None):
    """Re-run the live hotspot rules (rules.decide) every 15 minutes over the replayed signals."""
    timeline = []
    for t in steps:
        feeds, site_by = {}, {}
        for src, sigs in series.items():
            here = [s for s in sigs if s["cell"] == cell and t - 6 * 3600 < s["t"] <= t]
            obs = sum(s["score"] for s in here)
            if obs:
                z, ratio, elevated = baseline.judge(obs, expected.get((cell, src), 0.0), src, history.get(src, 0))
                feeds[src] = {"observed": round(obs, 1), "z": round(z, 1), "elevated": elevated,
                              "expected": round(expected.get((cell, src), 0.0), 2),
                              "baseline": history.get(src, 0) >= baseline.MIN_HISTORY_HOURS}
                for s in here:
                    if s.get("site"):
                        site_by.setdefault(src, s["site"])
        level, reason, site = rules.decide(feeds, cell, expected, site_by) if feeds else (None, None, None)
        timeline.append({"t": iso(t), "feeds": feeds, "level": level, "note": reason if not level else None, "reason": reason})
    return timeline


def run(lat, lon, start, end, wiki_titles=(), keywords=(), log=print, firms_key=None):
    keywords = [k.lower() for k in keywords]
    con = db.connect()
    # Judge the square the physical evidence points at: the nearest sensitive site if there is one
    site_here = sites.near(con, lat, lon)
    cell = db.cell_of(lat, lon)
    report = {"place": {"lat": lat, "lon": lon, "cell": cell, "site": site_here}, "window": [iso(start), iso(end)], "gdelt": [], "wiki": {}, "notes": []}

    # Normal level for this square from stored history (same-hours-of-day method as live)
    exp_by = {}
    expected, history = baseline.expectations(con, 6)
    exp6 = expected.get((cell, "gdelt"), 0.0)
    report["baseline"] = {"gdelt_expected_per_6h": round(exp6, 2), "history_hours": history.get("gdelt", 0)}

    # GDELT, slot by slot
    slot = datetime.fromtimestamp(start, timezone.utc).replace(second=0, microsecond=0)
    slot = slot.replace(minute=slot.minute - slot.minute % 15)
    found, n = [], 0
    while slot.timestamp() <= end:
        try:
            sigs, counts = gdelt.fetch_slot(slot)
        except Exception as e:
            report["notes"].append(f"GDELT {slot:%H:%M} failed: {e}")
            sigs = []
        for s in sigs:
            near = km(lat, lon, s["lat"], s["lon"]) <= RADIUS_KM
            kw = any(k in (s.get("url") or "").lower() or k in (s.get("place") or "").lower() for k in keywords)
            if near or kw:
                found.append({"slot": int(slot.timestamp()), "near": near, "keyword": kw, "place": s.get("place"),
                              "title": s["title"], "url": s["url"], "score": s["score"], "cell": db.cell_of(s["lat"], s["lon"])})
        n += 1
        if n % 8 == 0:
            log(f"[replay] gdelt {slot:%H:%M} checked, {len(found)} relevant so far")
        slot += timedelta(minutes=15)
    report["gdelt"] = found

    # When would the rules have fired? Walk forward slot by slot with a 6-hour window
    in_cell = [f for f in found if f["cell"] == cell]
    fired = None
    for f in in_cell:
        window = [g for g in in_cell if f["slot"] - 6 * 3600 < g["slot"] <= f["slot"]]
        obs = sum(g["score"] for g in window)
        z, ratio, elevated = baseline.judge(obs, exp6, "gdelt", history.get("gdelt", 0))
        if elevated and z >= baseline.SOLO_Z["gdelt"]:
            fired = {"at": iso(f["slot"] + 15 * 60), "rule": "news reports alone, far above normal", "observed": round(obs, 1), "z": round(z, 1)}
            break
    report["hotspot_gdelt_alone"] = fired

    # Wikipedia
    wiki_series = []
    for title in wiki_titles:
        try:
            revs = wiki_revisions(title, start, end)
        except Exception as e:
            report["notes"].append(f"Wikipedia revisions for {title} failed: {e}")
            continue
        burst = None
        for i, r in enumerate(revs):
            win = [x for x in revs[: i + 1] if x["t"] > r["t"] - 1800]
            if len(win) >= 6 and len({x["user"] for x in win}) >= 3:
                burst = iso(r["t"])
                break
        # Replay the live burst tracker over the real edits, placed at the article's coordinates
        try:
            coords = wikic.coordinates("en.wikipedia.org", title)
        except Exception:
            coords = None
        tracker, tiers = wikic.BurstTracker(), []
        for r in revs:
            hit = tracker.add("enwiki", title, r["user"], r["t"])
            if hit and coords:
                sig = {"source": "wiki", "lat": coords[0], "lon": coords[1], "score": hit[3], "extra": {}}
                sig = sites.tag_and_filter(con, [sig])[0]
                wiki_series.append({"t": r["t"], "cell": db.cell_of(*coords), "score": hit[3], "site": sig["extra"].get("site")})
                tiers.append(f"{iso(r['t'])}: tier {hit[2] + 1} ({hit[0]} edits by {hit[1]} editors)")
        report["wiki"][title] = {"coordinates": coords, "burst_tiers": tiers, "edits": len(revs), "first_edit": iso(revs[0]["t"]) if revs else None,
                                 "burst_rule_fires": burst, "first_edits": [dict(r, t=iso(r["t"])) for r in revs[:8]]}
    if keywords:
        try:
            report["wiki"]["new_articles"] = [dict(p, t=iso(p["t"])) for p in wiki_new_pages(keywords, start, end)]
        except Exception as e:
            report["notes"].append(f"Wikipedia new-article search failed: {e}")

    # Fires (the fast tripwire for anything that burns)
    fires = []
    if firms_key:
        try:
            fires = sites.tag_and_filter(con, firms_area(firms_key, lat, lon, start, end, notes=report["notes"]))
            report["firms_raw"] = getattr(firms_area, "raw", [])[:60]
            report["firms"] = [{"t": iso(f["observed_at"]), "cell": db.cell_of(f["lat"], f["lon"]), "detections": f["extra"]["detections"],
                                "site": (f["extra"].get("site") or {}).get("name"),
                                "sensor": f["extra"]["sensor"], "km": round(km(lat, lon, f["lat"], f["lon"]), 1), "score": round(f["score"], 1)}
                               for f in sorted(fires, key=lambda f: f["observed_at"])]
            report["first_firms"] = report["firms"][0]["t"] if fires else None
        except Exception as e:
            report["notes"].append(f"FIRMS failed: {e}")

    # Step through the window with the live rules
    series = {"gdelt": [{"t": f["slot"] + 15 * 60, "cell": f["cell"], "score": f["score"]} for f in found],
              "wiki": wiki_series,
              "firms": [{"t": f["observed_at"] + 3 * 3600, "cell": db.cell_of(f["lat"], f["lon"]), "score": f["score"],
                         "site": f["extra"].get("site")} for f in fires]}
    # (FIRMS publishes ~3 hours after the satellite pass, so a fire counts from pass time + 3h)
    steps = list(range(start - start % 900, end + 1, 900))
    tl = simulate(series, cell, expected, history, steps, con)
    first = {}
    for row in tl:
        for key in ("signal", "reported"):
            if row["level"] == key and key not in first:
                first[key] = row["t"]
        if row["note"] and "held back" not in first:
            first["held back"] = f'{row["t"]}: {row["note"]}'
    report["rules"] = first
    report["timeline"] = [r for r in tl if r["feeds"]][:200]

    report["first_gdelt"] = iso(found[0]["slot"]) if found else None
    report["first_gdelt_in_square"] = iso(in_cell[0]["slot"]) if in_cell else None
    path = os.path.join(db.DATA, "replay.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    log(f"[replay] done, written to {path}")
    return report


def parse_time(s):
    return int(datetime.strptime(s, "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc).timestamp())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--lat", type=float, required=True)
    ap.add_argument("--lon", type=float, required=True)
    ap.add_argument("--start", required=True, help="UTC, e.g. 2026-10-19T04:00")
    ap.add_argument("--end", required=True)
    ap.add_argument("--wiki", default="", help="comma-separated article titles")
    ap.add_argument("--keywords", default="")
    a = ap.parse_args()
    run(a.lat, a.lon, parse_time(a.start), parse_time(a.end),
        [w for w in a.wiki.split(",") if w], [k for k in a.keywords.split(",") if k])
