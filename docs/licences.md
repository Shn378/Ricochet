# Data licences: what the free site can use, and what blocks paid alerts

Audit 24 Oct 2026 from each source's own pages (links below). **Not legal advice**: have a solicitor
check before charging anyone. Short version: the free site is fine with everything we use;
**paid alerts can't include aircraft (adsb.fi) or Cloudflare Radar data** without a commercial
deal, and IODA, aisstream.io, the Met Office and the news RSS feeds need their terms confirmed first.

| Source | Licence / terms | Free site | Paid alerts | What we must do |
|---|---|---|---|---|
| GDELT | Unrestricted, incl. commercial; redistribution allowed | Yes | Yes | Cite GDELT and link gdeltproject.org (credits do) |
| NASA FIRMS | NASA full and open data sharing; provided "as is" | Yes | Yes | Pass on (or link) the LANCE disclaimer: not for tactical decisions or local-scale conditions; acknowledge LANCE/ESDIS |
| USGS earthquakes | US public domain | Yes | Yes | Credit "U.S. Geological Survey" |
| NOAA tsunami centres | US government work, public domain | Yes | Yes | Credit NOAA |
| GDACS | CC BY 4.0 (stated in its feed) | Yes | Yes | Credit GDACS |
| Wikidata (sites) / GeoNames (places) | CC0 / CC BY 4.0 | Yes | Yes | Credit GeoNames |
| Wikimedia EventStreams | Public stream; edits are CC BY-SA content | Yes | Probably (we use counts, not text) | Credit Wikimedia; **confirm** stream terms and User-Agent rules |
| **adsb.fi** (aircraft) | "Personal, non-commercial use only"; no licensing or selling; 1 req/s; must cite and link | Yes (non-commercial) | **No** | Commercial deal, or keep aircraft out of paid products |
| **Cloudflare Radar** | API data **CC BY-NC 4.0** | Yes (non-commercial) | **No** | Commercial permission from Cloudflare, or keep Radar out of paid products |
| IODA (Georgia Tech) | Not confirmed (its about page blocks automated reading) | Probably | **Unknown** | Shane to read the terms in a browser or email the team |
| aisstream.io (shipping) | Free; publishes limits but **no terms or licence**; no SLA | Yes | **Unknown** | Ask aisstream.io in writing before using it commercially |
| Met Office warnings RSS | Crown copyright; RSS terms not reached | Yes (context only) | **Unknown** | Confirm the RSS terms |
| News RSS feeds (BBC etc.) | Publisher by publisher; many RSS terms are personal/non-commercial | Headlines + links | **Unknown, per feed** | Check each backbone feed's terms before paid use; always link out (we do) |
| Status pages (infra) | Public status pages | Yes | Probably | Link to the provider's page |

**For the paid tier:** derived alerts ("airspace over X emptied") may still copy restricted data in
substance, so treat the "No" rows as off-limits until there's a written deal. The cleanest paid
core today is GDELT + FIRMS + USGS + NOAA + GDACS + our own analysis, plus news links.

Sources: [Cloudflare Radar about](https://radar.cloudflare.com/about) ·
[GDELT about](https://www.gdeltproject.org/about.html) ·
[adsb.fi open data](https://github.com/adsbfi/opendata) ·
[aisstream.io](https://aisstream.io/) and [documentation](https://aisstream.io/documentation) ·
[NASA LANCE (FIRMS disclaimer)](https://www.earthdata.nasa.gov/data/projects/lance) ·
[USGS copyrights and credits](https://www.usgs.gov/information-policies-and-instructions/copyrights-and-credits) ·
GDACS licence from its RSS (`gdacs:acknowledgements`, CC BY 4.0).
