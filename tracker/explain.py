"""Layer 2 of the hotspot card: a short AI-written "what the reporting says", from Gemini.

The model only ever sees what the engine holds (the at-a-glance lines and up to 15 signals),
and every answer is checked in code before it is kept: it must cite signals that exist, never
say "confirmed", start its title "Reported/Reports/Unverified" and keep outages causeless.
An answer that fails is stored with the reason and never shown. Confidence and the hotspot
level stay the engine's own; the model never sets them.

Runs only when a hotspot opens or its signals change (at most every 30 min per square), never
per visitor, with a hard daily spending cap. In preview mode (the default) answers go to
/api/explanations and web/preview.html only, not the public cards.

config.ini:
    [gemini]
    enabled = yes
    api_key = ...
    preview = yes          ; no: show on the public cards
    daily_cap_gbp = 0.50
"""
import hashlib
import json
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from . import db

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEFAULTS = {"enabled": "no", "api_key": "", "model": "gemini-3.1-flash-lite", "preview": "yes",
            "daily_cap_gbp": "0.50", "price_in_usd": "0.25", "price_out_usd": "1.50", "gbp_per_usd": "0.77"}
MIN_GAP = 30 * 60        # re-explain a square at most every 30 minutes
MAX_SIGNALS = 15
MAX_PER_PASS = 10        # a burst of new hotspots is spread over several minutes
UNCONFIRMED = {"ioda", "radar", "infra"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS explanations (
    id INTEGER PRIMARY KEY, cell TEXT NOT NULL, fingerprint TEXT, created INTEGER NOT NULL,
    model TEXT, tokens_in INTEGER, tokens_out INTEGER, cost_gbp REAL,
    ok INTEGER NOT NULL, reason TEXT, answer TEXT, refs TEXT
);
CREATE INDEX IF NOT EXISTS ix_explanations_cell ON explanations(cell, created);
"""

PROMPT = """You write the short explanation on a hotspot card for Ricochet, a public tracker of early signs of conflict and security incidents. You get one hotspot: the feeds that tripped it and its signals, numbered S1, S2...

RULES (follow all of them):
1. Use ONLY the signals given. No outside knowledge: no names, numbers, causes or background that aren't in the signals.
2. Every sentence must cite the signals it comes from, like [S3] or [S2, S7].
3. NEVER link two stories as cause and effect ("following", "after", "in response to", "sparked") unless one signal itself says they're linked. Separate stories in the same place are listed side by side.
4. Never write "confirmed". Write "reported" or "reports say", and name who said it where the signal does ("France 24 reports...").
5. The title leads with the strongest REPORTED story and starts with "Reported:" or "Reports of". If there are only hints and no news report, start it "Unverified:", e.g. "Unverified: possible fire at the Eagle's Nest".
6. Don't call anything an attack, terrorism or a strike unless the reporting uses that word, and then attribute it ("described by franceinfo as a racist attack").
7. Internet outages, traffic anomalies and provider incidents ([ioda], [radar], [infra]): never give a cause, always say "cause not confirmed", never link them to any other event.
8. [gdelt] lines are machine-coded and the code is often wrong: never repeat the code ("fight", "riot") as what happened. Their link is an unverified hint: you may say "an article address suggests...", never state it as fact.
9. [wiki] edit bursts show people are editing that article now: attention, not evidence of what happened.
10. [firms] heat clusters show heat, not its cause. With nothing else, say there isn't enough to say what's happening.
11. "supporting" = signals about the same story that aren't proof (edit bursts, link hints). "unrelated" = a different story, place or time, or opinion and commentary rather than reporting.
12. At most 2 stories: the main one and, if there is one, the next most important. If the signals don't support an explanation, say so. Never fill gaps. Plain, calm UK English, no drama words. Translate every non-English headline, exactly.

OUTPUT: only JSON in exactly this shape:
{"title": "under 12 words", "summary": "1-2 sentences with [S#] citations",
 "stories": [{"text": "one story, attributed", "based_on": ["S#"]}],
 "supporting": ["S#"], "unrelated": [{"id": "S#", "why": "a few words"}],
 "translations": {"S#": "English translation"}, "gaps": "what these signals can't tell us"}

HOTSPOT:
"""


def settings(cfg):
    sec = cfg["gemini"] if cfg.has_section("gemini") else {}
    out = {k: (sec.get(k, v) if hasattr(sec, "get") else v) for k, v in DEFAULTS.items()}
    out["enabled"] = str(out["enabled"]).strip().lower() in ("yes", "true", "1", "on") and bool(str(out["api_key"]).strip())
    out["preview"] = str(out["preview"]).strip().lower() not in ("no", "false", "0", "off")
    for k in ("daily_cap_gbp", "price_in_usd", "price_out_usd", "gbp_per_usd"):
        out[k] = float(out[k])
    return out


# --- what the model sees ------------------------------------------------------------------

def _hint(url):
    """A GDELT link as a short hint: host and path, no tracking."""
    m = re.match(r"https?://(?:www\.)?([^?#]+)", url or "")
    return m.group(1).rstrip("/") if m else ""


def build_input(h, signals):
    """The hotspot as text, plus refs {S#: {title, url, source}} for the card's links.
    signals: the square's signals (newest first preferred); duplicates are dropped."""
    icon = {"spike": "▲ well above normal", "above": "● above normal", "normal": "○ usual level"}
    lines = [f"place: {h.get('place') or 'not named'} ({h['lat']:.2f}, {h['lon']:.2f})", "feeds:"]
    for g in (h.get("glance") or {}).get("lines", []):
        lines.append(f"  - [{g['feed']}] {g['text']} · {g['note']} ({icon.get(g['state'], g['state'])})")
    if (h.get("glance") or {}).get("site"):
        lines.append(f"sensitive site: {h['glance']['site']}")
    if h.get("expected"):
        lines.append(f"expected event: {h['expected']['name']}")
    lines.append("signals (newest first):")
    refs, seen = {}, set()
    for s in sorted(signals, key=lambda s: -(s.get("observed_at") or 0)):
        title = (s.get("title") or "").strip()
        if not title or (s["source"], title) in seen or s["source"] == "hazard":
            continue
        seen.add((s["source"], title))
        sid = f"S{len(refs) + 1}"
        when = datetime.fromtimestamp(s["observed_at"], timezone.utc).strftime("%H:%MZ")
        if s["source"] == "gdelt":
            code = title.split(":", 1)[-1].split("(")[0].strip()
            where = title[title.find("(") + 1:title.rfind(")")] if "(" in title else ""
            text = f'coded "{code}" ({where}) · unverified link hint: {_hint(s.get("url"))}'
        else:
            text = title[:200]
        lines.append(f"  {sid} {when} [{s['source']}] {text}")
        refs[sid] = {"title": title, "url": s.get("url"), "source": s["source"]}
        if len(refs) >= MAX_SIGNALS:
            break
    return "\n".join(lines), refs


def fingerprint(h, refs):
    """Changes when the level, a feed's state or the set of signals shown changes."""
    states = sorted((g["feed"], g["state"]) for g in (h.get("glance") or {}).get("lines", []))
    key = json.dumps([h.get("level"), states, sorted(r["title"] for r in refs.values())])
    return hashlib.sha1(key.encode()).hexdigest()[:16]


# --- checking the answer ------------------------------------------------------------------

_CITE = re.compile(r"\bS(\d+)\b")
_TITLE_OK = re.compile(r"^\s*(reported|reports?|unverified)\b", re.I)
_CONFIRMED = re.compile(r"\bconfirm(ed|s|ation)?\b", re.I)
_NEGATED = re.compile(r"\b(not|no|never|yet to be|n't)\b[\w\s]{0,20}?confirm\w*", re.I)   # "has not been confirmed" is fine


def check(answer, refs, h):
    """Returns None if the answer may be kept, else the reason it can't."""
    if not isinstance(answer, dict):
        return "not a JSON object"
    for k in ("title", "summary"):
        if not isinstance(answer.get(k), str) or not answer[k].strip():
            return f"missing {k}"
    if not _TITLE_OK.match(answer["title"]):
        return "title not attributed (must start Reported/Reports/Unverified)"
    if len(answer["title"]) > 110:
        return "title too long"
    text = json.dumps(answer, ensure_ascii=False)
    cited = {f"S{n}" for n in _CITE.findall(text)}
    if not cited:
        return "no citations"
    if cited - set(refs):
        return f"cites signals that don't exist: {', '.join(sorted(cited - set(refs)))}"
    if not _CITE.search(answer["summary"]):
        return "summary has no citations"
    prose = " ".join([answer["title"], answer["summary"]] + [s.get("text", "") for s in answer.get("stories") or [] if isinstance(s, dict)])
    if _CONFIRMED.search(_NEGATED.sub("", prose)):
        return 'says "confirmed"'
    feeds = {g["feed"] for g in (h.get("glance") or {}).get("lines", [])}
    if feeds & UNCONFIRMED and re.search(r"\boutage|anomal|disruption", prose, re.I) and not re.search(r"cause not confirmed|not confirmed", prose, re.I):
        return 'mentions an outage without "cause not confirmed"'
    if len(answer.get("stories") or []) > 2:
        answer["stories"] = answer["stories"][:2]
    return None


# --- calling Gemini -----------------------------------------------------------------------

def call(st, text, timeout=60):
    """One generateContent call. Returns (answer dict or None, tokens_in, tokens_out)."""
    body = {"contents": [{"role": "user", "parts": [{"text": PROMPT + text}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2, "maxOutputTokens": 900,
                                 "thinkingConfig": {"thinkingLevel": "minimal"}}}
    req = urllib.request.Request(ENDPOINT.format(model=st["model"]), data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "x-goog-api-key": st["api_key"]})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        res = json.loads(r.read())
    use = res.get("usageMetadata") or {}
    t_in = use.get("promptTokenCount", 0)
    t_out = use.get("candidatesTokenCount", 0) + use.get("thoughtsTokenCount", 0)   # thinking is billed as output
    try:
        raw = res["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(raw), t_in, t_out
    except (KeyError, IndexError, ValueError):
        return None, t_in, t_out


def cost_gbp(st, t_in, t_out):
    return (t_in * st["price_in_usd"] + t_out * st["price_out_usd"]) / 1e6 * st["gbp_per_usd"]


def spent_today(con, now=None):
    day = int(now or time.time()) // 86400
    r = con.execute("SELECT COALESCE(SUM(cost_gbp), 0) FROM explanations WHERE created >= ?", (day * 86400,)).fetchone()
    return float(r[0])


# --- the engine loop ----------------------------------------------------------------------

def explain_once(con, st, hotspots, signals_for, now=None, caller=call, log=print):
    """One pass over the current hotspots. signals_for(cell) -> that square's signals."""
    now = now or time.time()
    done = 0
    for h in hotspots:
        if h.get("level") not in ("signal", "reported") or done >= MAX_PER_PASS:
            continue
        text, refs = build_input(h, signals_for(h["cell"]))
        if not refs:
            continue
        fp = fingerprint(h, refs)
        last = con.execute("SELECT fingerprint, created FROM explanations WHERE cell=? ORDER BY created DESC LIMIT 1", (h["cell"],)).fetchone()
        if last and (last["fingerprint"] == fp or now - last["created"] < MIN_GAP):
            continue
        if spent_today(con, now) >= st["daily_cap_gbp"]:
            log(f"[explain] daily cap of £{st['daily_cap_gbp']:.2f} reached, card summaries paused until midnight UTC")
            return done
        reason, answer, t_in, t_out = None, None, 0, 0
        try:
            answer, t_in, t_out = caller(st, text)
            reason = check(answer, refs, h) if answer is not None else "answer wasn't valid JSON"
        except urllib.error.HTTPError as e:
            reason = f"HTTP {e.code}"
        except Exception as e:
            reason = f"call failed: {str(e)[:120]}"
        failed = bool(reason) and reason.startswith(("HTTP", "call failed"))
        with db._lock:   # a failed call keeps no fingerprint, so the square is retried after MIN_GAP
            con.execute("INSERT INTO explanations (cell, fingerprint, created, model, tokens_in, tokens_out, cost_gbp, ok, reason, answer, refs) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (h["cell"], None if failed else fp, int(now), st["model"], t_in, t_out, cost_gbp(st, t_in, t_out), 0 if reason else 1, reason,
                         json.dumps(answer, ensure_ascii=False) if answer is not None else None, json.dumps(refs, ensure_ascii=False)))
            con.commit()
        done += 1
        if failed:
            log(f"[explain] {h['cell']}: {reason}")
            if reason in ("HTTP 401", "HTTP 403", "HTTP 429"):
                return done      # bad key, no credit or rate-limited: stop this pass
    return done


def latest(con, cells=None):
    """Newest kept answer per square: {cell: {"title", "summary", "stories", "translations", "refs", "created", "model"}}."""
    q = "SELECT * FROM explanations WHERE ok=1 AND id IN (SELECT MAX(id) FROM explanations WHERE ok=1 GROUP BY cell)"
    out = {}
    for r in con.execute(q):
        if cells is not None and r["cell"] not in cells:
            continue
        a = json.loads(r["answer"])
        out[r["cell"]] = {"title": a.get("title"), "summary": a.get("summary"), "stories": a.get("stories") or [],
                          "translations": a.get("translations") or {}, "gaps": a.get("gaps"),
                          "refs": json.loads(r["refs"] or "{}"), "created": r["created"], "model": r["model"]}
    return out


def recent(con, limit=60):
    """Every recent attempt, kept or not, for the preview page."""
    rows = [dict(r) for r in con.execute("SELECT * FROM explanations ORDER BY id DESC LIMIT ?", (limit,))]
    for r in rows:
        r["answer"] = json.loads(r["answer"]) if r["answer"] else None
        r["refs"] = json.loads(r["refs"] or "{}")
    return rows


def stats(con):
    day = int(time.time()) // 86400 * 86400
    r = con.execute("SELECT COUNT(*), COALESCE(SUM(ok), 0), COALESCE(SUM(cost_gbp), 0), COALESCE(SUM(tokens_in), 0), COALESCE(SUM(tokens_out), 0) "
                    "FROM explanations WHERE created >= ?", (day,)).fetchone()
    total = con.execute("SELECT COALESCE(SUM(cost_gbp), 0) FROM explanations").fetchone()[0]
    return {"today": {"calls": r[0], "kept": r[1], "cost_gbp": round(r[2], 4), "tokens_in": r[3], "tokens_out": r[4]},
            "all_time_gbp": round(total, 4)}


PUBLIC = False     # set by the runner when preview is off: the server then adds answers to the cards


def runner(cfg, hotspots_fn, log=print):
    global PUBLIC
    st = settings(cfg)
    if not st["enabled"]:
        log("[explain] off: add [gemini] api_key and enabled = yes to config.ini for AI card summaries")
        return
    con = db.connect()
    con.executescript(SCHEMA)
    PUBLIC = not st["preview"]
    log(f"[explain] on ({st['model']}, {'preview only' if st['preview'] else 'public cards'}, cap £{st['daily_cap_gbp']:.2f}/day)")
    sig = lambda cell: [dict(r) for r in con.execute(
        "SELECT source, kind, title, url, observed_at FROM signals WHERE cell=? AND observed_at >= ? ORDER BY observed_at DESC LIMIT 60",
        (cell, int(time.time()) - 6 * 3600))]
    while True:
        try:
            with db._lock:   # two weeks of history is plenty for the preview page and cost checks
                con.execute("DELETE FROM explanations WHERE created < ?", (int(time.time()) - 14 * 86400,))
                con.commit()
            explain_once(con, st, hotspots_fn(con), sig, log=log)
        except Exception as e:
            log(f"[explain] pass failed: {e}")
        time.sleep(60)
