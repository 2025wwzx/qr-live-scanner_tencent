import time
from collections.abc import AsyncIterator

import numpy as np
import pytest

from qr_live_scanner_tencent.interfaces import (
    AuthMode,
    FramePacket,
    GameID,
    QRCandidate,
    ROIConfig,
    StreamFormat,
    StreamInfo,
)
from qr_live_scanner_tencent.smoke import (
    DecodeProbeResult,
    SmokeTarget,
    _build_real_smoke_decoder,
    run_decode_probe,
    run_decode_smoke,
)


class MockStreamSource:
    def __init__(self, frames: list[FramePacket]) -> None:
        self.frames_requested = 0
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
        self.frames_requested += 1
        for frame in self._frames:
            yield frame


class SequenceDecoder:
    def __init__(self, payload_prefix: str = "candidate") -> None:
        self.payload_prefix = payload_prefix
        self.seen_rois: list[ROIConfig] = []

    def decode(self, frame: FramePacket, roi: ROIConfig) -> QRCandidate | None:
        self.seen_rois.append(roi)
        return QRCandidate(
            payload=f"{self.payload_prefix}-{frame.sequence}",
            detected_at=frame.received_at + 0.02,
            source_frame_received_at=frame.received_at,
            roi=roi,
            backend="mock",
        )


class EmptyDecoder:
    def decode(self, frame: FramePacket, roi: ROIConfig) -> QRCandidate | None:
        return None


class ROIThresholdDecoder:
    def __init__(self, target_roi: ROIConfig) -> None:
        self.target_roi = target_roi
        self.seen_rois: list[ROIConfig] = []

    def decode(self, frame: FramePacket, roi: ROIConfig) -> QRCandidate | None:
        self.seen_rois.append(roi)
        if roi != self.target_roi:
            return None
        return QRCandidate(
            payload=f"candidate-{frame.sequence}",
            detected_at=frame.received_at + 0.02,
            source_frame_received_at=frame.received_at,
            roi=roi,
            backend="mock",
        )


def make_frame(sequence: int) -> FramePacket:
    return FramePacket(
        data=np.zeros((32, 32, 3), dtype=np.uint8),
        received_at=time.perf_counter(),
        width=32,
        height=32,
        sequence=sequence,
    )


def test_real_smoke_decoder_uses_zxing_only_by_default() -> None:
    decoder = _build_real_smoke_decoder(enable_pyzbar_fallback=False)

    assert [backend.name for backend in decoder.backends] == ["zxing-cpp"]


def test_real_smoke_decoder_can_enable_pyzbar_fallback() -> None:
    decoder = _build_real_smoke_decoder(enable_pyzbar_fallback=True)

    assert [backend.name for backend in decoder.backends] == ["zxing-cpp", "pyzbar"]


@pytest.mark.asyncio
async def test_run_decode_smoke_samples_stream_without_confirming_login() -> None:
    target = SmokeTarget(
        platform="mock", room_id="room-1", game_id=GameID.HONOR_OF_KINGS, uid="10001"
    )
    source = MockStreamSource([make_frame(index) for index in range(50)])
    decoder = SequenceDecoder()
    roi = ROIConfig(x=0.1, y=0.2, width=0.3, height=0.4)

    report = await run_decode_smoke(
        source=source,
        decoder=decoder,
        target=target,
        roi=roi,
        auth_mode=AuthMode.COOKIE,
        sample_count=50,
        target_p95_ms=800.0,
    )

    assert source.resolved_room_ids == ["room-1"]
    assert source.resolved_auth_modes == [AuthMode.COOKIE]
    assert source.frames_requested == 1
    assert decoder.seen_rois == [roi] * 50
    assert report.sample_count == 50
    assert report.passed is True
    assert report.p95_ms < 800.0


@pytest.mark.asyncio
async def test_run_decode_probe_accepts_single_sample_without_smoke_report() -> None:
    target = SmokeTarget(
        platform="mock", room_id="room-1", game_id=GameID.HONOR_OF_KINGS, uid="10001"
    )
    source = MockStreamSource([make_frame(1)])

    result = await run_decode_probe(
        source=source,
        decoder=SequenceDecoder(),
        target=target,
        roi=ROIConfig.full_frame(),
        sample_count=1,
        max_wait_seconds=1.0,
    )

    assert isinstance(result, DecodeProbeResult)
    assert result.sample_count == 1
    assert result.first_latency_ms >= 0
    assert result.max_latency_ms >= result.min_latency_ms
    assert "payload" not in result.to_text().lower()
    assert "candidate-1" not in result.to_text()


@pytest.mark.asyncio
async def test_run_decode_probe_reports_safe_tencent_qr_game_summary() -> None:
    target = SmokeTarget(
        platform="mock", room_id="room-1", game_id=GameID.HONOR_OF_KINGS, uid="10001"
    )
    source = MockStreamSource([make_frame(1)])
    ticket = "SECRET_TICKET"
    payload = (
        "https://ssl.ptlogin2.qq.com/ptqrlogin"
        f"?ptqrtoken={ticket}&appid=honor_of_kings"
    )

    result = await run_decode_probe(
        source=source,
        decoder=SequenceDecoder(payload_prefix=payload),
        target=target,
        roi=ROIConfig.full_frame(),
        sample_count=1,
        max_wait_seconds=1.0,
    )

    text = result.to_text()
    assert result.qr_game == GameID.HONOR_OF_KINGS
    assert "qr_game=honor_of_kings" in text
    assert ticket not in text
    assert "qr_code_in_game" not in text
    assert "payload" not in text.lower()


@pytest.mark.asyncio
async def test_run_decode_probe_timeout_omits_payload() -> None:
    target = SmokeTarget(
        platform="mock", room_id="room-1", game_id=GameID.HONOR_OF_KINGS, uid="10001"
    )
    source = MockStreamSource([make_frame(index) for index in range(2)])

    with pytest.raises(ValueError, match="collected 0") as exc_info:
        await run_decode_probe(
            source=source,
            decoder=EmptyDecoder(),
            target=target,
            roi=ROIConfig.full_frame(),
            sample_count=1,
            max_wait_seconds=0.001,
        )

    assert "payload" not in str(exc_info.value).lower()
    assert "candidate" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_run_decode_probe_uses_roi_fallback_after_consecutive_misses() -> None:
    target = SmokeTarget(
        platform="mock", room_id="room-1", game_id=GameID.HONOR_OF_KINGS, uid="10001"
    )
    fallback_roi = ROIConfig(x=0.35, y=0.35, width=0.30, height=0.30)
    decoder = ROIThresholdDecoder(target_roi=fallback_roi)
    source = MockStreamSource([make_frame(index) for index in range(4)])

    result = await run_decode_probe(
        source=source,
        decoder=decoder,
        target=target,
        roi=ROIConfig(x=0.375, y=0.375, width=0.25, height=0.25),
        sample_count=1,
        max_wait_seconds=1.0,
        enable_roi_fallback=True,
    )

    assert result.sample_count == 1
    assert decoder.seen_rois == [
        ROIConfig(x=0.375, y=0.375, width=0.25, height=0.25),
        ROIConfig(x=0.375, y=0.375, width=0.25, height=0.25),
        ROIConfig(x=0.375, y=0.375, width=0.25, height=0.25),
        fallback_roi,
    ]


@pytest.mark.asyncio
async def test_run_decode_smoke_reports_timeout_when_samples_are_not_collected() -> None:
    target = SmokeTarget(
        platform="mock", room_id="room-1", game_id=GameID.HONOR_OF_KINGS, uid="10001"
    )
    source = MockStreamSource([make_frame(index) for index in range(50)])

    with pytest.raises(ValueError, match="timed out"):
        await run_decode_smoke(
            source=source,
            decoder=EmptyDecoder(),
            target=target,
            roi=ROIConfig.full_frame(),
            sample_count=50,
            max_wait_seconds=0.001,
        )


@pytest.mark.asyncio
async def test_run_decode_smoke_rejects_zero_wait_before_resolving_source() -> None:
    target = SmokeTarget(
        platform="mock", room_id="room-1", game_id=GameID.HONOR_OF_KINGS, uid="10001"
    )
    source = MockStreamSource([make_frame(index) for index in range(50)])

    with pytest.raises(ValueError, match="positive"):
        await run_decode_smoke(
            source=source,
            decoder=SequenceDecoder(),
            target=target,
            roi=ROIConfig.full_frame(),
            sample_count=50,
            max_wait_seconds=0.0,
        )

    assert source.resolved_room_ids == []
    assert source.frames_requested == 0


@pytest.mark.asyncio
async def test_run_decode_smoke_rejects_non_finite_target_before_resolving_source() -> None:
    target = SmokeTarget(
        platform="mock", room_id="room-1", game_id=GameID.HONOR_OF_KINGS, uid="10001"
    )
    source = MockStreamSource([make_frame(index) for index in range(50)])

    with pytest.raises(ValueError, match="target P95"):
        await run_decode_smoke(
            source=source,
            decoder=SequenceDecoder(),
            target=target,
            roi=ROIConfig.full_frame(),
            sample_count=50,
            target_p95_ms=float("inf"),
        )

    assert source.resolved_room_ids == []
    assert source.frames_requested == 0
