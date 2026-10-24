import base64
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from tracker import bq


def _prime(bits, rnd):
    while True:
        n = rnd.getrandbits(bits) | (1 << bits - 1) | 1
        if all(pow(a, n - 1, n) == 1 for a in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37)):
            return n


def _der_int(v):
    b = v.to_bytes((v.bit_length() + 8) // 8, "big")
    return b"\x02" + _len(b) + b


def _len(b):
    n = len(b)
    return bytes([n]) if n < 128 else bytes([0x80 | ((n.bit_length() + 7) // 8)]) + n.to_bytes((n.bit_length() + 7) // 8, "big")


class Signing(unittest.TestCase):
    def test_rs256_round_trip(self):
        rnd = random.Random(7)
        p, q, e = _prime(512, rnd), _prime(512, rnd), 65537
        n, d = p * q, pow(e, -1, (p - 1) * (q - 1))
        seq = b"".join(_der_int(x) for x in (0, n, e, d, p, q, d % (p - 1), d % (q - 1), pow(q, -1, p)))
        pem = "-----BEGIN RSA PRIVATE KEY-----\n" + base64.b64encode(b"\x30" + _len(seq) + seq).decode() + "\n-----END RSA PRIVATE KEY-----"
        self.assertEqual(bq.private_key(pem), (n, d))
        sig = bq.rs256(b"hello", n, d)
        em = pow(int.from_bytes(sig, "big"), e, n).to_bytes(len(sig), "big")
        self.assertTrue(em.startswith(b"\x00\x01\xff"))
        self.assertTrue(em.endswith(bq.hashlib.sha256(b"hello").digest()))

    def test_not_set_up(self):
        import configparser
        self.assertIsNone(bq.settings(configparser.ConfigParser()))

    def test_sql_is_partition_bounded(self):
        sql = bq.gdelt_sql(1792800000, 1792803600)
        self.assertIn("_PARTITIONTIME BETWEEN", sql)
        self.assertIn("IsRootEvent = 1", sql)


if __name__ == "__main__":
    unittest.main()
