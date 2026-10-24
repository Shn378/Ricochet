"""The at-a-glance strip: plain words from the engine's numbers, outages never given a cause."""
import unittest

from tracker import glance


class Glance(unittest.TestCase):
    def test_lines_order_and_wording(self):
        feeds = {
            "gdelt": {"observed": 3, "expected": 2.9, "z": 0.1, "ratio": 1.0, "elevated": False, "baseline": True, "n": 3},
            "news": {"observed": 6, "expected": 1.5, "z": 3.6, "ratio": 4.0, "elevated": True, "baseline": True, "n": 6},
            "ioda": {"observed": 2, "expected": 0.2, "z": 1.5, "ratio": 10.0, "elevated": False, "baseline": True, "n": 1},
            "adsb": {"observed": 1, "expected": 0, "z": 1, "ratio": None, "elevated": True, "baseline": False, "n": 1},
        }
        items = [{"source": "news", "kind": "news_item"}] * 6 + [{"source": "gdelt", "kind": "conflict_report"}] * 3 + \
                [{"source": "ioda", "kind": "internet_outage"}, {"source": "adsb", "kind": "gps_jamming"}]
        g = glance.build(feeds, items, {"name": "Shaybah", "kind": "oil refinery", "km": 4.2})
        self.assertEqual([x["feed"] for x in g["lines"]], ["news", "adsb", "ioda", "gdelt"])
        self.assertEqual(g["lines"][0]["text"], "6 news reports")
        self.assertEqual(g["lines"][0]["note"], "4.0× normal")
        self.assertEqual(g["lines"][1]["text"], "GPS jamming")
        self.assertEqual(g["lines"][1]["note"], "no normal level learnt yet")
        self.assertTrue(g["lines"][2]["note"].endswith("cause not confirmed"))
        self.assertEqual(g["lines"][3]["state"], "normal")
        self.assertEqual(g["active"], 3)
        self.assertEqual(g["site"], "Shaybah (oil refinery, 4.2 km)")

    def test_severity_and_unconfirmed_even_at_normal(self):
        g = glance.build({"news": {"z": 0.3, "ratio": 1.3, "elevated": True, "baseline": True, "n": 3, "severity": "report of 10 killed"},
                          "radar": {"z": 0, "ratio": 1.0, "elevated": False, "baseline": False, "n": 1, "observed": 1}},
                         [{"source": "news", "kind": "news_item"}] * 3 + [{"source": "radar", "kind": "traffic_anomaly"}])
        self.assertEqual(g["lines"][0]["note"], "report of 10 killed")
        self.assertIn("cause not confirmed", g["lines"][1]["note"])


if __name__ == "__main__":
    unittest.main()
