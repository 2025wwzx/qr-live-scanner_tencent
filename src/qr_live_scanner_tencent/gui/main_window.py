from __future__ import annotations

import asyncio
import time
from contextlib import suppress
from pathlib import Path
from typing import Protocol

import httpx
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QCloseEvent, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from qr_live_scanner_tencent.accounts import (
    KeyringAccountStore,
    LocalDeviceIdStore,
    TencentAccountQRLoginError,
    TencentAccountQRLoginService,
    TencentAccountQRLoginState,
    TencentAccountQRLoginStatus,
    TencentAccountQRTicket,
    TencentSession,
)
from qr_live_scanner_tencent.auth.tencent import TencentGameConfig, default_game_configs
from qr_live_scanner_tencent.gui.about import NON_COMMERCIAL_WARNING, AboutDialog
from qr_live_scanner_tencent.gui.monitor import (
    DecodeOnlyMonitorCallbacks,
    DecodeOnlyMonitorController,
    DecodeOnlyMonitorRequest,
    DecodeOnlyMonitorSnapshot,
    LocalDemoMonitorController,
    QtDecodeOnlyMonitorController,
)
from qr_live_scanner_tencent.gui.roi_editor import ROIEditorWidget
from qr_live_scanner_tencent.gui.state import (
    GuiAccountEntry,
    GuiState,
    load_gui_state,
    save_gui_state,
)
from qr_live_scanner_tencent.interfaces import (
    DEFAULT_AGGRESSIVE_ROI,
    AccountStore,
    AccountStoreError,
    GameID,
    ROIConfig,
    TencentLoginProvider,
)
from qr_live_scanner_tencent.monitor import DEFAULT_BROWSER_PROFILE_DIR

DECODE_ONLY_HINT = "当前为只解码不确认模式，不会扫码或确认登录。"
AUTO_CONFIRM_HINT = "自动确认链路已接通；默认真实腾讯协议未验证时会安全拒绝 scan/confirm。"
ACCOUNT_STORE_ERROR_HINT = "账号管理：请检查本机凭证存储配置"

PLATFORM_OPTIONS = ("B站", "抖音")
GAME_DISPLAY_NAMES = {
    GameID.HONOR_OF_KINGS: "王者荣耀",
}
ROI_PRESETS = {
    "激进（默认）": DEFAULT_AGGRESSIVE_ROI,
    "标准": ROIConfig(x=0.35, y=0.35, width=0.30, height=0.30),
    "宽松": ROIConfig(x=0.30, y=0.30, width=0.40, height=0.40),
    "全画面": ROIConfig.full_frame(),
}
ROI_DEBUG_PRESET_NAME = "专业调试"


ACCOUNT_QR_OUTPUT_PATH = Path("work/tencent-account-login-qr.png")
ACCOUNT_QR_LOGIN_ERROR_HINT = (
    "Tencent account login is not validated; use a mock or validated config first"
)


class TencentAccountQRLoginServiceProtocol(Protocol):
    async def fetch_qr(self) -> TencentAccountQRTicket:
        """Create a QR ticket for rendering."""

    async def query_qr(self, ticket: TencentAccountQRTicket) -> TencentAccountQRLoginStatus:
        """Poll QR login status."""

    def write_qr_png(self, ticket: TencentAccountQRTicket, output_path: Path) -> None:
        """Render the QR payload into a local PNG."""

    async def aclose(self) -> None:
        """Close runtime resources."""


class TencentAccountQRLoginServiceFactory(Protocol):
    def __call__(self, provider: TencentLoginProvider) -> TencentAccountQRLoginServiceProtocol:
        """Return a Tencent account QR login service for the selected provider."""


class TencentAccountQRLoginWorker(QThread):
    qr_ready = Signal(str)
    status_changed = Signal(str)
    session_ready = Signal(object)
    failed = Signal()
    canceled = Signal()

    def __init__(
        self,
        *,
        provider: TencentLoginProvider,
        service_factory: TencentAccountQRLoginServiceFactory,
        qr_output_path: Path,
        timeout_seconds: float,
        poll_interval_seconds: float,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._provider = provider
        self._service_factory = service_factory
        self._qr_output_path = qr_output_path
        self._timeout_seconds = timeout_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self._cancel_requested = False

    def cancel(self) -> None:
        self._cancel_requested = True

    def run(self) -> None:
        try:
            asyncio.run(self._run_login())
        except TencentAccountQRLoginError:
            self.failed.emit()
        except Exception:
            self.failed.emit()

    async def _run_login(self) -> None:
        service = self._service_factory(self._provider)
        try:
            ticket = await service.fetch_qr()
            if self._cancel_requested:
                self.canceled.emit()
                return
            service.write_qr_png(ticket, self._qr_output_path)
            self.qr_ready.emit(str(self._qr_output_path))
            self.status_changed.emit("QR generated; waiting for scan")
            deadline = time.monotonic() + self._timeout_seconds
            scanned_reported = False
            while time.monotonic() < deadline:
                if self._cancel_requested:
                    self.canceled.emit()
                    return
                status = await service.query_qr(ticket)
                if self._cancel_requested:
                    self.canceled.emit()
                    return
                if status.state is TencentAccountQRLoginState.SCANNED and not scanned_reported:
                    scanned_reported = True
                    self.status_changed.emit("QR scanned; waiting for confirmation")
                elif status.state is TencentAccountQRLoginState.CONFIRMED:
                    if status.session is None:
                        msg = "confirmed Tencent account session is missing"
                        raise TencentAccountQRLoginError(msg)
                    self.session_ready.emit(status.session)
                    return
                elif status.state is TencentAccountQRLoginState.EXPIRED:
                    msg = "Tencent account QR login expired"
                    raise TencentAccountQRLoginError(msg)
                elif status.state is TencentAccountQRLoginState.FAILED:
                    msg = "Tencent account QR login failed"
                    raise TencentAccountQRLoginError(msg)

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(self._poll_interval_seconds, remaining))

            msg = "Tencent account QR login timed out"
            raise TencentAccountQRLoginError(msg)
        finally:
            with suppress(Exception):
                await service.aclose()


class MainWindow(QMainWindow):
    """桌面端主窗口外壳；真实监测只解码，登录态流程不触发 confirm。"""

    def __init__(
        self,
        parent: QWidget | None = None,
        account_store: AccountStore | None = None,
        monitor_controller: DecodeOnlyMonitorController | None = None,
        account_qr_login_service_factory: TencentAccountQRLoginServiceFactory | None = None,
        state_path: str | Path | None = None,
    ) -> None:
        super().__init__(parent)
        self.account_store = account_store if account_store is not None else KeyringAccountStore()
        self.account_qr_login_service_factory = (
            account_qr_login_service_factory
            if account_qr_login_service_factory is not None
            else _new_account_qr_login_service
        )
        self.monitor_controller = (
            monitor_controller
            if monitor_controller is not None
            else QtDecodeOnlyMonitorController(self, self.account_store)
        )
        self.state_path = Path(state_path) if state_path is not None else None
        self._state = load_gui_state(self.state_path) if self.state_path is not None else GuiState()
        self._default_uid = self._state.default_uid
        self._default_provider = self._state.default_provider
        self._account_entries: dict[tuple[TencentLoginProvider, str], GuiAccountEntry] = {}
        self.setWindowTitle("腾讯扫码器")
        self.setMinimumSize(560, 640)
        self.resize(560, 640)
        self._build_menu_bar()
        self.setStyleSheet(CLIENT_STYLE_SHEET)

        self.account_table = QTableWidget(0, 3)
        self.account_table.setObjectName("account_table")
        self.account_table.setHorizontalHeaderLabels(["UID", "登录态", "默认"])
        self.account_table.verticalHeader().setVisible(False)
        self.account_table.setAlternatingRowColors(True)
        self.account_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.account_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.account_table.horizontalHeader().setStretchLastSection(True)
        self.account_table.itemSelectionChanged.connect(self._sync_selected_account_label)

        self.platform_combo = QComboBox()
        self.platform_combo.addItems(PLATFORM_OPTIONS)
        self.platform_combo.currentIndexChanged.connect(self._sync_platform_fields)

        self.game_combo = QComboBox()
        for game_id in GameID:
            self.game_combo.addItem(GAME_DISPLAY_NAMES[game_id], game_id.value)

        self.provider_combo = QComboBox()
        self.provider_combo.addItem("QQ", TencentLoginProvider.QQ.value)
        self.provider_combo.addItem("微信", TencentLoginProvider.WECHAT.value)

        self.room_input = QLineEdit()
        self.room_input.setPlaceholderText("Room ID")

        self.browser_profile_input = QLineEdit()
        self.browser_profile_input.setPlaceholderText("Douyin browser profile")

        self.chrome_path_input = QLineEdit()
        self.chrome_path_input.setPlaceholderText("留空时使用内置 Playwright Chromium")
        self.browser_profile_label = QLabel("Browser profile")
        self.chrome_path_label = QLabel("Chrome")

        self.start_button = QPushButton("监视直播间")
        self.start_button.setCheckable(True)
        self.stop_button = QPushButton("停止监视")
        self.stop_button.setEnabled(False)
        self.start_button.clicked.connect(self._start_decode_only_monitoring)
        self.stop_button.clicked.connect(self._stop_decode_only_monitoring)
        self.about_button = QPushButton("关于")
        self.about_button.clicked.connect(self._show_about_dialog)
        self.auto_confirm_checkbox = QCheckBox("自动二次确认")
        self.auto_confirm_checkbox.setEnabled(False)
        self.auto_confirm_checkbox.setToolTip(AUTO_CONFIRM_HINT)
        self.auto_exit_checkbox = QCheckBox("扫码成功后自动退出")
        self.demo_mode_checkbox = QCheckBox("本地模拟")
        self.demo_mode_checkbox.setToolTip("使用本地模拟指标演练 GUI，不连接直播平台。")

        self.selected_account_label = QLabel("绑定账号：未选择")
        self.selected_account_label.setObjectName("selected_account_label")
        self.authorization_state_label = QLabel("账号授权：未选择")
        self.protocol_gate_label = QLabel()
        self.protocol_gate_label.setWordWrap(True)
        self.monitor_status_label = QLabel("监测状态：未启动")
        self.monitor_status_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.monitor_metrics_label = QLabel("帧数=0 候选=0 重复=0 延迟=- 解码器=-")
        self.monitor_metrics_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.last_candidate_label = QLabel("最近识别：无")
        self.last_candidate_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.last_candidate_label.setWordWrap(True)

        self.roi_editor = ROIEditorWidget()
        self.license_notice = QLabel(NON_COMMERCIAL_WARNING)
        self.license_notice.setWordWrap(True)
        self.license_notice.setAlignment(Qt.AlignmentFlag.AlignLeft)

        self.setCentralWidget(self._build_central_widget())
        self.statusBar().showMessage("客户端就绪：只解码监测不会扫码或确认登录")
        self._restore_state(self._state)
        self._sync_platform_fields()
        self._sync_protocol_gate_label()
        self._sync_selected_account_label()
        self.game_combo.currentIndexChanged.connect(self._sync_protocol_gate_label)
        self.provider_combo.currentIndexChanged.connect(self._handle_provider_changed)

    def _build_central_widget(self) -> QWidget:
        central = QWidget(self)
        layout = QVBoxLayout(central)

        layout.addWidget(self._build_accounts_table_group())
        layout.addWidget(self._build_stream_group())
        layout.addWidget(self._build_monitor_group())
        layout.addWidget(self.license_notice)
        return central

    def _build_menu_bar(self) -> None:
        account_menu = QMenu("账号管理", self)
        account_menu.addAction("新增账号", self._show_add_account_dialog)
        account_menu.addAction("删除账号", self._clear_selected_account)
        account_menu.addAction("设为默认账号", self._set_selected_account_as_default)
        account_menu.addAction("打开配置文件").setEnabled(False)
        roi_menu = QMenu("ROI设置", self)
        roi_menu.addAction("打开 ROI 设置", self._show_roi_dialog)
        about_menu = QMenu("关于", self)
        about_menu.addAction("关于", self._show_about_dialog)
        about_menu.addAction("帮助和反馈").setEnabled(False)
        self.menuBar().addMenu(account_menu)
        self.menuBar().addMenu(roi_menu)
        self.menuBar().addMenu(about_menu)

    def _build_accounts_table_group(self) -> QGroupBox:
        group = QGroupBox("账号列表")
        group.setObjectName("accounts_table_group")
        layout = QVBoxLayout(group)
        layout.addWidget(self.account_table)
        layout.addWidget(self.selected_account_label)
        layout.addWidget(self.authorization_state_label)
        return group

    def _build_stream_group(self) -> QGroupBox:
        group = QGroupBox("直播源")
        group.setObjectName("stream_group")
        controls = QGridLayout(group)
        controls.addWidget(QLabel("直播平台"), 0, 0)
        controls.addWidget(self.platform_combo, 0, 1)
        controls.addWidget(QLabel("游戏"), 1, 0)
        controls.addWidget(self.game_combo, 1, 1)
        controls.addWidget(QLabel("登录渠道"), 2, 0)
        controls.addWidget(self.provider_combo, 2, 1)
        controls.addWidget(QLabel("直播间 ID"), 3, 0)
        controls.addWidget(self.room_input, 3, 1)
        controls.addWidget(self.browser_profile_label, 4, 0)
        controls.addWidget(self.browser_profile_input, 4, 1)
        controls.addWidget(self.chrome_path_label, 5, 0)
        controls.addWidget(self.chrome_path_input, 5, 1)
        return group

    def _build_monitor_group(self) -> QGroupBox:
        group = QGroupBox("监测")
        group.setObjectName("monitor_group")
        layout = QVBoxLayout(group)
        buttons = QHBoxLayout()
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.stop_button)
        buttons.addWidget(self.about_button)
        layout.addLayout(buttons)
        options = QHBoxLayout()
        options.addWidget(self.auto_confirm_checkbox)
        options.addWidget(self.auto_exit_checkbox)
        options.addWidget(self.demo_mode_checkbox)
        layout.addLayout(options)
        layout.addWidget(self.protocol_gate_label)
        layout.addWidget(self.monitor_status_label)
        layout.addWidget(self.monitor_metrics_label)
        layout.addWidget(self.last_candidate_label)
        return group

    def _selected_game_id(self) -> GameID:
        data = self.game_combo.currentData()
        return GameID(str(data))

    def _selected_platform(self) -> str:
        text = self.platform_combo.currentText()
        if text == "抖音":
            return "douyin"
        return "bilibili"

    def _selected_provider(self) -> TencentLoginProvider:
        data = self.provider_combo.currentData()
        return TencentLoginProvider(str(data))

    def _sync_platform_fields(self) -> None:
        douyin_selected = self._selected_platform() == "douyin"
        self.browser_profile_label.setEnabled(douyin_selected)
        self.browser_profile_input.setEnabled(douyin_selected)
        self.chrome_path_label.setEnabled(douyin_selected)
        self.chrome_path_input.setEnabled(douyin_selected)

    def _start_decode_only_monitoring(self) -> None:
        room_id = self.room_input.text().strip()
        if not room_id and not self.demo_mode_checkbox.isChecked():
            self.monitor_status_label.setText("监测状态：请输入直播间")
            self.start_button.setChecked(False)
            return
        if self.monitor_controller.is_running():
            self.monitor_status_label.setText("监测状态：已在运行")
            self.start_button.setChecked(True)
            return
        selected_uid = self._selected_table_uid()
        auto_confirm_enabled = self.auto_confirm_checkbox.isChecked()
        if auto_confirm_enabled and not self._is_authorized_table_uid(selected_uid):
            self.monitor_status_label.setText("监测状态：请选择已保存登录态的账号")
            self.start_button.setChecked(False)
            return
        if auto_confirm_enabled and not self._is_protocol_validated():
            self.monitor_status_label.setText("监测状态：腾讯真实协议未验证，已阻止自动确认")
            self.start_button.setChecked(False)
            return

        request = DecodeOnlyMonitorRequest(
            platform=self._selected_platform(),
            room_id=room_id or "local-demo",
            game_id=self._selected_game_id(),
            roi=self.roi_editor.roi(),
            browser_user_data_dir=self.browser_profile_input.text().strip()
            or DEFAULT_BROWSER_PROFILE_DIR,
            chrome_executable_path=self._optional_chrome_path(),
            account_uid=selected_uid or None,
            provider=self._selected_provider(),
            auto_confirm=auto_confirm_enabled,
            auto_exit=self.auto_exit_checkbox.isChecked(),
        )
        callbacks = DecodeOnlyMonitorCallbacks(
            on_status=self._set_monitor_status,
            on_snapshot=self._set_monitor_snapshot,
            on_error=self._set_monitor_error,
            on_finished=self._handle_monitor_finished,
        )

        self.monitor_status_label.setText("监测状态：正在启动")
        self.last_candidate_label.setText("最近识别：无")
        self.statusBar().showMessage(self._monitor_start_hint(auto_confirm_enabled))
        try:
            self._active_monitor_controller().start(request, callbacks)
        except ValueError as exc:
            self.monitor_status_label.setText(f"监测状态：{exc}")
            self.start_button.setEnabled(True)
            self.start_button.setChecked(False)
            self.stop_button.setEnabled(False)
            return
        self.start_button.setEnabled(False)
        self.start_button.setChecked(True)
        self.stop_button.setEnabled(True)

    def _stop_decode_only_monitoring(self) -> None:
        self._active_monitor_controller().stop()

    def _active_monitor_controller(self) -> DecodeOnlyMonitorController:
        if self.demo_mode_checkbox.isChecked():
            if not isinstance(self.monitor_controller, LocalDemoMonitorController):
                self.monitor_controller = LocalDemoMonitorController()
        elif isinstance(self.monitor_controller, LocalDemoMonitorController):
            self.monitor_controller = QtDecodeOnlyMonitorController(self, self.account_store)
        return self.monitor_controller

    def _optional_chrome_path(self) -> str | None:
        text = self.chrome_path_input.text().strip()
        return text or None

    def _set_monitor_status(self, status: str) -> None:
        self.monitor_status_label.setText(f"监测状态：{status}")

    def _set_monitor_snapshot(self, snapshot: DecodeOnlyMonitorSnapshot) -> None:
        latency = "-" if snapshot.last_latency_ms is None else f"{snapshot.last_latency_ms:.2f}ms"
        backend = snapshot.last_backend or "-"
        confirm_latency = (
            "-"
            if snapshot.last_confirm_latency_ms is None
            else f"{snapshot.last_confirm_latency_ms:.2f}ms"
        )
        self.monitor_metrics_label.setText(
            " ".join(
                [
                    f"帧数={snapshot.frames_seen}",
                    f"候选={snapshot.candidates_seen}",
                    f"重复={snapshot.duplicate_candidates}",
                    f"延迟={latency}",
                    f"解码器={backend}",
                    f"scan={snapshot.scan_sent}",
                    f"confirm={snapshot.confirm_sent}",
                    f"授权失败={snapshot.authorization_failures}",
                    f"确认延迟={confirm_latency}",
                ]
            )
        )
        summary = snapshot.last_candidate_summary or "无"
        self.last_candidate_label.setText(f"最近识别：{summary}")

    def _set_monitor_error(self, message: str) -> None:
        self.monitor_status_label.setText(f"监测状态：{message}")
        self.start_button.setEnabled(True)
        self.start_button.setChecked(False)
        self.stop_button.setEnabled(False)

    def _handle_monitor_finished(self) -> None:
        if "失败" not in self.monitor_status_label.text():
            self.monitor_status_label.setText("监测状态：已停止")
        self.start_button.setEnabled(True)
        self.start_button.setChecked(False)
        self.stop_button.setEnabled(False)

    def _clear_selected_account(self) -> None:
        uid = self._selected_table_uid()
        if not uid:
            self.statusBar().showMessage("请先在账号列表中选中一个账号")
            return

        provider = self._selected_provider()
        try:
            self.account_store.delete_tencent_session(uid, provider)
        except AccountStoreError:
            self.statusBar().showMessage(ACCOUNT_STORE_ERROR_HINT)
            return

        row = self._account_table_row(uid)
        if row >= 0:
            self.account_table.removeRow(row)
        self._account_entries.pop((provider, uid), None)
        if self._default_uid == uid and self._default_provider is provider:
            self._default_uid = ""
            self._default_provider = TencentLoginProvider.QQ
        self._sync_auto_confirm_availability()
        self._sync_default_account_marks()
        self._sync_selected_account_label()
        self._save_state()
        self.statusBar().showMessage("本地账号已删除")

    def _show_add_account_dialog(self) -> None:
        dialog = TencentAccountDialog(
            parent=self,
            provider=self._selected_provider(),
            account_store=self.account_store,
            service_factory=self.account_qr_login_service_factory,
        )
        if dialog.exec() != int(QDialog.DialogCode.Accepted):
            return
        uid = dialog.uid()
        if not uid:
            self.statusBar().showMessage("账号未保存：UID 为空")
            return
        self._refresh_account_table_row(uid)

    def _refresh_account_table_row(self, uid: str) -> None:
        provider = self._selected_provider()
        try:
            authorized = self.account_store.is_tencent_authorized(uid, provider)
        except AccountStoreError:
            self.statusBar().showMessage(ACCOUNT_STORE_ERROR_HINT)
            return
        self._remember_account(uid, provider)
        if authorized:
            self._set_account_table_row(uid, "已保存")
            self.statusBar().showMessage("账号登录态已保存")
        else:
            self._set_account_table_row(uid, "未保存")
            self.statusBar().showMessage("账号登录态未保存")
        self._save_state()

    def _remember_account(
        self,
        uid: str,
        provider: TencentLoginProvider | None = None,
        display_name: str = "",
    ) -> None:
        uid = uid.strip()
        if not uid:
            return
        provider = provider if provider is not None else self._selected_provider()
        self._account_entries[(provider, uid)] = GuiAccountEntry(
            uid=uid,
            provider=provider,
            display_name=display_name,
        )

    def _provider_default_uid(self) -> str:
        if self._default_provider is not self._selected_provider():
            return ""
        return self._default_uid

    def _reload_account_table_for_selected_provider(self) -> None:
        selected_provider = self._selected_provider()
        selected_uid = self._selected_table_uid()
        self.account_table.setRowCount(0)
        for account in self._account_entries.values():
            if account.provider is selected_provider:
                self._restore_account_row(account.uid)
        target_uid = self._provider_default_uid() or selected_uid
        if target_uid:
            row = self._account_table_row(target_uid)
            if row >= 0:
                self.account_table.selectRow(row)

    def _refresh_account_authorization_rows(self) -> None:
        selected_uid = self._selected_table_uid()
        for uid in self._account_table_uids():
            try:
                authorized = self.account_store.is_tencent_authorized(
                    uid,
                    self._selected_provider(),
                )
            except AccountStoreError:
                self.statusBar().showMessage(ACCOUNT_STORE_ERROR_HINT)
                return
            self._set_account_table_row(uid, "已保存" if authorized else "未保存")
        if selected_uid:
            row = self._account_table_row(selected_uid)
            if row >= 0:
                self.account_table.selectRow(row)

    def _handle_provider_changed(self) -> None:
        self._reload_account_table_for_selected_provider()
        self._sync_selected_account_label()
        self._save_state()

    def _show_about_dialog(self) -> None:
        AboutDialog(self).exec()

    def _show_roi_dialog(self) -> None:
        dialog = ROISettingsDialog(self.roi_editor.roi(), parent=self)
        if dialog.exec() == int(QDialog.DialogCode.Accepted):
            self.roi_editor.set_roi(dialog.roi())
            self.statusBar().showMessage("ROI 设置已更新")

    def _set_account_table_row(self, uid: str, status: str) -> None:
        row = self._account_table_row(uid)
        if row < 0:
            row = self.account_table.rowCount()
            self.account_table.insertRow(row)
        values = [uid, status, "是" if uid == self._provider_default_uid() else ""]
        for column, value in enumerate(values):
            self.account_table.setItem(row, column, QTableWidgetItem(value))
        self.account_table.selectRow(row)
        self._sync_default_account_marks()
        self._sync_selected_account_label()

    def _account_table_row(self, uid: str) -> int:
        for row in range(self.account_table.rowCount()):
            item = self.account_table.item(row, 0)
            if item is not None and item.text().strip() == uid:
                return row
        return -1

    def _selected_table_uid(self) -> str:
        row = self.account_table.currentRow()
        if row < 0:
            return ""
        item = self.account_table.item(row, 0)
        if item is None:
            return ""
        return item.text().strip()

    def _set_selected_account_as_default(self) -> None:
        uid = self._selected_table_uid()
        if not uid:
            self.statusBar().showMessage("请先在账号列表中选中一个账号")
            return
        self._default_uid = uid
        self._default_provider = self._selected_provider()
        self._sync_default_account_marks()
        self._sync_selected_account_label()
        self._save_state()
        self.statusBar().showMessage("默认账号已更新")

    def _is_authorized_table_uid(self, uid: str) -> bool:
        if not uid:
            return False
        row = self._account_table_row(uid)
        if row < 0:
            return False
        try:
            return self.account_store.is_tencent_authorized(uid, self._selected_provider())
        except AccountStoreError:
            self.statusBar().showMessage(ACCOUNT_STORE_ERROR_HINT)
            return False

    def _sync_default_account_marks(self) -> None:
        for row in range(self.account_table.rowCount()):
            uid_item = self.account_table.item(row, 0)
            uid = "" if uid_item is None else uid_item.text().strip()
            self.account_table.setItem(
                row,
                2,
                QTableWidgetItem("是" if uid == self._provider_default_uid() else ""),
            )

    def _sync_selected_account_label(self) -> None:
        uid = self._selected_table_uid()
        self.selected_account_label.setText(f"绑定账号：{uid}" if uid else "绑定账号：未选择")
        self._sync_auto_confirm_availability()
        self._sync_authorization_state_label()

    def _sync_auto_confirm_availability(self) -> None:
        enabled = (
            self._is_authorized_table_uid(self._selected_table_uid())
            and self._is_protocol_validated()
        )
        self.auto_confirm_checkbox.setEnabled(enabled)
        if not enabled:
            self.auto_confirm_checkbox.setChecked(False)

    def _restore_state(self, state: GuiState) -> None:
        self.platform_combo.setCurrentText("抖音" if state.platform == "douyin" else "B站")
        game_index = self.game_combo.findData(state.game_id.value)
        if game_index >= 0:
            self.game_combo.setCurrentIndex(game_index)
        provider_index = self.provider_combo.findData(state.provider.value)
        if provider_index >= 0:
            self.provider_combo.setCurrentIndex(provider_index)
        self.room_input.setText(state.room_id)
        self.browser_profile_input.setText(
            state.browser_user_data_dir or DEFAULT_BROWSER_PROFILE_DIR
        )
        self.chrome_path_input.setText(state.chrome_executable_path or "")
        self.roi_editor.set_roi(state.roi)
        self.auto_confirm_checkbox.setChecked(state.auto_confirm)
        self.auto_exit_checkbox.setChecked(state.auto_exit)
        self.demo_mode_checkbox.setChecked(state.demo_mode)
        self._default_uid = state.default_uid
        self._default_provider = state.default_provider
        for account in state.accounts:
            self._remember_account(account.uid, account.provider, account.display_name)
        self._reload_account_table_for_selected_provider()
        self._sync_auto_confirm_availability()
        default_uid = self._provider_default_uid()
        if default_uid:
            row = self._account_table_row(default_uid)
            if row >= 0:
                self.account_table.selectRow(row)
        self._sync_default_account_marks()

    def _restore_account_row(self, uid: str) -> None:
        try:
            authorized = self.account_store.is_tencent_authorized(uid, self._selected_provider())
        except AccountStoreError:
            self.statusBar().showMessage(ACCOUNT_STORE_ERROR_HINT)
            return
        self._set_account_table_row(uid, "已保存" if authorized else "未保存")

    def _save_state(self) -> None:
        if self.state_path is None:
            return
        for uid in self._account_table_uids():
            self._remember_account(uid)
        accounts = list(self._account_entries.values())
        state = GuiState(
            platform=self._selected_platform(),
            game_id=self._selected_game_id(),
            provider=self._selected_provider(),
            room_id=self.room_input.text().strip(),
            browser_user_data_dir=self.browser_profile_input.text().strip()
            or DEFAULT_BROWSER_PROFILE_DIR,
            chrome_executable_path=self._optional_chrome_path(),
            roi=self.roi_editor.roi(),
            auto_confirm=self.auto_confirm_checkbox.isChecked(),
            auto_exit=self.auto_exit_checkbox.isChecked(),
            demo_mode=self.demo_mode_checkbox.isChecked(),
            default_uid=self._default_uid,
            default_provider=self._default_provider,
            accounts=accounts,
        )
        save_gui_state(self.state_path, state)

    def _sync_protocol_gate_label(self) -> None:
        config = _default_game_configs()[self._selected_game_id()]
        if config.validated_protocol:
            self.protocol_gate_label.setText("协议状态：已验证，可按授权策略执行确认")
            return
        self.protocol_gate_label.setText("协议状态：未验证，真实 scan/confirm 已禁用")
        self.auto_confirm_checkbox.setChecked(False)
        self._sync_auto_confirm_availability()

    def _sync_authorization_state_label(self) -> None:
        uid = self._selected_table_uid()
        if not uid:
            self.authorization_state_label.setText("账号授权：未选择")
            return
        provider = self._selected_provider().value
        state = "已授权" if self._is_authorized_table_uid(uid) else "未授权"
        self.authorization_state_label.setText(f"账号授权：{state} provider={provider}")

    def _is_protocol_validated(self) -> bool:
        return _default_game_configs()[self._selected_game_id()].validated_protocol

    def _monitor_start_hint(self, auto_confirm_enabled: bool) -> str:
        if self.demo_mode_checkbox.isChecked():
            return "本地模拟监测：不会连接直播平台或腾讯协议"
        return AUTO_CONFIRM_HINT if auto_confirm_enabled else DECODE_ONLY_HINT

    def _account_table_uids(self) -> list[str]:
        uids: list[str] = []
        for row in range(self.account_table.rowCount()):
            item = self.account_table.item(row, 0)
            if item is not None:
                uid = item.text().strip()
                if uid and uid not in uids:
                    uids.append(uid)
        return uids

    def closeEvent(self, event: QCloseEvent) -> None:
        self._save_state()
        super().closeEvent(event)


def _default_game_configs() -> dict[GameID, TencentGameConfig]:
    return default_game_configs()


class ROISettingsDialog(QDialog):
    """弹出式 ROI 设置窗口，主界面默认隐藏 ROI 细节。"""

    def __init__(self, roi: ROIConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("ROI 设置")
        self.preset_combo = QComboBox()
        self.preset_combo.addItems([*ROI_PRESETS, ROI_DEBUG_PRESET_NAME])
        self.preset_combo.currentTextChanged.connect(self._sync_preset)
        self.editor = ROIEditorWidget(self)
        self.editor_group = QGroupBox("专业调试")
        editor_layout = QVBoxLayout(self.editor_group)
        self.debug_help_label = QLabel(
            "X/Y 是左上角位置，Width/Height 是识别区域大小，"
            "全部使用 0-1 的画面比例；例如默认激进值表示画面中心约 25% 区域。"
        )
        self.debug_help_label.setWordWrap(True)
        editor_layout.addWidget(self.debug_help_label)
        editor_layout.addWidget(self.editor)
        self.editor.set_roi(roi)
        self._set_initial_preset(roi)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("预设方案"))
        layout.addWidget(self.preset_combo)
        layout.addWidget(self.editor_group)
        layout.addWidget(buttons)

    def roi(self) -> ROIConfig:
        preset_name = self.preset_combo.currentText()
        if preset_name in ROI_PRESETS:
            return ROI_PRESETS[preset_name]
        return self.editor.roi()

    def _set_initial_preset(self, roi: ROIConfig) -> None:
        for preset_name, preset_roi in ROI_PRESETS.items():
            if roi == preset_roi:
                self.preset_combo.setCurrentText(preset_name)
                self.editor_group.setEnabled(False)
                self.editor_group.setVisible(False)
                return
        self.preset_combo.setCurrentText(ROI_DEBUG_PRESET_NAME)
        self.editor_group.setEnabled(True)
        self.editor_group.setVisible(True)

    def _sync_preset(self, preset_name: str) -> None:
        if preset_name in ROI_PRESETS:
            self.editor.set_roi(ROI_PRESETS[preset_name])
            self.editor_group.setEnabled(False)
            self.editor_group.setVisible(False)
            return
        self.editor_group.setEnabled(True)
        self.editor_group.setVisible(True)


class _ManualTencentAccountDialog(QDialog):
    """首版腾讯账号占位授权对话框。

    真实 QQ/微信扫码授权协议尚未验证，当前只保存用户手动填写的本地账号标识，
    用于 GUI 状态和自动确认 gate 的本地授权检查。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("新增腾讯账号")
        self.uid_input = QLineEdit()
        self.uid_input.setPlaceholderText("账号标识")
        hint = QLabel("真实腾讯登录协议未验证；这里只保存本地占位授权。")
        hint.setWordWrap(True)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("账号标识"))
        layout.addWidget(self.uid_input)
        layout.addWidget(hint)
        layout.addWidget(buttons)

    def uid(self) -> str:
        return self.uid_input.text().strip()


def _new_account_qr_login_service(provider: TencentLoginProvider) -> TencentAccountQRLoginService:
    return TencentAccountQRLoginService(
        client=httpx.AsyncClient(timeout=10.0),
        device_id_store=LocalDeviceIdStore.default(),
        config=TencentAccountQRLoginService.default_configs()[provider],
    )


class TencentAccountDialog(QDialog):
    """腾讯账号二维码登录弹窗。

    默认真实 QQ/微信协议仍然未验证，因此普通运行时会在发 HTTP 前安全失败。
    测试或后续正式接入可以注入 `service_factory`，由 service 生成二维码并返回确认会话。
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        provider: TencentLoginProvider = TencentLoginProvider.QQ,
        account_store: AccountStore | None = None,
        service_factory: TencentAccountQRLoginServiceFactory | None = None,
        qr_output_path: Path = ACCOUNT_QR_OUTPUT_PATH,
        timeout_seconds: float = 60.0,
        poll_interval_seconds: float = 2.0,
    ) -> None:
        super().__init__(parent)
        self._provider = TencentLoginProvider(str(provider))
        self._account_store = account_store if account_store is not None else KeyringAccountStore()
        self._service_factory = (
            service_factory if service_factory is not None else _new_account_qr_login_service
        )
        self._qr_output_path = Path(qr_output_path)
        self._timeout_seconds = timeout_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self._uid = ""
        self._login_worker: TencentAccountQRLoginWorker | None = None

        self.setWindowTitle("Tencent account QR login")
        self.provider_label = QLabel(f"Provider: {self._provider.value}")
        self.status_label = QLabel("Ready")
        self.status_label.setWordWrap(True)
        self.qr_path_label = QLabel("QR image: -")
        self.qr_path_label.setWordWrap(True)
        self.qr_preview_label = QLabel()
        self.qr_preview_label.setObjectName("qr_preview_label")
        self.qr_preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr_preview_label.setFixedSize(240, 240)
        self.qr_preview_label.setText("QR")
        self.start_login_button = QPushButton("Generate QR")
        self.start_login_button.clicked.connect(self._run_qr_login)
        self.cancel_login_button = QPushButton("Cancel")
        self.cancel_login_button.setEnabled(False)
        self.cancel_login_button.clicked.connect(self._cancel_qr_login)

        self.demo_qr_button = QPushButton("Generate dry-run QR")
        self.demo_qr_button.clicked.connect(self._write_dry_run_qr)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        self.ok_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setEnabled(False)
        self.buttons.accepted.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addWidget(self.provider_label)
        login_buttons = QHBoxLayout()
        login_buttons.addWidget(self.start_login_button)
        login_buttons.addWidget(self.cancel_login_button)
        login_buttons.addWidget(self.demo_qr_button)
        layout.addLayout(login_buttons)
        layout.addWidget(self.qr_preview_label)
        layout.addWidget(self.qr_path_label)
        layout.addWidget(self.status_label)
        layout.addWidget(self.buttons)

    def uid(self) -> str:
        return self._uid

    def _run_qr_login(self) -> None:
        if self._login_worker is not None and self._login_worker.isRunning():
            return
        self._uid = ""
        self.ok_button.setEnabled(False)
        self._set_login_running(True)
        self.status_label.setText("Generating QR")
        worker = TencentAccountQRLoginWorker(
            provider=self._provider,
            service_factory=self._service_factory,
            qr_output_path=self._qr_output_path,
            timeout_seconds=self._timeout_seconds,
            poll_interval_seconds=self._poll_interval_seconds,
            parent=self,
        )
        worker.qr_ready.connect(self._handle_qr_ready)
        worker.status_changed.connect(self.status_label.setText)
        worker.session_ready.connect(self._handle_login_session)
        worker.failed.connect(self._handle_login_failed)
        worker.canceled.connect(self._handle_login_canceled)
        worker.finished.connect(self._handle_login_worker_finished)
        self._login_worker = worker
        worker.start()

    def _cancel_qr_login(self) -> None:
        if self._login_worker is None or not self._login_worker.isRunning():
            return
        self.cancel_login_button.setEnabled(False)
        self.status_label.setText("Canceling Tencent account QR login")
        self._login_worker.cancel()

    def _write_dry_run_qr(self) -> None:
        service = TencentAccountQRLoginService.dry_run(
            provider=self._provider,
            device_id_store=LocalDeviceIdStore.default(),
        )
        try:
            ticket = service.dry_run_ticket()
            service.write_qr_png(ticket, self._qr_output_path)
            self._handle_qr_ready(str(self._qr_output_path))
            self.status_label.setText("Dry-run QR image generated")
        finally:
            asyncio.run(service.aclose())

    def _handle_qr_ready(self, path_text: str) -> None:
        self.qr_path_label.setText(f"QR image: {path_text}")
        pixmap = QPixmap(path_text)
        if pixmap.isNull():
            self.qr_preview_label.setText("QR image generated")
            return
        self.qr_preview_label.setPixmap(
            pixmap.scaled(
                self.qr_preview_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def _handle_login_session(self, session: object) -> None:
        if not isinstance(session, TencentSession):
            self._handle_login_failed()
            return
        if session.provider is not self._provider:
            self._handle_login_failed()
            return
        try:
            self._account_store.save_tencent_session(session, authorized=True)
        except AccountStoreError:
            self.status_label.setText(ACCOUNT_STORE_ERROR_HINT)
            self._set_login_running(False)
            return
        self._uid = session.uid
        self.ok_button.setEnabled(True)
        self.status_label.setText("Tencent account session saved")
        self._set_login_running(False)
        self.accept()

    def _handle_login_failed(self) -> None:
        self.status_label.setText(ACCOUNT_QR_LOGIN_ERROR_HINT)
        self._set_login_running(False)

    def _handle_login_canceled(self) -> None:
        self.status_label.setText("Tencent account QR login canceled")
        self._set_login_running(False)

    def _handle_login_worker_finished(self) -> None:
        if not self._uid:
            self._set_login_running(False)
        self._login_worker = None

    def _set_login_running(self, running: bool) -> None:
        self.start_login_button.setEnabled(not running)
        self.demo_qr_button.setEnabled(not running)
        self.cancel_login_button.setEnabled(running)


CLIENT_STYLE_SHEET = """
QMainWindow {
    background: #f5f7fb;
}
QGroupBox {
    border: 1px solid #ccd3df;
    border-radius: 6px;
    margin-top: 10px;
    padding: 8px;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
}
QLineEdit, QComboBox, QDoubleSpinBox {
    min-height: 24px;
}
QPushButton {
    min-height: 28px;
    padding: 3px 10px;
}
QTableWidget {
    background: #ffffff;
    alternate-background-color: #f0f4f8;
    gridline-color: #dde3ea;
}
QLabel#selected_account_label {
    font-weight: 600;
}
"""
