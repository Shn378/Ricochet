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
| Secrets | `~/ricochet/config.ini` on the VM only (FIRMS map key, Cloudflare Radar token, aisstream key). Never in git, never repeated in chat. |
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
  Backbone in `tracker/backbone.json` (46 feeds + Al Arabiya EN on probation since 21 Oct). Layer 2 backbone, 3 leased (not built),
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

It may push only safe repairs: retiring dead feeds, fixing confirmed feed addresses and
removing duplicates. Everything else, including rules, thresholds, trust, keys and config,
is a proposal for Shane to approve.

## Open items / backlog

Done 21 Oct (from the first audit): Radio Okapi moved to its feedburner address; lone quakes
stay as dots; expected-event notes follow the lead feed; Wikipedia site matching fixed;
`?feed=` filter; future-dated feed items capped; Al Arabiya EN on probation. Still open from
the audit: Kyiv Independent and ICI Grand Est need confirmed feed addresses; ship coverage at
7/10 chokepoints; the "ground rarely shakes" test is weak outside the US (USGS lists mostly
M4+ abroad, so most places look quiet), so EMSC would help.

- Aircraft: adsb.lol rate-limited the VM; airplanes.live awaiting reply to Shane's email.
  Long-term: become a feeder (Raspberry Pi + aerial).
- Next sources: EMSC seismic stream, Bluesky Jetstream (ws client exists), alerts.in.ua
  (needs token), US State Dept travel advisories, public Telegram previews (t.me/s/), OpenSky.
- Story grouping by subject ("surge words"), finer gazetteer (≥1k), Wikipedia people pages
  without coordinates, scout leases (layer 3), promotion/demotion from audit scores.
- Phase 3 investigator: an AI model reads articles behind hotspots (budget ~£2/day cap).
- R2 evidence archive (raw snapshots currently pruned after 14 days).
- Cloudflare caching/bot protection when traffic grows; warm standby on the HP/Lenovo.
- Animated splash for the stream; YouTube/stream shop window.

### Ideas list, 21 Oct (Shane; for Saturday or later)

Suggested order: quick wins first (UK alerts, tsunami, threat levels), then Ukraine alerts
once the token arrives; terms of use drafted alongside.

- **Terms of use.** Disclaimer (not for life-safety decisions; nothing confirmed unless the
  card says so; follow official warnings), data credits and licences, short privacy note.
  Licence audit first: adsb.fi is non-commercial only, so aircraft data must stay free-tier or
  get a commercial deal before paid alerts; check aisstream, FIRMS, GDELT, Radar terms too.
  Solicitor check advised.
- **Protecting Tripwire.** Code is covered by copyright and the repo is private. The idea itself
  can't be owned, and UK software patents are impractical. Trade mark: "Tripwire" likely clashes
  with Tripwire Inc. (cybersecurity), so check and register "Ricochet" (UK IPO) instead.
- **Missile / air raid alerts.** Ukraine: alerts.in.ua (Shane to request token). Israel: Home
  Front Command feed reportedly answers Israeli connections only, so don't work around it; use
  news unless official access is offered. Taiwan: no public machine-readable feed known.
- **Emergency alert systems as sources.** GOV.UK alerts Atom feed (gov.uk/alerts/feed.atom);
  other countries via CAP (Common Alerting Protocol) feeds. Official, so strong evidence.
- **Threat levels on country cards.** UK (MI5), France (Vigipirate), Netherlands (NCTV), US
  (NTAS) etc. Rarely change, so a small hand-kept table that the morning audit checks daily;
  card line e.g. "UK national threat level: Substantial".
- **Tsunami warnings.** NOAA tsunami warning centre Atom feeds (tsunami.gov) plus GDACS for
  global cover; pair with the usgs collector (a warning helps tell real quakes from noise).
- **Site analytics.** Cloudflare dashboard traffic view works now (country-level, counts IPs not
  devices, bots included). Proposal: Cloudflare Web Analytics (free, cookieless) for device
  type, browser, referrers, with a line on the site saying what's counted. Needs Shane's OK.

### Audit method upgrades (from reviewing an outside take, 21 Oct; for the weekend)

- **Fixed benchmark, out-of-sample testing.** Every truth-list incident from the morning audit
  joins a growing benchmark set (start: the 13 from 21 Oct). Every proposed rule change is
  replayed old vs new across the whole set, not just the incident that inspired it; the
  proposal shows hits gained, hits lost, false alarms gained. Builds on `replay.py`.
- **Label every proposal:** general improvement / conditional (one region, source or event
  type) / overfitting risk (explains one incident, no shown predictive value).
- **Knowable-then vs learnt-later.** Lead time stays measured against first public report.
  Add a revisit of incidents weeks later to see if a warning sign only shows in hindsight;
  needs raw data kept beyond 14 days, so bump the R2 evidence archive up the list.
- **Second independent truth source.** 21 Oct the audit couldn't reach Wikipedia Current
  events or ReliefWeb; add a second incident record (e.g. ACLED, if its terms allow).
- **Redo the morning audit task** in this project (Shane moving it back from Smart Home).
  Fold in the benchmark and labels above; change the fallback (currently "a dated doc in the
  Smart Home project"); add `Shn378/Ricochet` to the task's own repo list so safe repairs can push.

### Weather and natural hazards (21 Oct; for the weekend)

- **Routine weather = context only.** Met Office warnings (and other countries' equivalents)
  as a background layer that never raises a hotspot. Used to explain cards: outages or
  evacuation stories inside a warned area get "Met Office amber wind warning in force here,
  weather the likely cause (not confirmed)". Named storms over the Highlands must not
  saturate the UK. Confirm the Met Office warning feeds before building. Pairs with the
  GOV.UK alerts item.
- **Major disasters = incident-worthy (Shane: a cat 5 heading for a population centre or a
  small island nation is an incident).** Use GDACS (UN/EU Global Disaster Alert and
  Coordination System, free RSS/API): its green/orange/red levels are impact-based (severity
  x people exposed), not raw intensity, so they filter out storms over empty sea or hills.
  Proposal: red can open a hotspot on its own as an official source; orange is a context
  note; green is ignored. Covers cyclones, floods, quakes, tsunamis, volcanoes. Optional
  extra: NHC/JTWC cyclone tracks for the forecast cone on the card.
- Show natural hazards as their own category (own label/colour, "Natural hazard"), never
  mixed with security, so the tour stays honest about what it is.
- GDACS trust: Shane is provisionally fine with GDACS getting a higher trust rating
  (red can open a hotspot alone), subject to change testing: replay old vs new rules
  against the benchmark set before going live, and keep it only if false alarms stay low.

### Chronic hotspots vs the baseline (21 Oct; for the weekend)

Problem: places that are always busy (Haiti, Yemen, Ukraine, Albania right now) set their own
bar so high that real incidents don't stand out. The 21 Oct audit missed the Yemen camp strike
(73 dead), the Tirana clashes and the overnight Ukraine strikes this way. Baselines currently
average all history since launch (same hours of day), so a long crisis keeps the bar high.
All three below are rules changes: replay against the benchmark set first, Shane approves.

- **Severity override.** A story with a casualty count over a threshold ("73 killed",
  "dozens dead") counts regardless of baseline.
- **Baseline that forgets.** Rolling 4-week median instead of an all-history average, so one
  terrible week doesn't raise the bar for months and an easing crisis lets it fall back.
- **Ongoing places get a proper presence.** Shade Ongoing/Escalating countries on the globe with
  a card of latest headlines, so chronic crises stay visible without beating the baseline.
- **Check Haiti.** It wasn't in the 21 Oct Ongoing list; may lack GDELT volume (situations.py
  MIN_TOTAL 400) and be falling through both nets.