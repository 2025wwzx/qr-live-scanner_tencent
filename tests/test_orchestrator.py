import time
from dataclasses import dataclass, field

import numpy as np
import pytest

from qr_live_scanner_tencent.accounts import FakeAccountStore, TencentSession
from qr_live_scanner_tencent.detection import QRDeduplicator
from qr_live_scanner_tencent.interfaces import (
    AccountRef,
    AuthorizationError,
    ConfirmResult,
    FramePacket,
    GameID,
    QRCandidate,
    ROIConfig,
    ScanResult,
)
from qr_live_scanner_tencent.orchestrator import LoginOrchestrator, OrchestratorMetrics
from qr_live_scanner_tencent.pipeline import FramePipeline, PipelineMetrics


@dataclass(slots=True)
class RecordingAuthAdapter:
    scan_calls: list[QRCandidate] = field(default_factory=list)
    confirm_calls: list[ScanResult] = field(default_factory=list)
    confirm_offset_seconds: float = 0.1

    async def scan(self, candidate: QRCandidate, account: AccountRef) -> ScanResult:
        self.scan_calls.append(candidate)
        return ScanResult(candidate=candidate, account=account, scan_token="mock-scan-token")

    async def confirm(self, scan_result: ScanResult) -> ConfirmResult:
        self.confirm_calls.append(scan_result)
        return ConfirmResult(
            scan=scan_result,
            success=True,
            sent_at=scan_result.candidate.source_frame_received_at + self.confirm_offset_seconds,
            message="ok",
        )


class SequenceDecoder:
    def decode(self, frame: FramePacket, roi: ROIConfig) -> QRCandidate | None:
        return QRCandidate(
            payload=f"payload-{frame.sequence}",
            detected_at=frame.received_at + 0.01,
            source_frame_received_at=frame.received_at,
            roi=roi,
            backend="sequence",
        )


class StaticDecoder:
    def decode(self, frame: FramePacket, roi: ROIConfig) -> QRCandidate | None:
        return QRCandidate(
            payload="same-payload",
            detected_at=frame.received_at + 0.01,
            source_frame_received_at=frame.received_at,
            roi=roi,
            backend="static",
        )


def make_frame(*, sequence: int, received_at: float) -> FramePacket:
    return FramePacket(
        data=np.zeros((64, 64, 3), dtype=np.uint8),
        received_at=received_at,
        width=64,
        height=64,
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
async def test_orchestrator_scans_and_confirms_authorized_candidate() -> None:
    account = AccountRef(uid="10001", game_id=GameID.HONOR_OF_KINGS)
    adapter = RecordingAuthAdapter(confirm_offset_seconds=0.2)
    orchestrator = LoginOrchestrator(
        account_store=authorized_store(account),
        auth_adapter=adapter,
        deduplicator=QRDeduplicator(ttl_seconds=10.0),
        metrics=OrchestratorMetrics(),
    )
    candidate = SequenceDecoder().decode(
        make_frame(sequence=1, received_at=10.0), ROIConfig.full_frame()
    )
    assert candidate is not None

    result = await orchestrator.handle_candidate(candidate, account)

    assert result is not None
    assert result.success is True
    assert len(adapter.scan_calls) == 1
    assert len(adapter.confirm_calls) == 1
    assert orchestrator.metrics.confirm_sent == 1
    assert orchestrator.metrics.last_confirm_latency_ms == pytest.approx(200.0)


@pytest.mark.asyncio
async def test_orchestrator_rejects_unauthorized_account_before_external_calls() -> None:
    account = AccountRef(uid="10001", game_id=GameID.HONOR_OF_KINGS)
    store = FakeAccountStore()
    store.save_tencent_session(
        TencentSession(
            uid=account.uid,
            provider=account.provider,
            credentials={"access_token": "SECRET_ACCESS_TOKEN", "openid": "SECRET_OPENID"},
        ),
        authorized=False,
    )
    adapter = RecordingAuthAdapter()
    orchestrator = LoginOrchestrator(
        account_store=store,
        auth_adapter=adapter,
        deduplicator=QRDeduplicator(ttl_seconds=10.0),
        metrics=OrchestratorMetrics(),
    )
    candidate = SequenceDecoder().decode(
        make_frame(sequence=1, received_at=10.0), ROIConfig.full_frame()
    )
    assert candidate is not None

    with pytest.raises(AuthorizationError, match="not authorized"):
        await orchestrator.handle_candidate(candidate, account)

    assert adapter.scan_calls == []
    assert adapter.confirm_calls == []
    assert orchestrator.metrics.authorization_failures == 1


@pytest.mark.asyncio
async def test_orchestrator_requires_tencent_session_before_external_calls() -> None:
    account = AccountRef(uid="10001", game_id=GameID.HONOR_OF_KINGS)
    adapter = RecordingAuthAdapter()
    orchestrator = LoginOrchestrator(
        account_store=FakeAccountStore(),
        auth_adapter=adapter,
        deduplicator=QRDeduplicator(ttl_seconds=10.0),
        metrics=OrchestratorMetrics(),
    )
    candidate = SequenceDecoder().decode(
        make_frame(sequence=1, received_at=10.0), ROIConfig.full_frame()
    )
    assert candidate is not None

    with pytest.raises(AuthorizationError, match="Tencent session"):
        await orchestrator.handle_candidate(candidate, account)

    assert adapter.scan_calls == []
    assert adapter.confirm_calls == []
    assert orchestrator.metrics.authorization_failures == 1


@pytest.mark.asyncio
async def test_pipeline_to_orchestrator_confirms_duplicate_payload_once() -> None:
    account = AccountRef(uid="10001", game_id=GameID.HONOR_OF_KINGS)
    adapter = RecordingAuthAdapter(confirm_offset_seconds=0.1)
    orchestrator = LoginOrchestrator(
        account_store=authorized_store(account),
        auth_adapter=adapter,
        deduplicator=QRDeduplicator(ttl_seconds=10.0),
        metrics=OrchestratorMetrics(),
    )
    pipeline = FramePipeline(
        decoder=StaticDecoder(),
        deduplicator=QRDeduplicator(ttl_seconds=10.0),
        roi=ROIConfig.full_frame(),
        metrics=PipelineMetrics(),
    )
    frames = [make_frame(sequence=index, received_at=float(index)) for index in range(2)]

    for frame in frames:
        candidate = pipeline.process_frame(frame)
        if candidate is not None:
            await orchestrator.handle_candidate(candidate, account)

    assert len(adapter.confirm_calls) == 1
    assert pipeline.metrics.duplicate_candidates == 1
    assert orchestrator.metrics.duplicate_candidates == 0


@pytest.mark.asyncio
async def test_orchestrator_confirm_latency_p95_stays_under_800ms() -> None:
    account = AccountRef(uid="10001", game_id=GameID.HONOR_OF_KINGS)
    adapter = RecordingAuthAdapter(confirm_offset_seconds=0.25)
    orchestrator = LoginOrchestrator(
        account_store=authorized_store(account),
        auth_adapter=adapter,
        deduplicator=QRDeduplicator(ttl_seconds=10.0),
        metrics=OrchestratorMetrics(),
    )
    decoder = SequenceDecoder()

    for index in range(50):
        frame = make_frame(sequence=index, received_at=time.perf_counter())
        candidate = decoder.decode(frame, ROIConfig.full_frame())
        assert candidate is not None
        await orchestrator.handle_candidate(candidate, account)

    assert len(adapter.confirm_calls) == 50
    assert orchestrator.metrics.confirm_latency_p95_ms() < 800
