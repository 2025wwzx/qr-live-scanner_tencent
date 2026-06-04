from __future__ import annotations

import time
from dataclasses import dataclass

from qr_live_scanner_tencent.detection.roi_fallback import ROIFallbackStrategy
from qr_live_scanner_tencent.interfaces import (
    FramePacket,
    QRCandidate,
    QRDecoder,
    QRDeduplicator,
    ROIConfig,
)


@dataclass(slots=True)
class PipelineMetrics:
    decoded_frames: int = 0
    accepted_candidates: int = 0
    duplicate_candidates: int = 0
    last_processing_latency_ms: float | None = None


@dataclass(slots=True)
class FramePipeline:
    decoder: QRDecoder
    deduplicator: QRDeduplicator
    roi: ROIConfig
    metrics: PipelineMetrics
    enable_roi_fallback: bool = False
    roi_fallback_strategy: ROIFallbackStrategy | None = None

    def process_frame(self, frame: FramePacket) -> QRCandidate | None:
        start = time.perf_counter()
        self.metrics.decoded_frames += 1
        try:
            active_roi = self._active_roi()
            candidate = self.decoder.decode(frame, active_roi)
            if candidate is None:
                self._record_roi_miss()
                return None
            self._record_roi_hit()
            if not self.deduplicator.accept(candidate):
                self.metrics.duplicate_candidates += 1
                return None
            self.metrics.accepted_candidates += 1
            return candidate
        finally:
            self.metrics.last_processing_latency_ms = (time.perf_counter() - start) * 1000

    def _active_roi(self) -> ROIConfig:
        strategy = self._roi_strategy()
        if strategy is None:
            return self.roi
        return strategy.current_roi()

    def _record_roi_miss(self) -> None:
        strategy = self._roi_strategy()
        if strategy is not None:
            strategy.record_miss()

    def _record_roi_hit(self) -> None:
        strategy = self._roi_strategy()
        if strategy is not None:
            strategy.record_hit()

    def _roi_strategy(self) -> ROIFallbackStrategy | None:
        if not self.enable_roi_fallback:
            return None
        if self.roi_fallback_strategy is None:
            self.roi_fallback_strategy = ROIFallbackStrategy()
        return self.roi_fallback_strategy
