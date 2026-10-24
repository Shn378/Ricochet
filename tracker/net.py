"""Small HTTP helper using only the standard library."""
import gzip
import urllib.error
import urllib.request

# Use the operating system's certificate checks (Windows fetches missing
# intermediate certificates the way browsers do). Optional: falls back to
# Python's own store if the package isn't installed.
try:
    import truststore
    truststore.inject_into_ssl()
    TRUSTSTORE = True
except ImportError:
    TRUSTSTORE = False

USER_AGENT = "Ricochet-Tripwire/0.1 (personal research project)"


def get(url, timeout=60, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            data = gzip.decompress(data)
        return data


def get_if_changed(url, etag=None, modified=None, timeout=60):
    """Conditional GET: (data, etag, modified), with data None when the server says nothing changed (304).
    Feeds that support it send nothing new for an unchanged check, which is kinder to the outlet."""
    headers = {k: v for k, v in (("If-None-Match", etag), ("If-Modified-Since", modified)) if v}
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                data = gzip.decompress(data)
            return data, r.headers.get("ETag"), r.headers.get("Last-Modified")
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return None, etag, modified
        raise
