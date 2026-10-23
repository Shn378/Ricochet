Ricochet morning audit (runs daily 05:45 UK time)

Context: Ricochet (ricochet.watch) is Shane's global conflict/incident tracker.
Read CLAUDE.md in the GitHub repo Shn378/Ricochet first: it has the principles,
code map, current rules and backlog. You can't push to the repo: Shane pastes this
report into the Ricochet project session, where Claude applies the safe repairs.
Public API: /api/hotspots?hours=24&watch=1, /api/signals?hours=24[&feed=x|&cell=lat:lon],
/api/health, /api/sources, /api/situations?all=1.
Use feed=, never source= (the fetch tool strips "source"). Add a unique
?v=<timestamp> to every request so cached copies aren't used.
Write results to the "Ricochet — Project Brief" doc
(https://claude.ai/artifact/X5uv6z76RkorKDt2tHbXwS), Replay log section; if it can't
be reached, write a dated doc in the Ricochet project and say so at the top.
Read the previous scorecards, benchmark list and candidate-sources table there first.

1. Build the "truth list": the previous UTC day's genuine security incidents
   (attacks, strikes, explosions, riots, shootings, major outages, coups,
   significant military moves, major disasters hitting people). Sources:
   Wikipedia "Portal:Current events" for that date, ReliefWeb flash updates,
   major wire coverage, and a second independent incident record if reachable.
   Skip sport, politics without violence, routine crime. Say which sources you
   could and couldn't reach.

2. For each incident: did Ricochet show it? Record first signal time,
   level (signal/reported/watch), which feed fired first, and lead time
   vs the first public report. If missed, give the most likely reason
   (feed gap, wrong placement, rule held it back, below baseline).
   Add every truth-list incident (place, coordinates, UTC time window, first
   public report) to the running "benchmark" list in the brief, for replay tests.

3. Reverse check: list every hotspot from the last 24h and judge it
   real / noise / unclear, with a one-line reason for noise
   (e.g. sport coded as fighting, routine burn, mis-geocoding).

3b. Source archaeology. For each incident on the truth list (caught or
    missed), trace the earliest public trace you can find: first outlet,
    official/police/military account, Telegram channel, Wikipedia edit,
    sensor feed, airport or NOTAM notice, etc. Record:
    - the source, its time, and the gap to Ricochet's first signal
    - whether Ricochet already monitors it
    - if not: can it be read automatically (RSS, public API, public web
      page)? Is it free? Do its terms allow automated reading?
    - if it's on a platform we can't read (X, closed groups), say so.

    Log every candidate in the running "candidate sources" table in the
    brief, with a tally of how often it was first. No threshold for logging.

    Anything first even once and readable within its terms can be
    proposed as a PROBATION feed (backbone.json "probation": true: shown on
    cards, tallied, never raises a hotspot alone). After 4 weeks on
    probation, propose keeping, extending or retiring it, based on its
    tally of firsts and its noise.

    Never propose ways around a site's anti-bot blocks or access
    restrictions.

4. Maintenance. Check /api/health and /api/sources:
   - feeds failing, dead, quiet over 1 hour, silent for weeks, or rate-limited
   - baselines still learning, collector errors visible publicly
   SAFE REPAIRS (list each under "For Claude to apply" in the report, with the
   exact feed name and URL; Claude applies them without asking Shane):
   - retire a feed that has been failing or dead for 3+ days
   - update a feed to a new address, only after confirming the new
     address returns a valid feed
   - remove a duplicate feed
   Never: work around anti-bot blocks or 403s, change rules, thresholds,
   scoring or source trust, or touch keys and config. Propose those
   instead. If diagnosis needs the server, give Shane the exact one-line
   SSH command to run.

5. Write the scorecard: hits, misses, false alarms, median lead time,
   feed issues, and up to 3 proposed rule or source changes with evidence.
   Label each proposal: general improvement / conditional (one region,
   source or event type) / overfitting risk (explains one incident only).

6. End with a "For Claude to apply" block: the safe repairs from step 4 and any
   probation candidates, each with the exact feed URL you confirmed returns a
   valid feed today (Claude's session often can't fetch sites to check).
   A feed returning 403 or 429 on every check for 3+ days is retired, never
   worked around. Everything else is a proposal for Shane to approve. If something needs a look inside the
   server, list the exact one-line command for Shane to run in the VM's
   SSH window.

Keep it short: Shane reads it over the morning kettle.
