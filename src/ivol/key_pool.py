"""Thread-safe iVolatility API key pool.

Provides:
  - Round-robin key rotation with per-key token-bucket rate limiting
  - Adaptive throttle: automatically increases gap between requests when
    repeated HTTP 429 responses are detected
  - Secret redaction (never logs full keys)

Usage:
    from src.ivol.key_pool import load_key_pool

    pool = load_key_pool(per_key_rps=2.0)
    ...
    key = pool.acquire()          # blocks until a key slot is available
    pool.record_success()         # call after each successful response
    pool.record_429()             # call on HTTP 429
"""
from __future__ import annotations

import logging
import os
import threading
import time

log = logging.getLogger("ivol.keypool")


class KeyPool:
    """
    Thread-safe pool of API keys with per-key rate limiting.

    Each key is allowed at most `per_key_rps` requests per second.
    `acquire()` returns the key whose next slot is available soonest,
    blocking if all keys are still within their rate-limit window.

    On repeated HTTP 429 responses, `record_429()` gradually widens the
    per-key gap (reduces throughput) until the pressure subsides.
    """

    def __init__(self, keys: list[str], per_key_rps: float = 2.0) -> None:
        if not keys:
            raise ValueError("KeyPool requires at least one API key.")
        self._keys = list(keys)
        self._n = len(keys)
        self._lock = threading.Lock()
        self._per_key_rps = per_key_rps
        self._floor_gap = 1.0 / per_key_rps        # minimum gap; never go below this
        self._min_gap = self._floor_gap             # current gap, may widen under 429 pressure
        self._consecutive_429s = 0

        # Stagger initial slots across one gap window to avoid a cold-start burst
        # where all workers fire simultaneously and immediately hit 429.
        now = time.monotonic()
        self._next_available: dict[str, float] = {
            k: now + i * (self._floor_gap / max(len(keys), 1))
            for i, k in enumerate(keys)
        }
        log.info(
            "KeyPool ready: %d key(s), per_key_rps=%.1f  [%s]",
            self._n,
            per_key_rps,
            ", ".join(self._redact(k) for k in keys),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def count(self) -> int:
        return self._n

    def acquire(self) -> str:
        """
        Return the next available API key, blocking until a rate-limit
        slot opens.  Thread-safe; multiple workers may call this concurrently.
        """
        while True:
            with self._lock:
                now = time.monotonic()
                # Pick the key whose next slot is earliest.
                best = min(self._keys, key=lambda k: self._next_available[k])
                wait = self._next_available[best] - now
                if wait <= 0:
                    # Claim this slot atomically before releasing the lock.
                    self._next_available[best] = now + self._min_gap
                    return best
            # All keys are still within their window; sleep briefly and retry.
            time.sleep(min(wait, 0.010))  # ≤ 10 ms polling interval

    def record_success(self) -> None:
        """
        Signal a successful response.  Gradually relaxes adaptive throttle
        if it was previously tightened by 429 pressure.

        Mirrors record_429(): every 5 successes steps the gap back down by
        the same factor (÷ 1.5), floored at the originally configured rate.
        """
        with self._lock:
            if self._consecutive_429s > 0:
                self._consecutive_429s = max(0, self._consecutive_429s - 1)
                # Step gap back down in sync with the counter crossing a 5-boundary.
                if self._consecutive_429s % 5 == 0 and self._min_gap > self._floor_gap:
                    old = self._min_gap
                    self._min_gap = max(self._min_gap / 1.5, self._floor_gap)
                    log.info(
                        "429 pressure easing (count=%d) — relaxing rate: "
                        "min_gap %.2fs -> %.2fs  (effective RPS: %.1f/key)",
                        self._consecutive_429s,
                        old,
                        self._min_gap,
                        1.0 / self._min_gap,
                    )

    def record_429(self) -> None:
        """
        Signal an HTTP 429.  Every 5 consecutive 429s widens the per-key
        gap by 50% (up to a ceiling of 30 s), reducing effective throughput.
        """
        with self._lock:
            self._consecutive_429s += 1
            if self._consecutive_429s % 5 == 0:
                old = self._min_gap
                self._min_gap = min(self._min_gap * 1.5, 30.0)
                log.warning(
                    "429 pressure (count=%d) — tightening rate: "
                    "min_gap %.2fs -> %.2fs  (effective RPS: %.1f/key)",
                    self._consecutive_429s,
                    old,
                    self._min_gap,
                    1.0 / self._min_gap,
                )

    # ------------------------------------------------------------------
    # Secret helpers
    # ------------------------------------------------------------------

    def _redact(self, key: str) -> str:
        """Return a redacted key safe for logging."""
        return f"***{key[-4:]}" if len(key) >= 4 else "***?"

    def redact(self, key: str) -> str:
        return self._redact(key)

    def all_redacted(self) -> list[str]:
        return [self._redact(k) for k in self._keys]


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def load_key_pool(per_key_rps: float = 2.0) -> KeyPool:
    """
    Build a KeyPool from environment variables.

    Priority:
      1. IVOL_API_KEYS  — comma-separated list, e.g.  key1,key2,key3,key4,key5
      2. IVOL_API_KEY   — single key (backward compat)

    Both are merged and deduplicated (order-preserving).
    Raises RuntimeError if no keys are found.
    """
    keys: list[str] = []

    raw_multi = os.getenv("IVOL_API_KEYS", "").strip()
    if raw_multi:
        for part in raw_multi.split(","):
            k = part.strip()
            if k:
                keys.append(k)

    single = os.getenv("IVOL_API_KEY", "").strip()
    if single:
        keys.append(single)

    # Deduplicate, preserving order.
    seen: set[str] = set()
    unique: list[str] = []
    for k in keys:
        if k not in seen:
            seen.add(k)
            unique.append(k)

    if not unique:
        raise RuntimeError(
            "No API keys found. Set IVOL_API_KEYS (comma-separated) "
            "or IVOL_API_KEY in .env."
        )

    return KeyPool(unique, per_key_rps=per_key_rps)
