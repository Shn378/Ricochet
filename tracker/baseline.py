"""Phase 2: what's normal for each area, and how far from normal it is now.

Each area (1-degree cell) and feed gets an hourly total in the `buckets` table.
The baseline is the average over the history we have, outside the recent window.
A cell is "elevated" when the recent total is well above what that average
predicts. London produces crime stories every hour, so its baseline is high and
ordinary days stay quiet. A village that never appears suddenly producing
reports, fires and an outage is far above its baseline.

On first start, history is downloaded so baselines work within minutes:
GDELT 72 hours, FIRMS 5 days, IODA 7 days.
"""
import math
import sqlite3
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from . import db, sites
from .collectors import firms, gdelt, ioda

GDELT_HOURS = 7 * 24
FIRMS_DAYS = 5
IODA_HOURS = 7 * 24
MIN_HISTORY_HOURS = 24          # below this a feed has no usable baseline yet
SOLO_Z = {"gdelt": 4.0, "firms": 6.0, "ioda": 0.0, "wiki": 0.0, "adsb": 0.0, "news": 4.0, "seismic": 0.0, "radar": 0.0, "ais": 0.0, "infra": 0.0}  # feeds with no baseline need just presence
ELEVATED_Z = 3.0
ELEVATED_RATIO = 2.0
RATIO_BY_SOURCE = {"firms": 4.0}   # burning seasons swing a lot day to day
NEIGHBOUR_SOURCES = {"firms"}      # fire fronts drift between squares; compare with the surrounding area too


def _bucket_rows(signals):
    for s in signals:
        cell = db.cell_of(s.get("lat"), s.get("lon"))
        if cell and s["kind"] not in db.CONTEXT_KINDS:
            yield cell, s["source"], int(s["observed_at"]) // 3600, s.get("score", 1)


def _mark_live_from(con, source, hour):
    if not db.get_meta(con, f"live_from:{source}"):
        db.set_meta(con, f"live_from:{source}", hour)


def prepare(con, sources):
    """Before collectors start: fix the hour from which live data fills the buckets."""
    hour = int(time.time()) // 3600
    if not db.get_meta(con, "backfill_done_v3:firms"):
        # Fire cluster rules changed (nominal confidence, sensitive sites): rebuild fire history
        with db._lock:
            con.execute("DELETE FROM buckets WHERE source='firms'")
            con.execute("DELETE FROM signals WHERE source='firms'")
            con.execute("DELETE FROM meta WHERE key IN ('live_from:firms', 'history_from:firms')")
            con.commit()
    if not db.get_meta(con, "backfill_done_v3:gdelt"):
        # GDELT rules changed (riots, nationwide reports): rebuild its history from scratch
        with db._lock:
            con.execute("DELETE FROM buckets WHERE source='gdelt'")
            con.execute("DELETE FROM country_day")
            con.execute("DELETE FROM meta WHERE key IN ('live_from:gdelt', 'backfill_progress:gdelt', 'history_from:gdelt')")
            con.commit()
    for s in sources:
        _mark_live_from(con, s, hour)


def backfill(con, firms_key=None, log=print):
    """Download history for each feed once. Safe to call every start; skips what's done."""
    now = datetime.now(timezone.utc)

    if not db.get_meta(con, "backfill_done:ioda"):
        try:
            raw, sigs = ioda.fetch(hours=IODA_HOURS)
            cutoff = db.live_from_hour(con, "ioda")
            db.add_buckets(con, [r for r in _bucket_rows(sigs) if r[2] < cutoff])
            db.set_meta(con, "history_from:ioda", cutoff - IODA_HOURS)
            db.set_meta(con, "backfill_done:ioda", int(time.time()))
            log(f"[baseline] ioda: {len(sigs)} outages from the last 7 days loaded")
        except Exception as e:
            log(f"[baseline] ioda backfill failed, will retry next start: {e}")

    if firms_key and not db.get_meta(con, "backfill_done_v3:firms"):
        try:
            cutoff = db.live_from_hour(con, "firms")
            first_day = (now - timedelta(days=FIRMS_DAYS)).replace(hour=0, minute=0, second=0, microsecond=0)
            with db._lock:  # redo cleanly: drop any older backfilled fire history
                con.execute("DELETE FROM buckets WHERE source='firms' AND hour < ?", (cutoff,))
                con.commit()
            for d in range(FIRMS_DAYS, -1, -1):   # includes today, up to the live cutoff
                day = (now - timedelta(days=d)).strftime("%Y-%m-%d")
                sigs = sites.tag_and_filter(con, firms.fetch_day(firms_key, day))
                db.add_buckets(con, [r for r in _bucket_rows(sigs) if r[2] < cutoff])
                log(f"[baseline] firms: {day} loaded ({len(sigs)} clusters)")
            db.set_meta(con, "history_from:firms", int(first_day.timestamp()) // 3600)
            db.set_meta(con, "backfill_done_v3:firms", int(time.time()))
        except Exception as e:
            log(f"[baseline] firms backfill failed, will retry next start: {e}")

    if not db.get_meta(con, "backfill_done_v3:gdelt"):
        cutoff = db.live_from_hour(con, "gdelt")
        start_slot = datetime.fromtimestamp(cutoff * 3600, timezone.utc) - timedelta(hours=GDELT_HOURS)
        done_to = int(db.get_meta(con, "backfill_progress:gdelt", 0))
        slot, n, missing = start_slot, 0, 0
        try:
            while slot < datetime.fromtimestamp(cutoff * 3600, timezone.utc):
                ts = int(slot.timestamp())
                if ts > done_to:
                    sigs, counts = gdelt.fetch_slot(slot)
                    if not sigs and not counts:
                        missing += 1
                    db.add_buckets(con, _bucket_rows(sigs))
                    db.add_country_counts(con, counts, ts // 86400)
                    db.set_meta(con, "backfill_progress:gdelt", ts)
                    n += 1
                    if n % 96 == 0:
                        log(f"[baseline] gdelt: {n} of {GDELT_HOURS * 4} history files loaded")
                slot += timedelta(minutes=15)
            db.set_meta(con, "history_from:gdelt", cutoff - GDELT_HOURS)
            db.set_meta(con, "backfill_done_v3:gdelt", int(time.time()))
            log(f"[baseline] gdelt: 7 days of history complete ({missing} files missing on the server)")
        except Exception as e:
            log(f"[baseline] gdelt backfill paused, resumes next start: {e}")

    for s in ("wiki", "adsb", "news"):  # no public history: they learn from live data
        if not db.get_meta(con, f"history_from:{s}"):
            db.set_meta(con, f"history_from:{s}", db.live_from_hour(con, s))


def history_hours(con, source, window_end_hour):
    start = db.get_meta(con, f"history_from:{source}")
    if start is None:
        return 0
    return max(0, window_end_hour - int(start))


def status(con):
    """How much history each feed has, for the health panel."""
    now_hour = int(time.time()) // 3600
    out = {}
    for s in ("gdelt", "ioda", "firms", "wiki", "adsb", "news"):
        h = history_hours(con, s, now_hour)
        out[s] = {"history_hours": h, "ready": h >= MIN_HISTORY_HOURS,
                  "backfilled": bool(db.get_meta(con, f"backfill_done:{s}") or db.get_meta(con, f"backfill_done_v3:{s}"))}
    return out


def expectations(con, window_hours, now=None, cells=None, median=False):
    """Expected score per (cell, source) for the recent window, taken from the SAME
    hours of the day on previous days. Fire satellites pass at fixed times and news
    follows a daily cycle, so a 24-hour average would make every afternoon look
    abnormal. Returns ({(cell, source): expected}, {source: history_hours}).

    now: judge as at this time (replays and the benchmark); cells: only these squares
    (plus neighbours for fires); median: the typical day over the 4 weeks kept, so one
    terrible week doesn't raise the bar for a month."""
    both, hist = expectations_both(con, window_hours, now=now, cells=cells)
    return both["median" if median else "mean"], hist


# Filtering by square keeps the lookup on the (cell, source, hour) key. Without it every bucket of
# the feed for 4 weeks is read, which made the hourly benchmark noise pass crawl. SQLite since 3.32
# allows 32,766 parameters; older builds stop at 999, so a "too many variables" error falls back.
MAX_CELLS = 30000


def _rows(con, now_hour, end, d, window_hours, source, want):
    base = """SELECT cell, (? - hour) / 24 AS d, SUM(score) AS total FROM buckets
              WHERE hour < ? AND hour >= ? - 24 * ? AND ((? - hour) % 24) < ? AND source = ?"""
    args = (now_hour, end, now_hour, d, now_hour, window_hours, source)
    if want:
        try:
            return con.execute(base + f" AND cell IN ({','.join('?' * len(want))}) GROUP BY cell, d",
                               (*args, *sorted(want))).fetchall()
        except sqlite3.OperationalError as e:
            if "too many" not in str(e):
                raise
    return [r for r in con.execute(base + " GROUP BY cell, d", args) if not want or r["cell"] in want]


def expectations_both(con, window_hours, now=None, cells=None):
    """Mean and median expectations from one pass over the buckets (the benchmark needs both).
    Returns ({"mean": exp, "median": exp}, hist)."""
    now_hour = int(now or time.time()) // 3600
    end = now_hour - window_hours + 1          # first hour of the recent window
    hist, days = {}, {}
    for s in ("gdelt", "ioda", "firms", "wiki", "adsb", "news"):
        hist[s] = history_hours(con, s, end)
        days[s] = hist[s] // 24
    want = None
    if cells is not None:
        want = set()
        for c in cells:
            la, lo = map(int, c.split(":"))
            want.update(f"{la + a}:{lo + b}" for a in (-1, 0, 1) for b in (-1, 0, 1))
    if want and len(want) > MAX_CELLS:   # past SQLite's parameter limit: read everything instead
        want = None
    out = {"mean": defaultdict(float), "median": defaultdict(float)}
    for s, d in days.items():
        if hist[s] < MIN_HISTORY_HOURS or d < 1:
            continue
        per_cell = defaultdict(list)
        for r in _rows(con, now_hour, end, d, window_hours, s, want):
            per_cell[r["cell"]].append(r["total"])
        for c, totals in per_cell.items():
            vals = sorted(totals + [0.0] * max(0, d - len(totals)))
            m = len(vals) // 2
            out["median"][(c, s)] = vals[m] if len(vals) % 2 else (vals[m - 1] + vals[m]) / 2
            out["mean"][(c, s)] = sum(totals) / d
    # Fires: a square inside a burning region inherits the region's normal level
    for exp in out.values():
        for s in NEIGHBOUR_SOURCES:
            own = {c: v for (c, src), v in exp.items() if src == s}
            for c in list(own):
                la, lo = map(int, c.split(":"))
                for dla in (-1, 0, 1):
                    for dlo in (-1, 0, 1):
                        n = f"{la + dla}:{lo + dlo}"
                        exp[(n, s)] = max(exp.get((n, s), 0.0), own[c] * 0.75)
    return out, hist


def judge(observed, expected, source, history):
    """How unusual is this? Returns (z, ratio, elevated)."""
    z = (observed - expected) / math.sqrt(expected + 1.0)
    ratio = observed / expected if expected > 0 else None
    if history < MIN_HISTORY_HOURS:
        # No baseline yet: fall back to strict fixed rules (fires never alone).
        # Seismic, Radar and AIS always land here: their collectors already judge "unusual"
        # (30 days of quakes, Cloudflare's own detection, chokepoint history).
        fixed = {"gdelt": 12, "ioda": 2, "wiki": 2, "adsb": 1.5, "firms": None, "news": 3.5, "seismic": 2.5, "radar": 2.0, "ais": 2.5, "infra": 2.0}.get(source)
        return z, ratio, fixed is not None and observed >= fixed
    need_ratio = RATIO_BY_SOURCE.get(source, ELEVATED_RATIO)
    return z, ratio, z >= ELEVATED_Z and (ratio is None or ratio >= need_ratio)
