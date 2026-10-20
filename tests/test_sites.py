"""Sensitive sites, the shared hotspot rules, fire clusters and Wikipedia burst tiers."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker import db, rules, sites  # noqa: E402
from tracker.collectors import firms, wiki  # noqa: E402


def feed(z, elevated, baseline=True):
    return {"z": z, "elevated": elevated, "baseline": baseline, "observed": 3.0}


class Sites(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        db.DATA, db.DB_PATH, db.RAW_DIR = d, d + "/t.db", d + "/raw"
        self.con = db.connect()
        self.con.executescript(sites.SCHEMA)
        self.con.executemany("INSERT INTO sites VALUES (?,?,?,?,?,?,?)", [
            ("Q1", "Wainwright Regional Training Area", "military base", 52.74344, -110.88224, "CA", 15),
            ("Q2", "Some Refinery", "oil refinery", 29.7, -95.0, "US", 5)])
        self.con.commit()
        sites.reset_cache()

    def test_near_and_tag(self):
        self.assertEqual(sites.near(self.con, 52.77, -110.92)["name"], "Wainwright Regional Training Area")
        self.assertIsNone(sites.near(self.con, 53.5, -110.9))           # ~85 km away
        fire = lambda lat, lon, n: {"source": "firms", "lat": lat, "lon": lon, "title": "Heat", "extra": {"detections": n}}
        kept = sites.tag_and_filter(self.con, [fire(52.77, -110.92, 2), fire(-15.5, 49.5, 2), fire(-15.5, 49.5, 6)])
        self.assertEqual(len(kept), 2)                                   # small cluster kept only at the base
        self.assertIn("Wainwright", kept[0]["title"])

    def test_rules(self):
        site = {"name": "Wainwright Regional Training Area", "kind": "military base"}
        # Wainwright: fires alone, not a conflict zone, but at a military base -> shown
        level, why, s = rules.decide({"firms": feed(6.0, True)}, "52:-111", {}, {"firms": site})
        self.assertEqual((level, s["name"]), ("signal", site["name"]))
        # Quiet site: a small fire (z below 3) where normally nothing burns still counts
        quiet = {"firms": {"z": 1.6, "elevated": False, "baseline": True, "observed": 1.6, "expected": 0.0}}
        self.assertEqual(rules.decide(quiet, "52:-111", {}, {"firms": site})[0], "signal")
        # Madagascar: fires alone, no site, no conflict -> held back
        self.assertIsNone(rules.decide({"firms": feed(9.0, True)}, "-16:49", {}, {})[0])
        # Refinery flares at their normal level: not elevated -> nothing
        flares = {"firms": dict(feed(0.5, False), expected=5.0, observed=6.0)}
        self.assertIsNone(rules.decide(flares, "29:-96", {}, {"firms": {"name": "R", "kind": "oil refinery"}})[0])
        # News near a site doesn't get the site lift (news is placed too loosely)
        self.assertEqual(rules.decide({"gdelt": feed(2.0, True)}, "52:-111", {}, {"gdelt": site})[0], None)


class Fires(unittest.TestCase):
    def test_nominal_only_cluster_counts_at_lower_score(self):
        head = "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_ti5,frp,daynight"
        rows = [f"52.76{i},-110.92{i},330,0.4,0.4,2026-10-17,2049,N,VIIRS,n,2.0NRT,290,10,D" for i in range(8)]
        out = firms.parse(("\n".join([head] + rows)).encode(), "VIIRS_NOAA20_NRT")
        self.assertEqual(len(out), 1)
        self.assertIn("nominal", out[0]["title"])
        self.assertLess(out[0]["score"], 2.0)


class WikiTiers(unittest.TestCase):
    def test_big_bursts_score_higher(self):
        tr, hits = wiki.BurstTracker(), []
        for i in range(36):
            h = tr.add("enwiki", "CFB Wainwright", f"u{i % 9}", 1000 + i * 120)
            if h:
                hits.append((h[2], h[3]))
        self.assertEqual([t for t, _ in hits], [0, 1, 2])
        self.assertEqual(hits[-1][1], 4.0)


if __name__ == "__main__":
    unittest.main()


class FiresOnlyConflictTest(unittest.TestCase):
    def test_fires_alone_need_an_ongoing_situation_nearby(self):
        from tracker import rules
        feeds = lambda: {"firms": {"observed": 40, "expected": 2, "z": 9, "elevated": True, "baseline": True}}
        # Farm burning in Arkansas: big jump, but no conflict situation in the US
        self.assertIsNone(rules.decide(feeds(), "35:-91", {}, {}, {"UA", "SD"})[0])
        # Same jump near the Ukrainian border (Kursk) counts
        self.assertEqual(rules.decide(feeds(), "51:36", {}, {}, {"UA", "SD"})[0], "signal")


class QuietSiteSizeTest(unittest.TestCase):
    def test_small_fire_near_a_range_is_not_lifted(self):
        from tracker import rules
        site = {"firms": {"name": "McAlester Army Ammunition Plant", "kind": "military base"}}
        small = {"firms": {"observed": 0.99, "expected": 0.0, "z": 1.0, "elevated": False, "baseline": True}}
        self.assertIsNone(rules.decide(small, "34:-96", {}, site, set())[0])
        big = {"firms": {"observed": 1.6, "expected": 0.0, "z": 1.6, "elevated": False, "baseline": True}}
        self.assertEqual(rules.decide(big, "52:-111", {}, {"firms": {"name": "Wainwright", "kind": "military base"}}, set())[0], "signal")


class LoneBaseFireTest(unittest.TestCase):
    site = {"firms": {"name": "Fort Campbell", "kind": "military base"}}
    big = staticmethod(lambda: {"firms": {"observed": 7.0, "expected": 0.19, "z": 6.2, "elevated": True, "baseline": True}})

    def test_lone_fire_at_base_waits_for_a_second_source(self):
        from tracker import rules
        self.assertEqual(rules.decide(self.big(), "36:-88", {}, self.site, set(), [("gdelt", "News report: shooting (Nashville) https://x.com/nashville-shooting")])[0], "watch")

    def test_news_nearby_promotes_it(self):
        from tracker import rules
        self.assertEqual(rules.decide(self.big(), "36:-88", {}, self.site, set(), [("gdelt", "News report: fight https://globalnews.ca/fort-campbell-fire-crews")])[0], "signal")

    def test_conflict_zone_does_not_wait(self):
        from tracker import rules
        self.assertEqual(rules.decide(self.big(), "51:36", {}, self.site, {"UA"}, [])[0], "signal")


class BackgroundFeedTest(unittest.TestCase):
    def test_everyday_news_does_not_make_it_reported(self):
        from tracker import rules
        feeds = {"infra": {"observed": 2.5, "expected": 0.0, "z": 2.5, "elevated": True, "baseline": False},
                 "gdelt": {"observed": 20.0, "expected": 19.0, "z": 0.2, "elevated": False, "baseline": True}}
        self.assertEqual(rules.decide(feeds, "51:0", {}, {}, set(), [])[0], "signal")

    def test_two_feeds_both_up_is_reported(self):
        from tracker import rules
        feeds = {"infra": {"observed": 2.5, "expected": 0.0, "z": 2.5, "elevated": True, "baseline": False},
                 "gdelt": {"observed": 40.0, "expected": 19.0, "z": 4.7, "elevated": True, "baseline": True}}
        self.assertEqual(rules.decide(feeds, "51:0", {}, {}, set(), [])[0], "reported")

    def test_base_fire_in_a_busy_city_still_waits(self):
        from tracker import rules
        feeds = {"firms": {"observed": 3.0, "expected": 0.2, "z": 2.5, "elevated": True, "baseline": True},
                 "gdelt": {"observed": 10.0, "expected": 11.0, "z": -0.3, "elevated": False, "baseline": True}}
        site = {"firms": {"name": "CFB St. Hubert", "kind": "air base"}}
        self.assertEqual(rules.decide(feeds, "45:-74", {}, site, set(), [("gdelt", "News report: fight (Montreal)")])[0], "watch")
