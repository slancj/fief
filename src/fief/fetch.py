"""Shared download helpers: fetch bytes + sha256 verification."""

from __future__ import annotations

import hashlib
import urllib.request


def fetch(url: str, timeout: int = 120) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "fief"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def verify_sha256(data: bytes, want: str, what: str) -> str:
    """Return the hex digest, or raise on mismatch. `want` may have trailing filename."""
    want_hash = want.split()[0]
    got = hashlib.sha256(data).hexdigest()
    if got != want_hash:
        raise RuntimeError(f"checksum mismatch for {what}: {got} != {want_hash}")
    return got
