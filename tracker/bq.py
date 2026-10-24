"""BigQuery, read-only: GDELT history for replays and the benchmark, without downloading
hundreds of 15-minute zip files from GDELT's servers.

Setup (once, on the server): a service account with BigQuery Job User + BigQuery Data Viewer,
its JSON key at ~/ricochet/bq-key.json (never in git), and in config.ini:
    [bigquery]
    key_file = bq-key.json
    project = <the Google Cloud project id>
Check it with:  python3 -m tracker.bq check

Costs: queries on GDELT's public tables count against the free 1 TiB a month. Every query
carries a hard cap (MAX_BYTES), so a mistake fails instead of running up a bill.

Stdlib only: the RS256 signature for Google's token exchange is done here with plain
integers (PKCS#1 v1.5), since the standard library has no RSA.
"""
import base64
import configparser
import hashlib
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

from . import db

TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/bigquery.readonly"
API = "https://bigquery.googleapis.com/bigquery/v2/projects/{}/queries"
MAX_BYTES = 3 * 10 ** 9          # per query; GDELT events for a day or two is a few hundred MB
GDELT_TABLE = "gdelt-bq.gdeltv2.events_partitioned"
ROOTS = ("14", "17", "18", "19", "20")   # protest, coerce, assault, fight, mass violence: what gdelt.kind_of uses

_token = {"value": None, "until": 0}


# ---------- config ----------
def settings(cfg=None):
    """(key dict, project) or None when BigQuery isn't set up."""
    if cfg is None:
        cfg = configparser.ConfigParser()
        cfg.read(os.path.join(db.ROOT, "config.ini"), encoding="utf-8")
    if not cfg.has_section("bigquery"):
        return None
    path = cfg.get("bigquery", "key_file", fallback="").strip()
    if not path:
        return None
    if not os.path.isabs(path):
        path = os.path.join(db.ROOT, path)
    try:
        with open(path, encoding="utf-8") as f:
            key = json.load(f)
    except (OSError, ValueError):
        return None
    return key, cfg.get("bigquery", "project", fallback="").strip() or key.get("project_id")


# ---------- RS256 with plain integers ----------
def _der(buf, i):
    """One DER element at i -> (tag, content bytes, next index)."""
    tag, ln = buf[i], buf[i + 1]
    i += 2
    if ln & 0x80:
        n = ln & 0x7F
        ln = int.from_bytes(buf[i:i + n], "big")
        i += n
    return tag, buf[i:i + ln], i + ln


def _ints(seq):
    out, i = [], 0
    while i < len(seq):
        tag, body, i = _der(seq, i)
        if tag == 0x02:
            out.append(int.from_bytes(body, "big"))
    return out


def private_key(pem):
    """PKCS#8 or PKCS#1 PEM -> (n, d)."""
    body = "".join(l for l in pem.strip().splitlines() if not l.startswith("-----"))
    der = base64.b64decode(body)
    _, top, _ = _der(der, 0)
    tag, first, i = _der(top, 0)            # version
    tag, nxt, j = _der(top, i)
    if tag == 0x30:                          # PKCS#8: algorithm, then OCTET STRING with PKCS#1 inside
        _, octet, _ = _der(top, j)
        _, rsa, _ = _der(octet, 0)
        nums = _ints(rsa)
    else:                                    # PKCS#1 directly
        nums = _ints(top)
    return nums[1], nums[3]                 # version, n, e, d, ...


_SHA256_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")


def rs256(data, n, d):
    k = (n.bit_length() + 7) // 8
    t = _SHA256_PREFIX + hashlib.sha256(data).digest()
    em = b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t
    return pow(int.from_bytes(em, "big"), d, n).to_bytes(k, "big")


def _b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=")


def token(key):
    if _token["value"] and time.time() < _token["until"] - 60:
        return _token["value"]
    now = int(time.time())
    head = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    claims = _b64(json.dumps({"iss": key["client_email"], "scope": SCOPE, "aud": TOKEN_URL,
                              "iat": now, "exp": now + 3600}).encode())
    n, d = private_key(key["private_key"])
    jwt = head + b"." + claims + b"." + _b64(rs256(head + b"." + claims, n, d))
    body = f"grant_type=urn%3Aietf%3Aparams%3Aoauth%3Agrant-type%3Ajwt-bearer&assertion={jwt.decode()}".encode()
    req = urllib.request.Request(TOKEN_URL, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=30) as r:
        res = json.load(r)
    _token.update(value=res["access_token"], until=now + int(res.get("expires_in", 3600)))
    return _token["value"]


# ---------- queries ----------
def query(sql, setup=None, dry_run=False, max_bytes=MAX_BYTES, timeout_ms=120000):
    """Standard SQL -> (field names, rows as lists of strings). dry_run returns bytes it would scan."""
    setup = setup or settings()
    if not setup:
        raise RuntimeError("BigQuery isn't set up: add [bigquery] key_file and project to config.ini")
    key, project = setup
    payload = {"query": sql, "useLegacySql": False, "dryRun": dry_run, "timeoutMs": timeout_ms,
               "maximumBytesBilled": str(max_bytes), "maxResults": 50000}
    req = urllib.request.Request(API.format(project), data=json.dumps(payload).encode(),
                                 headers={"Authorization": f"Bearer {token(key)}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout_ms / 1000 + 30) as r:
        res = json.load(r)
    if dry_run:
        return int(res.get("totalBytesProcessed", 0))
    if not res.get("jobComplete"):
        raise RuntimeError("BigQuery query didn't finish in time")
    fields = [f["name"] for f in res["schema"]["fields"]]
    rows = [[c.get("v") for c in r["f"]] for r in res.get("rows", [])]
    page, job = res.get("pageToken"), res["jobReference"]
    while page:   # long results come in pages
        url = (f"https://bigquery.googleapis.com/bigquery/v2/projects/{project}/queries/{job['jobId']}"
               f"?pageToken={page}&location={job.get('location', 'US')}&maxResults=50000")
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token(key)}"})
        with urllib.request.urlopen(req, timeout=120) as r:
            more = json.load(r)
        rows += [[c.get("v") for c in r["f"]] for r in more.get("rows", [])]
        page = more.get("pageToken")
    return fields, rows


def _stamp(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y%m%d%H%M%S")


def gdelt_sql(start, end):
    d0 = datetime.fromtimestamp(start, timezone.utc).strftime("%Y-%m-%d")
    d1 = datetime.fromtimestamp(end + 86400, timezone.utc).strftime("%Y-%m-%d")
    roots = ", ".join(f"'{r}'" for r in ROOTS)
    return (f"SELECT * FROM `{GDELT_TABLE}` "
            f"WHERE _PARTITIONTIME BETWEEN TIMESTAMP('{d0}') AND TIMESTAMP('{d1}') "
            f"AND DATEADDED BETWEEN {_stamp(start)} AND {_stamp(end)} "
            f"AND IsRootEvent = 1 AND EventRootCode IN ({roots})")


def gdelt_signals(start, end, setup=None):
    """GDELT conflict events added between start and end (unix seconds), turned into Ricochet
    signals exactly as the live collector would, grouped by 15-minute export slot:
    {slot_start_unix: [signal, ...]}."""
    from .collectors import gdelt
    fields, rows = query(gdelt_sql(start, end), setup)
    if len(fields) < 61:
        raise RuntimeError(f"unexpected GDELT table shape ({len(fields)} columns)")
    added = fields.index("DATEADDED")
    by_slot = {}
    for r in rows:
        cells = ["" if v is None else str(v) for v in r[:61]]
        try:
            t = int(datetime.strptime(cells[added], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            continue
        by_slot.setdefault(t - t % 900, []).append("\t".join(cells))
    return {slot: gdelt.parse_export("\n".join(lines).encode()) for slot, lines in sorted(by_slot.items())}


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "check":
        s = settings()
        if not s:
            sys.exit("Not set up: config.ini needs [bigquery] key_file = bq-key.json and the key file must exist.")
        now = int(time.time())
        scan = query(gdelt_sql(now - 86400, now), s, dry_run=True)
        print(f"BigQuery OK as {s[0]['client_email']} (project {s[1]}). "
              f"A day of GDELT conflict events would scan {scan / 1e6:.0f} MB (free: 1,000,000 MB a month).")
    else:
        print(__doc__)
