import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from tracker import baseline, benchmark, conflicts, db, rules, server


def feed(obs, exp=0.0, z=None, elevated=False, has_baseline=True, **kw):
    z = (obs - exp) / (exp + 1) ** 0.5 if z is None else z
    return dict({"observed": obs, "expected": exp, "z": z, "elevated": elevated, "baseline": has_baseline}, **kw)


class FlagRules(unittest.TestCase):
    def test_flags_live_set(self):
        # Switched on 24 Oct with Shane's approval; the rest stay off until benchmarked and approved
        self.assertEqual({k for k, v in rules.FLAGS.items() if v}, {"quake_strict", "severity"})

    def test_pair_signal(self):
        f = {"gdelt": feed(3.5, 1.0), "news": feed(3.5, 1.0)}
        self.assertIsNone(rules.decide(dict(f), "26:49", {}, flags=dict(rules.FLAGS, pair_signal=False))[0])
        lvl, why, _ = rules.decide({k: dict(v) for k, v in f.items()}, "26:49", {}, flags=dict(rules.FLAGS, pair_signal=True))
        self.assertEqual(lvl, "signal")
        self.assertIn("together", why)
        # One feed barely above normal isn't a pair
        weak = {"gdelt": feed(3.0, 1.0), "news": feed(1.2, 1.0)}
        self.assertIsNone(rules.decide(weak, "26:49", {}, flags=dict(rules.FLAGS, pair_signal=True))[0])

    def test_quake_swarm_is_not_a_test(self):
        swarm = {"seismic": feed(6.0, has_baseline=False, elevated=True, n=4, max=1.8)}
        self.assertEqual(rules.decide({k: dict(v) for k, v in swarm.items()}, "63:-24", {}, hot_countries=set(), flags=dict(rules.FLAGS, quake_strict=False))[0], "signal")
        self.assertIsNone(rules.decide(swarm, "63:-24", {}, hot_countries=set(), flags=dict(rules.FLAGS, quake_strict=True))[0])
        single = {"seismic": feed(5.5, has_baseline=False, elevated=True, n=1, max=5.5)}
        self.assertEqual(rules.decide(single, "41:129", {}, hot_countries=set(), flags=dict(rules.FLAGS, quake_strict=True))[0], "signal")

    def test_death_toll(self):
        self.assertEqual(rules.death_toll("houthi-strike-on-camp-kills-73"), 73)
        self.assertEqual(rules.death_toll("Death toll rises to 153 in Abyei"), 153)
        self.assertEqual(rules.death_toll("Dozens dead after market attack"), 24)
        self.assertIsNone(rules.death_toll("Remembering the 1918 dead"))
        # injury tolls and URL ids aren't death tolls
        self.assertEqual(rules.death_toll("Russian strike on Lozova market: Two killed, injury toll rises to 36"), None)
        self.assertIsNone(rules.death_toll("https://e.com/news/death-toll/20261022/1147"))
        self.assertEqual(rules.death_toll("Death toll climbs to 42 after floods"), 42)

    def test_severity_and_national_via_evaluate(self):
        hist = {"gdelt": 200, "news": 200}
        exp = {("14:46", "gdelt"): 4.0}
        items = [{"source": "gdelt", "kind": "conflict_report", "score": 2.0, "title": "x",
                  "url": "https://e.com/houthi-strike-on-army-camp-kills-73-soldiers", "extra": "{}"}]
        self.assertIsNone(rules.evaluate("14:46", items, exp, hist, flags={"severity": False})["level"])
        ev = rules.evaluate("14:46", items, exp, hist, flags={"severity": True})
        self.assertEqual(ev["level"], "signal")
        self.assertIn("73 killed", ev["reason"])
        nat = [{"source": "gdelt", "kind": "conflict_report_national", "score": 9.0, "title": "US", "url": "", "extra": "{}"}]
        self.assertEqual(rules.evaluate("38:-98", nat, {}, hist)["level"], "signal")
        self.assertIsNone(rules.evaluate("38:-98", nat, {}, hist, flags={"national_no_open": True})["level"])

    def test_na_admin_no_open(self):
        self.assertTrue(rules.na_admin({"source": "gdelt", "country": "US", "place": "Ohio, United States"}))
        self.assertTrue(rules.na_admin({"source": "gdelt", "country": "US", "place": "Clark County, Nevada, United States"}))
        self.assertTrue(rules.na_admin({"source": "gdelt", "country": "CA", "place": "British Columbia, Canada"}))
        self.assertFalse(rules.na_admin({"source": "gdelt", "country": "US", "place": "Gulfport, Mississippi, United States"}))
        self.assertFalse(rules.na_admin({"source": "news", "country": "US", "place": "Ohio, United States"}))
        self.assertFalse(rules.na_admin({"source": "gdelt", "country": "UK", "place": "Kent, United Kingdom"}))
        hist = {"gdelt": 200, "news": 200}
        st = [{"source": "gdelt", "kind": "conflict_report", "score": 3.0, "title": "x", "url": "", "extra": "{}",
               "country": "US", "place": "Ohio, United States"} for _ in range(6)]
        self.assertEqual(rules.evaluate("40:-83", st, {}, hist, flags={"na_admin_no_open": False})["level"], "signal")
        off = rules.evaluate("40:-83", st, {}, hist, flags={"na_admin_no_open": True})
        self.assertIsNone(off["level"])
        self.assertIn("state- or county-level", off["reason"])
        # A town-level report alongside can still open it
        town = st + [{"source": "gdelt", "kind": "conflict_report", "score": 3.0, "title": "y", "url": "", "extra": "{}",
                      "country": "US", "place": "Columbus, Ohio, United States"} for _ in range(6)]
        self.assertEqual(rules.evaluate("40:-83", town, {}, hist, flags={"na_admin_no_open": True})["level"], "signal")

    def test_listed_conflicts(self):
        self.assertTrue(conflicts.listed_near("50:30"))          # Kyiv
        self.assertTrue(conflicts.listed_near("18:-73"))         # Haiti
        self.assertTrue(conflicts.listed_near("52:34"))          # Bryansk, Russian border region
        self.assertFalse(conflicts.listed_near("55:37"))         # Moscow
        self.assertFalse(conflicts.listed_near("60:100"))        # Siberia
        fires = {"firms": feed(20.0, 1.0, z=19, elevated=True)}
        self.assertIsNone(rules.decide(dict(fires), "18:-73", {}, hot_countries=set())[0])
        self.assertEqual(rules.decide({"firms": dict(fires["firms"])}, "18:-73", {}, hot_countries=set(),
                                      flags=dict(rules.FLAGS, listed_conflicts=True))[0], "signal")


class Store(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        db.DATA, db.DB_PATH, db.RAW_DIR = self.tmp, os.path.join(self.tmp, "t.db"), os.path.join(self.tmp, "raw")
        self.con = db.connect()

    def test_median_baseline_forgets_one_bad_week(self):
        hour = int(time.time()) // 3600
        db.set_meta(self.con, "history_from:gdelt", hour - 28 * 24)
        # 3 quiet weeks (1/h), then one terrible week (20/h) just gone
        rows = [("15:44", "gdelt", h, 20.0 if h >= hour - 7 * 24 else 1.0) for h in range(hour - 28 * 24, hour - 6)]
        db.add_buckets(self.con, rows)
        mean, _ = baseline.expectations(self.con, 6, cells=["15:44"])
        med, _ = baseline.expectations(self.con, 6, cells=["15:44"], median=True)
        self.assertGreater(mean[("15:44", "gdelt")], 20)
        self.assertAlmostEqual(med[("15:44", "gdelt")], 6.0, delta=0.5)

    def test_benchmark_freezes_and_scores(self):
        now = int(time.time())
        start = now - 30 * 3600
        for s in ("gdelt", "news"):
            db.set_meta(self.con, f"history_from:{s}", (start // 3600) - 72)
        # An old signal so the window doesn't look pruned
        sig = lambda uid, src, lat, lon, t, score=2.0, url="": {"uid": uid, "source": src, "kind": "conflict_report", "lat": lat, "lon": lon,
                                                                "title": uid, "url": url, "score": score, "observed_at": t}
        db.add_signals(self.con, [sig("old", "gdelt", 0.5, 0.5, start - 20 * 3600)])
        db.add_signals(self.con, [sig(f"g{i}", "gdelt", 26.4, 49.8, start + 3600 + i * 60) for i in range(6)] +
                                 [sig("n1", "news", 26.5, 49.7, start + 4000, score=3)])
        self.con.execute("UPDATE signals SET fetched_at = observed_at + 300")   # collected 5 min after
        inc = {"id": "t1", "name": "test", "points": [[26.47, 49.80]],
               "start": time.strftime("%Y-%m-%dT%H:%M", time.gmtime(start)),
               "end": time.strftime("%Y-%m-%dT%H:%M", time.gmtime(start + 2 * 3600)),
               "first_report": time.strftime("%Y-%m-%dT%H:%M", time.gmtime(start + 4 * 3600))}
        path = os.path.join(self.tmp, "bench.json")
        with open(path, "w") as f:
            json.dump({"incidents": [inc]}, f)
        benchmark.INCIDENTS, benchmark.FROZEN, benchmark.RESULT = path, os.path.join(self.tmp, "frozen"), os.path.join(self.tmp, "res.json")
        res = benchmark.run(self.con, log=lambda *a: None)
        cur = res["detail"]["t1"]["current"]
        self.assertEqual(cur["level"], "reported")
        self.assertGreater(cur["lead_min"], 0)          # before the first public report
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "frozen", "t1.json")))
        self.assertEqual([v["variant"] for v in res["summary"]][0], "current")
        # The frozen copy is reused even after the signals are pruned
        self.con.execute("DELETE FROM signals")
        res2 = benchmark.run(self.con, log=lambda *a: None)
        self.assertEqual(res2["detail"]["t1"]["current"]["level"], "reported")

    def test_live_hotspots_still_work_through_evaluate(self):
        now = int(time.time())
        sig = lambda uid, src, lat, lon, score=1: {"uid": uid, "source": src, "kind": "k", "lat": lat, "lon": lon, "title": uid, "score": score, "observed_at": now}
        db.add_signals(self.con, [sig("d", "ioda", -5.5, 30.5, 7)])
        server._cache.clear()
        hs = {h["cell"]: h for h in server.hotspots(self.con)}
        self.assertEqual(hs["-6:30"]["level"], "signal")


if __name__ == "__main__":
    unittest.main()
