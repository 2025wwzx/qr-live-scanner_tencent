import time

import numpy as np

from qr_live_scanner_tencent.detection import QRDeduplicator
from qr_live_scanner_tencent.interfaces import FramePacket, QRCandidate, ROIConfig
from qr_live_scanner_tencent.pipeline import FramePipeline, PipelineMetrics


class StaticDecoder:
    def __init__(self) -> None:
        self.calls = 0

    def decode(self, frame: FramePacket, roi: ROIConfig) -> QRCandidate | None:
        self.calls += 1
        return QRCandidate(
            payload="same-payload",
            detected_at=time.perf_counter(),
            source_frame_received_at=frame.received_at,
            roi=roi,
            backend="static",
        )


class ROIThresholdDecoder:
    def __init__(self, target_roi: ROIConfig) -> None:
        self.target_roi = target_roi
        self.seen_rois: list[ROIConfig] = []

    def decode(self, frame: FramePacket, roi: ROIConfig) -> QRCandidate | None:
        self.seen_rois.append(roi)
        if roi != self.target_roi:
            return None
        return QRCandidate(
            payload=f"payload-{frame.width}-{len(self.seen_rois)}",
            detected_at=time.perf_counter(),
            source_frame_received_at=frame.received_at,
            roi=roi,
            backend="threshold",
        )


def make_frame() -> FramePacket:
    return FramePacket(
        data=np.zeros((32, 32, 3), dtype=np.uint8),
        received_at=time.perf_counter(),
        width=32,
        height=32,
    )


def test_frame_pipeline_decodes_and_deduplicates_candidates() -> None:
    decoder = StaticDecoder()
    metrics = PipelineMetrics()
    pipeline = FramePipeline(
        decoder=decoder,
        deduplicator=QRDeduplicator(ttl_seconds=10.0),
        roi=ROIConfig.full_frame(),
        metrics=metrics,
    )
    frame = make_frame()

    first = pipeline.process_frame(frame)
    duplicate = pipeline.process_frame(frame)

    assert first is not None
    assert duplicate is None
    assert decoder.calls == 2
    assert metrics.decoded_frames == 2
    assert metrics.accepted_candidates == 1
    assert metrics.duplicate_candidates == 1
    assert metrics.last_processing_latency_ms is not None


def test_frame_pipeline_can_expand_roi_after_consecutive_misses() -> None:
    primary_roi = ROIConfig(x=0.375, y=0.375, width=0.25, height=0.25)
    fallback_roi = ROIConfig(x=0.35, y=0.35, width=0.30, height=0.30)
    decoder = ROIThresholdDecoder(target_roi=fallback_roi)
    pipeline = FramePipeline(
        decoder=decoder,
        deduplicator=QRDeduplicator(ttl_seconds=10.0),
        roi=primary_roi,
        metrics=PipelineMetrics(),
        enable_roi_fallback=True,
    )

    misses = [pipeline.process_frame(make_frame()) for _ in range(3)]
    hit = pipeline.process_frame(make_frame())
    reset_miss = pipeline.process_frame(make_frame())

    assert misses == [None, None, None]
    assert hit is not None
    assert hit.roi == fallback_roi
    assert reset_miss is None
    assert decoder.seen_rois == [
        primary_roi,
        primary_roi,
        primary_roi,
        fallback_roi,
        primary_roi,
    ]
