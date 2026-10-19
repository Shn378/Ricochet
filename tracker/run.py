"""Runs every collector on its own schedule, plus the local web server.

Start with:  python -m tracker.run
Then open:   http://localhost:8765
"""
import configparser
import os
import re
import socket
import subprocess
import urllib.request
import threading
import time
import traceback
from datetime import datetime, timezone

from . import baseline, db, sites
from .collectors import adsb, firms, gdelt, ioda, wiki
from .server import serve

CONFIG_PATH = os.path.join(db.ROOT, "config.ini")
DEFAULTS = {
    "general": {"port": "8765", "host": "127.0.0.1", "keep_days": "14"},
    "firms": {"enabled": "yes", "map_key": "", "every_minutes": "30"},
    "gdelt": {"enabled": "yes", "every_minutes": "15"},
    "ioda": {"enabled": "yes", "every_minutes": "10"},
    "adsb": {"enabled": "yes", "every_minutes": "5"},
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
    while True:
        try:
            raw, signals = fn()
            signals = sites.tag_and_filter(con, signals)
            snap, sha = db.save_snapshot(name, raw)
            added = db.add_signals(con, signals, snap, sha)
            if name == "gdelt" and gdelt.LAST_COUNTS.get("_slot") and gdelt.LAST_COUNTS["_slot"] != db.get_meta(con, "last_slot:gdelt"):
                slot_ts = int(datetime.strptime(gdelt.LAST_COUNTS["_slot"].rsplit("/", 1)[-1][:14], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc).timestamp())
                db.add_country_counts(con, gdelt.LAST_COUNTS, slot_ts // 86400)
                db.set_meta(con, "last_slot:gdelt", gdelt.LAST_COUNTS["_slot"])
            db.log_run(con, name, True, added, f"{len(signals)} signals, {added} new")
            log(f"[{name}] ok: {len(signals)} signals, {added} new")
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
    db.log_run(con, "wiki", True, 0, "stream starting")
    wiki.stream(on_signal, on_error)


def housekeeping(con, keep_days):
    while True:
        try:
            db.prune(con, keep_days)
        except Exception as e:
            log(f"[prune] failed: {e}")
        time.sleep(6 * 3600)


def port_busy(port):
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def take_over(port):
    """If an older Ricochet is already running, stop it so this one can start.
    Means restarting is just: run start.bat again."""
    if not port_busy(port):
        return
    log("An older Ricochet is running; stopping it first")
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/api/shutdown", timeout=3).read()
    except Exception:
        pass
    for _ in range(10):
        time.sleep(0.5)
        if not port_busy(port):
            return
    if os.name == "nt":  # older versions had no shutdown address: end that process directly
        try:
            out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, timeout=10).stdout
            for line in out.splitlines():
                m = re.search(rf"127\.0\.0\.1:{port}\s+\S+\s+LISTENING\s+(\d+)", line) or re.search(rf"0\.0\.0\.0:{port}\s+\S+\s+LISTENING\s+(\d+)", line)
                if m and int(m.group(1)) != os.getpid():
                    subprocess.run(["taskkill", "/PID", m.group(1), "/F"], capture_output=True, timeout=10)
                    log(f"Stopped the older engine (process {m.group(1)})")
        except Exception as e:
            log(f"Couldn't stop the older engine: {e}")
        time.sleep(1)


def main():
    cfg = load_config()
    take_over(cfg.getint("general", "port"))
    con = db.connect()
    db.rescore_ioda(con)
    db.migrate_v2(con)
    jobs = []
    if cfg.getboolean("gdelt", "enabled"):
        jobs.append(("gdelt", gdelt.fetch, cfg.getint("gdelt", "every_minutes")))
    if cfg.getboolean("ioda", "enabled"):
        jobs.append(("ioda", ioda.fetch, cfg.getint("ioda", "every_minutes")))
    if cfg.getboolean("adsb", "enabled"):
        jobs.append(("adsb", adsb.fetch, cfg.getint("adsb", "every_minutes")))
    if cfg.getboolean("firms", "enabled"):
        key = cfg.get("firms", "map_key").strip()
        if key:
            jobs.append(("firms", lambda: firms.fetch(key), cfg.getint("firms", "every_minutes")))
        else:
            log("[firms] skipped: add your free map key to config.ini to switch on heat detection")
    sites.refresh(con, log)   # Wikidata list of sensitive sites, monthly
    baseline.prepare(con, ["gdelt", "ioda", "firms", "wiki", "adsb", "news"])
    key = cfg.get("firms", "map_key").strip() if cfg.getboolean("firms", "enabled") else None
    threading.Thread(target=baseline.backfill, args=(db.connect(), key, log), daemon=True, name="backfill").start()
    for i, (name, fn, every) in enumerate(jobs):
        threading.Thread(target=poller, args=(con, name, fn, every), daemon=True, name=name).start()
        time.sleep(2)  # stagger first runs so the laptop isn't hit all at once
    if cfg.getboolean("wiki", "enabled"):
        threading.Thread(target=wiki_runner, args=(con,), daemon=True, name="wiki").start()
    if cfg.getboolean("news", "enabled"):
        from .attention import Budget
        threading.Thread(target=Budget(cfg, log).run, daemon=True, name="news").start()
    threading.Thread(target=housekeeping, args=(con, cfg.getint("general", "keep_days")), daemon=True).start()
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
