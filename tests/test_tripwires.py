import json
import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from tracker.collectors import adsb, usgs


def feature(i, lat, lon, depth, mag, typ="earthquake"):
    return {"id": i, "geometry": {"coordinates": [lon, lat, depth]},
            "properties": {"mag": mag, "type": typ, "place": "somewhere", "time": 1792400000000, "url": "u", "title": "t"}}


class SeismicTest(unittest.TestCase):
    def events(self, *f):
        return usgs.parse(json.dumps({"features": list(f)}).encode())

    def test_explosions_flagged_quarry_blasts_ignored(self):
        sig = usgs.to_signals(self.events(feature("a", 33.9, 35.5, 0, 3.3, "explosion"), feature("b", 40, -80, 0, 2.0, "quarry blast")), {})
        self.assertEqual([s["kind"] for s in sig], ["seismic_blast"])
        self.assertGreaterEqual(sig[0]["score"], 2.5)

    def test_shallow_quake_only_where_ground_is_quiet(self):
        busy = {(35, 139): 40}          # Japan shakes all the time
        sig = usgs.to_signals(self.events(feature("j", 35.5, 139.5, 8, 4.0), feature("q", 51.5, -0.1, 5, 3.0)), busy)
        self.assertEqual([s["uid"] for s in sig], ["seismic:q"])

    def test_deep_and_small_quakes_dropped(self):
        self.assertEqual(usgs.to_signals(self.events(feature("d", 51, 0, 80, 4.5), feature("s", 51, 0, 5, 1.2)), {}), [])


class AirspaceTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.row_factory = sqlite3.Row
        self.con.executescript(adsb.SCHEMA)

    def plane(self, nac=10, mil=False):
        return {"lat": 33.0, "lon": 35.0, "nac_p": nac, "dbFlags": 1 if mil else 0}

    def history(self, now, civil, poor):
        slot = now // 900
        for d in (1, 2, 3):
            self.con.execute("INSERT INTO airspace VALUES (?,?,?,?,?)", ("X", slot - 96 * d, civil, civil, poor))

    def test_airspace_emptying(self):
        now = 1792400000
        self.history(now, 60, 3)
        sig = adsb.judge_region("X", 33, 35, [self.plane() for _ in range(10)], now, self.con)
        self.assertEqual([s["kind"] for s in sig], ["airspace_drop"])

    def test_normal_traffic_quiet(self):
        now = 1792400000
        self.history(now, 60, 3)
        self.assertEqual(adsb.judge_region("X", 33, 35, [self.plane() for _ in range(55)], now, self.con), [])

    def test_gps_jamming_against_usual_share(self):
        now = 1792400000
        self.history(now, 40, 4)        # usually 10% poor
        planes = [self.plane(nac=4) for _ in range(20)] + [self.plane() for _ in range(20)]
        self.assertIn("gps_jamming", [s["kind"] for s in adsb.judge_region("X", 33, 35, planes, now, self.con)])

    def test_always_jammed_region_stays_quiet(self):
        now = 1792400000
        self.history(now, 40, 24)       # Baltic: usually 60% poor
        planes = [self.plane(nac=4) for _ in range(26)] + [self.plane() for _ in range(14)]
        self.assertEqual(adsb.judge_region("X", 33, 35, planes, now, self.con), [])

    def test_military_not_counted_as_civil(self):
        civil, _, _ = adsb.region_stats([self.plane(mil=True), self.plane()])
        self.assertEqual(len(civil), 1)


if __name__ == "__main__":
    unittest.main()


class RadarTest(unittest.TestCase):
    def test_outage_and_anomaly_parse(self):
        from tracker.collectors import radar
        outages = {"annotations": [{"id": "1", "locations": ["IR"], "startDate": "2026-10-19T18:00:00Z",
                                    "outage": {"outageCause": "GOVERNMENT_DIRECTED", "outageType": "NATIONWIDE"},
                                    "scope": "Nationwide", "description": "Shutdown during protests"}]}
        anomalies = {"trafficAnomalies": [{"uuid": "a", "startDate": "2026-10-19T19:00:00Z", "type": "LOCATION",
                                           "locationDetails": {"code": "GM", "name": "Gambia"}}]}
        o = radar.outage_signals(outages, 0)
        a = radar.anomaly_signals(anomalies, 0)
        self.assertEqual((o[0]["kind"], o[0]["place"]), ("internet_outage", "Iran"))
        self.assertGreaterEqual(o[0]["score"], 3)
        self.assertEqual(a[0]["country"], "GM")


class ShippingTest(unittest.TestCase):
    def setUp(self):
        from tracker.collectors import ais
        self.ais = ais
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(ais.SCHEMA)
        self.slot = 1792400000 // 900
        for d in (1, 2, 3):
            self.con.executemany("INSERT INTO shipping VALUES (?,?,?)", [(p, self.slot - 96 * d, 20) for p in ais.CHOKEPOINTS])

    def test_one_chokepoint_drops(self):
        counts = {p: 20 for p in self.ais.CHOKEPOINTS}
        counts["Bab el-Mandeb"] = 3
        sig = self.ais.judge_slot(self.con, self.slot, counts)
        self.assertEqual([s["place"] for s in sig], ["Bab el-Mandeb"])

    def test_everything_drops_is_the_feed(self):
        self.assertEqual(self.ais.judge_slot(self.con, self.slot, {p: 2 for p in self.ais.CHOKEPOINTS}), [])

    def test_counter_rolls_over_and_counts_distinct_ships(self):
        c = self.ais.Counter()
        t = (c.slot) * 900
        c.add(1, 12.5, 43.3, t); c.add(1, 12.6, 43.3, t); c.add(2, 12.5, 43.4, t)
        done = c.add(3, 0, 0, t + 900)
        self.assertEqual(done[1]["Bab el-Mandeb"], 2)
