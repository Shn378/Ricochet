"""AI card summaries: the input the model sees, the checks every answer must pass, the
daily cap and when a square is (re-)explained. No network: the model call is faked."""
import configparser
import json
import os
import time
import unittest

from tests import helpers
from tracker import db, explain

NOW = 1_800_000_000
H = {"cell": "47:13", "level": "reported", "place": "Berchtesgaden, Bayern, Germany", "lat": 47.62, "lon": 13.03, "first": NOW - 3600,
     "glance": {"lines": [{"feed": "wiki", "state": "spike", "text": "2 Wikipedia edit bursts", "note": "not usually seen here"},
                          {"feed": "gdelt", "state": "above", "text": "1 conflict event coded by GDELT", "note": "5.1× normal"}], "site": None}}
SIGS = [{"source": "gdelt", "kind": "conflict_report", "title": "News report: fight (Berchtesgaden, Bayern, Germany)",
         "url": "https://cyprus-mail.com/2026/10/24/fire-devastates-former-nazi-guest-house-eagles-nest?utm=x", "observed_at": NOW - 600},
        {"source": "wiki", "kind": "edit_burst", "title": "Edit burst, 15 edits by 6 editors: Kehlsteinhaus (dewiki)", "url": "https://de.wikipedia.org/wiki/Kehlsteinhaus", "observed_at": NOW - 700},
        {"source": "wiki", "kind": "edit_burst", "title": "Edit burst, 15 edits by 6 editors: Kehlsteinhaus (dewiki)", "url": "https://de.wikipedia.org/wiki/Kehlsteinhaus", "observed_at": NOW - 900}]
GOOD = {"title": "Unverified: possible fire at the Eagle's Nest", "summary": "An article address suggests a fire at the former guest house [S1]; editors are updating its Wikipedia article [S2].",
        "stories": [{"text": "An article address suggests a fire at the Eagle's Nest [S1].", "based_on": ["S1"]}], "supporting": ["S2"], "unrelated": [], "translations": {}, "gaps": "No news report yet."}


class Input(unittest.TestCase):
    def test_gdelt_becomes_a_hint_and_duplicates_go(self):
        text, refs = explain.build_input(H, SIGS)
        self.assertEqual(list(refs), ["S1", "S2"])
        self.assertIn('coded "fight" (Berchtesgaden, Bayern, Germany) · unverified link hint: cyprus-mail.com/2026/10/24/fire-devastates-former-nazi-guest-house-eagles-nest', text)
        self.assertNotIn("utm", text)
        self.assertIn("[wiki] 2 Wikipedia edit bursts · not usually seen here", text)


class Checks(unittest.TestCase):
    def setUp(self):
        _, self.refs = explain.build_input(H, SIGS)

    def test_good_answer_passes(self):
        self.assertIsNone(explain.check(json.loads(json.dumps(GOOD)), self.refs, H))

    def test_gemini_v1_title_rejected(self):   # its real first answer: stated the fire as fact, unattributed
        bad = dict(GOOD, title="Fire at former Nazi guest house in Berchtesgaden")
        self.assertIn("title not attributed", explain.check(bad, self.refs, H))

    def test_made_up_citation_rejected(self):
        self.assertIn("don't exist: S7", explain.check(dict(GOOD, summary="Reports say so [S7]."), self.refs, H))

    def test_confirmed_rejected_but_not_confirmed_is_fine(self):
        self.assertEqual(explain.check(dict(GOOD, summary="A fire is confirmed [S1]."), self.refs, H), 'says "confirmed"')
        self.assertIsNone(explain.check(dict(GOOD, summary="A fire has not been confirmed [S1]."), self.refs, H))

    def test_outage_needs_cause_not_confirmed(self):
        h = dict(H, glance={"lines": [{"feed": "ioda", "state": "spike", "text": "1 internet outage", "note": "x"}]})
        self.assertIn("cause not confirmed", explain.check(dict(GOOD, summary="An internet outage was detected [S1]."), self.refs, h))
        self.assertIsNone(explain.check(dict(GOOD, summary="An internet outage was detected, cause not confirmed [S1]."), self.refs, h))


class Loop(unittest.TestCase):
    def setUp(self):
        self.tmp = helpers.tmpdir()
        db.DATA, db.DB_PATH, db.RAW_DIR = self.tmp, os.path.join(self.tmp, "t.db"), os.path.join(self.tmp, "raw")
        self.con = db.connect()
        self.con.executescript(explain.SCHEMA)
        cfg = configparser.ConfigParser()
        cfg.read_dict({"gemini": {"enabled": "yes", "api_key": "k", "daily_cap_gbp": "0.01"}})
        self.st = explain.settings(cfg)
        self.calls = 0

    def fake(self, st, text):
        self.calls += 1
        return json.loads(json.dumps(GOOD)), 2000, 300

    def test_once_per_change_and_the_cap(self):
        run = lambda t, hs: explain.explain_once(self.con, self.st, hs, lambda c: SIGS, now=t, caller=self.fake, log=lambda *a: None)
        run(NOW, [H])
        self.assertEqual(self.calls, 1)
        self.assertEqual(explain.latest(self.con)["47:13"]["title"], GOOD["title"])
        run(NOW + 3 * 3600, [H])                      # nothing changed: no new call
        self.assertEqual(self.calls, 1)
        changed = dict(H, level="signal")
        run(NOW + 60, [changed])                      # changed, but within 30 min: wait
        self.assertEqual(self.calls, 1)
        run(NOW + 3600, [changed])
        self.assertEqual(self.calls, 2)
        # 2 calls ≈ £0.0013; a cap of 1p stops a burst of new squares before it's passed
        many = [dict(H, cell=f"{i}:0") for i in range(30)]
        explain.MAX_PER_PASS, old = 100, explain.MAX_PER_PASS
        try:
            run(NOW + 7200, many)
        finally:
            explain.MAX_PER_PASS = old
        self.assertLessEqual(explain.spent_today(self.con, NOW + 7200), self.st["daily_cap_gbp"] + 0.001)
        self.assertLess(self.calls, 32)

    def test_failed_call_is_retried_later(self):
        def broken(st, text):
            import urllib.error
            raise urllib.error.HTTPError("u", 403, "no", {}, None)
        explain.explain_once(self.con, self.st, [H], lambda c: SIGS, now=NOW, caller=broken, log=lambda *a: None)
        explain.explain_once(self.con, self.st, [H], lambda c: SIGS, now=NOW + 31 * 60, caller=self.fake, log=lambda *a: None)
        self.assertEqual(self.calls, 1)
        self.assertIn("47:13", explain.latest(self.con))


if __name__ == "__main__":
    unittest.main()
