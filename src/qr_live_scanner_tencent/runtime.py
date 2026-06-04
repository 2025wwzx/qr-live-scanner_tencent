from __future__ import annotations

import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from qr_live_scanner_tencent.interfaces import AccountRef, ConfirmResult, FramePacket
from qr_live_scanner_tencent.orchestrator import LoginOrchestrator
from qr_live_scanner_tencent.pipeline import FramePipeline


@dataclass(slots=True)
class RuntimeMetrics:
    """记录端到端运行循环的轻量指标。"""

    frames_seen: int = 0
    candidates_seen: int = 0
    confirms_sent: int = 0
    last_loop_latency_ms: float | None = None


@dataclass(slots=True)
class ScannerRuntime:
    """串联异步帧流、二维码处理管线与登录确认编排器。"""

    frames: AsyncIterator[FramePacket]
    pipeline: FramePipeline
    orchestrator: LoginOrchestrator
    account: AccountRef
    metrics: RuntimeMetrics = field(default_factory=RuntimeMetrics)

    async def run(self, *, max_confirms: int | None = None) -> list[ConfirmResult]:
        if max_confirms is not None and max_confirms <= 0:
            msg = "max confirms must be positive"
            raise ValueError(msg)
        results: list[ConfirmResult] = []
        async for frame in self.frames:
            loop_start = time.perf_counter()
            self.metrics.frames_seen += 1
            try:
                candidate = self.pipeline.process_frame(frame)
                if candidate is None:
                    continue
                self.metrics.candidates_seen += 1
                confirm = await self.orchestrator.handle_candidate(candidate, self.account)
                if confirm is None:
                    continue
                results.append(confirm)
                self.metrics.confirms_sent += 1
                if max_confirms is not None and self.metrics.confirms_sent >= max_confirms:
                    break
            finally:
                self.metrics.last_loop_latency_ms = (time.perf_counter() - loop_start) * 1000
        return results
