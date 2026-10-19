"""Ongoing situations: countries where conflict dominates the news for days on end.

Spike detection compares an area with its own recent past, so something that
has been going on for weeks (a war, a long wave of riots) looks "normal" to it.
This layer asks a different question: what share of all news about a country
is about conflict, compared with the world as a whole, day after day?

Ukraine, Sudan or a country in the middle of nationwide riots will sit well
above the world level for many days running. Busy media countries like the US
or UK produce a lot of conflict stories, but as a share of their total coverage
they stay near the world level, so they don't qualify just by being loud.
"""
import math
import time

MIN_TOTAL = 400          # main events about the country over the window, so tiny samples don't qualify
MIN_CONFLICT = 60
MIN_DAY_TOTAL = 30
ONGOING_RATIO = 1.8      # conflict share vs the world's, over the window
HOT_DAY_RATIO = 1.5      # a day counts as "hot" above this
MIN_HOT_DAYS = 3
# Big wars draw lots of diplomatic coverage, which dilutes the share (Ukraine):
# very high conflict volume with an above-average share also counts as ongoing
HIGH_VOLUME = 450
HIGH_VOLUME_RATIO = 1.25
# Escalating: the last two days well above the country's OWN earlier level (France riots)
ESCALATE_VS_OWN = 2.0
ESCALATE_VS_WORLD = 1.5
ESCALATE_MIN_CONFLICT = 40


def compute(con, days=7):
    today = int(time.time()) // 86400
    first = today - days + 1
    rows = con.execute("SELECT country, day, total, conflict FROM country_day WHERE day >= ?", (first,)).fetchall()
    if not rows:
        return {"world_share": None, "countries": []}
    by = {}
    world_total = world_conflict = 0
    for r in rows:
        c = by.setdefault(r["country"], {"total": 0, "conflict": 0, "days": {}})
        c["total"] += r["total"]
        c["conflict"] += r["conflict"]
        c["days"][r["day"]] = (r["total"], r["conflict"])
        world_total += r["total"]
        world_conflict += r["conflict"]
    world = world_conflict / world_total if world_total else 0
    info = {r["country"]: dict(r) for r in con.execute("SELECT * FROM country_info")}
    out = []
    for cc, c in by.items():
        if c["total"] < MIN_TOTAL or not world:
            continue
        share = c["conflict"] / c["total"]
        ratio = share / world
        series, hot_days = [], 0
        for d in range(first, today + 1):
            tot, con_ = c["days"].get(d, (0, 0))
            s = con_ / tot if tot >= MIN_DAY_TOTAL else None
            series.append(round(s * 100, 1) if s is not None else None)
            if s is not None and s / world >= HOT_DAY_RATIO:
                hot_days += 1
        week = share * 100
        # recent = last two days, earlier = the days before, both as conflict / total
        rec_days = [d for d in range(today - 1, today + 1) if c["days"].get(d, (0, 0))[0] >= MIN_DAY_TOTAL]
        old_days = [d for d in range(first, today - 1) if c["days"].get(d, (0, 0))[0] >= MIN_DAY_TOTAL]
        sum_ = lambda days, k: sum(c["days"][d][k] for d in days)
        recent = sum_(rec_days, 1) / sum_(rec_days, 0) * 100 if rec_days else None
        earlier = sum_(old_days, 1) / sum_(old_days, 0) * 100 if old_days else None
        trend = "steady"
        if recent is not None:
            trend = "rising" if recent >= week * 1.25 else "easing" if recent <= week * 0.75 else "steady"
        escalating = (recent is not None and earlier is not None and len(old_days) >= 3
                      and recent >= earlier * ESCALATE_VS_OWN and recent >= world * 100 * ESCALATE_VS_WORLD
                      and sum_(rec_days, 1) >= ESCALATE_MIN_CONFLICT)
        sustained = (ratio >= ONGOING_RATIO and hot_days >= MIN_HOT_DAYS and c["conflict"] >= MIN_CONFLICT) or \
                    (c["conflict"] >= HIGH_VOLUME and ratio >= HIGH_VOLUME_RATIO)
        i = info.get(cc, {})
        out.append({
            "country": cc, "name": i.get("name") or cc, "lat": i.get("lat"), "lon": i.get("lon"),
            "share": round(week, 1), "ratio": round(ratio, 2), "conflict": c["conflict"], "total": c["total"],
            "hot_days": hot_days, "series": series, "trend": trend,
            "recent": round(recent, 1) if recent is not None else None,
            "earlier": round(earlier, 1) if earlier is not None else None,
            "state": "escalating" if escalating else "ongoing" if sustained else None,
            "ongoing": bool(escalating or sustained) and i.get("lat") is not None,
            "rank": (3 if escalating else 1) * ratio * math.log10(max(c["conflict"], 10)),
        })
    out.sort(key=lambda x: x["rank"], reverse=True)
    return {"world_share": round(world * 100, 1), "days": days, "countries": out}
