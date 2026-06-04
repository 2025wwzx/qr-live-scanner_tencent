from __future__ import annotations

import statistics
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from math import isfinite
from pathlib import Path

import numpy as np

from qr_live_scanner_tencent.accounts import FakeAccountStore, TencentSession
from qr_live_scanner_tencent.auth.tencent import parse_tencent_game_qr_payload
from qr_live_scanner_tencent.detection import (
    DecoderChain,
    PyzbarDecoder,
    QRDeduplicator,
    ZxingDecoder,
)
from qr_live_scanner_tencent.detection.decoder import DecoderBackend
from qr_live_scanner_tencent.detection.roi_fallback import ROIFallbackStrategy
from qr_live_scanner_tencent.interfaces import (
    DEFAULT_AGGRESSIVE_ROI,
    AccountRef,
    AuthMode,
    AuthorizationError,
    ConfirmResult,
    FramePacket,
    GameID,
    QRCandidate,
    QRDecoder,
    ROIConfig,
    ScanResult,
    StreamSource,
)
from qr_live_scanner_tencent.orchestrator import LoginOrchestrator
from qr_live_scanner_tencent.pipeline import FramePipeline, PipelineMetrics
from qr_live_scanner_tencent.runtime import ScannerRuntime
from qr_live_scanner_tencent.stream.bilibili import BilibiliStreamSource
from qr_live_scanner_tencent.stream.douyin_browser import (
    DouyinBrowserStreamSource,
    PlaywrightDouyinBrowserResolver,
)

MIN_SMOKE_SAMPLES = 50
DEFAULT_TARGET_P95_MS = 1000.0
DEFAULT_DECODE_SMOKE_MAX_WAIT_SECONDS = 60.0


@dataclass(frozen=True, slots=True)
class SmokeTarget:
    """记录一次手动 smoke 验收的公开目标信息。"""

    platform: str
    room_id: str
    game_id: GameID
    uid: str


@dataclass(frozen=True, slots=True)
class SmokeReport:
    """保存手动 smoke 延迟样本的聚合结果，不包含敏感凭证或二维码全文。"""

    target: SmokeTarget
    sample_count: int
    p95_ms: float
    min_ms: float
    max_ms: float
    target_p95_ms: float
    passed: bool

    def to_text(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        return "\n".join(
            [
                "qr-live-scanner-tencent smoke report",
                f"status={status}",
                f"platform={self.target.platform}",
                "room_id=[REDACTED]",
                f"game_id={self.target.game_id.value}",
                "uid=[REDACTED]",
                f"sample_count={self.sample_count}",
                f"P95={self.p95_ms:.2f}ms target={self.target_p95_ms:.2f}ms",
                f"min={self.min_ms:.2f}ms max={self.max_ms:.2f}ms",
            ]
        )


@dataclass(frozen=True, slots=True)
class DecodeProbeResult:
    """保存探索型解码采样结果，不包含二维码全文或账号敏感值。"""

    target: SmokeTarget
    sample_count: int
    first_latency_ms: float
    min_latency_ms: float
    max_latency_ms: float
    qr_game: GameID | None = None

    def to_text(self) -> str:
        return "\n".join(
            [
                "qr-live-scanner-tencent decode probe",
                f"platform={self.target.platform}",
                "room_id=[REDACTED]",
                f"game_id={self.target.game_id.value}",
                "uid=[REDACTED]",
                f"qr_game={self.qr_game.value if self.qr_game is not None else 'unknown'}",
                f"sample_count={self.sample_count}",
                f"first={self.first_latency_ms:.2f}ms",
                f"min={self.min_latency_ms:.2f}ms max={self.max_latency_ms:.2f}ms",
            ]
        )


def build_smoke_report(
    target: SmokeTarget, latencies_ms: list[float], *, target_p95_ms: float = DEFAULT_TARGET_P95_MS
) -> SmokeReport:
    """根据手动采集的延迟样本生成 smoke 报告。

    输入样本只应为 frame-received 到 confirm-sent 的毫秒耗时，不应包含
    token、cookie、二维码 payload 或账号密码。样本少于 50 个时直接拒绝，
    避免误把单次偶然结果当作 Phase 4 验收结论。
    """

    if not isfinite(target_p95_ms) or target_p95_ms <= 0:
        msg = "target P95 must be finite and positive"
        raise ValueError(msg)
    _validate_latencies(latencies_ms)
    p95_ms = statistics.quantiles(latencies_ms, n=20)[18]
    return SmokeReport(
        target=target,
        sample_count=len(latencies_ms),
        p95_ms=p95_ms,
        min_ms=min(latencies_ms),
        max_ms=max(latencies_ms),
        target_p95_ms=target_p95_ms,
        passed=p95_ms < target_p95_ms,
    )


def parse_latency_csv(value: str) -> list[float]:
    """解析逗号分隔的毫秒样本，并拒绝空值或非正数。"""

    samples: list[float] = []
    for raw_part in value.split(","):
        part = raw_part.strip()
        if not part:
            msg = "empty latency sample"
            raise ValueError(msg)
        try:
            sample = float(part)
        except ValueError as exc:
            msg = "invalid latency sample"
            raise ValueError(msg) from exc
        if not isfinite(sample):
            msg = "latency samples must be finite"
            raise ValueError(msg)
        if sample < 0:
            msg = "latency samples must be non-negative"
            raise ValueError(msg)
        samples.append(sample)
    return samples


def _validate_latencies(latencies_ms: list[float]) -> None:
    if len(latencies_ms) < MIN_SMOKE_SAMPLES:
        msg = f"smoke report requires at least {MIN_SMOKE_SAMPLES} latency samples"
        raise ValueError(msg)
    if any(not isfinite(sample) for sample in latencies_ms):
        msg = "latency samples must be finite"
        raise ValueError(msg)
    if any(sample < 0 for sample in latencies_ms):
        msg = "latency samples must be non-negative"
        raise ValueError(msg)


async def run_synthetic_smoke(
    target: SmokeTarget,
    *,
    sample_count: int = MIN_SMOKE_SAMPLES,
    target_p95_ms: float = DEFAULT_TARGET_P95_MS,
) -> SmokeReport:
    """运行不访问外部服务的 synthetic smoke 链路并生成报告。"""

    if sample_count < MIN_SMOKE_SAMPLES:
        msg = f"synthetic smoke requires at least {MIN_SMOKE_SAMPLES} samples"
        raise ValueError(msg)

    account = AccountRef(uid=target.uid, game_id=target.game_id)
    store = FakeAccountStore()
    store.save_tencent_session(
        TencentSession(
            uid=account.uid,
            provider=account.provider,
            credentials={
                "access_token": "FAKE_SYNTHETIC_ACCESS_TOKEN",
                "openid": "FAKE_SYNTHETIC_OPENID",
            },
        ),
        authorized=True,
    )
    adapter = SyntheticAuthAdapter(confirm_offset_seconds=0.05)
    orchestrator = LoginOrchestrator(
        account_store=store,
        auth_adapter=adapter,
        deduplicator=QRDeduplicator(ttl_seconds=0.0),
    )
    runtime = ScannerRuntime(
        frames=_synthetic_frames(sample_count),
        pipeline=FramePipeline(
            decoder=SyntheticDecoder(),
            deduplicator=QRDeduplicator(ttl_seconds=0.0),
            roi=ROIConfig.full_frame(),
            metrics=PipelineMetrics(),
        ),
        orchestrator=orchestrator,
        account=account,
    )

    await runtime.run(max_confirms=sample_count)
    return build_smoke_report(
        target,
        orchestrator.metrics.confirm_latency_samples_ms,
        target_p95_ms=target_p95_ms,
    )


async def run_decode_smoke(
    *,
    source: StreamSource,
    decoder: QRDecoder,
    target: SmokeTarget,
    roi: ROIConfig,
    auth_mode: AuthMode = AuthMode.AUTO,
    sample_count: int = MIN_SMOKE_SAMPLES,
    target_p95_ms: float = DEFAULT_TARGET_P95_MS,
    max_wait_seconds: float = DEFAULT_DECODE_SMOKE_MAX_WAIT_SECONDS,
    enable_roi_fallback: bool = False,
) -> SmokeReport:
    """运行真实帧源的解码采样 smoke，不触发扫码或确认登录。"""

    if sample_count < MIN_SMOKE_SAMPLES:
        msg = f"decode smoke requires at least {MIN_SMOKE_SAMPLES} samples"
        raise ValueError(msg)
    if not isfinite(target_p95_ms) or target_p95_ms <= 0:
        msg = "target P95 must be finite and positive"
        raise ValueError(msg)
    if not isfinite(max_wait_seconds) or max_wait_seconds <= 0:
        msg = "decode smoke max wait seconds must be finite and positive"
        raise ValueError(msg)

    stream_info = await source.resolve(target.room_id, auth_mode=auth_mode)
    deadline = time.monotonic() + max_wait_seconds
    latencies_ms: list[float] = []
    roi_strategy = ROIFallbackStrategy() if enable_roi_fallback else None
    async for frame in source.frames(stream_info):
        if time.monotonic() >= deadline:
            break
        active_roi = roi_strategy.current_roi() if roi_strategy is not None else roi
        candidate = decoder.decode(frame, active_roi)
        if candidate is None:
            if roi_strategy is not None:
                roi_strategy.record_miss()
            continue
        if roi_strategy is not None:
            roi_strategy.record_hit()
        latency_ms = max(
            0.0,
            (candidate.detected_at - candidate.source_frame_received_at) * 1000,
        )
        latencies_ms.append(latency_ms)
        if len(latencies_ms) >= sample_count:
            break

    if len(latencies_ms) < sample_count:
        msg = (
            "decode smoke timed out before collecting "
            f"{sample_count} QR samples; collected {len(latencies_ms)}"
        )
        raise ValueError(msg)

    return build_smoke_report(target, latencies_ms, target_p95_ms=target_p95_ms)


async def run_decode_probe(
    *,
    source: StreamSource,
    decoder: QRDecoder,
    target: SmokeTarget,
    roi: ROIConfig,
    auth_mode: AuthMode = AuthMode.AUTO,
    sample_count: int = 1,
    max_wait_seconds: float = DEFAULT_DECODE_SMOKE_MAX_WAIT_SECONDS,
    enable_roi_fallback: bool = False,
) -> DecodeProbeResult:
    """运行探索型真实帧解码采样，不触发扫码或确认登录。"""

    if sample_count <= 0:
        msg = "decode probe sample count must be positive"
        raise ValueError(msg)
    if not isfinite(max_wait_seconds) or max_wait_seconds <= 0:
        msg = "decode probe max wait seconds must be finite and positive"
        raise ValueError(msg)

    stream_info = await source.resolve(target.room_id, auth_mode=auth_mode)
    deadline = time.monotonic() + max_wait_seconds
    latencies_ms: list[float] = []
    qr_game: GameID | None = None
    roi_strategy = ROIFallbackStrategy() if enable_roi_fallback else None
    async for frame in source.frames(stream_info):
        if time.monotonic() >= deadline:
            break
        active_roi = roi_strategy.current_roi() if roi_strategy is not None else roi
        candidate = decoder.decode(frame, active_roi)
        if candidate is None:
            if roi_strategy is not None:
                roi_strategy.record_miss()
            continue
        if roi_strategy is not None:
            roi_strategy.record_hit()
        latency_ms = max(
            0.0,
            (candidate.detected_at - candidate.source_frame_received_at) * 1000,
        )
        latencies_ms.append(latency_ms)
        if qr_game is None:
            qr_game = _tencent_game_from_qr_payload(candidate.payload)
        if len(latencies_ms) >= sample_count:
            break

    if len(latencies_ms) < sample_count:
        msg = (
            "decode probe timed out before collecting "
            f"{sample_count} QR samples; collected {len(latencies_ms)}"
        )
        raise ValueError(msg)

    return DecodeProbeResult(
        target=target,
        sample_count=len(latencies_ms),
        first_latency_ms=latencies_ms[0],
        min_latency_ms=min(latencies_ms),
        max_latency_ms=max(latencies_ms),
        qr_game=qr_game,
    )


async def run_bilibili_decode_smoke(
    target: SmokeTarget,
    *,
    auth_mode: AuthMode = AuthMode.AUTO,
    roi: ROIConfig | None = None,
    sample_count: int = MIN_SMOKE_SAMPLES,
    target_p95_ms: float = DEFAULT_TARGET_P95_MS,
    max_wait_seconds: float = DEFAULT_DECODE_SMOKE_MAX_WAIT_SECONDS,
    cookie: str | None = None,
    enable_pyzbar_fallback: bool = False,
    enable_roi_fallback: bool = True,
) -> SmokeReport:
    """运行 B站真实直播源的解码采样 smoke，不触发扫码或确认登录。"""

    if target.platform != "bilibili":
        msg = "Bilibili decode smoke requires platform=bilibili"
        raise ValueError(msg)
    return await run_decode_smoke(
        source=BilibiliStreamSource(cookie=cookie),
        decoder=_build_real_smoke_decoder(enable_pyzbar_fallback=enable_pyzbar_fallback),
        target=target,
        roi=roi or DEFAULT_AGGRESSIVE_ROI,
        auth_mode=auth_mode,
        sample_count=sample_count,
        target_p95_ms=target_p95_ms,
        max_wait_seconds=max_wait_seconds,
        enable_roi_fallback=enable_roi_fallback,
    )


async def run_bilibili_decode_probe(
    target: SmokeTarget,
    *,
    auth_mode: AuthMode = AuthMode.AUTO,
    roi: ROIConfig | None = None,
    sample_count: int = 1,
    max_wait_seconds: float = DEFAULT_DECODE_SMOKE_MAX_WAIT_SECONDS,
    cookie: str | None = None,
    enable_pyzbar_fallback: bool = False,
    enable_roi_fallback: bool = True,
) -> DecodeProbeResult:
    """运行 B站真实直播源的探索型解码采样，不触发扫码或确认登录。"""

    if target.platform != "bilibili":
        msg = "Bilibili decode probe requires platform=bilibili"
        raise ValueError(msg)
    return await run_decode_probe(
        source=BilibiliStreamSource(cookie=cookie),
        decoder=_build_real_smoke_decoder(enable_pyzbar_fallback=enable_pyzbar_fallback),
        target=target,
        roi=roi or DEFAULT_AGGRESSIVE_ROI,
        auth_mode=auth_mode,
        sample_count=sample_count,
        max_wait_seconds=max_wait_seconds,
        enable_roi_fallback=enable_roi_fallback,
    )


async def run_douyin_browser_decode_smoke(
    target: SmokeTarget,
    *,
    auth_mode: AuthMode = AuthMode.AUTO,
    roi: ROIConfig | None = None,
    sample_count: int = MIN_SMOKE_SAMPLES,
    target_p95_ms: float = DEFAULT_TARGET_P95_MS,
    max_wait_seconds: float = DEFAULT_DECODE_SMOKE_MAX_WAIT_SECONDS,
    user_data_dir: str,
    chrome_executable_path: str | None = None,
    enable_pyzbar_fallback: bool = False,
    enable_roi_fallback: bool = True,
) -> SmokeReport:
    """通过本机浏览器拦截抖音真实直播源后运行解码 smoke，不触发扫码或确认登录。"""

    if target.platform != "douyin":
        msg = "Douyin browser decode smoke requires platform=douyin"
        raise ValueError(msg)
    return await run_decode_smoke(
        source=_build_douyin_browser_source(
            user_data_dir=user_data_dir,
            chrome_executable_path=chrome_executable_path,
        ),
        decoder=_build_real_smoke_decoder(enable_pyzbar_fallback=enable_pyzbar_fallback),
        target=target,
        roi=roi or DEFAULT_AGGRESSIVE_ROI,
        auth_mode=auth_mode,
        sample_count=sample_count,
        target_p95_ms=target_p95_ms,
        max_wait_seconds=max_wait_seconds,
        enable_roi_fallback=enable_roi_fallback,
    )


async def run_douyin_browser_decode_probe(
    target: SmokeTarget,
    *,
    auth_mode: AuthMode = AuthMode.AUTO,
    roi: ROIConfig | None = None,
    sample_count: int = 1,
    max_wait_seconds: float = DEFAULT_DECODE_SMOKE_MAX_WAIT_SECONDS,
    user_data_dir: str,
    chrome_executable_path: str | None = None,
    enable_pyzbar_fallback: bool = False,
    enable_roi_fallback: bool = True,
) -> DecodeProbeResult:
    """通过本机浏览器拦截抖音真实直播源后运行探索型解码采样。"""

    if target.platform != "douyin":
        msg = "Douyin browser decode probe requires platform=douyin"
        raise ValueError(msg)
    return await run_decode_probe(
        source=_build_douyin_browser_source(
            user_data_dir=user_data_dir,
            chrome_executable_path=chrome_executable_path,
        ),
        decoder=_build_real_smoke_decoder(enable_pyzbar_fallback=enable_pyzbar_fallback),
        target=target,
        roi=roi or DEFAULT_AGGRESSIVE_ROI,
        auth_mode=auth_mode,
        sample_count=sample_count,
        max_wait_seconds=max_wait_seconds,
        enable_roi_fallback=enable_roi_fallback,
    )


def _build_douyin_browser_source(
    *, user_data_dir: str, chrome_executable_path: str | None
) -> DouyinBrowserStreamSource:
    chrome_path = None if chrome_executable_path is None else Path(chrome_executable_path)
    return DouyinBrowserStreamSource(
        browser_resolver=PlaywrightDouyinBrowserResolver(
            user_data_dir=Path(user_data_dir),
            chrome_executable_path=chrome_path,
        )
    )


def _build_real_smoke_decoder(*, enable_pyzbar_fallback: bool) -> DecoderChain:
    backends: list[DecoderBackend] = [ZxingDecoder()]
    if enable_pyzbar_fallback:
        backends.append(PyzbarDecoder())
    return DecoderChain(backends=backends)


def _tencent_game_from_qr_payload(payload: str) -> GameID | None:
    """从腾讯游戏二维码中提取安全的游戏归属，不返回 ticket 或原始 URL。"""

    try:
        parse_tencent_game_qr_payload(payload)
    except AuthorizationError:
        return None
    return GameID.HONOR_OF_KINGS


@dataclass(slots=True)
class SyntheticAuthAdapter:
    confirm_offset_seconds: float

    async def scan(self, candidate: QRCandidate, account: AccountRef) -> ScanResult:
        return ScanResult(candidate=candidate, account=account, scan_token="FAKE_SYNTHETIC_SCAN")

    async def confirm(self, scan_result: ScanResult) -> ConfirmResult:
        return ConfirmResult(
            scan=scan_result,
            success=True,
            sent_at=scan_result.candidate.source_frame_received_at + self.confirm_offset_seconds,
        )


class SyntheticDecoder:
    def decode(self, frame: FramePacket, roi: ROIConfig) -> QRCandidate | None:
        return QRCandidate(
            payload=f"synthetic-{frame.sequence}",
            detected_at=frame.received_at + 0.001,
            source_frame_received_at=frame.received_at,
            roi=roi,
            backend="synthetic",
        )


async def _synthetic_frames(sample_count: int) -> AsyncIterator[FramePacket]:
    for sequence in range(sample_count):
        yield FramePacket(
            data=np.zeros((32, 32, 3), dtype=np.uint8),
            received_at=time.perf_counter(),
            width=32,
            height=32,
            sequence=sequence,
        )
