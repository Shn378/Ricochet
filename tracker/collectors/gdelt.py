"""GDELT 2.0 event export, published every 15 minutes.

We keep only material-conflict events (CAMEO root codes 18 assault, 19 fight,
20 unconventional mass violence) that have a location and a source article.
Each signal links out to the article that reported it.
"""
import csv
import hashlib
import io
import urllib.error
import zipfile
from datetime import datetime, timedelta, timezone

from ..net import get

NAME = "gdelt"
BASE = "http://data.gdeltproject.org/gdeltv2/"
LASTUPDATE = BASE + "lastupdate.txt"
CONFLICT_ROOTS = {"18": "assault", "19": "fight", "20": "mass violence"}
# Also conflict, filed under other categories: riots sit under "protest" (14),
# violent crackdowns under "coerce" (17)
CONFLICT_CODES = {"145": "riot", "1451": "riot", "1452": "riot", "1453": "riot", "1454": "riot", "175": "violent crackdown"}
LAST_COUNTS = {}   # per-country totals from the most recent fetch, for "ongoing situations"

# Column positions in the GDELT 2.0 events export (61 columns)
C_ID, C_ISROOT, C_CODE, C_ROOT, C_QUAD, C_GOLD, C_MENTIONS, C_SOURCES = 0, 25, 26, 28, 29, 30, 31, 32
C_GEO_TYPE, C_GEO_NAME, C_GEO_CC, C_LAT, C_LON, C_ADDED, C_URL = 51, 52, 53, 56, 57, 59, 60


def latest_export_url(text):
    for line in text.splitlines():
        parts = line.split()
        if parts and parts[-1].endswith(".export.CSV.zip"):
            return parts[-1]
    raise ValueError("No export file listed in lastupdate.txt")


def kind_of(root, code):
    if code in CONFLICT_CODES:
        return CONFLICT_CODES[code]
    return CONFLICT_ROOTS.get(root)


def parse_export(csv_bytes, counts=None):
    """One signal per article per 1-degree area. GDELT often codes several events
    from a single article; counting each would let one story look like a wave.

    If `counts` (a dict) is given, it is filled with per-country totals of all
    main events and conflict events: the raw material for "ongoing situations"."""
    best = {}
    rows = csv.reader(io.StringIO(csv_bytes.decode("utf-8", "replace")), delimiter="\t")
    for r in rows:
        if len(r) < 61 or r[C_ISROOT] != "1":   # only what each article is mainly about
            continue
        root, code, cc = r[C_ROOT], r[C_CODE], r[C_GEO_CC]
        what = kind_of(root, code)
        if counts is not None and cc:
            c = counts.setdefault(cc, {"total": 0, "conflict": 0, "name": None, "lat": None, "lon": None})
            c["total"] += 1
            if what:
                c["conflict"] += 1
            if r[C_GEO_TYPE] == "1" and r[C_LAT]:
                c["name"], c["lat"], c["lon"] = r[C_GEO_NAME], float(r[C_LAT]), float(r[C_LON])
            elif not c["name"] and r[C_GEO_NAME]:
                c["name"] = r[C_GEO_NAME].split(",")[-1].strip()
        if not what or not r[C_LAT] or not r[C_LON] or not r[C_URL]:
            continue
        try:
            lat, lon = float(r[C_LAT]), float(r[C_LON])
            when = datetime.strptime(r[C_ADDED], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        national = r[C_GEO_TYPE] == "1"   # placed at the country's centre, not a town
        url = r[C_URL]
        cell = f"{int(lat // 1)}:{int(lon // 1)}"
        key = hashlib.sha1(f"{url}|{cell}".encode()).hexdigest()[:16]
        score = min(5.0, 1 + int(r[C_SOURCES] or 1) / 2) * (0.5 if national else 1.0)
        if key in best and best[key]["score"] >= score:
            best[key]["extra"]["events"] += 1
            continue
        events = best[key]["extra"]["events"] + 1 if key in best else 1
        best[key] = {
            "uid": f"gdelt:{key}",
            "source": NAME,
            "kind": "conflict_report_national" if national else "conflict_report",
            "lat": lat, "lon": lon,
            "place": r[C_GEO_NAME],
            "country": cc,
            "title": f"News report: {what} ({r[C_GEO_NAME]})",
            "url": url,
            "score": score,
            "observed_at": int(when.timestamp()),
            "extra": {"cameo": code, "mentions": r[C_MENTIONS], "sources": r[C_SOURCES], "events": events},
        }
    return list(best.values())


def candidate_urls(now=None):
    """Export files for the last few 15-minute slots, newest first."""
    now = now or datetime.now(timezone.utc)
    slot = now.replace(minute=now.minute - now.minute % 15, second=0, microsecond=0)
    return [f"{BASE}{(slot - timedelta(minutes=15 * i)).strftime('%Y%m%d%H%M%S')}.export.CSV.zip" for i in range(8)]


def fetch_slot(when):
    """One historical 15-minute export, for backfilling. Returns (signals, country_counts)."""
    url = f"{BASE}{when.strftime('%Y%m%d%H%M%S')}.export.CSV.zip"
    try:
        blob = get(url, timeout=120)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return [], {}
        raise
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        counts = {}
        sigs = parse_export(z.read(z.namelist()[0]), counts)
        return sigs, counts


def fetch():
    """Returns (raw_bytes_for_archive, signals)."""
    urls = []
    try:
        urls.append(latest_export_url(get(LASTUPDATE, timeout=30).decode()))
    except Exception:
        pass  # lastupdate.txt unavailable: fall back to computed slot names
    urls += [u for u in candidate_urls() if u not in urls]
    last_err = None
    for url in urls:
        try:
            blob = get(url, timeout=120)
        except urllib.error.HTTPError as e:
            if e.code == 404:  # not published yet; try the previous slot
                last_err = e
                continue
            raise
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            data = z.read(z.namelist()[0])
        counts = {}
        sigs = parse_export(data, counts)
        LAST_COUNTS.clear()
        LAST_COUNTS.update({"_slot": url, **counts})
        return blob, sigs
    raise RuntimeError(f"No GDELT export found in the last 2 hours ({last_err})")
