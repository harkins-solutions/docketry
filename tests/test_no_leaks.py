"""Leak tripwire: no private identifiers may enter this public repo.

The denylist is salted-HMAC digests of identifiers private to the
maintainer's environment; the salt lives OUTSIDE the repo (CI secret
DOCKETRY_LEAK_SALT / ~/.config/docketry-leak-salt), so the published list is
not dictionary-testable. Without the salt the check reports itself skipped
rather than passing silently.

If this test fails: the named file contains something that must not be
published. Remove it BEFORE committing; if it was ever pushed, rewrite the
history, not just the tip.
"""
import hashlib
import hmac
import os
import re
import subprocess
import unittest
from pathlib import Path

_DENY = {
    "0b5e1413ecdfa55138d057f6ad8109b5ee3ee5d218215b92237638941ddb3a4f",
    "0e86a3ea4deb5898e2270242624cb0f5496b7370d466b4dc6559814cc6872a69",
    "19803aa11bfc5abe9d5234733160186ff929ac9f2e890b0caf157a6b880de36d",
    "1f80472c34b05e3432c9bb67f00ae3ef3e6adabf59bbeba2056de05cb816c863",
    "22e18ac52372db4ffd3af3cf31d2d6301781863261cc5d30c7290aba894ae2d0",
    "281d88a429d3f7d1606978726a13cc9df24f95b56b1e8fda115ba4ee3670ea01",
    "36ec9f3795017b57f6e029b70e5886b38315487dab7e5914f1af5aa8b05b76fb",
    "458fdd39016ba80974b6faea93213d3c1bce9a812b9b36689b9bddd40e2b972d",
    "597a94dab9fb594fe538256e4e19883e3e00a8ebe750af321fbe467727a97156",
    "5982ab3768ad9a9bece921feb24760ed19adc7ecdc07317c6a5873bbe27c7adc",
    "715a0f7153b81e138e166156b6bfb79d9c2c2ad7222e10208319019bc973992a",
    "7dd8df75664b302a83d21f2e5beeb2a684d4e8cee293eb81a5b7c7a00f7da887",
    "834515dcdcd7b9628b5339f0d12fac028204aa72cd76ed7426239744e7b27839",
    "8cf4579d2ab250fb1c2353c1034aa7da31c22c201cbce1f15966cf3166a57755",
    "933005f12e4bf27ef99ab5afbaec3528969242c4575137d8ce8182f3a1a6cbbf",
    "9397dccb89d44b09f0d2a1d3986617c94f8abf779c721cc365b2bfc9fe092c66",
    "94709df22171aac831b57fa41b19c9ff29f6088fac1098ebeae45c7b6cdc3f53",
    "d41af82316eb30154e1101873b43a542acdcde42e47758946e27482c7eafcaa7",
    "d604409a21045ad48fee7a37f7e19b40111bc352414dff78cc426bf7e13823d5",
    "e99eae2ac13b6f6ed9539c864230dc4573b4a0bc3f8df117f0c7095a3cfdb087",
    "ea72a0930da85c81b0e55c539c13bf05680ce2047d862db80f66361e545ef0f0",
    "f3bf42d18d708072be009866aa42ea5dcf761691170dda2ff87c95c13808705c",
    "f5c5b50da45e45a0c13372a99b09811d9de855ba2d31c380d0011b854cd633e2",
}

_TOKEN = re.compile(r"[a-z0-9]+(?:[.\-_][a-z0-9]+)*")
_SKIP_SUFFIX = {".png", ".jpg", ".pdf", ".ico"}


def _salt() -> str | None:
    if os.environ.get("DOCKETRY_LEAK_SALT"):
        return os.environ["DOCKETRY_LEAK_SALT"].strip()
    p = Path.home() / ".config" / "docketry-leak-salt"
    if p.exists():
        return p.read_text().strip()
    return None


class TestNoLeaks(unittest.TestCase):
    def test_tracked_files_carry_no_private_identifiers(self):
        salt = _salt()
        if not salt:
            self.skipTest("DOCKETRY_LEAK_SALT not available; leak check runs in project CI")
        key = salt.encode()
        files = subprocess.run(
            ["git", "ls-files"], capture_output=True, text=True, check=True
        ).stdout.splitlines()
        hits = []
        for f in files:
            p = Path(f)
            if p.suffix in _SKIP_SUFFIX or not p.exists():
                continue
            try:
                text = p.read_text(errors="ignore").lower()
            except (OSError, UnicodeDecodeError):
                continue
            for tok in set(_TOKEN.findall(text)):
                for piece in {tok, *tok.split("."), *tok.split("_")}:
                    digest = hmac.new(key, piece.encode(), hashlib.sha256).hexdigest()
                    if digest in _DENY:
                        hits.append(f)
                        break
        self.assertEqual(hits, [], f"private identifiers found in: {sorted(set(hits))}")


if __name__ == "__main__":
    unittest.main()
