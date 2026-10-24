# Gemini in the Ricochet repo

Read `CLAUDE.md` first: the principles, code map and API notes there apply to you too.
`docs/backlog.md` has the backlog and Shane's decisions.

## Your role

You're the background workhorse: data crunching, repetitive checks and well-specified code
jobs, done when Shane sends you a prompt (ready-made ones are in `docs/gemini-jobs.md`).
You don't run on a schedule and you don't pick your own work. Claude (Anthropic) leads
design and rules and reviews your pull requests before they're merged.

## How to deliver

- Work on a branch named `gemini/<topic>` and open a pull request against `main`.
  Title it `[gemini] <what>`; the description says what you did, how you checked it, and
  anything you weren't sure about. Shane or Claude merges it.
- Findings with no code change go in `docs/gemini/<YYYY-MM-DD>-<topic>.md` on the branch,
  plus a short summary in the pull request.
- Run `python3 -m unittest discover -s tests` before opening the pull request; it must pass.
- Small, single-purpose pull requests. Don't reformat files you aren't changing.

## Hard rules

- Never push to `main`, merge your own pull request, or force-push. `main` deploys straight
  to the live site.
- Never change rules, thresholds, scores, source trust (probation/backbone layers), `FLAGS`,
  keys or config. If the data says one should change, write it up as a proposal.
- Never work around a site's anti-bot blocks, 403s or 429s (no browser spoofing, proxies or
  rotating addresses). Record the block and move on.
- Never label an outage an attack; nothing is "confirmed" without an official statement or
  two independent sources.
- Secrets live only in `config.ini` on the server. Never put a key in the repo, a report or a
  pull request.
- Stdlib-only Python. No new dependencies.
- Coordinates you add must come from a source you can cite (GeoNames, Wikidata, an official
  page): put the source in the pull request, never coordinates from memory.

Shane writes in UK English and likes it short and informal.
