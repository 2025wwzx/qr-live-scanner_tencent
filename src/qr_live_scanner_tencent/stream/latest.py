from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock

from qr_live_scanner_tencent.interfaces import FramePacket


@dataclass(slots=True)
class LatestFrameBuffer:
    _frame: FramePacket | None = None
    _lock: Lock = field(default_factory=Lock)

    def put(self, frame: FramePacket) -> None:
        with self._lock:
            self._frame = frame

    def get_latest(self) -> FramePacket | None:
        with self._lock:
            return self._frame

    def clear(self) -> None:
        with self._lock:
            self._frame = None
