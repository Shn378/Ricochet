# Ricochet: phase 1

*Powered by the Tripwire engine.*

Collects live tripwire signals, stores them with archived snapshots, and shows them on a spinning globe at http://localhost:8765.

Built to run lean on an old laptop. It uses plain Python with no extra packages, and SQLite for storage.

## What it watches

| Feed | What it gives | How often | Needs |
| --- | --- | --- | --- |
| GDELT | News reports of assaults, fighting and mass violence, with a location and a link to the article | Every 15 min | Nothing |
| IODA | Country-level internet outages | Every 10 min | Nothing |
| adsb.lol | Emergency squawks (7500, 7600, 7700) worldwide, plus military aircraft counts by area | Every 5 min | Nothing |
| Wikipedia | Edit bursts on place articles, and new articles that look like incidents | Live stream | Nothing |
| NASA FIRMS | Clusters of satellite heat detections | Every 30 min | Free map key |

A **hotspot** opens when two different feeds agree in the same 1° area, or when one feed fires strongly. Everything stays at Signal or Reported. Nothing is labelled Confirmed until the investigator agent and human review exist (phases 3 and 4).

## First-time setup (Windows)

1. Install Python 3.10 or newer from python.org. Tick **"Add python.exe to PATH"**.
2. Unzip this folder somewhere permanent, for example `Documents\conflict-tracker`.
3. Double-click **`check.bat`**. It tests every feed once and shows which work.
4. Double-click **`start.bat`**, then open http://localhost:8765.

### FIRMS heat data (optional, free)

Get a map key at https://firms.modaps.eosdis.nasa.gov/api/map_key. It only needs an email address. Then put it in `config.ini`:

```
[firms]
map_key = your_key_here
```

### Viewing it from another screen in the house

In `config.ini`, set `host = 0.0.0.0` under `[general]`. Then open `http://<laptop-ip>:8765` on the other device. Windows will ask to allow Python through the firewall; allow it on **private networks only**.

### Start automatically

Press Win+R, type `shell:startup`, and put a shortcut to `start.bat` in that folder.

## Splash screen

On opening, the page shows the Ricochet logo while the map and first live data load (at least 5 seconds; click to skip). To use the animated logo video instead, save it as `web/splash.mp4` (H.264 MP4). It plays muted, and the page waits for it to finish.

## Files

- `data/tracker.db`: all signals and collector runs (SQLite)
- `data/raw/<date>/`: gzipped snapshot of every feed response, named with its SHA-256. This is the start of the audit trail.
- `data/tracker.log`: what each collector did
- Data older than 14 days is pruned automatically (`keep_days` in `config.ini`).

## Developer notes

- Run the tests with `python -m unittest discover tests`.
- Collectors live in `tracker/collectors/`. Each returns `(raw_bytes, signals)`.
- API endpoints: `/api/hotspots?hours=6`, `/api/signals?hours=24&cell=lat:lon`, `/api/health`.
