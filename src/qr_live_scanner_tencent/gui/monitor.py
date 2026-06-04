#!/usr/bin/env python3
from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from threading import Event
from typing import Protocol

from PySide6.QtCore import QObject, QThread, Signal, Slot

from qr_live_scanner_tencent.accounts import KeyringAccountStore
from qr_live_scanner_tencent.interfaces import AccountStore
from qr_live_scanner_tencent.monitor import (
    DecodeOnlyMonitorRequest,
    DecodeOnlyMonitorSnapshot,
    build_auto_confirm_request,
    build_decode_only_decoder,
    build_decode_only_source,
    run_decode_only_monitor,
)

__all__ = [
    "DecodeOnlyMonitorCallbacks",
    "DecodeOnlyMonitorController",
    "DecodeOnlyMonitorRequest",
    "DecodeOnlyMonitorSnapshot",
    "QtDecodeOnlyMonitorController",
]


@dataclass(frozen=True, slots=True)
class DecodeOnlyMonitorCallbacks:
    """封装 GUI 更新回调，便于真实 Qt 线程和测试 fake controller 共用。"""

    on_status: Callable[[str], None]
    on_snapshot: Callable[[DecodeOnlyMonitorSnapshot], None]
    on_error: Callable[[str], None]
    on_finished: Callable[[], None]


class DecodeOnlyMonitorController(Protocol):
    def start(
        self,
        request: DecodeOnlyMonitorRequest,
        callbacks: DecodeOnlyMonitorCallbacks,
    ) -> None:
        """启动只解码监测。"""

    def stop(self) -> None:
        """请求停止监测。"""

    def is_running(self) -> bool:
        """返回监测线程是否仍在运行。"""


class DecodeOnlyMonitorWorker(QObject):
    """在后台 Qt 线程中运行真实异步解码监测。"""

    status_changed = Signal(str)
    snapshot_changed = Signal(object)
    error_changed = Signal(str)
    finished = Signal()

    def __init__(self, request: DecodeOnlyMonitorRequest, account_store: AccountStore) -> None:
        super().__init__()
        self.request = request
        self.account_store = account_store
        self._stop_event = Event()

    @Slot()
    def run(self) -> None:
        try:
            asyncio.run(self._run_async())
        except Exception as exc:  # pragma: no cover - exercised by GUI integration.
            self.error_changed.emit(_safe_error_message(exc))
        finally:
            self.finished.emit()

    def stop(self) -> None:
        self._stop_event.set()

    async def _run_async(self) -> None:
        self.status_changed.emit("解析直播源中")
        source = build_decode_only_source(self.request)
        decoder = build_decode_only_decoder()
        auto_confirm = build_auto_confirm_request(self.request, self.account_store)
        summary = await run_decode_only_monitor(
            source=source,
            decoder=decoder,
            room_id=self.request.room_id,
            roi=self.request.roi,
            auth_mode=self.request.auth_mode,
            stop_requested=self._stop_event.is_set,
            on_snapshot=lambda snapshot: self.snapshot_changed.emit(snapshot),
            auto_confirm=auto_confirm,
        )
        self.status_changed.emit("已停止" if summary.state == "stopped" else "已结束")


class QtDecodeOnlyMonitorController(QObject):
    """管理只解码监测 worker 与 GUI 主线程之间的信号连接。"""

    def __init__(
        self,
        parent: QObject | None = None,
        account_store: AccountStore | None = None,
    ) -> None:
        super().__init__(parent)
        self.account_store = account_store if account_store is not None else KeyringAccountStore()
        self._thread: QThread | None = None
        self._worker: DecodeOnlyMonitorWorker | None = None
        self._callbacks: DecodeOnlyMonitorCallbacks | None = None
        self._running = False

    def start(
        self,
        request: DecodeOnlyMonitorRequest,
        callbacks: DecodeOnlyMonitorCallbacks,
    ) -> None:
        if self._running:
            msg = "decode-only monitor is already running"
            raise ValueError(msg)

        thread = QThread(self)
        worker = DecodeOnlyMonitorWorker(request, self.account_store)
        worker.moveToThread(thread)
        self._thread = thread
        self._worker = worker
        self._callbacks = callbacks
        self._running = True

        thread.started.connect(worker.run)
        worker.status_changed.connect(callbacks.on_status)
        worker.snapshot_changed.connect(callbacks.on_snapshot)
        worker.error_changed.connect(callbacks.on_error)
        worker.finished.connect(self._handle_finished)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def stop(self) -> None:
        if self._worker is not None:
            self._worker.stop()

    def is_running(self) -> bool:
        return self._running

    @Slot()
    def _handle_finished(self) -> None:
        self._running = False
        self._worker = None
        self._thread = None
        if self._callbacks is not None:
            self._callbacks.on_finished()


def _safe_error_message(exc: Exception) -> str:
    text = str(exc).strip()
    lowered = text.lower()
    sensitive_markers = ("token", "cookie", "payload", "authorization", "sessdata")
    if not text or any(marker in lowered for marker in sensitive_markers):
        return "监测失败：请检查直播间、浏览器或凭证配置"
    return f"监测失败：{text}"
