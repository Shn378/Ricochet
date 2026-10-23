"""Evidence archive: copies what's worth keeping to Cloudflare R2 before the server prunes it.

R2 speaks the S3 API. This signs requests with AWS Signature V4 using only the standard
library. The key lives in config.ini under [r2] (account, bucket, access_key, secret_key)
and is scoped to the one bucket, read and write only.

What goes up, once a day for the previous UTC day:
  digest/YYYY/MM/DD/signals.jsonl.gz    every signal stored that day (the raw material for replays)
  digest/YYYY/MM/DD/hotspots.jsonl.gz   what the globe showed, hour by hour
  benchmark/<incident>.json             frozen benchmark evidence (tracker/benchmark.py)
Selective on purpose: a few MB a day, not the ~200 MB of raw feed replies.
"""
import datetime as dt
import gzip
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from . import db

HOTSPOT_DIR = os.path.join(db.DATA, "hotspots")


def _sign(key, msg):
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def sigv4_headers(method, url, headers, payload, access_key, secret_key, region="auto", service="s3", now=None):
    """Headers (including Authorization) for an S3-style request. `headers` are extra headers to sign."""
    now = now or dt.datetime.now(dt.timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    day = now.strftime("%Y%m%d")
    u = urllib.parse.urlsplit(url)
    payload_hash = hashlib.sha256(payload).hexdigest()
    h = {k.lower(): str(v).strip() for k, v in headers.items()}
    h.setdefault("host", u.netloc)
    h["x-amz-content-sha256"] = payload_hash
    h["x-amz-date"] = amz_date
    signed = ";".join(sorted(h))
    canon_headers = "".join(f"{k}:{h[k]}\n" for k in sorted(h))
    path = urllib.parse.quote(u.path or "/", safe="/-_.~")
    query = "&".join(f"{urllib.parse.quote(k, safe='-_.~')}={urllib.parse.quote(v, safe='-_.~')}"
                     for k, v in sorted(urllib.parse.parse_qsl(u.query, keep_blank_values=True)))
    canonical = "\n".join([method, path, query, canon_headers, signed, payload_hash])
    scope = f"{day}/{region}/{service}/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    k = _sign(_sign(_sign(_sign(("AWS4" + secret_key).encode(), day), region), service), "aws4_request")
    sig = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
    h["authorization"] = f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders={signed}, Signature={sig}"
    return h


class R2:
    def __init__(self, account, bucket, access_key, secret_key):
        self.base = f"https://{account}.r2.cloudflarestorage.com/{bucket}/"
        self.ak, self.sk = access_key, secret_key

    @classmethod
    def from_config(cls, cfg):
        if not cfg.has_section("r2"):
            return None
        vals = [cfg.get("r2", k, fallback="").strip() for k in ("account", "bucket", "access_key", "secret_key")]
        return cls(*vals) if all(vals) else None

    def put(self, key, body, content_type="application/octet-stream"):
        url = self.base + urllib.parse.quote(key, safe="/-_.~")
        h = sigv4_headers("PUT", url, {"content-type": content_type}, body, self.ak, self.sk)
        req = urllib.request.Request(url, data=body, method="PUT", headers=h)
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status

    def exists(self, key):
        url = self.base + urllib.parse.quote(key, safe="/-_.~")
        h = sigv4_headers("HEAD", url, {}, b"", self.ak, self.sk)
        try:
            with urllib.request.urlopen(urllib.request.Request(url, method="HEAD", headers=h), timeout=30) as r:
                return r.status == 200
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return False
            raise


def record_hotspots(hotspots, now=None):
    """Append the globe's current hotspots to today's local log (called hourly)."""
    now = int(now or time.time())
    os.makedirs(HOTSPOT_DIR, exist_ok=True)
    day = dt.datetime.fromtimestamp(now, dt.timezone.utc).strftime("%Y-%m-%d")
    with open(os.path.join(HOTSPOT_DIR, f"{day}.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps({"t": now, "hotspots": hotspots}) + "\n")


def day_digest(con, day):
    """Gzipped JSON lines of every signal observed on a UTC day (YYYY-MM-DD)."""
    start = int(dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp())
    rows = con.execute("SELECT * FROM signals WHERE observed_at >= ? AND observed_at < ? ORDER BY observed_at", (start, start + 86400))
    lines = []
    for r in rows:
        d = dict(r)
        d.pop("id", None)
        try:
            d["extra"] = json.loads(d.get("extra") or "{}")
        except ValueError:
            pass
        lines.append(json.dumps(d, ensure_ascii=False))
    return gzip.compress(("\n".join(lines) + "\n").encode("utf-8")), len(lines)


def upload_day(con, store, day, log=print):
    y, m, d = day.split("-")
    blob, n = day_digest(con, day)
    store.put(f"digest/{y}/{m}/{d}/signals.jsonl.gz", blob, "application/gzip")
    sent = [f"{n} signals ({len(blob) // 1024} KB)"]
    hp = os.path.join(HOTSPOT_DIR, f"{day}.jsonl")
    if os.path.exists(hp):
        with open(hp, "rb") as f:
            hb = gzip.compress(f.read())
        store.put(f"digest/{y}/{m}/{d}/hotspots.jsonl.gz", hb, "application/gzip")
        sent.append(f"hotspot log ({len(hb) // 1024} KB)")
    from .benchmark import FROZEN
    if os.path.isdir(FROZEN):
        for name in sorted(os.listdir(FROZEN)):
            key = f"benchmark/{name}"
            if name.endswith(".json") and not db.get_meta(con, f"archived:{key}"):
                with open(os.path.join(FROZEN, name), "rb") as f:
                    store.put(key, f.read(), "application/json")
                db.set_meta(con, f"archived:{key}", int(time.time()))
                sent.append(key)
    log(f"[archive] {day}: " + ", ".join(sent))


def runner(cfg, hotspots_fn, log=print):
    """Engine loop: log hotspots hourly; after 01:00 UTC send yesterday's digest (and catch up)."""
    store = R2.from_config(cfg)
    if not store:
        log("[archive] no [r2] settings in config.ini: hotspot log kept locally only")
    con = db.connect()
    while True:
        try:
            record_hotspots(hotspots_fn(con))
        except Exception as e:
            log(f"[archive] hotspot log failed: {e}")
        try:   # local hotspot logs: keep two weeks (the archive keeps the rest)
            for name in os.listdir(HOTSPOT_DIR):
                if os.path.getmtime(os.path.join(HOTSPOT_DIR, name)) < time.time() - 15 * 86400:
                    os.remove(os.path.join(HOTSPOT_DIR, name))
        except OSError:
            pass
        if store:
            today = dt.datetime.now(dt.timezone.utc).date()
            for back in range(7, 0, -1):   # catch up on missed days (e.g. after downtime)
                day = (today - dt.timedelta(days=back)).isoformat()
                if db.get_meta(con, f"archived_day:{day}"):
                    continue
                oldest = con.execute("SELECT MIN(observed_at) FROM signals").fetchone()[0]
                if oldest is None or oldest > int(dt.datetime.fromisoformat(day).replace(tzinfo=dt.timezone.utc).timestamp()) + 86400:
                    continue
                try:
                    upload_day(con, store, day, log)
                    db.set_meta(con, f"archived_day:{day}", int(time.time()))
                except Exception as e:
                    log(f"[archive] {day} upload failed, will retry: {e}")
                    break
        time.sleep(3600)
