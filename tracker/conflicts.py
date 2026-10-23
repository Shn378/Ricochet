"""Listed armed conflicts: places at war whatever the news mix says.

The "ongoing" test in situations.py reads GDELT's conflict share, which works for
Yemen or Sudan but misses big wars that draw lots of diplomatic coverage (Ukraine
sat at 1.1x the world share in Oct 2026) and small, under-reported ones (Haiti).
This short hand-kept list fills the gap. Each entry is a country, optionally limited
to a box, so listing Russia's border regions doesn't make Siberian wildfires count.

Kept small and reviewed by the audit. Sources: ACLED Conflict Index, Wikipedia
"List of ongoing armed conflicts". Used only when rules.FLAGS["listed_conflicts"] is on.
"""

# (name, ISO2, box (lat_min, lat_max, lon_min, lon_max) or None for the whole country)
LISTED = [
    ("Russia-Ukraine war", "UA", None),
    ("Russia-Ukraine war (Russian border regions)", "RU", (49.5, 54.5, 31.0, 41.0)),
    ("Russia-Ukraine war (Crimea)", "UA", (44.3, 46.3, 32.4, 36.7)),
    ("Sudan civil war", "SD", None),
    ("Israel-Gaza war", "PS", None),
    ("Israel (front lines)", "IL", None),
    ("Lebanon (southern front)", "LB", (32.9, 33.9, 35.0, 36.7)),
    ("Yemen civil war", "YE", None),
    ("Myanmar civil war", "MM", None),
    ("Eastern DR Congo", "CD", (-5.0, 3.5, 26.0, 31.5)),
    ("Sahel insurgency", "ML", None),
    ("Sahel insurgency", "BF", None),
    ("Sahel insurgency", "NE", None),
    ("Somalia (al-Shabaab)", "SO", None),
    ("Haiti gang war", "HT", None),
    ("Syria", "SY", None),
    ("South Sudan", "SS", None),
    ("North-east Nigeria", "NG", (9.0, 14.0, 8.0, 14.7)),
]

_cache = {}


def _cell_centre(cell):
    la, lo = map(int, cell.split(":"))
    return la + 0.5, lo + 0.5


def listed_near(cell):
    """Names of listed conflicts in or next to this 1-degree square (about 2 degrees around)."""
    hit = _cache.get(cell)
    if hit is not None:
        return hit
    from .rules import countries_near
    lat, lon = _cell_centre(cell)
    near = countries_near(cell)
    hit = set()
    for name, iso, box in LISTED:
        if iso not in near:
            continue
        if box and not (box[0] - 0.5 <= lat <= box[1] + 0.5 and box[2] - 0.5 <= lon <= box[3] + 0.5):
            continue
        hit.add(name)
    _cache[cell] = hit
    return hit
