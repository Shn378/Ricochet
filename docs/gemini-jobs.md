# Gemini job prompts

Paste one into Gemini (with the `Shn378/Ricochet` repo connected). Each is self-contained.
Gemini works on a `gemini/<topic>` branch and opens a pull request; it never touches `main`.
Fill in anything in `<angle brackets>`.

---

## 1. Noise review: label yesterday's hotspots

> Read GEMINI.md and CLAUDE.md. Fetch `https://ricochet.watch/api/hotspots?hours=24&watch=1&v=<timestamp>`.
> For every hotspot, open the linked signals (`/api/signals?hours=24&cell=<cell>`) and judge it
> real / noise / unclear, with a one-line reason and the noise type (sport coded as fighting,
> local crime, routine burn, mis-placement, duplicate story, other). Write a table to
> `docs/gemini/<date>-noise.md`, then a short summary: counts per noise type, the 5 worst
> repeat offenders (feeds, places or GDELT patterns), and up to 3 proposals labelled
> general / conditional / overfitting risk. Pull request on `gemini/noise-<date>`.

## 2. Placement test cases from live feeds

> Read GEMINI.md, CLAUDE.md and `tracker/places.py`. Take 60 recent headlines from
> `/api/signals?hours=24&feed=news&limit=50` (plus a second call later in the day) and, for each,
> write down where the story actually happened (town and country) by reading the article.
> Run each through `places.locate(title, feed_country)` and list every disagreement in
> `docs/gemini/<date>-placement.md`. Add the clear-cut ones as unit tests in
> `tests/test_placement.py` (expected-failure tests are fine: mark them with
> `@unittest.expectedFailure` and a comment). Don't change `places.py`. Pull request on
> `gemini/placement-cases-<date>`.

## 3. Named places: facilities and seas

> Read GEMINI.md and `tracker/named_places.json`. From the last 2 weeks of incidents in
> `docs/benchmark.json` and the brief's truth lists, find facilities, ports, islands, bases and
> sea areas that headlines name but the town gazetteer can't place. Add them to
> `named_places.json` with coordinates from Wikidata or GeoNames (cite the item ID for each in
> the pull request). Keep keys lowercase and distinctive (no generic words alone). Add a test per
> new entry. Pull request on `gemini/named-places-<date>`.

## 4. Benchmark deep-dive

> Read GEMINI.md and CLAUDE.md. Fetch `/api/benchmark?detail=1&v=<timestamp>`. For every missed
> incident, explain the miss from the frozen evidence (which feeds had signals, how far from
> normal, which rule held it back). For each switch still off, list its extra openings and judge
> 10 of them real/noise. Write `docs/gemini/<date>-benchmark.md`, ending with a ranked list of
> the changes most likely to gain hits without extra noise. Proposals only: don't edit
> `tracker/rules.py`. Pull request on `gemini/benchmark-<date>`.

## 5. Feed hunt for a region

> Read GEMINI.md and the candidate-sources notes in CLAUDE.md. Region: `<region or country>`.
> Find up to 10 local outlets or official bodies that publish incident news in RSS/Atom
> (local language is fine). For each: feed URL, language, sample of 3 recent headlines, whether
> its terms allow automated reading, and whether it blocks automated requests (if it does,
> drop it). Write `docs/gemini/<date>-feeds-<region>.md`. Don't add anything to
> `backbone.json`: probation is Shane's call. Pull request on `gemini/feeds-<region>`.

## 6. Incident words for a language

> Read GEMINI.md and `tracker/collectors/news.py` (INCIDENT / STRONG / NOT_INCIDENT). Language:
> `<language>`. Propose incident words in that language (with inflected forms, as the
> Ukrainian ones do), and a NOT_INCIDENT list for harmless meanings (sport, politics).
> Test them against 100 real headlines from a feed in that language: report hits, misses and
> false hits. Put the words behind nothing: open the pull request with the regex change and the
> test results in the description; Claude decides whether to merge. Branch
> `gemini/words-<language>`.

## 7. Tests for a module

> Read GEMINI.md. Module: `<tracker/...py>`. Write unit tests for its untested functions using
> small inline fixtures (no network, no real database: use `tempfile` like `tests/test_audit.py`).
> Don't change the module itself; if you find a bug, write a failing test marked
> `@unittest.expectedFailure` and describe it in the pull request. Branch `gemini/tests-<module>`.

## 8. Truth-list backfill

> Read GEMINI.md and `docs/benchmark.json`. Dates: `<from>` to `<to>`. Build the truth list of
> genuine security incidents for each day (Wikipedia Portal:Current events, wire coverage) with
> place, coordinates (cite the source), UTC time window and first public report time. Add them to
> `docs/benchmark.json` in the existing shape (id = `<place>-<day>`, audit = today's date).
> Skip anything already there. Pull request on `gemini/truth-<from>`.
