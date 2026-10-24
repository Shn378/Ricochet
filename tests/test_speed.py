"""The speed-ups must not change any answer: square-limited baselines match the full read,
and the shared API cache serves one copy between visitors."""
import os
import random
import time
import unittest

from tests import helpers
from tracker import baseline, db, rules, server


class SpeedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = helpers.tmpdir()
        db.DATA, db.DB_PATH, db.RAW_DIR = self.tmp, os.path.join(self.tmp, "t.db"), os.path.join(self.tmp, "raw")
        self.con = db.connect()

    def test_square_limited_baseline_matches_full_read(self):
        rnd = random.Random(4)
        hour = int(time.time()) // 3600
        for s in ("gdelt", "firms", "news"):
            db.set_meta(self.con, f"history_from:{s}", hour - 28 * 24)
        rows = [(f"{rnd.randint(10, 20)}:{rnd.randint(40, 50)}", rnd.choice(("gdelt", "firms", "news")), hour - rnd.randint(7, 28 * 24), rnd.random() * 3)
                for _ in range(4000)]
        db.add_buckets(self.con, rows)
        cells = ["15:44", "12:41", "20:50"]
        for median in (False, True):
            full, _ = baseline.expectations(self.con, 6, median=median)
            part, _ = baseline.expectations(self.con, 6, cells=cells, median=median)
            for c in cells:
                # What the rules read: every feed in its own square, fires' inherited level there,
                # and GDELT two squares out (conflict_zone)
                checks = [(c, s) for s in ("gdelt", "firms", "news")] + [(n, "gdelt") for n in rules.neighbours(c, radius=2)]
                for key in checks:
                    self.assertAlmostEqual(full.get(key, 0.0), part.get(key, 0.0), places=6, msg=(key, median))
                self.assertEqual(rules.conflict_zone(full, c), rules.conflict_zone(part, c))

    def test_shared_cache_and_hours_cap(self):
        sent = []

        class Fake(server.Handler):
            def __init__(self, path):
                self.path = path
            def _send(self, body):
                sent.append(body)

        server._responses.clear()
        calls = []
        real = server.health
        server.health = lambda con: calls.append(1) or {}
        try:
            Fake.con = self.con
            Fake("/api/health?v=1").do_GET()
            Fake("/api/health?v=2").do_GET()     # cache-buster ignored: same answer, no second query
        finally:
            server.health = real
        self.assertEqual(len(calls), 1)
        self.assertEqual(sent[0], sent[1])
        h = Fake("/api/signals?hours=99999&lite=1")
        h.do_GET()
        self.assertIn(("hours", "168"), h._key[1])


if __name__ == "__main__":
    unittest.main()
