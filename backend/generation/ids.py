"""Deterministic gateway-style identifier minting.

Identifiers look like Razorpay's (``pay_29QQoUBi66xm2f``) so the dataset reads
like real settlement data rather than like ``payment_1``. They are produced
from a seeded RNG, so the same seed always yields the same dataset - the
evaluation must be reproducible.
"""

from __future__ import annotations

import random
import string

ALPHABET = string.digits + string.ascii_letters  # base62
ID_BODY_LENGTH = 14


class IdMinter:
    """Seeded, collision-free identifier source."""

    def __init__(self, seed: int) -> None:
        self._rng = random.Random(seed)
        self._issued: set[str] = set()

    def mint(self, prefix: str) -> str:
        while True:
            body = "".join(self._rng.choices(ALPHABET, k=ID_BODY_LENGTH))
            candidate = f"{prefix}_{body}"
            if candidate not in self._issued:
                self._issued.add(candidate)
                return candidate

    @property
    def issued_count(self) -> int:
        return len(self._issued)
