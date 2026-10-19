"""Find where a headline is about, using a bundled gazetteer of ~26,000 towns
(population over 15,000, from GeoNames) plus country names.

Rules that keep false matches down:
- a feed tied to a country only matches towns in that country (ABC can't put a
  story in Wangaratta, Texas)
- world feeds only match bigger towns (50,000+) unless the country is named too
- common English words that happen to be town names are ignored
"""
import gzip
import json
import math
import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
with gzip.open(os.path.join(_HERE, "gazetteer.json.gz"), "rt", encoding="utf-8") as f:
    _G = json.load(f)
with open(os.path.join(_HERE, "countries.json"), encoding="utf-8") as f:
    _CENTRE = json.load(f)

COUNTRIES = _G["countries"]                     # ISO2 -> {name, fips, capital}
FIPS_TO_ISO = {v["fips"]: k for k, v in COUNTRIES.items() if v.get("fips")}
ISO_TO_FIPS = {k: v["fips"] for k, v in COUNTRIES.items() if v.get("fips")}

# Words that are town names somewhere but mostly mean something else in headlines
STOP = {"police", "nice", "mobile", "reading", "bath", "split", "march", "victoria", "union", "unity", "hope", "liberty",
        "independence", "progress", "concord", "commerce", "university", "college", "mission", "providence", "temple",
        "eagle", "orange", "paradise", "faith", "justice", "republic", "federal", "capital", "central", "north", "south",
        "east", "west", "new", "city", "port", "bay", "lake", "river", "valley", "mountain", "beach", "garden", "park",
        "salt", "gold", "sale", "best", "deal", "vice", "bar", "bury", "may", "june", "july", "august", "early", "march",
        "marion", "florence", "alexandria", "jackson", "lincoln", "washington", "wellington", "hamilton", "aurora",
        "victory", "tribune", "telegraph", "herald", "post", "times", "guardian", "sky", "news", "trump", "president",
        "premier", "minister", "council", "court", "storm", "fire", "gaza strip", "strip", "russia", "ukraine"}

_INDEX = {}
for names, lat, lon, cc, pop in _G["cities"]:
    for j, n in enumerate(names):          # names[0] is the main name; the rest are other spellings
        k = n.lower()
        if len(k) >= 4 and k not in STOP:
            _INDEX.setdefault(k, []).append((lat, lon, cc, pop, names[0], j == 0))
_MAXWORDS = 3

_COUNTRY_NAMES = {}
for iso, c in COUNTRIES.items():
    _COUNTRY_NAMES[c["name"].lower()] = iso
# Other names, demonyms, and actors that pin a story to a country
for alias, iso in {
        "uk": "GB", "britain": "GB", "british": "GB", "england": "GB", "english": "GB", "scotland": "GB", "scottish": "GB",
        "wales": "GB", "welsh": "GB", "northern ireland": "GB", "us": "US", "usa": "US", "america": "US", "american": "US",
        "united states": "US", "australian": "AU", "french": "FR", "german": "DE", "italian": "IT", "spanish": "ES",
        "irish": "IE", "canadian": "CA", "russian": "RU", "russia": "RU", "kremlin": "RU", "ukrainian": "UA",
        "israeli": "IL", "idf": "IL", "palestinian": "PS", "gaza": "PS", "west bank": "PS", "palestine": "PS", "hamas": "PS",
        "lebanese": "LB", "hezbollah": "LB", "syrian": "SY", "syria": "SY", "iraqi": "IQ", "iranian": "IR", "iran": "IR",
        "yemeni": "YE", "houthi": "YE", "houthis": "YE", "saudi": "SA", "afghan": "AF", "taliban": "AF", "pakistani": "PK",
        "indian": "IN", "chinese": "CN", "taiwanese": "TW", "taiwan": "TW", "japanese": "JP", "korean": "KR",
        "south korea": "KR", "north korea": "KP", "north korean": "KP", "turkish": "TR", "turkey": "TR", "türkiye": "TR",
        "sudanese": "SD", "rsf": "SD", "somali": "SO", "al-shabaab": "SO", "ethiopian": "ET", "eritrean": "ER",
        "nigerian": "NG", "boko haram": "NG", "malian": "ML", "burkinabe": "BF", "congolese": "CD", "drc": "CD", "congo": "CD",
        "m23": "CD", "kenyan": "KE", "egyptian": "EG", "libyan": "LY", "venezuelan": "VE", "colombian": "CO", "mexican": "MX",
        "brazilian": "BR", "haitian": "HT", "myanmar": "MM", "burmese": "MM", "filipino": "PH", "indonesian": "ID",
        "ivory coast": "CI", "polish": "PL", "romanian": "RO", "albanian": "AL", "serbian": "RS", "georgian": "GE",
        "armenian": "AM", "azerbaijani": "AZ", "belarusian": "BY", "dutch": "NL", "belgian": "BE", "swedish": "SE",
        "macron": "FR", "meloni": "IT", "starmer": "GB", "albanese": "AU", "zelensky": "UA", "zelenskyy": "UA",
        "putin": "RU", "netanyahu": "IL", "erdogan": "TR", "erdoğan": "TR", "modi": "IN", "merz": "DE", "carney": "CA",
        "luxon": "NZ", "sybiha": "UA", "lula": "BR",
}.items():
    _COUNTRY_NAMES[alias] = iso
_COUNTRY_RE = re.compile(r"(?<![\w-])(" + "|".join(sorted(map(re.escape, _COUNTRY_NAMES), key=len, reverse=True)) + r")(?![\w-])", re.I)
_WORD = re.compile(r"[^\W\d_][\w'’\-]*", re.UNICODE)


def country_centre(iso):
    c = _CENTRE.get(iso)
    return (c["ll"][0], c["ll"][1], c["n"]) if c else None


def countries_in(text):
    """Countries a text points at, in order of first mention."""
    seen = []
    for m in _COUNTRY_RE.finditer(text):
        word = m.group(1)
        if word.lower() == "us" and word != "US":   # "us" the pronoun, not the country
            continue
        iso = _COUNTRY_NAMES[word.lower()]
        if iso not in seen:
            seen.append(iso)
    return seen


def find_town(text, allowed=None, world=False, named=()):
    """Best town mentioned in the text. allowed: set of ISO2 codes, or None for anywhere."""
    words = _WORD.findall(text)
    best = None
    for i in range(len(words)):
        for n in range(_MAXWORDS, 0, -1):
            if i + n > len(words):
                continue
            chunk = words[i:i + n]
            if not chunk[0][:1].isupper():      # place names are capitalised in headlines
                continue
            cands = _INDEX.get(" ".join(chunk).lower())
            if not cands:
                continue
            for lat, lon, cc, pop, name, main in cands:
                if allowed is not None and cc not in allowed:
                    continue
                # Unanchored world-desk stories: only big, unambiguous towns
                if world and cc not in named and (pop < 200000 or not main):
                    continue
                score = math.log10(pop) + (3 if cc in named else 0) + n + (3 if main else 0)
                if not best or score > best[0]:
                    best = (score, lat, lon, cc, name)
            break
    if best:
        return {"lat": best[1], "lon": best[2], "place": f"{best[4]}, {COUNTRIES.get(best[3], {}).get('name', best[3])}",
                "country": best[3], "precision": "town"}
    return None


def locate(text, feed_country=None, towns_only=False):
    """Where a story is about. A national feed's own country is assumed only when
    the story names no other country. Returns {lat, lon, place, country, precision} or None."""
    named = countries_in(text)
    if feed_country:
        # towns in the feed's own country or any country the story names
        # ("Russian strike on Mykolaiv" from a Ukrainian feed stays in Ukraine)
        allowed, world = {feed_country} | set(named), False
    else:
        allowed, world = (set(named) if named else None), not named
    town = find_town(text, allowed, world, named)
    if town or towns_only:
        return town
    iso = (feed_country if feed_country in named else named[0]) if named else feed_country
    if iso and country_centre(iso):
        lat, lon, name = country_centre(iso)
        return {"lat": lat, "lon": lon, "place": name, "country": iso, "precision": "country"}
    return None


def nearest_country(lat, lon):
    """Country of the nearest town, for turning a hotspot into a country."""
    best, bd = None, 1e9
    for names, la, lo, cc, pop in _G["cities"]:
        d = (la - lat) ** 2 + ((lo - lon) * math.cos(math.radians(lat))) ** 2
        if d < bd:
            best, bd = cc, d
    return best if bd < 9 else None   # within ~3 degrees
