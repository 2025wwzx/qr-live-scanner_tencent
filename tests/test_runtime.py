import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import numpy as np
import pytest

from qr_live_scanner_tencent.accounts import FakeAccountStore, TencentSession
from qr_live_scanner_tencent.detection import QRDeduplicator
from qr_live_scanner_tencent.interfaces import (
    AccountRef,
    ConfirmResult,
    FramePacket,
    GameID,
    QRCandidate,
    ROIConfig,
    ScanResult,
)
from qr_live_scanner_tencent.orchestrator import LoginOrchestrator
from qr_live_scanner_tencent.pipeline import FramePipeline, PipelineMetrics
from qr_live_scanner_tencent.runtime import RuntimeMetrics, ScannerRuntime


@dataclass(slots=True)
class RecordingAuthAdapter:
    confirms: list[ScanResult] = field(default_factory=list)

    async def scan(self, candidate: QRCandidate, account: AccountRef) -> ScanResult:
        return ScanResult(candidate=candidate, account=account, scan_token="mock-token")

    async def confirm(self, scan_result: ScanResult) -> ConfirmResult:
        self.confirms.append(scan_result)
        return ConfirmResult(
            scan=scan_result,
            success=True,
            sent_at=scan_result.candidate.source_frame_received_at + 0.05,
        )


class PayloadBySequenceDecoder:
    def decode(self, frame: FramePacket, roi: ROIConfig) -> QRCandidate | None:
        payload = "same" if frame.sequence < 2 else "new"
        return QRCandidate(
            payload=payload,
            detected_at=frame.received_at + 0.01,
            source_frame_received_at=frame.received_at,
            roi=roi,
            backend="test",
        )


async def frame_stream(frames: list[FramePacket]) -> AsyncIterator[FramePacket]:
    for frame in frames:
        yield frame


def make_frame(sequence: int) -> FramePacket:
    return FramePacket(
        data=np.zeros((32, 32, 3), dtype=np.uint8),
        received_at=time.perf_counter(),
        width=32,
        height=32,
        sequence=sequence,
    )


def authorized_store(account: AccountRef) -> FakeAccountStore:
    store = FakeAccountStore()
    store.save_tencent_session(
        TencentSession(
            uid=account.uid,
            provider=account.provider,
            credentials={"access_token": "SECRET_ACCESS_TOKEN", "openid": "SECRET_OPENID"},
        ),
        authorized=True,
    )
    return store


@pytest.mark.asyncio
async def test_scanner_runtime_links_stream_pipeline_and_orchestrator() -> None:
    account = AccountRef(uid="10001", game_id=GameID.HONOR_OF_KINGS)
    store = authorized_store(account)
    adapter = RecordingAuthAdapter()
    runtime = ScannerRuntime(
        frames=frame_stream([make_frame(0), make_frame(1), make_frame(2)]),
        pipeline=FramePipeline(
            decoder=PayloadBySequenceDecoder(),
            deduplicator=QRDeduplicator(ttl_seconds=10.0),
            roi=ROIConfig.full_frame(),
            metrics=PipelineMetrics(),
        ),
        orchestrator=LoginOrchestrator(
            account_store=store,
            auth_adapter=adapter,
            deduplicator=QRDeduplicator(ttl_seconds=10.0),
        ),
        account=account,
        metrics=RuntimeMetrics(),
    )

    results = await runtime.run(max_confirms=2)

    assert len(results) == 2
    assert len(adapter.confirms) == 2
    assert runtime.metrics.frames_seen == 3
    assert runtime.metrics.candidates_seen == 2
    assert runtime.metrics.confirms_sent == 2
    assert runtime.metrics.last_loop_latency_ms is not None


@pytest.mark.asyncio
async def test_scanner_runtime_rejects_non_positive_max_confirms_before_frames() -> None:
    account = AccountRef(uid="10001", game_id=GameID.HONOR_OF_KINGS)
    store = authorized_store(account)
    adapter = RecordingAuthAdapter()
    runtime = ScannerRuntime(
        frames=frame_stream([make_frame(0)]),
        pipeline=FramePipeline(
            decoder=PayloadBySequenceDecoder(),
            deduplicator=QRDeduplicator(ttl_seconds=10.0),
            roi=ROIConfig.full_frame(),
            metrics=PipelineMetrics(),
        ),
        orchestrator=LoginOrchestrator(
            account_store=store,
            auth_adapter=adapter,
            deduplicator=QRDeduplicator(ttl_seconds=10.0),
        ),
        account=account,
        metrics=RuntimeMetrics(),
    )

    with pytest.raises(ValueError, match="max confirms"):
        await runtime.run(max_confirms=0)

    assert runtime.metrics.frames_seen == 0
    assert adapter.confirms == []
