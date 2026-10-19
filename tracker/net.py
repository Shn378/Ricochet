"""Small HTTP helper using only the standard library."""
import gzip
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
