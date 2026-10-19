"""Offline tests for every collector's parser and the hotspot logic.
Run with:  python -m unittest discover tests
"""
import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker import baseline, db, server, situations  # noqa: E402
from tracker.collectors import adsb, firms, gdelt, ioda, wiki  # noqa: E402


def gdelt_row(eid, root, lat, lon, geo_type="4", url="https://example.org/a", is_root="1", code=None, cc="UP", name="Kharkiv, Kharkivs'ka Oblast', Ukraine"):
    r = [""] * 61
    r[0], r[25], r[26], r[28], r[31], r[32] = eid, is_root, code or root + "0", root, "4", "3"
    r[51], r[52], r[53], r[56], r[57] = geo_type, name, cc, str(lat), str(lon)
    r[59], r[60] = "20261019120000", url
    return "\t".join(r)


class Parsers(unittest.TestCase):
    def test_gdelt_keeps_only_located_conflict_events(self):
        rows = [gdelt_row("1", "19", 49.98, 36.25), gdelt_row("2", "04", 49.98, 36.25),
                gdelt_row("4", "20", "", ""),
                gdelt_row("5", "19", 49.5, 36.5, url="https://example.org/b", is_root="0")]
        out = gdelt.parse_export("\n".join(rows).encode())
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["url"], "https://example.org/a")
        self.assertEqual(gdelt.latest_export_url("1 abc http://x/20261019.export.CSV.zip\n2 d http://x/m.mentions.CSV.zip"), "http://x/20261019.export.CSV.zip")

    def test_gdelt_riots_and_nationwide_reports_count(self):
        # France-style unrest: riots (protest category) and reports tagged to the whole country
        rows = [gdelt_row("1", "14", 48.86, 2.35, code="145", url="https://example.org/r1", cc="FR", name="Paris, France"),
                gdelt_row("2", "14", 46.0, 2.0, code="145", geo_type="1", url="https://example.org/r2", cc="FR", name="France"),
                gdelt_row("3", "14", 46.0, 2.0, code="141", geo_type="1", url="https://example.org/r3", cc="FR", name="France"),
                gdelt_row("4", "04", 46.0, 2.0, geo_type="1", url="https://example.org/r4", cc="FR", name="France")]
        counts = {}
        out = gdelt.parse_export("\n".join(rows).encode(), counts)
        self.assertEqual(sorted(s["kind"] for s in out), ["conflict_report", "conflict_report_national"])
        self.assertIn("riot", out[0]["title"])
        self.assertEqual((counts["FR"]["total"], counts["FR"]["conflict"], counts["FR"]["name"]), (4, 2, "France"))

    def test_ioda_accepts_location_string_and_entity_shapes(self):
        a = json.dumps({"data": [{"location": "country/IR", "location_name": "Iran", "start": 1760870000, "score": 250000},
                                 {"location": "asn/123", "start": 1}]}).encode()
        b = json.dumps({"data": [{"entity": {"type": "country", "code": "et", "name": "Ethiopia"}, "start": 1760870000}]}).encode()
        self.assertEqual(ioda.parse(a)[0]["country"], "IR")
        self.assertEqual(len(ioda.parse(a)), 1)
        self.assertEqual(ioda.parse(b)[0]["place"], "Ethiopia")

    def test_firms_clusters_and_drops_low_confidence(self):
        head = "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_ti5,frp,daynight"
        rows = [f"49.7{i},36.5{i},340,0.4,0.4,2026-10-19,{100 + i},N,VIIRS,{'h' if i == 0 else 'n'},2.0NRT,290,{20 + i},N" for i in range(5)]
        rows += ["10.1,20.1,330,0.4,0.4,2026-10-19,100,N,VIIRS,l,2.0NRT,290,5,N"] * 6
        out = firms.parse(("\n".join([head] + rows)).encode())
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["extra"]["detections"], 5)

    def test_adsb_squawks_and_military_counts(self):
        ac = adsb.parse_aircraft(json.dumps({"ac": [{"hex": "abc", "flight": "TEST1 ", "lat": 51.2, "lon": -1.1},
                                                    {"hex": "def", "lat": None, "lon": None}]}).encode())
        self.assertEqual(len(ac), 1)
        self.assertIn("TEST1", adsb.squawk_signals(ac, "7700", 1000)[0]["title"])
        mil = adsb.military_signals([{"lat": 51, "lon": -1, "t": "A400"}, {"lat": 52, "lon": -2, "t": "C17"}], 1000)
        self.assertEqual(mil[0]["extra"]["count"], 2)

    def test_wiki_burst_and_new_incident_article(self):
        tr = wiki.BurstTracker()
        lookup = lambda server, title: (50.0, 36.2)
        base = {"wiki": "enwiki", "namespace": 0, "server_name": "en.wikipedia.org", "title": "Vesnianka", "type": "edit"}
        out = [wiki.handle({**base, "user": f"u{i % 3}", "timestamp": 1000 + i}, tr, lookup) for i in range(6)]
        self.assertIsNone(out[4])
        self.assertEqual(out[5]["kind"], "edit_burst")
        new = wiki.handle({**base, "type": "new", "title": "2026 Vesnianka explosion", "timestamp": 2000}, tr, lookup)
        self.assertEqual(new["kind"], "new_incident_article")
        self.assertIsNone(wiki.handle({**base, "type": "new", "title": "Village pond", "timestamp": 2000}, tr, lookup))
        self.assertIsNone(wiki.handle({**base, "bot": True, "timestamp": 3000}, tr, lookup))


    def test_gdelt_one_article_counts_once(self):
        # The gangster funeral case: one article coded as several "fight" events
        rows = [gdelt_row(str(i), "19", 51.5, -0.12, url="https://example.org/funeral") for i in range(4)]
        out = gdelt.parse_export("\n".join(rows).encode())
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["extra"]["events"], 4)

    def test_firms_same_pass_has_same_id(self):
        head = "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_ti5,frp,daynight"
        rows = [f"49.7{i},36.5{i},340,0.4,0.4,2026-10-19,1{i}05,N,VIIRS,h,2.0NRT,290,20,N" for i in range(5)]
        a = firms.parse(("\n".join([head] + rows)).encode(), "S")
        b = firms.parse(("\n".join([head] + rows)).encode(), "S")
        self.assertEqual([s["uid"] for s in a], [s["uid"] for s in b])


class Store(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        db.DATA, db.DB_PATH, db.RAW_DIR = self.tmp, os.path.join(self.tmp, "t.db"), os.path.join(self.tmp, "raw")
        self.con = db.connect()

    def test_baseline_quiets_busy_city_but_catches_spike(self):
        now = int(time.time()); hour = now // 3600
        for s in ("gdelt", "firms", "ioda", "wiki", "adsb"):
            db.set_meta(self.con, f"live_from:{s}", hour - 1)
            db.set_meta(self.con, f"history_from:{s}", hour - 72)
        # History: London ~3 reports an hour; Vesnianka never appears
        db.add_buckets(self.con, [("51:-1", "gdelt", h, 1.5) for h in range(hour - 72, hour - 6) for _ in range(2)])
        sig = lambda uid, lat, lon, src="gdelt", score=1.5: {"uid": uid, "source": src, "kind": "k", "lat": lat, "lon": lon, "title": uid, "score": score, "observed_at": now - 600}
        # Now: an ordinary 6 hours in London, and a sudden cluster in a quiet town
        db.add_signals(self.con, [sig(f"lon{i}", 51.5, -0.1) for i in range(12)])
        db.add_signals(self.con, [sig(f"ves{i}", 49.7, 36.5) for i in range(6)] + [sig("fire", 49.8, 36.6, "firms", 3)])
        server._cache.clear(); hs = {h["cell"]: h for h in server.hotspots(self.con)}
        self.assertNotIn("51:-1", hs)                       # London at its normal level: quiet
        self.assertEqual(hs["49:36"]["level"], "reported")  # new activity + a second feed
        self.assertIn("gdelt", hs["49:36"]["elevated"])
        # A genuine London spike (10x normal) still trips it
        db.add_signals(self.con, [sig(f"spike{i}", 51.5, -0.1, score=5) for i in range(30)])
        server._cache.clear(); hs = {h["cell"]: h for h in server.hotspots(self.con)}
        self.assertEqual(hs["51:-1"]["level"], "signal")

    def test_ongoing_situation_needs_sustained_high_share(self):
        day = int(time.time()) // 86400
        mk = lambda total, conflict, name, lat: {"total": total, "conflict": conflict, "name": name, "lat": lat, "lon": 2.0}
        for d in range(day - 6, day + 1):
            db.add_country_counts(self.con, {
                "FR": mk(400, 120, "France", 46.0),      # 30% conflict all week: riots
                "US": mk(4000, 400, "United States", 38.0),  # loud, but 10%
                "GB": mk(1500, 150, "United Kingdom", 54.0),
                "XX": mk(300, 30, "Elsewhere", 0.0)}, d)
        res = situations.compute(self.con)
        ongoing = [c["name"] for c in res["countries"] if c["ongoing"]]
        self.assertEqual(ongoing, ["France"])

    def test_escalating_when_country_jumps_above_its_own_level(self):
        day = int(time.time()) // 86400
        mk = lambda total, conflict, name: {"total": total, "conflict": conflict, "name": name, "lat": 46.0, "lon": 2.0}
        for d in range(day - 6, day + 1):
            fr = mk(500, 35, "France") if d < day - 1 else mk(500, 130, "France")   # 7% all week, then 26%
            db.add_country_counts(self.con, {"FR": fr, "US": mk(5000, 400, "United States"), "DE": mk(2000, 160, "Germany")}, d)
        fr = [c for c in situations.compute(self.con)["countries"] if c["country"] == "FR"][0]
        self.assertEqual((fr["state"], fr["trend"]), ("escalating", "rising"))

    def test_dedupe_snapshot_and_hotspot_levels(self):
        now = int(time.time())
        snap, sha = db.save_snapshot("test", b"raw feed bytes")
        self.assertTrue(os.path.exists(os.path.join(self.tmp, snap)))
        self.assertEqual(len(sha), 64)
        sig = lambda uid, src, lat, lon, score=1: {"uid": uid, "source": src, "kind": "k", "lat": lat, "lon": lon, "title": uid, "score": score, "observed_at": now}
        added = db.add_signals(self.con, [sig("a", "gdelt", 49.5, 36.5), sig("a", "gdelt", 49.5, 36.5), sig("b", "firms", 49.6, 36.7),
                                          sig("c", "gdelt", 10.5, 10.5), sig("d", "ioda", -5.5, 30.5, 7)], snap, sha)
        self.assertEqual(added, 4)
        server._cache.clear(); hs = {h["cell"]: h for h in server.hotspots(self.con)}
        self.assertNotIn("49:36", hs)                           # two weak signals, no baseline yet: not enough
        self.assertEqual(hs["-6:30"]["level"], "signal")        # one strong signal
        self.assertNotIn("10:10", hs)                           # one weak signal: no hotspot
        h = server.health(self.con)
        self.assertIsNone(h["gdelt"]["ok"])


if __name__ == "__main__":
    unittest.main()
