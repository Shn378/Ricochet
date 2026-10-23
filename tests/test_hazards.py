import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from tracker import db, rules
from tracker.collectors import hazards

GDACS = b"""<?xml version="1.0"?><rss xmlns:gdacs="http://www.gdacs.org" xmlns:geo="http://www.w3.org/2003/01/geo/wgs84_pos#"><channel>
<item><title>Red tropical cyclone X</title><link>http://www.gdacs.org/report.aspx?eventtype=TC&amp;eventid=1</link>
<pubDate>Fri, 23 Oct 2026 10:34:39 GMT</pubDate><gdacs:iscurrent>true</gdacs:iscurrent>
<gdacs:fromdate>Fri, 23 Oct 2026 10:19:16 GMT</gdacs:fromdate><geo:Point><geo:lat>18.5</geo:lat><geo:long>-72.3</geo:long></geo:Point>
<gdacs:eventtype>TC</gdacs:eventtype><gdacs:alertlevel>Red</gdacs:alertlevel><gdacs:eventid>1</gdacs:eventid><gdacs:episodeid>9</gdacs:episodeid>
<gdacs:country>Haiti</gdacs:country></item>
<item><title>Green earthquake</title><link>x</link><gdacs:fromdate>Fri, 23 Oct 2026 10:19:16 GMT</gdacs:fromdate>
<geo:Point><geo:lat>38</geo:lat><geo:long>14</geo:long></geo:Point><gdacs:eventtype>EQ</gdacs:eventtype><gdacs:alertlevel>Green</gdacs:alertlevel></item>
</channel></rss>"""

TSU = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom" xmlns:geo="http://www.w3.org/2003/01/geo/wgs84_pos#">
<author><name>NWS Pacific Tsunami Warning Center</name></author>
<entry><title>Off Honshu</title><updated>2026-10-23T10:45:01Z</updated><geo:lat>38.1</geo:lat><geo:long>142.9</geo:long>
<summary type="xhtml"><div xmlns="http://www.w3.org/1999/xhtml"><strong>Category:</strong> Warning<br/></div></summary>
<id>urn:uuid:abc</id><link rel="alternate" href="https://www.tsunami.gov/b.txt"/></entry></feed>"""

NOW = 1792760000   # 23 Oct 2026 ~13:33 UTC


class Hazards(unittest.TestCase):
    def test_gdacs_keeps_red_drops_green(self):
        sigs = hazards.parse_gdacs(GDACS, now=NOW)
        self.assertEqual([s["kind"] for s in sigs], ["gdacs_red"])
        self.assertTrue(sigs[0]["url"].startswith("https://"))
        self.assertEqual(sigs[0]["extra"]["hazard"], "tropical cyclone")

    def test_tsunami_category(self):
        sigs = hazards.parse_tsunami(TSU, now=NOW)
        self.assertEqual(sigs[0]["kind"], "tsunami_warning")
        self.assertIn("Pacific", sigs[0]["title"])

    def test_old_items_dropped(self):
        self.assertEqual(hazards.parse_gdacs(GDACS, now=NOW + 30 * 86400), [])

    def test_every_kind_is_context_only(self):
        # Until Shane decides otherwise, hazards never count towards a hotspot
        self.assertTrue(hazards.KINDS <= db.CONTEXT_KINDS)


if __name__ == "__main__":
    unittest.main()
