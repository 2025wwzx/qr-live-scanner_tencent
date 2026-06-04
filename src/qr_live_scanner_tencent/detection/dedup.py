from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from qr_live_scanner_tencent.interfaces import QRCandidate


@dataclass(slots=True)
class QRDeduplicator:
    ttl_seconds: float = 10.0
    clock: Callable[[], float] = time.monotonic
    _seen: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.ttl_seconds < 0:
            msg = "QR dedup TTL must be non-negative"
            raise ValueError(msg)

    def accept(self, candidate: QRCandidate) -> bool:
        now = self.clock()
        self._purge_expired(now)
        key = self._key(candidate.payload)
        last_seen = self._seen.get(key)
        if last_seen is not None and now - last_seen <= self.ttl_seconds:
            return False
        self._seen[key] = now
        return True

    def _purge_expired(self, now: float) -> None:
        expired = [key for key, seen_at in self._seen.items() if now - seen_at > self.ttl_seconds]
        for key in expired:
            del self._seen[key]

    @staticmethod
    def _key(payload: str) -> str:
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
