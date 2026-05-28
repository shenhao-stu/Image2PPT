"""Tiny proof-of-work challenge issuer + verifier.

Anti-bot gate in front of /api/auth/login. Cheap on humans (~1-2 s in
JS), expensive enough to discourage scripted brute-force.

Protocol (all body fields are strings):

    GET  /api/auth/pow     -> { "challenge": "<32 hex>",
                                "difficulty": 18,
                                "issued_at": 1779999999 }
    POST /api/auth/login   body adds { "pow_challenge": "<32 hex>",
                                       "pow_nonce": "<int as string>" }

Verification:
    sha256(challenge + ":" + nonce) must start with `difficulty` zero
    bits. Challenge must be one we issued in the last CHALLENGE_TTL
    seconds and not already redeemed (single-use).
"""
from __future__ import annotations

import hashlib
import os
import secrets
import time
from threading import Lock

DIFFICULTY_BITS = int(os.environ.get("DECKWEAVER_POW_BITS", "18"))
CHALLENGE_TTL = int(os.environ.get("DECKWEAVER_POW_TTL_SEC", "300"))
MAX_OPEN = 1024


_issued: dict[str, float] = {}
_lock = Lock()


def _gc_locked(now: float) -> None:
    cutoff = now - CHALLENGE_TTL
    stale = [k for k, ts in _issued.items() if ts < cutoff]
    for k in stale:
        del _issued[k]
    while len(_issued) > MAX_OPEN:
        oldest = min(_issued.items(), key=lambda kv: kv[1])[0]
        del _issued[oldest]


def issue() -> dict[str, object]:
    now = time.time()
    chal = secrets.token_hex(16)  # 128 bits
    with _lock:
        _gc_locked(now)
        _issued[chal] = now
    return {"challenge": chal, "difficulty": DIFFICULTY_BITS, "issued_at": int(now)}


def _leading_zero_bits(digest: bytes) -> int:
    n = 0
    for b in digest:
        if b == 0:
            n += 8
            continue
        for shift in (7, 6, 5, 4, 3, 2, 1, 0):
            if (b >> shift) & 1:
                return n
            n += 1
        break
    return n


def verify(challenge: str, nonce: str) -> tuple[bool, str]:
    """Validate (challenge, nonce). Returns (ok, reason).
    On success the challenge is consumed so it can't be re-played."""
    if not challenge or not nonce:
        return False, "missing_pow"
    if not isinstance(challenge, str) or not isinstance(nonce, str):
        return False, "bad_pow_type"
    if len(challenge) != 32:
        return False, "bad_pow_format"
    now = time.time()
    with _lock:
        _gc_locked(now)
        ts = _issued.get(challenge)
        if ts is None:
            return False, "stale_pow"
        if now - ts > CHALLENGE_TTL:
            del _issued[challenge]
            return False, "stale_pow"
        digest = hashlib.sha256(f"{challenge}:{nonce}".encode("ascii", errors="replace")).digest()
        if _leading_zero_bits(digest) < DIFFICULTY_BITS:
            return False, "weak_pow"
        del _issued[challenge]
    return True, "ok"
