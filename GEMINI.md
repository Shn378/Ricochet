# Gemini in the Ricochet repo

Read `CLAUDE.md` first: the principles, code map and API notes there apply to you too.
`docs/backlog.md` has the backlog and Shane's decisions.

Your role: the scheduled research jobs (the morning audit in `docs/morning-audit.md`) and
code work Shane hands you from a written spec. Claude (Anthropic) leads design and rules.

Hard rules:
- Never push to `main`. `main` deploys straight to the live site. Work on a branch named
  `gemini/<topic>` and open a pull request, or write your findings to a doc.
- Never change rules, thresholds, source trust, keys or config yourself: propose them.
- Never work around a site's anti-bot blocks, 403s or 429s (no browser spoofing, no proxies).
- Never label an outage an attack; nothing is "confirmed" without an official statement or
  two independent sources.
- Secrets live only in `config.ini` on the server. Never put a key in the repo or a report.
- Stdlib-only Python. Run `python3 -m unittest discover -s tests` before any pull request.
- Shane writes in UK English and likes it short and informal.
