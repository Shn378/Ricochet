Ricochet morning audit (runs daily 05:45 UK time)

_Copy of the scheduled task "Nightly Audit Report" (updated 24 Oct). Change both together._

Context: Ricochet (ricochet.watch) is Shane's global conflict/incident tracker.
Read CLAUDE.md in the GitHub repo Shn378/Ricochet first: it has the principles,
code map, current rules and backlog. The repo should be attached to this task;
if git refuses access, don't work around it: put the repairs in the
"For Claude to apply" block instead.
Public API: /api/hotspots?hours=24&watch=1, /api/signals?hours=24[&feed=x|&cell=lat:lon],
/api/health, /api/sources, /api/situations?all=1, /api/audit, /api/benchmark (?detail=1).
Use feed=, never source= (the fetch tool strips "source"). Add a unique
?v=<timestamp> to every request so cached copies aren't used.
Write results to the "Ricochet — Project Brief" doc
(https://claude.ai/artifact/X5uv6z76RkorKDt2tHbXwS), Replay log section, newest audit
first; if it can't be reached, write a dated doc in the Ricochet project and say so at the top.
Read the previous scorecards, benchmark list and candidate-sources table there first.

1. Truth list: the previous UTC day's genuine security incidents (attacks, strikes,
   explosions, riots, shootings, major outages, coups, significant military moves,
   major disasters hitting people). Sources: Wikipedia "Portal:Current events", ReliefWeb
   (RSS; the API needs an approved app name), major wire coverage, and a second
   independent incident record if reachable. Skip sport, politics without violence,
   routine crime. Say which sources you could and couldn't reach.

2. For each incident: first signal time, level, first feed, lead time vs the first public
   report; if missed, the likely reason (feed gap, wrong placement, rule held it back,
   below baseline). Note feeds whose timestamps run ahead of when we received the item
   (Ukrainska Pravda EN). Add every incident to the brief's benchmark list AND to
   docs/benchmark.json (same shape as existing entries).

3. Reverse check: every hotspot from the last 24h judged real / noise / unclear,
   one-line reason for noise.

3b. Source archaeology: earliest public trace per incident; whether we monitor it; whether
    it's readable automatically, free and allowed by its terms; platforms we can't read.
    Log candidates in the brief's "candidate sources" table with a tally of firsts.
    Anything first once and readable within its terms can be proposed for PROBATION
    ("probation": true in backbone.json). After 4 weeks, propose keep / extend / retire.
    Probation from 24 Oct: Arab News, LBCI, Ukrainska Pravda (UA), Kyiv Independent.
    Never propose ways around anti-bot blocks or access restrictions.

4. Maintenance. Start with /api/audit (server check at 04:xx UTC; it auto-retires feeds
   refusing us with 403/429 for 3+ days and lists them under for_claude: remove those from
   tracker/backbone.json). Then /api/health and /api/sources: failing, dead, quiet, silent,
   rate-limited feeds; baselines learning; collector errors. Check each entry in
   tracker/threat_levels.json against its official page.
   SAFE REPAIRS you may push (tests must pass; commit as Claude <noreply@anthropic.com>):
   retire a feed failing/dead 3+ days; update a feed address after confirming the new one
   returns a valid feed; remove a duplicate; add the day's incidents to docs/benchmark.json;
   a confirmed threat-level change. Never work around 403s, change rules, thresholds,
   scoring, trust, keys or config: propose those. Server diagnosis = a one-line SSH command
   for Shane.

4b. Benchmark: from /api/benchmark report current hits, Reported count, median lead, and per
    switch still off: hits gained/lost, extra openings. A switch with hits gained, none lost
    and zero extra openings may be switched on as a safe repair; trade-offs go to Shane with
    3-5 extra openings judged real/noise.

5. Scorecard: hits, misses, false alarms, median lead time, feed issues, up to 3 proposals
   with evidence, each labelled general improvement / conditional / overfitting risk.

6. End with a "For Claude to apply" block for anything you couldn't push (exact feed name,
   URL and evidence). Everything else is a proposal for Shane.

Shane reads it over the morning coffee: lead with the scorecard, short, UK English.
