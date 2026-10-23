# Ricochet backlog and decisions

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
