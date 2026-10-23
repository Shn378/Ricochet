"""Runs every collector on its own schedule, plus the local web server.

Start with:  python -m tracker.run
Then open:   http://localhost:8765
"""
import configparser
import os
import threading
import time
import traceback
from datetime import datetime, timezone

from . import baseline, db, sites
from .collectors import adsb, ais, firms, gdelt, hazards, infra, ioda, radar, usgs, wiki
from .server import serve

CONFIG_PATH = os.path.join(db.ROOT, "config.ini")
DEFAULTS = {
    "general": {"port": "8765", "host": "127.0.0.1", "keep_days": "14"},
    "firms": {"enabled": "yes", "map_key": "", "every_minutes": "30"},
    "gdelt": {"enabled": "yes", "every_minutes": "15"},
    "ioda": {"enabled": "yes", "every_minutes": "10"},
    "adsb": {"enabled": "yes", "every_minutes": "10"},
    "seismic": {"enabled": "yes", "every_minutes": "5"},
    "infra": {"enabled": "yes", "every_minutes": "5"},
    "hazard": {"enabled": "yes", "every_minutes": "15"},
    "radar": {"enabled": "yes", "token": "", "every_minutes": "10"},
    "ais": {"enabled": "yes", "key": ""},
    "wiki": {"enabled": "yes"},
    "news": {"enabled": "yes", "budget_per_min": "30", "quiet_minutes": "30", "hot_minutes": "2", "world_desk_minutes": "5"},
}


def load_config():
    cfg = configparser.ConfigParser()
    cfg.read_dict(DEFAULTS)
    if os.path.exists(CONFIG_PATH):
        cfg.read(CONFIG_PATH, encoding="utf-8")
    else:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            cfg.write(f)
    return cfg


def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    os.makedirs(db.DATA, exist_ok=True)
    with open(os.path.join(db.DATA, "tracker.log"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def poller(con, name, fn, every_minutes):
    # Pick up the schedule where the last run left off, so a restart (every code
    # update) doesn't fire an extra request at each upstream service.
    last = con.execute("SELECT MAX(started) FROM runs WHERE source=?", (name,)).fetchone()[0]
    if last:
        wait = last + every_minutes * 60 - time.time()
        if wait > 0:
            log(f"[{name}] next run in {int(wait // 60)} min {int(wait % 60)} s (keeping its schedule across the restart)")
            time.sleep(wait)
    while True:
        try:
            raw, signals = fn()
            signals = sites.tag_and_filter(con, signals)
            # Keep the raw reply only when it brought something new: it's the evidence behind
            # those signals. Unchanged replies (most quake, status and fire polls) aren't stored.
            snap, sha = db.save_snapshot(name, raw) if db.has_new(con, signals) else (None, None)
            added = db.add_signals(con, signals, snap, sha)
            if name == "gdelt" and gdelt.LAST_COUNTS.get("_slot") and gdelt.LAST_COUNTS["_slot"] != db.get_meta(con, "last_slot:gdelt"):
                slot_ts = int(datetime.strptime(gdelt.LAST_COUNTS["_slot"].rsplit("/", 1)[-1][:14], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc).timestamp())
                db.add_country_counts(con, gdelt.LAST_COUNTS, slot_ts // 86400)
                db.set_meta(con, "last_slot:gdelt", gdelt.LAST_COUNTS["_slot"])
            msg = f"{len(signals)} signals, {added} new"
            if name == "airspace":
                failed = adsb.LAST["failed"]
                msg += f", {len(adsb.REGIONS) - len(failed)}/{len(adsb.REGIONS)} regions" + (f" (failed: {'; '.join(failed)[:300]})" if failed else "")
            db.log_run(con, name, True, added, msg)
            log(f"[{name}] ok: {msg}")
        except Exception as e:
            db.log_run(con, name, False, 0, str(e))
            log(f"[{name}] failed: {e}")
        time.sleep(every_minutes * 60)


def wiki_runner(con):
    def on_signal(s, raw):
        s = (sites.tag_and_filter(con, [s]) or [s])[0]
        snap, sha = db.save_snapshot("wiki", raw)
        if db.add_signals(con, [s], snap, sha):
            db.log_run(con, "wiki", True, 1, s["title"])
            log(f"[wiki] {s['title']}")
    def on_error(msg):
        db.log_run(con, "wiki", False, 0, msg)
        log(f"[wiki] {msg}")
    def on_status(msg):
        db.log_run(con, "wiki", True, 0, msg)
    db.log_run(con, "wiki", True, 0, "stream starting")
    wiki.stream(on_signal, on_error, on_status)


def ais_runner(con, key):
    def on_signals(sigs, raw):
        snap, sha = db.save_snapshot("ais", raw)
        added = db.add_signals(con, sigs, snap, sha)
        for s in sigs[:added]:
            log(f"[ais] {s['title']}")
    def on_status(msg):
        db.log_run(con, "ais", True, 0, msg)
    def on_error(msg):
        db.log_run(con, "ais", False, 0, msg)
        log(f"[ais] {msg}")
    db.log_run(con, "ais", True, 0, "stream starting")
    ais.stream(key, on_signals, on_status, on_error)


def housekeeping(con, keep_days):
    while True:
        try:
            db.prune(con, keep_days)
        except Exception as e:
            log(f"[prune] failed: {e}")
        time.sleep(6 * 3600)


def main():
    cfg = load_config()
    con = db.connect()
    db.rescore_ioda(con)
    db.migrate_v2(con)
    jobs = []
    if cfg.getboolean("gdelt", "enabled"):
        jobs.append(("gdelt", gdelt.fetch, cfg.getint("gdelt", "every_minutes")))
    if cfg.getboolean("ioda", "enabled"):
        jobs.append(("ioda", ioda.fetch, cfg.getint("ioda", "every_minutes")))
    if cfg.getboolean("adsb", "enabled"):
        jobs.append(("adsb", adsb.fetch, max(10, cfg.getint("adsb", "every_minutes"))))   # community feed: no faster than every 10 min
        jobs.append(("airspace", adsb.fetch_regions, cfg.getint("adsb", "region_minutes", fallback=15)))
    if cfg.getboolean("infra", "enabled"):
        jobs.append(("infra", infra.fetch, cfg.getint("infra", "every_minutes")))
    if cfg.getboolean("hazard", "enabled"):
        jobs.append(("hazard", hazards.fetch, max(10, cfg.getint("hazard", "every_minutes"))))
    if cfg.getboolean("seismic", "enabled"):
        jobs.append(("seismic", usgs.fetch, cfg.getint("seismic", "every_minutes")))
    if cfg.getboolean("firms", "enabled"):
        key = cfg.get("firms", "map_key").strip()
        if key:
            jobs.append(("firms", lambda: firms.fetch(key), cfg.getint("firms", "every_minutes")))
        else:
            log("[firms] skipped: add your free map key to config.ini to switch on heat detection")
    if cfg.getboolean("radar", "enabled"):
        token = cfg.get("radar", "token").strip()
        if token:
            jobs.append(("radar", lambda: radar.fetch(token), cfg.getint("radar", "every_minutes")))
        else:
            log("[radar] skipped: add a Cloudflare Radar read token to config.ini")
    sites.refresh(con, log)   # Wikidata list of sensitive sites, monthly
    baseline.prepare(con, ["gdelt", "ioda", "firms", "wiki", "adsb", "news"])
    key = cfg.get("firms", "map_key").strip() if cfg.getboolean("firms", "enabled") else None
    threading.Thread(target=baseline.backfill, args=(db.connect(), key, log), daemon=True, name="backfill").start()
    for i, (name, fn, every) in enumerate(jobs):
        threading.Thread(target=poller, args=(con, name, fn, every), daemon=True, name=name).start()
        time.sleep(2)  # stagger first runs so the laptop isn't hit all at once
    if cfg.getboolean("wiki", "enabled"):
        threading.Thread(target=wiki_runner, args=(con,), daemon=True, name="wiki").start()
    if cfg.getboolean("ais", "enabled"):
        if cfg.get("ais", "key").strip():
            threading.Thread(target=ais_runner, args=(con, cfg.get("ais", "key").strip()), daemon=True, name="ais").start()
        else:
            log("[ais] skipped: add an aisstream.io key to config.ini")
    if cfg.getboolean("news", "enabled"):
        from .attention import Budget
        threading.Thread(target=Budget(cfg, log).run, daemon=True, name="news").start()
    threading.Thread(target=housekeeping, args=(con, cfg.getint("general", "keep_days")), daemon=True).start()
    from . import benchmark
    threading.Thread(target=benchmark.runner, args=(log,), daemon=True, name="benchmark").start()
    from . import archive, server as srv
    threading.Thread(target=archive.runner, args=(cfg, lambda c: srv.hotspots(c, 6, 200, watch=True), log),
                     daemon=True, name="archive").start()
    port = cfg.getint("general", "port")
    log(f"Ricochet running (Tripwire engine). Open http://localhost:{port}")
    try:
        serve(port, cfg.get("general", "host"))
    except KeyboardInterrupt:
        log("Stopped.")
    except Exception:
        log(traceback.format_exc())


if __name__ == "__main__":
    main()
