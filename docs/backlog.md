# Ricochet backlog and decisions

## Weekend progress (23-24 Oct) — read this first in a new session

Done 23 Oct (Fri night):
- VM moved to the free tier: `ricochet-us` (us-central1-a). Old `ricochet` (London) to be
  stopped and kept a week as a fallback. Check the bill in a week for the external IP charge.
- CLAUDE.md slimmed (backlog moved here); GEMINI.md written (Gemini's rules: never push main).
- Engine pass: `rules.evaluate()` is the one decision path (live, replay, benchmark). Replay now
  uses known events and ongoing countries. Laptop leftovers, /api/shutdown and .bat files removed.
- Benchmark harness (`tracker/benchmark.py`, `docs/benchmark.json` = 26 incidents). Proposed rules
  are switches in `rules.FLAGS`, all off: pair_signal, national_no_open, quake_strict, severity,
  listed_conflicts, median_baseline. Runs ~10 min after each deploy and daily; `/api/benchmark`.
- Snapshots: raw replies kept only when they bring new signals; FIRMS archive trimmed to 6 h.
- R2 archive (`tracker/archive.py`): daily digest + hourly hotspot log + frozen benchmark files,
  first upload after 01:00 UTC 24 Oct. SigV4 verified against AWS examples, not yet against R2.

Done 24 Oct (00:00-01:00):
- Health after the move: all 11 collectors OK, 46/46 news feeds, all baselines ready.
- First benchmark (commit 90b929d, 40 min on the e2-micro): current rules 11/26 hits, 4 Reported,
  median lead -165 min (hotspots open ~2¾ h AFTER the first public report). No flag met the
  ship rule (hits gained + zero extra openings), so all flags stay off; trade-offs sent to Shane:
  pair_signal +1 hit (mirage-20) / +14 openings; severity +2 (riyadh-20, tendelti-20) / +23;
  listed_conflicts +1 (bryansk-21) / +75 (mostly unplaced crop-burning fires);
  median_baseline +2 (riyadh-20, tendelti-20) / +85 (US local crime, base range fires);
  quake_strict 0 hits / 3 fewer openings; national_no_open no effect at all.
- Benchmark sped up ~14x (shared per-hour work, one baseline pass, low CPU priority); identical results.
- severity: death-toll regex no longer reads injury tolls or URL ids as deaths (rerun: +2 hits, +21 openings).
- **Live from 24 Oct (Shane approved): quake_strict and severity.** Watch in the audit: severity can borrow a
  toll from another story in the same square (Toronto/India national-centre cells) and one mis-placed GDELT
  story can open several squares (one Chinese "16 killed" story in 3 places).
- Benchmark speed-up barely showed on the server (2370 s -> 2274 s): the bottleneck is elsewhere. Profile
  with Shane's help: `cd ~/ricochet && python3 -m cProfile -s cumtime -m tracker.benchmark 2>&1 | head -40`.
- audit.py + /api/audit (auto-retires feeds refusing us 3+ days); threat levels on cards (UK, NL, FR);
  Met Office warnings as hazard context; new UI at ?ui=new (phone / &mode=wall / &mode=stream).
- New `hazards` collector (GDACS red/orange, NOAA PTWC/NTWC): context only until Shane decides
  whether GDACS red / tsunami warnings can open a hotspot. GOV.UK alerts parked: needs alert-area shapes.

Proposals from the 24 Oct audit (Shane to decide):
- US/Canada state- or county-level GDELT stories can't open a hotspot alone (12 of 24 noise hotspots).
- Place stories at ports, sites and offshore points, not country centres (Khoryv off Constanta sat at Romania's centre).
- Ukrainska Pravda (UA edition) + Kyiv Independent on probation. Also seen: UP (EN) stamps articles with the
  Ukrainian original's time, so arrivals run up to 5 h behind the stamp; and the Moldova miss ("drone crashed"
  not an incident word; "Russian" placed it in Russia).

Next, in order:
1. Check `/api/health` (all feeds back after the move) and `/api/benchmark`. If the benchmark
   errors, ask Shane for `journalctl -u ricochet --since -1h | grep -i bench`.
2. Read the benchmark: switch on (FLAGS default True) any flag with hits gained and zero extra
   openings; take trade-offs to Shane with the extra-opening examples. Sanity-check the extra
   openings by eye; the noise pass treats every new opening as a possible false alarm.
3. Check the first R2 upload in the log (`grep archive`).
4. BigQuery read-only key (walk Shane through the console: service account with BigQuery Job User
   + Data Viewer, JSON key into config.ini on the VM) for older GDELT replays and baselines.
5. Gemini morning audit: write its prompt (based on docs/morning-audit.md) writing to a Google Doc
   in a "Ricochet audits" Drive folder that Claude reads via the Drive connector.
6. Deterministic half of the audit as `audit.py` on the VM (feed health, auto-retire 3-day 403s).
7. Quick-win sources: GDACS + NOAA tsunami done (context); GOV.UK alerts (placement), threat levels, Met Office context still to do.
8. UI build (three modes, docs/ui) — biggest chunk; may slip to next weekend.
Done 24 Oct (from the 24 Oct audit): Sahel Intelligence moved to /feed (no slash); Arab News + LBCI on
probation; 12 incidents from 23 Oct added to docs/benchmark.json (38 total). Still pending: uptime alert in Cloud
Monitoring; site-name placement for stories (Shaybah miss) is not built yet.

Moved out of CLAUDE.md on 23 Oct to keep every session's context small. Read when planning work.

## Open items

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
- From the OSINT API list review (21 Oct): Wayback Machine "Save Page Now" for articles behind
  Reported hotspots (link the archived copy too; respect its rate limits), and PeeringDB internet
  exchange/data-centre locations as sensitive sites for outage cards. The rest of that list is
  people/domain investigation tooling: out of scope and against our principles.
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

### Optimisation passes (21 Oct; Saturday, before the benchmark)

- **Engine pass first.** Make replay and live share one decision path (replay currently skips
  the known-events calendar and the ongoing-country test), so benchmark results are trustworthy.
  Pull repeated logic (above-normal, conflict test, lead feed, site tagging) into one place;
  remove laptop-era leftovers (Windows take-over code, one-off migrations, old splash styles,
  .bat files if the HP is retired); cache expectations and country lookups for the e2-micro;
  add server/API tests.
- **Snapshot volume** (measured 21 Oct): ~210-230 MB/day of raw snapshots, ~3 GB on the VM at
  14 days. Likely mostly FIRMS (whole-day world file re-fetched every 30 min): store only new
  rows per fetch. R2 archive must stay selective: raw evidence behind hotspots + a daily signal
  digest (est. a few hundred MB/month), not everything (~6.5 GB/month). R2 is enabled (21 Oct),
  bucket `ricochet-evidence` created (Standard, ENAM), and a bucket-scoped Object Read & Write
  key is in the VM's config.ini under `[r2]` (account, bucket, access_key, secret_key).
  Saturday: build the uploader (S3-compatible, SigV4 in stdlib) to use it.
- **UI pass.** The layout dates from the "globe + one card" stream idea and has had a lot
  bolted on. Mock up 2-3 layouts first (phone-first, wall dashboard, stream view) for Shane
  to choose, then build once. Must make room for natural hazards as their own category,
  ongoing situations, a "Watching" list and the health/credits/intro pieces.
- **UI direction chosen (21 Oct):** Shane's design mockups are in `docs/ui/` (phone A1-A5,
  wall dashboard B, stream C, shared globe, design notes). Build as one page with three modes:
  phone (default on narrow screens, bottom tabs Tour/Situations/Watching/Signals/Feeds, card as
  pull-up sheet), wall (?mode=wall or wide screens, nothing scrolls), stream (?mode=stream, no
  controls, lower third + ticker). Hazards lime #c5d86d with swirl icon, labelled "not security
  incidents". Mockup content is made up; drop things we don't have data for (GDELT DOC article
  counts/tone, written "why" sentences until the AI investigator exists, rail-station site type),
  and fix credits (adsb.fi, not OpenSky).
  Decided: lime stays for hazards. Wall mode tours automatically; desktop/wall users can step
  with left/right arrow keys (and click), which pauses the tour; it resumes after 60 s untouched
  (phones keep the longer 3 min pause, since people read cards there).
- Order for Saturday: engine pass → benchmark → rule changes → UI mockups/build, with the
  quick-win sources fitted round them.


### Decided 23 Oct (Shane)

- Rule changes: Claude may ship any change that gains benchmark hits with zero extra false
  alarms; anything with a trade-off goes to Shane.
- BigQuery: yes, Shane will create a read-only service-account key (walk him through it).
- Move the VM to a free-tier US region: yes.

### Google Cloud free tier (21 Oct)

- **The VM isn't on the free tier.** Free e2-micro (plus 30 GB *standard* disk, 1 GB/month
  egress) only applies in us-west1, us-central1 or us-east1; ours is in europe-west2 (London),
  hence the ~£6-9/month. Moving it (new VM in us-central1, rerun install, re-point the tunnel)
  would make the VM itself free. Check first: whether the $300 new-account credit is what's
  covering it now, the external IPv4 charge, and egress (site traffic through the tunnel counts;
  above 1 GB/month is paid, so Cloudflare caching matters more once traffic grows).
- **BigQuery (1 TiB queries + 10 GiB storage/month free).** GDELT is a public BigQuery dataset
  (use the partitioned events tables to keep scans small). Uses: backfill and replay any
  benchmark incident without downloading 15-min zips; rebuild the rolling 4-week baselines
  from history; GKG/mentions for article counts and tone (dropped from the UI mockups for lack
  of data). Needs a read-only service-account key in config.ini.
- **Uptime check + email alert** (Cloud Monitoring) on ricochet.watch and /api/health, so Shane
  hears if the site or feeds go down.
- Minor/maybe: Natural Language API (5k units/month) for entity extraction on hotspot articles
  only; Cloud Run functions as a cheap off-VM watchdog. Not needed: Cloud Storage (R2 is
  bigger and has free egress), Firestore, Pub/Sub. Translation isn't on the free tier.
