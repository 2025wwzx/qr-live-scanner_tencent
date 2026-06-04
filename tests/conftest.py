from collections.abc import AsyncIterator

import numpy as np
import pytest

from qr_live_scanner_tencent.interfaces import (
    AccountRef,
    ConfirmResult,
    FramePacket,
    GameAuthAdapter,
    QRCandidate,
    ScanResult,
)


class MockGameAuthAdapter(GameAuthAdapter):
    def __init__(self) -> None:
        self.scans: list[QRCandidate] = []
        self.confirms: list[ScanResult] = []

    async def scan(self, candidate: QRCandidate, account: AccountRef) -> ScanResult:
        self.scans.append(candidate)
        return ScanResult(candidate=candidate, account=account, scan_token="mock-scan-token")

    async def confirm(self, scan_result: ScanResult) -> ConfirmResult:
        self.confirms.append(scan_result)
        return ConfirmResult(
            scan=scan_result,
            success=True,
            sent_at=scan_result.candidate.detected_at,
        )


async def frame_stream(frames: list[FramePacket]) -> AsyncIterator[FramePacket]:
    for frame in frames:
        yield frame


@pytest.fixture
def blank_frame() -> FramePacket:
    return FramePacket(
        data=np.zeros((720, 1280, 3), dtype=np.uint8),
        received_at=1.0,
        width=1280,
        height=720,
    )
