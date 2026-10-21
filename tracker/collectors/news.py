"""News feeds (RSS/Atom/RDF): country backbone and leased incident sources.

Only items that read like an incident become signals, and each one is placed on
the map from its headline (see places.py). Everything else in a feed (sport,
weather, politics) is ignored.
"""
import hashlib
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from .. import places
from ..net import get

NAME = "news"

INCIDENT = re.compile(r"""\b(
    attack(ed|s|er)?|assassinat\w*|stab(bed|bing|s)?|shoot(ing|s)?|shot\s+dead|gunm[ae]n|gunfire|opened\s+fire|
    explosion|explosive|blast|bomb(ing|ed|s)?|grenade|drone\s+strike|air\s*strike|missile|rocket|shelling|shelled|
    killed|massacre|hostage|siege|ramm(ed|ing)|car\s+attack|terror\w*|riot(s|ing|ers)?|clash(es|ed)?|
    lockdown|evacuat\w*|curfew|coup|mutiny|insurgent|militant|ambush|kidnap\w*|abduct\w*|
    attentat|fusillade|explosion|émeutes?|tué[es]*|blessé[es]*|poignard\w*|
    anschlag|explosion|schüsse|getötet|messerangriff|
    ataque|explosión|tiroteo|muertos?|asesinad\w*
)\b""", re.I | re.X)
STRONG = re.compile(r"\b(terror\w*|counter-terror\w*|assassinat\w*|massacre|gunm[ae]n|opened fire|explosion|bomb(ing|ed)?|hostage|ramm(ed|ing)|air\s*strike|missile|critically injured|seriously injured|mass casualty|attentat|anschlag)\b", re.I)
# Same words, harmless meaning
NOT_INCIDENT = re.compile(r"\b(heart\s+attack|panic\s+attack|asthma\s+attack|shark\s+attack|dog\s+attack|photo\s*shoot|"
                          r"title\s+clash|cup\s+clash|derby|attacking\s+midfielder|bombshell|box[- ]office|dead\s+heat|"
                          r"rocket\s+(launch|lab)|space\s*x|riot\s+of\s+colou?r|killed\s+it|drone\s+show|personal\s+attacks?|"
                          r"verbal\s+attacks?|political\s+attacks?|attack\s+ads?|attacks?\s+on\s+(his|her|their)\s+record)\b", re.I)
_TAG = re.compile(r"<[^>]+>")


def _text(el, *names):
    for n in names:
        for child in el:
            tag = child.tag.rsplit("}", 1)[-1]
            if tag == n:
                if n == "link" and child.get("href"):
                    return child.get("href")
                if child.text:
                    return child.text.strip()
    return None


def _when(s):
    if not s:
        return None
    try:
        return int(parsedate_to_datetime(s).timestamp())
    except (TypeError, ValueError):
        pass
    try:
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def parse_feed(xml_bytes):
    """Returns a list of {title, summary, link, guid, published}."""
    root = ET.fromstring(xml_bytes)
    items = []
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag not in ("item", "entry"):
            continue
        title = _text(el, "title") or ""
        link = _text(el, "link") or _text(el, "guid", "id") or ""
        summary = _TAG.sub(" ", _text(el, "description", "summary", "content") or "")[:400]
        items.append({"title": title.strip(), "summary": summary.strip(), "link": link.strip(),
                      "guid": (_text(el, "guid", "id") or link).strip(),
                      "published": _when(_text(el, "pubDate", "published", "updated", "date"))})
    return items


def to_signals(items, feed, now):
    out = []
    for it in items:
        text = f"{it['title']}. {it['summary']}"
        if not INCIDENT.search(it["title"]) and not STRONG.search(it["summary"]):
            continue
        if NOT_INCIDENT.search(it["title"]):
            continue
        cc = feed.get("country")
        # A town in the headline, then a town in the summary, then the country
        where = places.locate(it["title"], cc, towns_only=True) or places.locate(text, cc, towns_only=True) or places.locate(text, cc)
        if not where:
            continue
        strong = bool(STRONG.search(text))
        score = (2.0 if strong else 1.2) * (1.0 if where["precision"] == "town" else 0.5)
        out.append({
            # keyed on the article link, so the same story on two desks counts once
            "uid": "news:" + hashlib.sha1((it["link"].split("?")[0].rstrip("/") or it["guid"] or it["title"]).encode()).hexdigest()[:16],
            "source": NAME,
            "kind": "news_item" if where["precision"] == "town" else "news_item_national",
            "lat": where["lat"], "lon": where["lon"], "place": where["place"], "country": where["country"],
            "title": f"{feed['name']}: {it['title']}"[:240],
            "url": it["link"],
            "score": score,
            "observed_at": min(it["published"] or now, now),
            "extra": {"outlet": feed["name"], "layer": feed.get("layer", 2), "strong": strong},
        })
    return out


def fetch(feed, now):
    raw = get(feed["url"], timeout=30)
    items = parse_feed(raw)
    newest = max((min(i["published"], now) for i in items if i["published"]), default=None)   # some feeds stamp local time as UTC
    return raw, items, to_signals(items, feed, now), newest
