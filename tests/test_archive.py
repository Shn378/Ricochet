import datetime as dt
import gzip
import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from tracker import archive, db

AK, SK = "AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
WHEN = dt.datetime(2013, 5, 24, tzinfo=dt.timezone.utc)


class SigV4(unittest.TestCase):
    """Worked examples from the AWS Signature V4 documentation for S3."""

    def test_put_object(self):
        h = archive.sigv4_headers("PUT", "https://examplebucket.s3.amazonaws.com/test$file.text",
                                  {"Date": "Fri, 24 May 2013 00:00:00 GMT", "x-amz-storage-class": "REDUCED_REDUNDANCY"},
                                  b"Welcome to Amazon S3.", AK, SK, region="us-east-1", now=WHEN)
        self.assertTrue(h["authorization"].endswith("Signature=98ad721746da40c64f1a55b78f14c238d841ea1380cd77a1b5971af0ece108bd"))

    def test_get_object(self):
        h = archive.sigv4_headers("GET", "https://examplebucket.s3.amazonaws.com/test.txt", {"Range": "bytes=0-9"}, b"",
                                  AK, SK, region="us-east-1", now=WHEN)
        self.assertTrue(h["authorization"].endswith("Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"))


class Digest(unittest.TestCase):
    def test_day_digest_and_upload(self):
        d = tempfile.mkdtemp()
        db.DATA, db.DB_PATH, db.RAW_DIR = d, d + "/t.db", d + "/raw"
        archive.HOTSPOT_DIR = os.path.join(d, "hotspots")
        con = db.connect()
        t = int(dt.datetime(2026, 10, 20, 12, tzinfo=dt.timezone.utc).timestamp())
        db.add_signals(con, [{"uid": "a", "source": "gdelt", "kind": "k", "lat": 1, "lon": 2, "title": "x", "observed_at": t},
                             {"uid": "b", "source": "gdelt", "kind": "k", "lat": 1, "lon": 2, "title": "y", "observed_at": t + 86400}])
        archive.record_hotspots([{"cell": "1:2"}], now=t)

        class Fake:
            def __init__(self): self.got = {}
            def put(self, key, body, ctype=None): self.got[key] = body

        store = Fake()
        archive.upload_day(con, store, "2026-10-20", log=lambda *a: None)
        lines = gzip.decompress(store.got["digest/2026/10/20/signals.jsonl.gz"]).decode().strip().split("\n")
        self.assertEqual([json.loads(l)["uid"] for l in lines], ["a"])
        self.assertIn("digest/2026/10/20/hotspots.jsonl.gz", store.got)


if __name__ == "__main__":
    unittest.main()
