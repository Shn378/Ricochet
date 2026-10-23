import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from tracker import audit, db


class Audit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        db.DATA, db.DB_PATH, db.RAW_DIR = self.tmp, os.path.join(self.tmp, "t.db"), os.path.join(self.tmp, "raw")
        audit.RESULT, audit.RETIRED = os.path.join(self.tmp, "audit.json"), os.path.join(self.tmp, "retired.json")
        self.con = db.connect()
        audit.feeds(self.con, 0)   # creates the sources table
        now = self.now = int(time.time())
        add = lambda url, status, err, last_ok, added, layer=2: self.con.execute(
            "INSERT INTO sources (url, name, layer, status, last_error, last_ok, added, fails) VALUES (?,?,?,?,?,?,?,1)",
            (url, url, layer, status, err, last_ok, added))
        add("https://a/refused-long", "dead", "HTTP Error 403: Forbidden", now - 4 * 86400, now - 30 * 86400)
        add("https://b/refused-new", "failing", "HTTP Error 429: Too Many Requests", now - 86400, now - 30 * 86400)
        add("https://c/timeout", "dead", "timed out", now - 5 * 86400, now - 30 * 86400)
        add("https://d/never-ok", "dead", "HTTP Error 403: Forbidden", None, now - 4 * 86400)
        add("https://e/fine", "active", None, now, now - 30 * 86400, layer=5)
        self.con.commit()

    def test_only_long_refusals_are_retired(self):
        res = audit.run(self.con, log=lambda *a: None, now=self.now)
        self.assertEqual(sorted(x["url"] for x in res["retired_today"]), ["https://a/refused-long", "https://d/never-ok"])
        self.assertEqual(len(res["for_claude"]), 2)
        st = dict(self.con.execute("SELECT url, status FROM sources").fetchall())
        self.assertEqual(st["https://a/refused-long"], "retired")
        self.assertEqual(st["https://c/timeout"], "dead")          # not refused: reported, not retired
        self.assertEqual(st["https://b/refused-new"], "failing")   # under 3 days
        self.assertEqual(res["feeds"]["probation"][0]["url"], "https://e/fine")
        # Second run: nothing new, still listed
        res2 = audit.run(self.con, log=lambda *a: None, now=self.now + 60)
        self.assertEqual(res2["retired_today"], [])
        self.assertEqual(len(res2["auto_retired"]), 2)
        self.assertIn("https://a/refused-long", audit.load_retired())


if __name__ == "__main__":
    unittest.main()
