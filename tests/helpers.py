"""Shared test helpers. Temporary folders are removed when the test run ends: the server runs the
tests on every deploy, and Debian keeps /tmp in memory on a 1 GB machine."""
import atexit
import shutil
import tempfile

_made = []


def tmpdir():
    d = tempfile.mkdtemp(prefix="ricochet-test-")
    _made.append(d)
    return d


@atexit.register
def _cleanup():
    for d in _made:
        shutil.rmtree(d, ignore_errors=True)
