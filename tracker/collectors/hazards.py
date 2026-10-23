"""Natural hazards and official public alerts: GDACS, NOAA tsunami centres, GOV.UK Emergency Alerts.

All three are official, but they are not security incidents. For now every signal here is a
context kind (shown on the map in its own hazard colour, never counted towards a hotspot), so
switching them on can't change what the globe tours. Whether a GDACS red alert, a tsunami
warning or a GOV.UK alert should open a hotspot on its own is a rules decision for Shane,
to be benchmarked first (docs/backlog.md, "Weather and natural hazards").

- GDACS (UN/EU): impact-based alert levels (severity x people exposed), so a cyclone over empty
  sea stays green. Credit required: "GDACS" (CC BY 4.0).
- NOAA NTWC (Palmer) and PTWC (Honolulu) Atom feeds: warnings, advisories, watches, threats,
  information statements. US government work, public domain.
- Met Office UK warnings RSS: context only ("weather warning in force in this region"), pinned to
  a hand-kept region centre. Crown copyright: credit the Met Office.
- GOV.UK Emergency Alerts Atom feed: no coordinates, only area names. NOT polled yet: placing
  them from the name put "Barrow upon Soar" on Barrow-in-Furness and most areas on the middle of
  the UK. Needs the alert-area shapes before it goes live. Open Government Licence: cite GOV.UK.
"""
import re
import time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from datetime import datetime

from ..net import get

NAME = "hazard"
GDACS = "https://www.gdacs.org/xml/rss.xml"
TSUNAMI = ("https://www.tsunami.gov/events/xml/PAAQAtom.xml", "https://www.tsunami.gov/events/xml/PHEBAtom.xml")
GOVUK = "https://www.gov.uk/alerts/feed.atom"
METOFFICE = "https://www.metoffice.gov.uk/public/data/PWSCache/WarningsRSS/Region/UK"
# Met Office warning regions -> rough centre (hand-kept). Warnings are pinned here as context:
# "weather warning in force in this region", never a precise location.
MET_REGIONS = {
    "orkney & shetland": (59.6, -2.0), "highlands & eilean siar": (57.5, -5.0), "grampian": (57.2, -2.6),
    "strathclyde": (55.8, -4.6), "central, tayside & fife": (56.4, -3.5),
    "dumfries, galloway, lothian & borders": (55.4, -3.4), "sw scotland, lothian borders": (55.4, -3.4),
    "northern ireland": (54.6, -6.7), "wales": (52.3, -3.7), "north west england": (54.0, -2.7),
    "north east england": (54.9, -1.7), "yorkshire & humber": (53.9, -1.2), "west midlands": (52.5, -2.2),
    "east midlands": (52.9, -0.9), "east of england": (52.3, 0.7), "south west england": (50.8, -3.8),
    "london & south east england": (51.2, -0.4),
}
MET_SCORE = {"red": 2.0, "amber": 1.0, "yellow": 0.3}
MAX_AGE = 7 * 86400          # older items are history, not news

NS = {"gdacs": "http://www.gdacs.org", "geo": "http://www.w3.org/2003/01/geo/wgs84_pos#",
      "a": "http://www.w3.org/2005/Atom"}
EVENT = {"EQ": "earthquake", "TC": "tropical cyclone", "FL": "flood", "VO": "volcano", "DR": "drought",
         "WF": "wildfire", "TS": "tsunami"}
GDACS_SCORE = {"red": 3.0, "orange": 2.0, "green": 0.5}
TSU_SCORE = {"warning": 3.0, "threat": 3.0, "advisory": 2.0, "watch": 2.0, "information": 0.5}
# Every kind is context only (see db.CONTEXT_KINDS)
KINDS = {f"gdacs_{l}" for l in GDACS_SCORE} | {f"tsunami_{c}" for c in TSU_SCORE} | {"uk_alert", "met_warning"}


def _t(s):
    if not s:
        return None
    try:
        return int(parsedate_to_datetime(s).timestamp())
    except (TypeError, ValueError):
        pass
    try:
        return int(datetime.fromisoformat(s.strip().replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def parse_gdacs(payload, now=None):
    now = now or time.time()
    out = []
    for it in ET.fromstring(payload).iter("item"):
        g = lambda tag: (it.findtext(tag, namespaces=NS) or "").strip()
        lat, lon = _f(g("geo:Point/geo:lat")), _f(g("geo:Point/geo:long"))
        level = g("gdacs:alertlevel").lower()
        start = _t(g("gdacs:fromdate"))
        # An ongoing event (a cyclone tracked for days) is dated by GDACS's latest update, not its start
        t = _t(g("gdacs:datemodified")) or _t(g("pubDate")) or start
        if lat is None or lon is None or level not in GDACS_SCORE or not t:
            continue
        if level == "green":      # minor impact: ignored (backlog decision), keeps the map clean
            continue
        if g("gdacs:iscurrent").lower() == "false" and now - t > 86400:
            continue
        if now - t > MAX_AGE:
            continue
        etype = g("gdacs:eventtype")
        ev_id = g("gdacs:eventid")
        ep = g("gdacs:episodeid")
        out.append({
            "uid": f"hazard:gdacs:{etype}{ev_id}:{ep}:{level}",
            "source": NAME, "kind": f"gdacs_{level}",
            "lat": lat, "lon": lon, "place": g("gdacs:country") or None,
            "title": f"GDACS {level}: {g('title')}"[:300],
            "url": g("link").replace("http://", "https://", 1) or None,
            "score": GDACS_SCORE[level], "observed_at": t,
            "country": None,
            "extra": {"hazard": EVENT.get(etype, etype.lower()), "alert": level, "iso3": g("gdacs:iso3") or None,
                      "severity": g("gdacs:severity") or None, "population": g("gdacs:population") or None,
                      "started": start, "credit": "GDACS"},
        })
    return out


_CAT = re.compile(r"Category:\s*</strong>\s*([A-Za-z]+)", re.I)


def parse_tsunami(payload, now=None):
    now = now or time.time()
    out = []
    root = ET.fromstring(payload)
    centre = (root.findtext("a:author/a:name", namespaces=NS) or "NOAA tsunami warning centre").strip()
    for e in root.iter(f"{{{NS['a']}}}entry"):
        g = lambda tag: (e.findtext(tag, namespaces=NS) or "").strip()
        lat, lon, t = _f(g("geo:lat")), _f(g("geo:long")), _t(g("a:updated"))
        summary = ET.tostring(e.find("a:summary", NS), encoding="unicode") if e.find("a:summary", NS) is not None else ""
        m = _CAT.search(summary.replace("ns0:", "").replace("html:", ""))
        cat = (m.group(1) if m else "information").lower()
        if lat is None or lon is None or not t or now - t > MAX_AGE or cat not in TSU_SCORE:
            continue
        link = next((l.get("href") for l in e.findall("a:link", NS) if l.get("rel") == "alternate"), None)
        uid = g("a:id")
        out.append({
            "uid": f"hazard:tsunami:{uid}",
            "source": NAME, "kind": f"tsunami_{cat}",
            "lat": lat, "lon": lon, "place": g("a:title") or None,
            "title": f"Tsunami {cat} ({centre}): {g('a:title')}"[:300],
            "url": link or "https://www.tsunami.gov/",
            "score": TSU_SCORE[cat], "observed_at": t, "country": None,
            "extra": {"hazard": "tsunami", "alert": cat, "credit": "NOAA"},
        })
    return out


def parse_govuk(payload, locate=None, now=None):
    """GOV.UK alerts carry an area name, not a point: placed with the gazetteer (UK only)."""
    now = now or time.time()
    if locate is None:
        from ..places import locate
    out = []
    for e in ET.fromstring(payload).iter(f"{{{NS['a']}}}entry"):
        g = lambda tag: (e.findtext(tag, namespaces=NS) or "").strip()
        t = _t(g("a:published")) or _t(g("a:updated"))
        if not t or now - t > MAX_AGE:
            continue
        area = g("a:title")
        content = " ".join(x.strip() for x in e.find("a:content", NS).itertext()) if e.find("a:content", NS) is not None else ""
        where = locate(area, "GB") or locate(f"{area} {content}", "GB")
        if not where:
            continue
        test = bool(re.search(r"\btest(ing)?\b", content, re.I))
        out.append({
            "uid": f"hazard:govuk:{g('a:id')}",
            "source": NAME, "kind": "uk_alert",
            "lat": where["lat"], "lon": where["lon"], "place": area or where.get("place"),
            "title": f"GOV.UK Emergency Alert{' (test)' if test else ''}: {area}"[:300],
            "url": g("a:id") if g("a:id").startswith("https://www.gov.uk/") else "https://www.gov.uk/alerts",
            "score": 0.5 if test else 2.0, "observed_at": t, "country": "GB",
            "extra": {"hazard": "public alert", "alert": "test" if test else "live", "credit": "GOV.UK",
                      "placed": where.get("precision")},
        })
    return out


_MET = re.compile(r"^(Red|Amber|Yellow) warning of (.+?) affecting (.+)$", re.I)


def parse_metoffice(payload, now=None):
    """Met Office UK warnings RSS -> one context signal per warning per region."""
    now = now or time.time()
    out = []
    root = ET.fromstring(payload)
    published = _t(root.findtext("channel/pubDate")) or int(now)
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        m = _MET.match(title)
        if not m:
            continue
        colour, what, region = m.group(1).lower(), m.group(2).strip(), m.group(3).strip()
        where = MET_REGIONS.get(region.lower())
        if not where:
            continue        # unknown region name: better no pin than a wrong one
        guid = (it.findtext("guid") or it.findtext("link") or title).strip()
        out.append({
            "uid": f"hazard:met:{guid}",
            "source": NAME, "kind": "met_warning",
            "lat": where[0], "lon": where[1], "place": region,
            "title": f"Met Office {colour} warning of {what}: {region}"[:300],
            "url": (it.findtext("link") or "https://www.metoffice.gov.uk/weather/warnings-and-advice/uk-warnings").strip(),
            "score": MET_SCORE[colour], "observed_at": published, "country": "GB",
            "extra": {"hazard": "weather", "alert": colour, "weather": what,
                      "detail": (it.findtext("description") or "")[:300], "credit": "Met Office", "placed": "region"},
        })
    return out


def fetch():
    """One poll of GDACS, both tsunami centres and the Met Office. A feed that fails is skipped, the others still count."""
    sigs, raws, errors = [], [], []
    for name, fn in (("gdacs", lambda: parse_gdacs(_keep(raws, get(GDACS, timeout=60)))),
                     *((f"tsunami{i}", (lambda u=u: parse_tsunami(_keep(raws, get(u, timeout=30))))) for i, u in enumerate(TSUNAMI)),
                     ("metoffice", lambda: parse_metoffice(_keep(raws, get(METOFFICE, timeout=30))))):
        try:
            sigs.extend(fn())
        except Exception as e:
            errors.append(f"{name}: {e}")
    if errors and not sigs:
        raise RuntimeError("; ".join(errors))
    return b"\n".join(raws[-1:]), sigs     # keep only a small raw sample; the signals carry the detail


def _keep(raws, data):
    raws.append(data[:20000])
    return data
