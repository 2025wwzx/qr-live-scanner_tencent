import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import numpy as np
import pytest

from qr_live_scanner_tencent.accounts import FakeAccountStore, TencentSession
from qr_live_scanner_tencent.detection import QRDeduplicator
from qr_live_scanner_tencent.interfaces import (
    AccountRef,
    AuthMode,
    ConfirmResult,
    FramePacket,
    GameID,
    QRCandidate,
    ROIConfig,
    ScanResult,
    StreamFormat,
    StreamInfo,
)
from qr_live_scanner_tencent.monitor import (
    AutoConfirmMonitorRequest,
    DecodeOnlyMonitorSnapshot,
    run_decode_only_monitor,
)
from qr_live_scanner_tencent.orchestrator import LoginOrchestrator


class MockStreamSource:
    def __init__(self, frames: list[FramePacket]) -> None:
        self.resolved_room_ids: list[str] = []
        self.resolved_auth_modes: list[AuthMode] = []
        self._frames = frames

    async def resolve(self, room_id: str, auth_mode: AuthMode = AuthMode.AUTO) -> StreamInfo:
        self.resolved_room_ids.append(room_id)
        self.resolved_auth_modes.append(auth_mode)
        return StreamInfo(
            platform="mock",
            room_id=room_id,
            url="mock://stream",
            format=StreamFormat.UNKNOWN,
            auth_mode=auth_mode,
            ttl_seconds=60,
            requires_cookie=False,
        )

    async def frames(self, stream_info: StreamInfo) -> AsyncIterator[FramePacket]:
        for frame in self._frames:
            yield frame


class PayloadBySequenceDecoder:
    def decode(self, frame: FramePacket, roi: ROIConfig) -> QRCandidate | None:
        payload = "same" if frame.sequence < 2 else "new"
        return QRCandidate(
            payload=payload,
            detected_at=frame.received_at + 0.01,
            source_frame_received_at=frame.received_at,
            roi=roi,
            backend="mock",
        )


@dataclass(slots=True)
class RecordingAuthAdapter:
    scan_calls: list[QRCandidate] = field(default_factory=list)
    confirm_calls: list[ScanResult] = field(default_factory=list)

    async def scan(self, candidate: QRCandidate, account: AccountRef) -> ScanResult:
        self.scan_calls.append(candidate)
        return ScanResult(candidate=candidate, account=account, scan_token="mock-scan-token")

    async def confirm(self, scan_result: ScanResult) -> ConfirmResult:
        self.confirm_calls.append(scan_result)
        return ConfirmResult(
            scan=scan_result,
            success=True,
            sent_at=scan_result.candidate.source_frame_received_at + 0.1,
            message="ok",
        )


def make_frame(sequence: int) -> FramePacket:
    return FramePacket(
        data=np.zeros((32, 32, 3), dtype=np.uint8),
        received_at=time.perf_counter(),
        width=32,
        height=32,
        sequence=sequence,
    )


@pytest.mark.asyncio
async def test_decode_only_monitor_samples_frames_without_confirming_login() -> None:
    source = MockStreamSource([make_frame(index) for index in range(3)])
    snapshots: list[DecodeOnlyMonitorSnapshot] = []

    summary = await run_decode_only_monitor(
        source=source,
        decoder=PayloadBySequenceDecoder(),
        room_id="room-1",
        roi=ROIConfig.full_frame(),
        auth_mode=AuthMode.AUTO,
        on_snapshot=snapshots.append,
    )

    assert source.resolved_room_ids == ["room-1"]
    assert source.resolved_auth_modes == [AuthMode.AUTO]
    assert summary.frames_seen == 3
    assert summary.candidates_seen == 2
    assert summary.duplicate_candidates == 1
    assert summary.last_backend == "mock"
    assert summary.last_candidate_summary == "二维码候选（内容已隐藏）"
    assert "same" not in str(summary.last_candidate_summary)
    assert "new" not in str(summary.last_candidate_summary)
    assert not hasattr(summary, "payload")
    assert snapshots[-1].candidates_seen == 2
    assert snapshots[-1].last_candidate_summary == "二维码候选（内容已隐藏）"
    assert not hasattr(snapshots[-1], "payload")


@pytest.mark.asyncio
async def test_decode_only_monitor_can_stop_after_latest_frame() -> None:
    source = MockStreamSource([make_frame(index) for index in range(3)])
    seen = 0

    def stop_requested() -> bool:
        return seen >= 1

    def record_snapshot(_snapshot: object) -> None:
        nonlocal seen
        seen += 1

    summary = await run_decode_only_monitor(
        source=source,
        decoder=PayloadBySequenceDecoder(),
        room_id="room-1",
        roi=ROIConfig.full_frame(),
        stop_requested=stop_requested,
        on_snapshot=record_snapshot,
    )

    assert summary.frames_seen == 1
    assert summary.state == "stopped"


@pytest.mark.asyncio
async def test_auto_confirm_monitor_binds_selected_account_to_confirm_chain() -> None:
    account = AccountRef(uid="10001", game_id=GameID.HONOR_OF_KINGS)
    store = FakeAccountStore()
    store.save_tencent_session(
        TencentSession(
            uid=account.uid,
            provider=account.provider,
            credentials={"access_token": "SECRET_ACCESS_TOKEN"},
        ),
        authorized=True,
    )
    adapter = RecordingAuthAdapter()
    source = MockStreamSource([make_frame(0), make_frame(1)])
    snapshots: list[DecodeOnlyMonitorSnapshot] = []

    summary = await run_decode_only_monitor(
        source=source,
        decoder=PayloadBySequenceDecoder(),
        room_id="room-1",
        roi=ROIConfig.full_frame(),
        on_snapshot=snapshots.append,
        auto_confirm=AutoConfirmMonitorRequest(
            account=account,
            orchestrator=LoginOrchestrator(
                account_store=store,
                auth_adapter=adapter,
                deduplicator=QRDeduplicator(ttl_seconds=10.0),
            ),
        ),
    )

    assert summary.candidates_seen == 1
    assert summary.scan_sent == 1
    assert summary.confirm_sent == 1
    assert summary.authorization_failures == 0
    assert adapter.confirm_calls[0].account == account
    assert snapshots[-1].scan_sent == 1
    assert not hasattr(summary, "payload")


@pytest.mark.asyncio
async def test_auto_confirm_monitor_reports_authorization_failure_without_confirming() -> None:
    account = AccountRef(uid="10001", game_id=GameID.HONOR_OF_KINGS)
    adapter = RecordingAuthAdapter()

    summary = await run_decode_only_monitor(
        source=MockStreamSource([make_frame(0)]),
        decoder=PayloadBySequenceDecoder(),
        room_id="room-1",
        roi=ROIConfig.full_frame(),
        auto_confirm=AutoConfirmMonitorRequest(
            account=account,
            orchestrator=LoginOrchestrator(
                account_store=FakeAccountStore(),
                auth_adapter=adapter,
                deduplicator=QRDeduplicator(ttl_seconds=10.0),
            ),
        ),
    )

    assert summary.candidates_seen == 1
    assert summary.authorization_failures == 1
    assert summary.scan_sent == 0
    assert summary.confirm_sent == 0
    assert adapter.scan_calls == []
