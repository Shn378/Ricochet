"""Known events: dates when fires, bangs, flypasts and military chatter are expected.

The baseline only remembers four weeks, so it can't know that the whole of the UK
lights up around 5 November, or that NORAD "tracks Santa" every Christmas Eve.
This calendar fills that gap. It never hides anything: a hotspot that matches an
expected event gets a note on its card, and is held at Signal unless a feed the
event doesn't explain also backs it up.

Dates are UTC. Moveable festivals are listed per year and need adding each year.
"""
from datetime import datetime, timezone

# name, start, end (MM-DD for every year, or YYYY-MM-DD for one year), countries (ISO2, or None for anywhere),
# feeds the event explains, note shown on the card
EVENTS = [
    ("UK bonfire season", "10-15", "11-12", {"GB"}, {"firms", "news", "gdelt", "wiki"},
     "Bonfire nights and firework displays run from mid-October; 5 November is the peak. Fires and 'explosion' reports are expected."),
    ("Remembrance weekend", "2026-11-07", "2026-11-11", {"GB", "CA", "AU", "NZ"}, {"adsb", "news", "gdelt"},
     "Flypasts, gun salutes and military parades."),
    ("Diwali", "2026-11-06", "2026-11-11", {"IN", "NP", "PK", "BD", "LK", "GB", "SG", "MY", "FJ", "MU", "TT", "GY"},
     {"firms", "news", "gdelt", "seismic"}, "Fireworks and lamps across South Asia and the diaspora."),
    ("Crop-residue burning season", "10-01", "11-30", {"IN", "PK"}, {"firms"},
     "Farmers in Punjab and Haryana burn stubble after the rice harvest: large, real but routine fires."),
    ("NORAD Tracks Santa", "12-24", "12-25", None, {"wiki", "news", "gdelt", "adsb"},
     "NORAD's annual Santa tracker: tongue-in-cheek military and airspace stories are expected."),
    ("New Year's Eve", "12-31", "01-01", None, {"firms", "news", "gdelt", "seismic"},
     "Fireworks worldwide as midnight moves round the globe (and car burnings in parts of France)."),
    ("Independence Day (US)", "07-04", "07-05", {"US"}, {"firms", "news", "gdelt"}, "Fireworks across the United States."),
    ("Bastille Day", "07-14", "07-15", {"FR"}, {"firms", "news", "gdelt", "adsb"}, "Fireworks and the Paris military flypast."),
    ("Chaharshanbe Suri", "2027-03-16", "2027-03-17", {"IR", "AF", "AZ"}, {"firms", "news", "gdelt"},
     "Fire-jumping festival before Nowruz: street bonfires and firecrackers."),
]


def _span(start, end, year):
    def d(s, y):
        return datetime.strptime(s if len(s) == 10 else f"{y}-{s}", "%Y-%m-%d").replace(tzinfo=timezone.utc)
    a = d(start, year)
    b = d(end, year if len(end) == 10 or end >= start else year + 1)
    return a.timestamp(), b.timestamp() + 86400   # end date inclusive


def active(ts):
    """Events running at this moment."""
    year = datetime.fromtimestamp(ts, timezone.utc).year
    out = []
    for name, start, end, countries, feeds, note in EVENTS:
        for y in ({int(start[:4])} if len(start) == 10 else {year - 1, year}):
            a, b = _span(start, end, y)
            if a <= ts < b:
                out.append({"name": name, "countries": countries, "feeds": feeds, "note": note})
                break
    return out


def match(ts, country, feeds):
    """The expected event that explains these feeds in this country, if any."""
    for ev in active(ts):
        if ev["countries"] is not None and country not in ev["countries"]:
            continue
        if set(feeds) & ev["feeds"]:
            return ev
    return None
