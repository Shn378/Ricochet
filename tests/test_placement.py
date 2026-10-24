import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from tracker import places as P
from tracker.collectors import news


class Placement(unittest.TestCase):
    """24 Oct audit: Moldova drone crash placed in Russia, Khoryv sinking at Romania's centre."""

    def test_where_beats_who(self):
        w = P.locate("Russian drone crashes near homes and energy site in Moldova, Sandu says", "UA")
        self.assertEqual(w["country"], "MD")
        self.assertEqual(P.locate("Strikes on Iran overnight")["country"], "IR")

    def test_towns_still_win(self):
        w = P.locate("Russian strike on Mykolaiv kills 3", "UA")
        self.assertEqual((w["country"], w["precision"]), ("UA", "town"))

    def test_facilities_and_seas(self):
        self.assertEqual(P.locate("Drone strike on Shaybah oil field kills two", "SA")["precision"], "site")
        sea = P.locate("Bulk carrier hit by drone sinks in the Black Sea, Romania says")
        self.assertEqual((sea["precision"], sea["country"], int(sea["lat"]), int(sea["lon"])), ("sea", "RO", 43, 29))
        self.assertEqual(P.locate("Houthis claim attack on ship in the Red Sea")["country"], "YE")

    def test_slug(self):
        s = P.locate_slug("https://www.turkiyetoday.com/world/romania-rescues-9-from-black-sea-ship-after-drone-attack-3230869", "RO")
        self.assertEqual(s["precision"], "sea")
        self.assertEqual(P.locate_slug("https://x.com/news/2026/10/23/drone-attack-on-constanta-port", "RO")["precision"], "town")
        # Section folders and loose spellings don't count
        self.assertIsNone(P.locate_slug("https://www.theadvocate.com/baton_rouge/opinion/letters-russia-china-trump/article_1", "US"))
        self.assertIsNone(P.locate_slug("https://cedarnews.net/newstasks/1094041/convicted-madera-county-drunk-driver-returns-13/", "KE"))
        self.assertIsNone(P.locate_slug("https://www.riotimesonline.com/puerto-rico-killings-what-we-know-2026/", "PR"))

    def test_drone_crash_is_an_incident(self):
        sig = news.to_signals([{"title": "Russian drone crashes near homes and energy site in Moldova", "summary": "",
                                "link": "https://e.com/a", "guid": "a", "published": 1}], {"name": "UP", "country": "UA"}, 100)
        self.assertEqual(len(sig), 1)
        self.assertEqual(sig[0]["country"], "MD")
        quiet = news.to_signals([{"title": "Drone show lights up Chisinau", "summary": "", "link": "https://e.com/b",
                                  "guid": "b", "published": 1}], {"name": "UP", "country": "UA"}, 100)
        self.assertEqual(quiet, [])


if __name__ == "__main__":
    unittest.main()
