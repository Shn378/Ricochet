# Ricochet, powered by the Tripwire engine

Shane Ellis's global conflict and incident tracker. Live at https://ricochet.watch.
Shane works in UK English, prefers a relaxed, informal tone and wants maximum autonomy
from Claude, within the guardrails below.

## What it is

A spinning globe (Sky News weather style) that pauses on hotspots, with a card for each
explaining why it's there and linking to the original reporting. The aim is to beat the
headlines by spotting second-order "echoes" (fires, outages, airspace emptying, seismic
blasts, shipping holding back, Wikipedia edit bursts) and fusing them with news.

Principles (decided, keep to them):
- **Ally to newsrooms, not a parasite.** Always link out; credit every data source.
- **Transparency.** Every card says why it's there, which feeds, how far above normal.
- **Nothing is confirmed** until there's an official statement or two independent sources.
  Infrastructure outages say "Cause not confirmed"; never label something an attack on our own.
- **Counter-disinformation** on the free tier: call out specific false narratives.
- **Respect anti-bot blocks and access restrictions.** Never spoof a browser, use proxies or
  otherwise work around a 403/429. Retire or replace the source instead.
- **Don't suppress, explain.** Expected events (bonfire season, Diwali, New Year) get a note
  and are held at Signal; nothing is hidden.

Positioning: small businesses and individuals first, transparent and affordable, not
beholden to corporate giants. Shane: "I would rather take one pound from a million people
than a million pounds from one person." Route: shop window (site/stream) → paid alerts →
small-team B2B (site-radius alerts, morning brief, security-occurrence-log style reports).
Budget: Shane self-funds it; running costs about £10/month, and he's happy with an AI
budget around a gym membership (suggested hard cap £2/day). Keep costs visible.

## Where things live

| Thing | Where |
|---|---|
| Code | GitHub `Shn378/Ricochet` (private). `main` deploys automatically. |
| Server | Google Cloud VM `ricochet`, e2-micro, europe-west2 (London), Debian. About £6-9/month. |
| Public site | Cloudflare Tunnel → `ricochet.watch` (domain on Cloudflare, ~$34/yr) |
| Secrets | `~/ricochet/config.ini` on the VM only (FIRMS map key, Cloudflare Radar token, aisstream key, R2 bucket key). Never in git, never repeated in chat. |
| Project brief | Claude Docs: "Ricochet — Project Brief" (concept, sourcing, lifecycle, evidence standards, positioning, replay log) |
| Old laptop copy | HP laptop `C:\Users\shane\Desktop\Ricochet\ricochet` (pre-cloud, legacy) |

## Deploying

Push to `main`. Every minute the VM's `ricochet-update` timer fetches, runs
`python3 -m unittest discover -s tests` in a temporary worktree, and only if tests pass
resets to the new commit and restarts the `ricochet` service (`data/deploys.log`).
Collectors keep their schedule across restarts, so deploys don't hit upstream APIs.
Commit as `Claude <noreply@anthropic.com>` (repo hook expects it).

The safety checks refused a remote command channel and a keyed diagnostics endpoint.
Don't rebuild those. For anything inside the server, give Shane a one-line command to run
in the Google Cloud SSH-in-browser window and ask for a screenshot.

Check the live site via the public API; add a throwaway `?v=<n>` because the web-fetch tool
caches pages for 15 minutes:
`/api/hotspots?hours=6[&watch=1]`, `/api/signals?hours=24[&cell=lat:lon|&feed=x|&country=FIPS]`
(use `feed=`, not `source=`: the web-fetch tool strips `source` as a tracking parameter),
`/api/health`, `/api/sources`, `/api/situations?all=1`, `/api/near` (tuning).
`/api/replay` and `/api/shutdown` only answer genuine local requests on the server.

## How it works (code map)

Stdlib-only Python, SQLite (WAL), D3 orthographic globe on canvas (`web/index.html`).

- `tracker/run.py`: config defaults, pollers, stream runners (Wikipedia, AIS), housekeeping.
- `tracker/collectors/`: one module per feed:
  - `gdelt` (15 min; CAMEO 18/19/20 + riots/crackdowns; URL-slug filter drops sport,
    downweights obituaries and mis-geocoded stories)
  - `ioda`, `radar` (Cloudflare outage annotations + traffic anomalies)
  - `firms` (NASA fire clusters), `wiki` (edit bursts, new incident articles)
  - `usgs` (explosion-type events; shallow quakes where the ground rarely shakes)
  - `adsb` (adsb.fi open data, non-commercial, 1 req/s, credit required: 7500 hijack squawks,
    military counts, and 10 flashpoint regions for airspace emptying + GPS jamming)
  - `ais` (aisstream.io WebSocket: ships per 15 min at 10 chokepoints)
  - `infra` (Google Cloud, Cloudflare, GitHub, Azure, AWS status pages)
  - `news` (RSS/Atom via the attention budget)
- `tracker/attention.py`: news "attention budget": 30 checks/min shared by heat.
  Backbone in `tracker/backbone.json` (46 feeds; Al Arabiya EN retired 22 Oct after 403s on probation). Layer 2 backbone, 3 leased (not built),
  4 earned (not built), **5 probation** (`"probation": true`: shown on cards/map, tallied,
  never counts towards a hotspot). Feeds removed from the JSON are marked retired.
- `tracker/baseline.py`: hourly buckets per 1° cell; same-hours-of-day expectations;
  z = (obs−exp)/sqrt(exp+1); elevated = z≥3 and ratio≥2 (FIRMS 4). Fixed rules when no baseline.
- `tracker/rules.py` (shared by live and replay): `decide()`. Reported = two feeds *above their
  own normal* (`above_normal`), one well above. Fires alone count only near an
  ongoing/escalating country, and a lone seismic event only near one, at a site, or if it
  scores 5+ (nuclear). Sensitive sites (Wikidata, 9,303) lift physical signals; a Wikipedia
  burst only counts as at a site if the article is about the site. A lone base fire outside
  conflict is held as **watch** until a nearby report names the site and an incident.
- `tracker/known_events.py`: calendar of expected events; the note follows the hotspot's lead feed.
- `tracker/server.py`: hotspots (confidence 0-100 orders the tour), API.
- `tracker/situations.py`: country-level Ongoing/Escalating from GDELT conflict share.
- `tracker/replay.py`: rebuilds a past incident's signals and simulates the live rules.
- `tracker/places.py` + `gazetteer.json.gz`: placing stories (GeoNames ≥15k, demonyms, leaders).

## Working method

Shane's "build the brain" loop: replay real incidents (Haines MP attack, CFB Wainwright
fire), find what was missed or wrongly flagged, fix the rule, prove the fix with a replay
and a test. A scheduled **morning audit** (05:45 UK) does this daily:
- builds a truth list and scores hits, misses, false alarms and lead time;
- does source archaeology into a candidate-sources table;
- runs a maintenance pass.

It lists safe repairs (retiring dead feeds, fixing confirmed feed addresses, removing
duplicates) for Claude to apply. Everything else, including rules, thresholds, trust, keys and config,
is a proposal for Shane to approve.

## Backlog and decisions

`docs/backlog.md` holds the backlog, ideas lists, Shane's decisions and the weekend plan.
Read it when planning work. Standing decisions:
- Claude may ship a rule change that gains benchmark hits with zero extra false alarms;
  anything with a trade-off goes to Shane.
- The morning audit can't push: Shane pastes its report here and Claude applies the
  "For Claude to apply" block (safe repairs only).
