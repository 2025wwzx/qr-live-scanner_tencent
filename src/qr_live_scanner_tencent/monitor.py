#!/usr/bin/env python3
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from qr_live_scanner_tencent.auth.tencent import TencentGameAuthAdapter, default_game_configs
from qr_live_scanner_tencent.detection import DecoderChain, QRDeduplicator, ZxingDecoder
from qr_live_scanner_tencent.interfaces import (
    AccountRef,
    AccountStore,
    AuthMode,
    AuthorizationError,
    GameID,
    QRDecoder,
    ROIConfig,
    StreamSource,
    TencentLoginProvider,
)
from qr_live_scanner_tencent.orchestrator import LoginOrchestrator
from qr_live_scanner_tencent.pipeline import FramePipeline, PipelineMetrics
from qr_live_scanner_tencent.stream.bilibili import BilibiliStreamSource
from qr_live_scanner_tencent.stream.douyin_browser import (
    DouyinBrowserStreamSource,
    PlaywrightDouyinBrowserResolver,
)

DEFAULT_BROWSER_PROFILE_DIR = "profiles/douyin"
DEFAULT_DEDUP_TTL_SECONDS = 10.0

StopPredicate = Callable[[], bool]
SnapshotCallback = Callable[["DecodeOnlyMonitorSnapshot"], None]


@dataclass(frozen=True, slots=True)
class DecodeOnlyMonitorRequest:
    """保存桌面端监测请求，不包含账号凭证或二维码 payload。"""

    platform: str
    room_id: str
    game_id: GameID
    roi: ROIConfig
    auth_mode: AuthMode = AuthMode.AUTO
    browser_user_data_dir: str = DEFAULT_BROWSER_PROFILE_DIR
    chrome_executable_path: str | None = None
    account_uid: str | None = None
    provider: TencentLoginProvider = TencentLoginProvider.QQ
    auto_confirm: bool = False
    auto_exit: bool = False


@dataclass(frozen=True, slots=True)
class AutoConfirmMonitorRequest:
    """绑定自动确认所需的账号引用和编排器，不持有二维码 payload。"""

    account: AccountRef
    orchestrator: LoginOrchestrator
    stop_after_confirm: bool = False
    close: Callable[[], Awaitable[None]] | None = None


@dataclass(frozen=True, slots=True)
class DecodeOnlyMonitorSnapshot:
    """记录只解码监测的安全指标快照，不保存二维码全文。"""

    state: str
    frames_seen: int
    candidates_seen: int
    duplicate_candidates: int
    last_latency_ms: float | None
    last_backend: str | None
    last_roi: ROIConfig | None
    authorization_failures: int = 0
    scan_sent: int = 0
    confirm_sent: int = 0
    last_confirm_latency_ms: float | None = None


async def run_decode_only_monitor(
    *,
    source: StreamSource,
    decoder: QRDecoder,
    room_id: str,
    roi: ROIConfig,
    auth_mode: AuthMode = AuthMode.AUTO,
    stop_requested: StopPredicate | None = None,
    on_snapshot: SnapshotCallback | None = None,
    enable_roi_fallback: bool = True,
    max_frames: int | None = None,
    auto_confirm: AutoConfirmMonitorRequest | None = None,
) -> DecodeOnlyMonitorSnapshot:
    """运行监测循环；未传入自动确认请求时绝不调用扫码或确认登录适配器。

    输入的直播源和解码器可由 GUI 真实运行时或测试替换。函数只解析直播帧、执行 ROI 解码、
    记录安全指标；返回和回调都不包含二维码 payload、账号 token 或平台 Cookie。
    """

    if max_frames is not None and max_frames <= 0:
        msg = "max frames must be positive"
        raise ValueError(msg)

    try:
        stop = stop_requested or _never_stop
        stream_info = await source.resolve(room_id, auth_mode=auth_mode)
        pipeline = FramePipeline(
            decoder=decoder,
            deduplicator=QRDeduplicator(ttl_seconds=DEFAULT_DEDUP_TTL_SECONDS),
            roi=roi,
            metrics=PipelineMetrics(),
            enable_roi_fallback=enable_roi_fallback,
        )
        last_backend: str | None = None
        last_roi: ROIConfig | None = None
        snapshot = _snapshot("streaming", pipeline.metrics, last_backend, last_roi, auto_confirm)

        async for frame in source.frames(stream_info):
            if stop():
                snapshot = _snapshot(
                    "stopped",
                    pipeline.metrics,
                    last_backend,
                    last_roi,
                    auto_confirm,
                )
                break

            candidate = pipeline.process_frame(frame)
            if candidate is not None:
                last_backend = candidate.backend
                last_roi = candidate.roi
                try:
                    confirm = (
                        await auto_confirm.orchestrator.handle_candidate(
                            candidate,
                            auto_confirm.account,
                        )
                        if auto_confirm is not None
                        else None
                    )
                except AuthorizationError:
                    confirm = None
                if (
                    confirm is not None
                    and confirm.success
                    and auto_confirm is not None
                    and auto_confirm.stop_after_confirm
                ):
                    snapshot = _snapshot(
                        "completed",
                        pipeline.metrics,
                        last_backend,
                        last_roi,
                        auto_confirm,
                    )
                    if on_snapshot is not None:
                        on_snapshot(snapshot)
                    break

            snapshot = _snapshot(
                "streaming",
                pipeline.metrics,
                last_backend,
                last_roi,
                auto_confirm,
            )
            if on_snapshot is not None:
                on_snapshot(snapshot)

            if max_frames is not None and pipeline.metrics.decoded_frames >= max_frames:
                snapshot = _snapshot(
                    "completed",
                    pipeline.metrics,
                    last_backend,
                    last_roi,
                    auto_confirm,
                )
                break
        else:
            snapshot = _snapshot(
                "completed",
                pipeline.metrics,
                last_backend,
                last_roi,
                auto_confirm,
            )

        if stop() and snapshot.state != "stopped":
            snapshot = _snapshot("stopped", pipeline.metrics, last_backend, last_roi, auto_confirm)
        return snapshot
    finally:
        if auto_confirm is not None and auto_confirm.close is not None:
            await auto_confirm.close()


def build_decode_only_source(request: DecodeOnlyMonitorRequest) -> StreamSource:
    """根据 GUI 请求创建真实直播源；抖音默认走本机浏览器拦截 resolver。"""

    platform = request.platform.strip().lower()
    if platform == "bilibili":
        return BilibiliStreamSource()
    if platform == "douyin":
        chrome_path = (
            None if not request.chrome_executable_path else Path(request.chrome_executable_path)
        )
        return DouyinBrowserStreamSource(
            browser_resolver=PlaywrightDouyinBrowserResolver(
                user_data_dir=Path(request.browser_user_data_dir),
                chrome_executable_path=chrome_path,
            )
        )
    msg = "decode-only monitor supports only bilibili and douyin"
    raise ValueError(msg)


def build_decode_only_decoder() -> DecoderChain:
    """创建 GUI 真实监测默认解码器，只使用低延迟 zxing QR fast path。"""

    return DecoderChain(backends=[ZxingDecoder()])


def _snapshot(
    state: str,
    metrics: PipelineMetrics,
    last_backend: str | None,
    last_roi: ROIConfig | None,
    auto_confirm: AutoConfirmMonitorRequest | None,
) -> DecodeOnlyMonitorSnapshot:
    orchestrator_metrics = auto_confirm.orchestrator.metrics if auto_confirm is not None else None
    return DecodeOnlyMonitorSnapshot(
        state=state,
        frames_seen=metrics.decoded_frames,
        candidates_seen=metrics.accepted_candidates,
        duplicate_candidates=metrics.duplicate_candidates,
        last_latency_ms=metrics.last_processing_latency_ms,
        last_backend=last_backend,
        last_roi=last_roi,
        authorization_failures=(
            0 if orchestrator_metrics is None else orchestrator_metrics.authorization_failures
        ),
        scan_sent=0 if orchestrator_metrics is None else orchestrator_metrics.scan_sent,
        confirm_sent=0 if orchestrator_metrics is None else orchestrator_metrics.confirm_sent,
        last_confirm_latency_ms=(
            None if orchestrator_metrics is None else orchestrator_metrics.last_confirm_latency_ms
        ),
    )


def _never_stop() -> bool:
    return False


def build_auto_confirm_request(
    request: DecodeOnlyMonitorRequest,
    account_store: AccountStore,
    *,
    client: httpx.AsyncClient | None = None,
) -> AutoConfirmMonitorRequest | None:
    """根据 GUI 请求创建自动确认编排器；未显式开启或未绑定账号时返回 None。"""

    uid = str(request.account_uid or "").strip()
    if not request.auto_confirm or not uid:
        return None
    created_client = client is None
    http_client = client if client is not None else httpx.AsyncClient(timeout=10.0)
    account = AccountRef(uid=uid, game_id=request.game_id, provider=request.provider)
    adapter = TencentGameAuthAdapter(
        config=default_game_configs()[request.game_id],
        client=http_client,
        account_store=account_store,
    )
    return AutoConfirmMonitorRequest(
        account=account,
        orchestrator=LoginOrchestrator(
            account_store=account_store,
            auth_adapter=adapter,
            deduplicator=QRDeduplicator(ttl_seconds=DEFAULT_DEDUP_TTL_SECONDS),
        ),
        stop_after_confirm=request.auto_exit,
        close=http_client.aclose if created_client else None,
    )
