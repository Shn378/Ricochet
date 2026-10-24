"""News feeds, the incident filter, and the attention budget."""
import configparser
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import helpers  # noqa: E402  (temp folders cleaned up after the run)

from tracker import attention, db  # noqa: E402
from tracker.collectors import news  # noqa: E402

RSS = b"""<?xml version="1.0"?><rss><channel>
<item><title>Independent MP Helen Haines critically injured after being hit by car in Wangaratta</title>
<link>https://abc.example/1</link><guid>abc-1</guid><pubDate>Mon, 19 Oct 2026 05:10:00 GMT</pubDate>
<description>Police say the act was deliberate; counter-terrorism police have been called in.</description></item>
<item><title>Matildas seal title clash with stunning comeback</title><link>https://abc.example/2</link></item>
<item><title>Man in his 60s dies of heart attack at Sydney fun run</title><link>https://abc.example/3</link></item>
<item><title>Interest rates held steady</title><link>https://abc.example/4</link></item>
</channel></rss>"""
ATOM = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Explosions heard in Kharkiv as drones attack energy sites</title><link href="https://x.example/a"/>
<id>tag:a</id><updated>2026-10-19T05:00:00Z</updated><summary>Regional governor reports damage.</summary></entry></feed>"""


class Feeds(unittest.TestCase):
    def test_rss_incident_only_and_placed(self):
        sigs = news.to_signals(news.parse_feed(RSS), {"name": "ABC", "country": "AU"}, int(time.time()))
        self.assertEqual(len(sigs), 1)
        s = sigs[0]
        self.assertEqual((s["place"], s["kind"], s["country"]), ("Wangaratta, Australia", "news_item", "AU"))
        self.assertEqual(s["observed_at"], 1792386600)
        self.assertTrue(s["extra"]["strong"])

    def test_atom_world_desk(self):
        sigs = news.to_signals(news.parse_feed(ATOM), {"name": "World", "country": None}, int(time.time()))
        self.assertEqual(sigs[0]["place"], "Kharkiv, Ukraine")


    def test_placement_rules(self):
        now = int(time.time())
        place = lambda title, cc, summary="": news.to_signals(
            [{"title": title, "summary": summary, "link": "https://x/" + title[:9], "guid": title, "published": now}],
            {"name": "F", "country": cc}, now)
        # foreign story on a national feed goes to the country it names
        self.assertEqual(place("Australian MP Helen Haines in critical condition after deliberate vehicle attack", "CA")[0]["place"], "Australia")
        # attacker named, target town in the feed's own country
        self.assertEqual(place("Russian drone strike damages four private homes in Mykolaiv", "UA")[0]["place"], "Mykolayiv, Ukraine")
        # town found in the summary when the headline has none
        self.assertEqual(place("Indi MP Helen Haines critically injured after 'attack' at electorate office", "AU",
                               "Ms Haines was hit by a car outside her office in Wangaratta this afternoon.")[0]["place"], "Wangaratta, Australia")
        # leader names point at countries
        self.assertEqual(place("Meloni calls emergency meeting after weekend of violent clashes", None, "Clashes erupted in Rome.")[0]["country"], "IT")
        # harmless uses of incident words
        self.assertEqual(place("Personal attacks fire up debate before Brazil's run-off vote", None), [])


class Budget(unittest.TestCase):
    def setUp(self):
        d = helpers.tmpdir()
        db.DATA, db.DB_PATH, db.RAW_DIR = d, d + "/t.db", d + "/raw"
        cfg = configparser.ConfigParser()
        cfg.read_dict({"news": {"budget_per_min": "6", "quiet_minutes": "30", "hot_minutes": "2", "world_desk_minutes": "5"}})
        self.b = attention.Budget(cfg, log=lambda m: None)

    def test_hot_countries_checked_faster(self):
        src = lambda cc, layer=2: {"layer": layer, "country": cc}
        self.b.hot_countries = {"AU"}
        self.assertEqual(self.b.interval(src("AU")), 120)
        self.assertEqual(self.b.interval(src("GB")), 1800)
        self.assertEqual(self.b.interval(src(None)), 300)
        self.assertEqual(self.b.interval(src("GB", 3)), 60)

    def test_due_order_and_backoff(self):
        now = int(time.time())
        with db._lock:
            self.b.con.execute("UPDATE sources SET last_checked=?", (now,))
            self.b.con.execute("UPDATE sources SET last_checked=? WHERE country='AU'", (now - 200,))
            self.b.con.execute("UPDATE sources SET last_checked=?, fails=3 WHERE name='SBS News'", (now - 200,))
            self.b.con.commit()
        self.b.hot_countries = {"AU"}
        due = [s["name"] for s in self.b.due(now)]
        self.assertIn("ABC News Just In", due)       # hot and 200s since last check: due
        self.assertNotIn("SBS News", due)            # same, but failing: backed off
        self.assertNotIn("BBC UK", due)              # quiet and just checked


if __name__ == "__main__":
    unittest.main()


class ConditionalChecks(unittest.TestCase):
    """An unchanged feed answers 304 and costs nothing; the validators are sent back next time."""
    def test_etag_round_trip(self):
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer
        from tracker.collectors import news
        body = b'<rss><channel><item><title>Explosion at depot in Kharkiv</title><link>https://e.com/a</link></item></channel></rss>'
        seen = []

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append(self.headers.get("If-None-Match"))
                if self.headers.get("If-None-Match") == '"v1"':
                    self.send_response(304); self.end_headers(); return
                self.send_response(200); self.send_header("ETag", '"v1"'); self.end_headers(); self.wfile.write(body)
            def log_message(self, *a):
                pass

        srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            feed = {"name": "t", "country": "UA", "layer": 2, "url": f"http://127.0.0.1:{srv.server_port}/feed", "last_ok": None}
            raw, items, sigs, newest, (etag, mod) = news.fetch(feed, 1_800_000_000)
            self.assertEqual((len(items), etag), (1, '"v1"'))
            raw, items, sigs, newest, (etag2, _) = news.fetch(dict(feed, etag=etag), 1_800_000_000)
            self.assertEqual((raw, items, sigs, etag2), (b"", [], [], '"v1"'))
            self.assertEqual(seen, [None, '"v1"'])
        finally:
            srv.shutdown()
