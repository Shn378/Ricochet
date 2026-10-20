"""Critical infrastructure tripwire: public status pages of the big cloud and network providers.

When a provider that banks, hospitals and governments run on goes down, it matters
whatever the cause. Most outages are faulty updates; some are attacks. Ricochet
reports the disruption and says plainly that the cause is unconfirmed until the
provider or an official source states it. It never labels an outage an attack on
its own.

Sources (all public, no keys):
- Google Cloud: status.cloud.google.com/incidents.json (has affected regions)
- Statuspage-style pages: Cloudflare, GitHub
- RSS status feeds: Microsoft Azure, AWS
"""
import json
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from .. import places
from ..net import get
from .news import parse_feed

NAME = "infra"
GCP = "https://status.cloud.google.com/incidents.json"
STATUSPAGE = {"Cloudflare": "https://www.cloudflarestatus.com/api/v2/incidents/unresolved.json",
              "GitHub": "https://www.githubstatus.com/api/v2/incidents/unresolved.json"}
RSS = {"Microsoft Azure": "https://azure.status.microsoft/en-gb/status/feed/",
       "AWS": "https://status.aws.amazon.com/rss/all.rss"}
RECENT = 6 * 3600   # RSS status feeds keep history: only items from the last few hours count

REGIONS = {   # cloud region -> (lat, lon, place); unknown regions fall back to place names in the text
    "europe-west2": (51.51, -0.13, "London"), "europe-west1": (50.45, 3.82, "St. Ghislain, Belgium"),
    "europe-west3": (50.11, 8.68, "Frankfurt"), "europe-west4": (53.44, 6.84, "Eemshaven, Netherlands"),
    "europe-west6": (47.37, 8.54, "Zurich"), "europe-west9": (48.86, 2.35, "Paris"), "europe-north1": (60.57, 27.19, "Hamina, Finland"),
    "europe-central2": (52.23, 21.01, "Warsaw"), "europe-southwest1": (40.42, -3.70, "Madrid"), "europe-west8": (45.46, 9.19, "Milan"),
    "us-central1": (41.26, -95.86, "Council Bluffs, Iowa"), "us-east1": (33.20, -80.01, "Moncks Corner, South Carolina"),
    "us-east4": (39.04, -77.49, "Ashburn, Virginia"), "us-east5": (39.96, -83.00, "Columbus, Ohio"),
    "us-west1": (45.60, -121.18, "The Dalles, Oregon"), "us-west2": (34.05, -118.24, "Los Angeles"),
    "us-west4": (36.17, -115.14, "Las Vegas"), "us-south1": (32.78, -96.80, "Dallas"),
    "northamerica-northeast1": (45.50, -73.57, "Montreal"), "northamerica-northeast2": (43.65, -79.38, "Toronto"),
    "southamerica-east1": (-23.55, -46.63, "São Paulo"), "asia-east1": (24.07, 120.54, "Changhua, Taiwan"),
    "asia-east2": (22.32, 114.17, "Hong Kong"), "asia-northeast1": (35.68, 139.69, "Tokyo"), "asia-northeast3": (37.57, 126.98, "Seoul"),
    "asia-south1": (19.08, 72.88, "Mumbai"), "asia-southeast1": (1.35, 103.82, "Singapore"), "asia-southeast2": (-6.21, 106.85, "Jakarta"),
    "australia-southeast1": (-33.87, 151.21, "Sydney"), "me-west1": (32.09, 34.78, "Tel Aviv"), "me-central1": (25.29, 51.53, "Doha"),
    "me-central2": (26.43, 50.10, "Dammam"), "africa-south1": (-26.20, 28.05, "Johannesburg"),
}
IMPACT = {"critical": 3.0, "major": 2.5, "high": 2.5, "medium": 2.0, "minor": 1.0, "low": 1.0, "none": 0.5}


def _ts(s):
    if not s:
        return None
    try:
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
    except ValueError:
        try:
            return int(parsedate_to_datetime(s).timestamp())
        except (TypeError, ValueError):
            return None


def _where(text, region_ids=()):
    """Places for an incident: known cloud regions first, otherwise a town or country in the text."""
    out = [REGIONS[r] for r in region_ids if r in REGIONS]
    if out:
        return out
    hit = places.locate(text or "")
    if hit and hit.get("lat") is not None:
        return [(hit["lat"], hit["lon"], hit.get("place"))]
    # Status pages name data-centre cities ("errors in Frankfurt"): any city of 100k+
    best = None
    for w in re.findall(r"[A-Z][\w'-]+(?: [A-Z][\w'-]+)?", text or ""):
        for lat, lon, cc, pop, name, main in places._INDEX.get(w.lower(), ()):
            if pop >= 100000 and (best is None or pop > best[0]):
                best = (pop, lat, lon, f"{name}, {places.COUNTRIES.get(cc, {}).get('name', cc)}")
    return [best[1:]] if best else []


def _signal(provider, iid, title, detail, url, score, start, loc, extra):
    lat, lon, place = loc
    return {
        "uid": f"infra:{provider}:{iid}:{place}", "source": NAME, "kind": "infra_outage",
        "lat": lat, "lon": lon, "place": place,
        "title": f"{provider} disruption affecting {place}: {title[:140]}. Cause not confirmed",
        "url": url, "score": score, "observed_at": start or int(time.time()),
        "extra": dict(extra, provider=provider, detail=(detail or "")[:300]),
    }


def gcp_signals(payload, now):
    out = []
    for inc in json.loads(payload):
        if inc.get("end"):
            continue
        regions = [l.get("id") for l in inc.get("currently_affected_locations") or inc.get("previously_affected_locations") or []]
        desc = inc.get("external_desc") or ""
        score = IMPACT.get((inc.get("severity") or "").lower(), 2.0)
        for loc in _where(desc, regions):
            out.append(_signal("Google Cloud", inc.get("id"), desc, (inc.get("most_recent_update") or {}).get("text"),
                               "https://status.cloud.google.com/" + (inc.get("uri") or ""), score, _ts(inc.get("begin")), loc,
                               {"products": [p.get("title") for p in inc.get("affected_products") or []][:8], "regions": regions}))
    return out


def statuspage_signals(provider, payload, now):
    out = []
    for inc in json.loads(payload).get("incidents") or []:
        name = inc.get("name") or ""
        comps = " ".join(c.get("name", "") for c in inc.get("components") or [])
        update = ((inc.get("incident_updates") or [{}])[0]).get("body")
        score = IMPACT.get((inc.get("impact") or "").lower(), 1.5)
        for loc in _where(f"{name} {comps}"):
            out.append(_signal(provider, inc.get("id"), name, update, inc.get("shortlink") or "", score,
                               _ts(inc.get("created_at")), loc, {"impact": inc.get("impact"), "components": comps[:200]}))
    return out


def rss_signals(provider, payload, now):
    out = []
    for item in parse_feed(payload):
        t = item.get("published") or now
        if now - t > RECENT:
            continue
        text = f"{item.get('title', '')} {item.get('summary', '')}"
        if re.search(r"\b(resolved|mitigated|recovered)\b", item.get("title", ""), re.I):
            continue
        for loc in _where(text):
            out.append(_signal(provider, item.get("link") or item.get("title"), item.get("title", ""), item.get("summary"),
                               item.get("link") or "", 2.0, t, loc, {}))
    return out


def fetch():
    now = int(time.time())
    raws, signals, failed = [], [], []
    jobs = [("Google Cloud", GCP, gcp_signals)] + \
           [(p, u, lambda raw, n, p=p: statuspage_signals(p, raw, n)) for p, u in STATUSPAGE.items()] + \
           [(p, u, lambda raw, n, p=p: rss_signals(p, raw, n)) for p, u in RSS.items()]
    for provider, url, parse in jobs:
        try:
            raw = get(url, timeout=30)
            raws.append(raw)
            signals += parse(raw, now)
        except Exception as e:
            failed.append(f"{provider}: {e}")
    if len(failed) == len(jobs):
        raise RuntimeError("; ".join(failed)[:300])
    return b"\n".join(raws), signals
