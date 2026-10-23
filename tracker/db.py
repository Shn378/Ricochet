"""SQLite storage for tripwire signals, plus raw snapshot archiving.

Every response a collector fetches is saved gzipped with a SHA-256 hash, so
each signal can be traced back to exactly what the feed said at the time.
"""
import gzip
import hashlib
import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
DB_PATH = os.path.join(DATA, "tracker.db")
RAW_DIR = os.path.join(DATA, "raw")

_lock = threading.Lock()
CONTEXT_KINDS = {"military_air", "squawk_routine", "probation_report"}   # shown on the map, never counted towards alerts
# Natural hazards and official public alerts (collectors/hazards.py): context until Shane decides
CONTEXT_KINDS |= {"gdacs_red", "gdacs_orange", "gdacs_green", "tsunami_warning", "tsunami_threat",
                  "tsunami_advisory", "tsunami_watch", "tsunami_information", "uk_alert", "met_warning"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id          INTEGER PRIMARY KEY,
    uid         TEXT UNIQUE,          -- source-specific dedupe key
    source      TEXT NOT NULL,        -- gdelt, ioda, wiki, firms, adsb
    kind        TEXT NOT NULL,        -- e.g. conflict_event, outage, edit_burst, heat, squawk
    lat         REAL,
    lon         REAL,
    cell        TEXT,                 -- 1-degree grid cell, "lat:lon"
    place       TEXT,
    country     TEXT,
    title       TEXT,
    url         TEXT,
    score       REAL DEFAULT 1,
    observed_at INTEGER NOT NULL,     -- unix seconds, when it happened
    fetched_at  INTEGER NOT NULL,     -- unix seconds, when we captured it
    snapshot    TEXT,                 -- path of the raw snapshot it came from
    snapshot_sha TEXT,
    extra       TEXT                  -- JSON
);
CREATE INDEX IF NOT EXISTS ix_signals_time ON signals(observed_at);
CREATE INDEX IF NOT EXISTS ix_signals_cell ON signals(cell, observed_at);
CREATE TABLE IF NOT EXISTS runs (
    id        INTEGER PRIMARY KEY,
    source    TEXT NOT NULL,
    started   INTEGER NOT NULL,
    ok        INTEGER NOT NULL,
    added     INTEGER DEFAULT 0,
    message   TEXT
);
CREATE INDEX IF NOT EXISTS ix_runs_source ON runs(source, started);
-- Phase 2: hourly totals per area and feed, the raw material for baselines
CREATE TABLE IF NOT EXISTS buckets (
    cell   TEXT NOT NULL,
    source TEXT NOT NULL,
    hour   INTEGER NOT NULL,     -- unix hour (seconds // 3600)
    n      INTEGER NOT NULL DEFAULT 0,
    score  REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (cell, source, hour)
);
CREATE INDEX IF NOT EXISTS ix_buckets_src_hour ON buckets(source, hour);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
-- Per-country daily news totals (GDELT): how much of a country's news is conflict
CREATE TABLE IF NOT EXISTS country_day (
    country  TEXT NOT NULL,      -- GDELT country code
    day      INTEGER NOT NULL,   -- unix day (seconds // 86400)
    total    INTEGER NOT NULL DEFAULT 0,
    conflict INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (country, day)
);
CREATE TABLE IF NOT EXISTS country_info (
    country TEXT PRIMARY KEY, name TEXT, lat REAL, lon REAL
);
"""


def connect():
    os.makedirs(DATA, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    return con


def migrate_v2(con):
    """Phase 2 changed how GDELT and FIRMS signals are grouped. Clear the old-format
    ones once so they don't double up with the new ones."""
    if get_meta(con, "migrated_v2"):
        return
    with _lock:
        con.execute("DELETE FROM signals WHERE source IN ('gdelt', 'firms')")
        con.commit()
    set_meta(con, "migrated_v2", int(time.time()))


def rescore_ioda(con):
    """One-off fix for outage scores stored before the scale change."""
    from .collectors.ioda import outage_score
    with _lock:
        rows = con.execute("SELECT id, extra FROM signals WHERE source='ioda'").fetchall()
        for r in rows:
            s = json.loads(r["extra"] or "{}").get("ioda_score")
            con.execute("UPDATE signals SET score=? WHERE id=?", (outage_score(s), r["id"]))
        con.commit()


def get_meta(con, key, default=None):
    r = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def set_meta(con, key, value):
    with _lock:
        con.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?,?)", (key, str(value)))
        con.commit()


def live_from_hour(con, source):
    """Hours at or after this are counted from live signals; earlier ones come from the backfill."""
    return int(get_meta(con, f"live_from:{source}", 0))


def add_buckets(con, rows):
    """rows: iterable of (cell, source, hour, score). Adds to existing totals."""
    with _lock:
        con.executemany(
            """INSERT INTO buckets (cell, source, hour, n, score) VALUES (?,?,?,1,?)
               ON CONFLICT(cell, source, hour) DO UPDATE SET n = n + 1, score = score + excluded.score""",
            list(rows))
        con.commit()


def add_country_counts(con, counts, day):
    rows = [(cc, day, c["total"], c["conflict"]) for cc, c in counts.items() if not cc.startswith("_")]
    info = [(cc, c["name"], c["lat"], c["lon"]) for cc, c in counts.items() if not cc.startswith("_") and c.get("lat") is not None]
    names = [(cc, c["name"]) for cc, c in counts.items() if not cc.startswith("_") and c.get("name")]
    with _lock:
        con.executemany("""INSERT INTO country_day (country, day, total, conflict) VALUES (?,?,?,?)
                           ON CONFLICT(country, day) DO UPDATE SET total = total + excluded.total, conflict = conflict + excluded.conflict""", rows)
        con.executemany("INSERT OR IGNORE INTO country_info (country, name) VALUES (?,?)", names)
        con.executemany("""INSERT INTO country_info (country, name, lat, lon) VALUES (?,?,?,?)
                           ON CONFLICT(country) DO UPDATE SET name = excluded.name, lat = excluded.lat, lon = excluded.lon""", info)
        con.commit()


def cell_of(lat, lon):
    if lat is None or lon is None:
        return None
    return f"{int(lat // 1)}:{int(lon // 1)}"


def save_snapshot(source, payload: bytes):
    """Archive a raw feed response. Returns (path relative to data/, sha256)."""
    now = datetime.now(timezone.utc)
    day = now.strftime("%Y-%m-%d")
    folder = os.path.join(RAW_DIR, day)
    os.makedirs(folder, exist_ok=True)
    sha = hashlib.sha256(payload).hexdigest()
    name = f"{source}-{now.strftime('%H%M%S')}-{sha[:10]}.gz"
    path = os.path.join(folder, name)
    with gzip.open(path, "wb") as f:
        f.write(payload)
    return os.path.relpath(path, DATA).replace("\\", "/"), sha


def has_new(con, signals):
    """Does this batch contain any signal not stored yet? (Only then is the raw reply worth keeping.)"""
    uids = [s["uid"] for s in signals if s.get("uid")]
    for i in range(0, len(uids), 500):
        chunk = uids[i:i + 500]
        seen = con.execute(f"SELECT COUNT(*) FROM signals WHERE uid IN ({','.join('?' * len(chunk))})", chunk).fetchone()[0]
        if seen < len(set(chunk)):
            return True
    return False


def add_signals(con, signals, snapshot=None, sha=None):
    """Insert signals, skipping ones already seen. New ones also feed the hourly buckets.
    Returns number added."""
    now = int(time.time())
    added = 0
    live_from = {}
    with _lock:
        for s in signals:
            lat, lon = s.get("lat"), s.get("lon")
            cell = cell_of(lat, lon)
            observed = int(s.get("observed_at") or now)
            try:
                cur = con.execute(
                    """INSERT OR IGNORE INTO signals
                    (uid, source, kind, lat, lon, cell, place, country, title, url, score,
                     observed_at, fetched_at, snapshot, snapshot_sha, extra)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        s["uid"], s["source"], s["kind"], lat, lon, cell,
                        s.get("place"), s.get("country"), s.get("title"), s.get("url"),
                        s.get("score", 1), observed, now,
                        snapshot, sha, json.dumps(s.get("extra") or {}),
                    ),
                )
                added += cur.rowcount
                if cur.rowcount and cell and s["kind"] not in CONTEXT_KINDS:
                    src = s["source"]
                    if src not in live_from:
                        r = con.execute("SELECT value FROM meta WHERE key=?", (f"live_from:{src}",)).fetchone()
                        live_from[src] = int(r["value"]) if r else 0
                    hour = observed // 3600
                    if hour >= live_from[src]:
                        con.execute(
                            """INSERT INTO buckets (cell, source, hour, n, score) VALUES (?,?,?,1,?)
                               ON CONFLICT(cell, source, hour) DO UPDATE SET n = n + 1, score = score + excluded.score""",
                            (cell, src, hour, s.get("score", 1)))
            except sqlite3.Error as e:
                print(f"[db] skipped a signal from {s.get('source')}: {e}")
        con.commit()
    return added


def log_run(con, source, ok, added=0, message=""):
    with _lock:
        con.execute(
            "INSERT INTO runs (source, started, ok, added, message) VALUES (?,?,?,?,?)",
            (source, int(time.time()), 1 if ok else 0, added, message[:500]),
        )
        con.commit()


def prune(con, keep_days=14):
    """Keep the laptop lean: drop old signals, run logs and snapshots."""
    cutoff = int(time.time()) - keep_days * 86400
    with _lock:
        con.execute("DELETE FROM signals WHERE observed_at < ?", (cutoff,))
        con.execute("DELETE FROM runs WHERE started < ?", (cutoff,))
        con.execute("DELETE FROM buckets WHERE hour < ?", (cutoff // 3600 - 14 * 24,))  # keep 4 weeks of baseline
        con.execute("DELETE FROM country_day WHERE day < ?", (cutoff // 86400 - 60,))
        con.commit()
    if os.path.isdir(RAW_DIR):
        cutoff_day = datetime.fromtimestamp(cutoff, timezone.utc).strftime("%Y-%m-%d")
        for day in os.listdir(RAW_DIR):
            if day < cutoff_day:
                folder = os.path.join(RAW_DIR, day)
                for f in os.listdir(folder):
                    os.remove(os.path.join(folder, f))
                os.rmdir(folder)
