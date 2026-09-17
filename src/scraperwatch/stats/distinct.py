"""Counting distinct item keys in bounded memory."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from typing import Any

EXACT_LIMIT = 100_000
HLL_PRECISION = 14


def key_hash(values: Sequence[Any]) -> int:
    """Stable 64-bit hash of an item's key values."""
    encoded = json.dumps(list(values), sort_keys=True, default=str, ensure_ascii=False)
    return int.from_bytes(hashlib.blake2b(encoded.encode(), digest_size=8).digest(), "big")


class HyperLogLog:
    """Standard HyperLogLog over 64-bit hashes; about 0.8% error at the default precision."""

    def __init__(self, precision: int = HLL_PRECISION) -> None:
        self._p = precision
        self._m = 1 << precision
        self._registers = bytearray(self._m)
        self._rest_bits = 64 - precision

    def add(self, h: int) -> None:
        index = h >> self._rest_bits
        rest = h & ((1 << self._rest_bits) - 1)
        rank = self._rest_bits - rest.bit_length() + 1
        if rank > self._registers[index]:
            self._registers[index] = rank

    def estimate(self) -> float:
        m = self._m
        alpha = 0.7213 / (1 + 1.079 / m)
        raw = alpha * m * m / sum(2.0**-r for r in self._registers)
        zeros = self._registers.count(0)
        if raw <= 2.5 * m and zeros:
            return m * math.log(m / zeros)
        return raw


class DistinctCounter:
    """Exact until EXACT_LIMIT distinct keys, then switches to HyperLogLog."""

    def __init__(self, exact_limit: int = EXACT_LIMIT) -> None:
        self._exact_limit = exact_limit
        self._seen: set[int] | None = set()
        self._hll = HyperLogLog()
        self.total = 0

    @property
    def exact(self) -> bool:
        return self._seen is not None

    def add(self, h: int) -> bool | None:
        """Counts one key. Returns whether it was a repeat, or None once counting is approximate."""
        self.total += 1
        self._hll.add(h)
        if self._seen is None:
            return None
        if h in self._seen:
            return True
        self._seen.add(h)
        if len(self._seen) > self._exact_limit:
            self._seen = None
        return False

    def distinct(self) -> int:
        if self._seen is not None:
            return len(self._seen)
        return min(self.total, round(self._hll.estimate()))
