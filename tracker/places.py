"""Find where a headline is about, using a bundled gazetteer of ~26,000 towns
(population over 15,000, from GeoNames) plus country names.

Rules that keep false matches down:
- a feed tied to a country only matches towns in that country (ABC can't put a
  story in Wangaratta, Texas)
- world feeds only match bigger towns (50,000+) unless the country is named too
- common English words that happen to be town names are ignored
"""
import functools
import gzip
import json
import math
import os
import re
import unicodedata

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
# Ukrainian names missing from the gazetteer (it has the Russian forms)
for _uk, _en in {"київ": "Kyiv", "дніпро": "Dnipro", "львів": "Lviv"}.items():
    for _row in _INDEX.get(_en.lower(), []):
        if _row[2] == "UA":
            _INDEX.setdefault(_uk, []).append(_row)
_UK_ENDINGS = ("ові", "ами", "ями", "ах", "ях", "ом", "ем", "ою", "ею", "у", "ю", "а", "я", "і", "и", "е", "ї")
_UK_ALT = {"о": "і", "е": "і", "є": "ї"}   # Харкова -> Харків, Чернігова -> Чернігів, Миколаєва -> Миколаїв


def _uk_forms(word):
    """Dictionary forms a Ukrainian (Cyrillic) word might be: "Харкову" -> "харків", "Сумах" -> "суми"."""
    w = word.lower()
    if not any("\u0400" <= c <= "\u04ff" for c in w):
        return ()
    out = []
    for end in _UK_ENDINGS:
        if w.endswith(end) and len(w) - len(end) >= 3:
            stem = w[:-len(end)]
            out += [stem, stem + "и", stem + "а", stem + "я", stem + "ь"]
            if stem[-2:-1] in _UK_ALT:
                out.append(stem[:-2] + _UK_ALT[stem[-2]] + stem[-1])
    if w == "києва" or w.startswith("києв"):
        out.append("київ")
    return out

_COUNTRY_NAMES = {}
_PLACE_WORDS = set()     # words that name a place, not a people, party or leader
for iso, c in COUNTRIES.items():
    _COUNTRY_NAMES[c["name"].lower()] = iso
    _PLACE_WORDS.add(c["name"].lower())
_PLACE_WORDS |= {"uk", "britain", "england", "scotland", "wales", "northern ireland", "us", "usa", "america",
                 "united states", "russia", "syria", "iran", "turkey", "türkiye", "taiwan", "south korea", "north korea",
                 "gaza", "west bank", "palestine", "myanmar", "drc", "congo", "ivory coast",
                 "росія", "росії", "росією", "україна", "україні", "україни", "іран", "ірані", "ірану", "ізраїль", "ізраїлі",
                 "молдова", "молдові", "молдови", "білорусь", "білорусі", "крим", "криму"}
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
        # Ukrainian (for the UP Ukrainian edition): places, then peoples/adjectives
        "росія": "RU", "росії": "RU", "росією": "RU", "україна": "UA", "україні": "UA", "україни": "UA",
        "іран": "IR", "ірані": "IR", "ірану": "IR", "ізраїль": "IL", "ізраїлі": "IL", "молдова": "MD", "молдові": "MD",
        "молдови": "MD", "білорусь": "BY", "білорусі": "BY", "крим": "UA", "криму": "UA",
        "російський": "RU", "російська": "RU", "російські": "RU", "російського": "RU", "російському": "RU",
        "російських": "RU", "росіяни": "RU", "росіян": "RU", "іранський": "IR", "іранські": "IR",
}.items():
    _COUNTRY_NAMES[alias] = iso
_COUNTRY_RE = re.compile(r"(?<![\w-])(" + "|".join(sorted(map(re.escape, _COUNTRY_NAMES), key=len, reverse=True)) + r")(?![\w-])", re.I)
_WORD = re.compile(r"[^\W\d_][\w'’\-]*", re.UNICODE)


def country_centre(iso):
    c = _CENTRE.get(iso)
    return (c["ll"][0], c["ll"][1], c["n"]) if c else None


_LOCATIVE = re.compile(r"\b(in|on|at|over|across|into|near|off|inside|from)\s+(the\s+)?$", re.I)


# "Russian strike", "Iranian-backed forces", "російські обстріли": who did it, not where
_ARMED = re.compile(r"^[\s-]*(\w+[\s-]+)?(strikes?|drones?|missiles?|rockets?|attacks?|shelling|forces|troops|army|"
                    r"military|soldiers|jets|warplanes|navy|bombers?|backed|обстріл\w*|удар\w*|дрон\w*|атак\w*|ракет\w*|"
                    r"військ\w*|окупант\w*)\b", re.I)


def _mentions(text):
    """(iso, kind, after_in_on_at) per country mention, in order. kind: "place" (Moldova), "actor"
    (Russian drones) or "people" (Australian MP)."""
    out = []
    for m in _COUNTRY_RE.finditer(text):
        word = m.group(1)
        if word.lower() == "us" and word != "US":   # "us" the pronoun, not the country
            continue
        kind = "place" if word.lower() in _PLACE_WORDS else "actor" if _ARMED.match(text[m.end():m.end() + 30]) else "people"
        out.append((_COUNTRY_NAMES[word.lower()], kind, bool(_LOCATIVE.search(text[max(0, m.start() - 12):m.start()]))))
    return out


def countries_in(text):
    """Countries a text points at, in order of first mention."""
    seen = []
    for iso, _, _ in _mentions(text):
        if iso not in seen:
            seen.append(iso)
    return seen


def where_country(text, places_only=False):
    """The country a story happens in, when no town says so. "Russian drone crashes in Moldova":
    Moldova (a place, after "in"), not Russia (who did it). Order: a place after in/on/at/off...,
    then the first place named, then the first people, party or leader named."""
    ms = _mentions(text)
    tests = [lambda k, l: k == "place" and l, lambda k, l: k == "place", lambda k, l: k == "people"]
    if not places_only:
        tests.append(lambda k, l: True)
    for want in tests:
        for iso, kind, loc in ms:
            if want(kind, loc):
                return iso
    return None


with open(os.path.join(_HERE, "named_places.json"), encoding="utf-8") as f:
    _NAMED = json.load(f)
_FAC_RE = re.compile(r"(?<![\w-])(" + "|".join(sorted(map(re.escape, _NAMED["facilities"]), key=len, reverse=True)) + r")(?![\w-])", re.I)
_SEA_RE = re.compile(r"(?<![\w-])(" + "|".join(sorted(map(re.escape, _NAMED["seas"]), key=len, reverse=True)) + r")(?![\w-])", re.I)


def find_named(text, allowed=None, about=None):
    """A facility or sea named in the text (hand-kept list). allowed: ISO2 codes a facility may be in;
    about: the country the story is about, to pick the right stretch of a sea."""
    for m in _FAC_RE.finditer(text):
        lat, lon, cc, name = _NAMED["facilities"][m.group(1).lower()]
        if allowed is None or cc in allowed:
            return {"lat": lat, "lon": lon, "place": f"{name}, {COUNTRIES.get(cc, {}).get('name', cc)}",
                    "country": cc, "precision": "site"}
    m = _SEA_RE.search(text)
    if m:
        sea = _NAMED["seas"][m.group(1).lower()]
        lat, lon = sea.get("near", {}).get(about) or sea["at"]
        return {"lat": lat, "lon": lon, "place": m.group(1).title().replace(" Of ", " of "), "country": about,
                "precision": "sea"}
    return None


def _fold(name):
    """"Constanţa" -> "constanta": web addresses drop the accents."""
    return "".join(c for c in unicodedata.normalize("NFKD", name) if not unicodedata.combining(c)).lower()


def find_town(text, allowed=None, world=False, named=(), main_only=False):
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
            if not cands and n == 1:
                cands = next((_INDEX[f] for f in _uk_forms(chunk[0]) if f in _INDEX), None)
            if not cands:
                continue
            for lat, lon, cc, pop, name, main in cands:
                if allowed is not None and cc not in allowed:
                    continue
                said = " ".join(chunk).lower()
                if main_only and (said in _COUNTRY_NAMES or not (main or _fold(name) == said)):
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
    if town:
        return town
    # A national feed's story that names only who did it ("Russian strikes overnight" on a Ukrainian
    # desk) happens at home; one that names another place ("...in Moldova") happens there
    placed_abroad = where_country(text, places_only=True)
    if feed_country in named or (feed_country and not placed_abroad):
        about = feed_country
    else:
        about = placed_abroad or where_country(text) or feed_country
    spot = find_named(text, allowed, about)
    if spot or towns_only:
        return spot
    iso = about
    if iso and country_centre(iso):
        lat, lon, name = country_centre(iso)
        return {"lat": lat, "lon": lon, "place": name, "country": iso, "precision": "country"}
    return None


def locate_slug(url, iso):
    """Re-place a story GDELT could only pin to a country's centre, from words in its web address:
    a town in that country, a named facility, or a sea ("romania-...-black-sea-ship-..." -> off
    Constanta). Returns a place dict or None."""
    path = re.sub(r"^https?://[^/]+", "", url or "").split("?")[0]
    # Only the article's own slug: the longest hyphenated segment, not section folders ("baton_rouge/opinion")
    segs = [g for g in path.split("/") if g.count("-") >= 2]
    if not segs:
        return None
    words = [w for w in re.split(r"[\-_.]+", max(segs, key=len)) if w.isalpha() and len(w) > 1]
    text = " ".join(w.capitalize() for w in words)
    if not text or not iso:
        return None
    return find_town(text, {iso}, False, (iso,), main_only=True) or find_named(text, {iso}, iso)


_GRID = {}
for _names, _la, _lo, _cc, _pop in _G["cities"]:
    _GRID.setdefault((math.floor(_la), math.floor(_lo)), []).append((_la, _lo, _cc))


@functools.lru_cache(maxsize=100000)
def nearest_country(lat, lon):
    """Country of the nearest town within ~3 degrees, for turning a hotspot into a country.
    Looks only at grid squares that could hold such a town (it used to scan all 26,000 towns
    every call, which was 94% of the benchmark's time)."""
    c = math.cos(math.radians(lat))
    span = 180 if c < 3 / 180 else min(180, math.ceil(3 / c) + 1)
    best, bd = None, 9.0
    for gla in range(math.floor(lat) - 3, math.floor(lat) + 4):
        for k in range(-span, span + 1):
            glo = (math.floor(lon) + k + 180) % 360 - 180
            for la, lo, cc in _GRID.get((gla, glo), ()):
                d = (la - lat) ** 2 + ((lo - lon) * c) ** 2
                if d < bd:
                    best, bd = cc, d
    return best
