"""Tests each live feed once and reports what works.

Run with:  python -m tracker.check
Nothing is saved to the database.
"""
import io
import os
import sys
import time

from .collectors import adsb, firms, gdelt, ioda, wiki
from .run import load_config


def line(name, ok, detail):
    print(f"  {'OK  ' if ok else 'FAIL'}  {name:<6} {detail}")


def main():
    # Mirror output to data/check.txt so results can be reviewed remotely
    from . import db
    os.makedirs(db.DATA, exist_ok=True)
    buf = io.StringIO()
    class Tee:
        def write(self, s): sys.__stdout__.write(s); buf.write(s)
        def flush(self): sys.__stdout__.flush()
    sys.stdout = Tee()
    try:
        _main()
    finally:
        sys.stdout = sys.__stdout__
        with open(os.path.join(db.DATA, "check.txt"), "w", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S") + "\n" + buf.getvalue())


def _main():
    cfg = load_config()
    from .net import TRUSTSTORE
    print(f"Checking feeds (this takes up to a minute)... Windows certificate checks: {'on' if TRUSTSTORE else 'OFF (truststore not installed)'}\n")
    for name, fn in [("gdelt", gdelt.fetch), ("ioda", ioda.fetch), ("adsb", adsb.fetch)]:
        t = time.time()
        try:
            raw, sigs = fn()
            line(name, True, f"{len(sigs)} signals from {len(raw):,} bytes in {time.time() - t:.1f}s")
            if sigs:
                print(f"          e.g. {sigs[0]['title'][:90]}")
            elif name == "ioda":
                print(f"          raw starts: {raw[:200]!r}")
        except Exception as e:
            line(name, False, f"{type(e).__name__}: {e}")

    key = cfg.get("firms", "map_key").strip()
    if not key:
        line("firms", False, "no map key in config.ini yet (free: firms.modaps.eosdis.nasa.gov/api/map_key)")
    else:
        try:
            raw, sigs = firms.fetch(key)
            line("firms", True, f"{len(sigs)} heat clusters")
        except Exception as e:
            line("firms", False, str(e))

    try:
        c = wiki.coordinates("en.wikipedia.org", "Kharkiv")
        line("wiki", bool(c), f"coordinate lookup works, Kharkiv = {c}")
    except Exception as e:
        line("wiki", False, str(e))
    print("\nDone. Anything marked FAIL is skipped while the rest keep running.")


if __name__ == "__main__":
    main()
