from pathlib import Path

from PySide6.QtWidgets import QDialog, QGroupBox, QMenu
from pytest import MonkeyPatch
from pytestqt.qtbot import QtBot

import qr_live_scanner_tencent.gui as gui_package
import qr_live_scanner_tencent.gui.main_window as main_window_module
from qr_live_scanner_tencent.accounts import FakeAccountStore, TencentSession
from qr_live_scanner_tencent.gui import AboutDialog, MainWindow, ROIEditorWidget
from qr_live_scanner_tencent.gui.main_window import ROISettingsDialog
from qr_live_scanner_tencent.gui.monitor import (
    DecodeOnlyMonitorCallbacks,
    DecodeOnlyMonitorRequest,
    DecodeOnlyMonitorSnapshot,
)
from qr_live_scanner_tencent.interfaces import (
    AccountStoreError,
    GameID,
    ROIConfig,
    TencentLoginProvider,
)


def _tencent_session(uid: str, token: str = "secret-token") -> TencentSession:
    return TencentSession(
        uid=uid,
        provider=TencentLoginProvider.QQ,
        credentials={"access_token": token},
    )


class FailingAccountStore:
    def get_token(self, game_id: GameID, uid: str) -> str | None:
        raise AccountStoreError("SECRET_TOKEN_VALUE should not be visible")

    def save_token(self, game_id: GameID, uid: str, token: str, *, authorized: bool) -> None:
        raise AccountStoreError("SECRET_TOKEN_VALUE should not be visible")

    def delete_token(self, game_id: GameID, uid: str) -> None:
        raise AccountStoreError("SECRET_TOKEN_VALUE should not be visible")

    def is_account_authorized(self, uid: str, game_id: GameID) -> bool:
        raise AccountStoreError("SECRET_TOKEN_VALUE should not be visible")

    def get_tencent_session(
        self,
        uid: str,
        provider: TencentLoginProvider = TencentLoginProvider.QQ,
    ) -> TencentSession | None:
        raise AccountStoreError("SECRET_TOKEN_VALUE should not be visible")

    def save_tencent_session(self, session: object, *, authorized: bool) -> None:
        raise AccountStoreError("SECRET_TOKEN_VALUE should not be visible")

    def delete_tencent_session(
        self,
        uid: str,
        provider: TencentLoginProvider = TencentLoginProvider.QQ,
    ) -> None:
        raise AccountStoreError("SECRET_TOKEN_VALUE should not be visible")

    def is_tencent_authorized(
        self,
        uid: str,
        provider: TencentLoginProvider = TencentLoginProvider.QQ,
    ) -> bool:
        raise AccountStoreError("SECRET_TOKEN_VALUE should not be visible")


class RecordingMonitorController:
    def __init__(self) -> None:
        self.started_request: DecodeOnlyMonitorRequest | None = None
        self.stop_calls = 0
        self.callbacks: DecodeOnlyMonitorCallbacks | None = None
        self._running = False

    def start(
        self, request: DecodeOnlyMonitorRequest, callbacks: DecodeOnlyMonitorCallbacks
    ) -> None:
        self.started_request = request
        self.callbacks = callbacks
        self._running = True
        callbacks.on_status("监测中")
        callbacks.on_snapshot(
            DecodeOnlyMonitorSnapshot(
                state="streaming",
                frames_seen=3,
                candidates_seen=1,
                duplicate_candidates=0,
                last_latency_ms=8.5,
                last_backend="zxing-cpp",
                last_roi=request.roi,
            )
        )

    def stop(self) -> None:
        self.stop_calls += 1
        self._running = False
        if self.callbacks is not None:
            self.callbacks.on_finished()

    def is_running(self) -> bool:
        return self._running


def test_about_dialog_shows_non_commercial_warning(qtbot: QtBot) -> None:
    dialog = AboutDialog()
    qtbot.addWidget(dialog)

    text = dialog.text()

    assert "禁止任何形式的商业用途/贩卖" in text
    assert "CC BY-NC 4.0" in text


def test_roi_editor_round_trips_normalized_roi(qtbot: QtBot) -> None:
    widget = ROIEditorWidget()
    qtbot.addWidget(widget)
    roi = ROIConfig(x=0.1, y=0.2, width=0.3, height=0.4)

    widget.set_roi(roi)

    assert widget.roi() == roi


def test_roi_editor_defaults_to_aggressive_primary_roi(qtbot: QtBot) -> None:
    widget = ROIEditorWidget()
    qtbot.addWidget(widget)

    assert widget.roi() == ROIConfig(x=0.375, y=0.375, width=0.25, height=0.25)


def test_roi_editor_clamps_values_to_fit_frame(qtbot: QtBot) -> None:
    widget = ROIEditorWidget()
    qtbot.addWidget(widget)

    widget.x_spin.setValue(0.9)
    widget.width_spin.setValue(0.3)
    widget.y_spin.setValue(0.8)
    widget.height_spin.setValue(0.4)

    roi = widget.roi()

    assert roi.x + roi.width <= 1.0
    assert roi.y + roi.height <= 1.0


def test_roi_settings_dialog_round_trips_roi(qtbot: QtBot) -> None:
    dialog = ROISettingsDialog(ROIConfig(x=0.1, y=0.2, width=0.3, height=0.4))
    qtbot.addWidget(dialog)

    assert dialog.windowTitle() == "ROI 设置"
    assert dialog.preset_combo.currentText() == "专业调试"
    assert dialog.editor_group.isEnabled() is True
    assert dialog.editor_group.isHidden() is False
    assert dialog.roi() == ROIConfig(x=0.1, y=0.2, width=0.3, height=0.4)

    dialog.editor.set_roi(ROIConfig(x=0.2, y=0.3, width=0.4, height=0.5))

    assert dialog.roi() == ROIConfig(x=0.2, y=0.3, width=0.4, height=0.5)


def test_roi_settings_dialog_supports_presets(qtbot: QtBot) -> None:
    dialog = ROISettingsDialog(ROIConfig(x=0.375, y=0.375, width=0.25, height=0.25))
    qtbot.addWidget(dialog)

    preset_names = [
        dialog.preset_combo.itemText(index) for index in range(dialog.preset_combo.count())
    ]

    assert preset_names == [
        "激进（默认）",
        "标准",
        "宽松",
        "全画面",
        "专业调试",
    ]
    assert dialog.preset_combo.currentText() == "激进（默认）"
    assert dialog.editor_group.isEnabled() is False
    assert dialog.editor_group.isHidden() is True
    assert dialog.roi() == ROIConfig(x=0.375, y=0.375, width=0.25, height=0.25)

    dialog.preset_combo.setCurrentText("标准")

    assert dialog.editor_group.isHidden() is True
    assert dialog.roi() == ROIConfig(x=0.35, y=0.35, width=0.30, height=0.30)

    dialog.preset_combo.setCurrentText("宽松")

    assert dialog.roi() == ROIConfig(x=0.30, y=0.30, width=0.40, height=0.40)

    dialog.preset_combo.setCurrentText("全画面")

    assert dialog.roi() == ROIConfig.full_frame()


def test_roi_settings_dialog_enables_professional_debug(qtbot: QtBot) -> None:
    dialog = ROISettingsDialog(ROIConfig(x=0.375, y=0.375, width=0.25, height=0.25))
    qtbot.addWidget(dialog)

    dialog.preset_combo.setCurrentText("专业调试")
    dialog.editor.set_roi(ROIConfig(x=0.2, y=0.3, width=0.4, height=0.5))

    assert dialog.editor_group.isEnabled() is True
    assert dialog.editor_group.isHidden() is False
    assert "X/Y 是左上角位置" in dialog.debug_help_label.text()
    assert "Width/Height 是识别区域大小" in dialog.debug_help_label.text()
    assert "0-1" in dialog.debug_help_label.text()
    assert dialog.roi() == ROIConfig(x=0.2, y=0.3, width=0.4, height=0.5)


def test_main_window_contains_core_controls(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)

    assert window.windowTitle() == "腾讯扫码器"
    assert window.menuBar().actions()[0].text() == "账号管理"
    assert window.menuBar().actions()[1].text() == "ROI设置"
    assert window.menuBar().actions()[2].text() == "关于"
    account_menu = window.menuBar().actions()[0].menu()
    assert account_menu is not None
    assert isinstance(account_menu, QMenu)
    assert account_menu.actions()[0].text() == "新增账号"
    assert account_menu.actions()[1].text() == "删除账号"
    roi_menu = window.menuBar().actions()[1].menu()
    assert roi_menu is not None
    assert isinstance(roi_menu, QMenu)
    assert roi_menu.actions()[0].text() == "打开 ROI 设置"
    assert window.findChild(QGroupBox, "accounts_table_group") is not None
    assert window.findChild(QGroupBox, "stream_group") is not None
    assert window.findChild(QGroupBox, "account_group") is None
    assert window.findChild(QGroupBox, "monitor_group") is not None
    assert window.findChild(QGroupBox, "roi_group") is None
    assert "客户端就绪" in window.statusBar().currentMessage()
    assert window.account_table.columnCount() == 3
    uid_header = window.account_table.horizontalHeaderItem(0)
    status_header = window.account_table.horizontalHeaderItem(1)
    default_header = window.account_table.horizontalHeaderItem(2)
    assert uid_header is not None
    assert status_header is not None
    assert default_header is not None
    assert uid_header.text() == "UID"
    assert status_header.text() == "登录态"
    assert default_header.text() == "默认"
    assert window.platform_combo.count() == 2
    assert window.game_combo.count() == 1
    assert [window.game_combo.itemText(index) for index in range(window.game_combo.count())] == [
        "王者荣耀",
    ]
    assert window.game_combo.itemData(0) == GameID.HONOR_OF_KINGS.value
    assert window.start_button.text() == "监视直播间"
    assert window.start_button.isCheckable() is True
    assert window.stop_button.text() == "停止监视"
    assert window.about_button.text() == "关于"
    assert window.auto_confirm_checkbox.text() == "自动二次确认"
    assert window.auto_confirm_checkbox.isEnabled() is False
    assert window.selected_account_label.text() == "绑定账号：未选择"
    assert not hasattr(window, "screen_monitor_button")
    assert not hasattr(window, "auto_start_checkbox")
    assert "未启动" in window.monitor_status_label.text()
    assert "帧数=0" in window.monitor_metrics_label.text()
    assert not hasattr(window, "uid_input")
    assert not hasattr(window, "account_status_label")
    assert not hasattr(window, "authorize_button")
    assert not hasattr(window, "account_hint_label")
    assert "禁止任何形式的商业用途/贩卖" in window.license_notice.text()
    assert window.account_table.rowCount() == 0
    assert window.chrome_path_input.text() == ""
    assert "内置 Playwright Chromium" in window.chrome_path_input.placeholderText()


def test_main_window_enables_browser_fields_only_for_douyin(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)

    assert window._selected_platform() == "bilibili"
    assert window.browser_profile_input.isEnabled() is False
    assert window.chrome_path_input.isEnabled() is False

    window.platform_combo.setCurrentText("抖音")

    assert window._selected_platform() == "douyin"
    assert window.browser_profile_input.isEnabled() is True
    assert window.chrome_path_input.isEnabled() is True

    window.platform_combo.setCurrentText("B站")

    assert window._selected_platform() == "bilibili"
    assert window.browser_profile_input.isEnabled() is False
    assert window.chrome_path_input.isEnabled() is False


def test_gui_public_api_excludes_game_sdk_account_dialog() -> None:
    assert "MhyAccountLoginDialog" not in gui_package.__all__
    assert not hasattr(gui_package, "MhyAccountLoginDialog")


def test_main_window_starts_decode_only_monitoring(qtbot: QtBot) -> None:
    controller = RecordingMonitorController()
    window = MainWindow(monitor_controller=controller)
    qtbot.addWidget(window)

    window.platform_combo.setCurrentText("抖音")
    window.room_input.setText("https://live.douyin.com/12345")
    window.browser_profile_input.setText("profiles/douyin-test")
    window.chrome_path_input.setText(r"C:\Chrome\chrome.exe")
    window.roi_editor.set_roi(ROIConfig(x=0.1, y=0.2, width=0.3, height=0.4))

    window.start_button.click()

    assert controller.started_request is not None
    assert controller.started_request.platform == "douyin"
    assert controller.started_request.room_id == "https://live.douyin.com/12345"
    assert controller.started_request.roi == ROIConfig(x=0.1, y=0.2, width=0.3, height=0.4)
    assert controller.started_request.browser_user_data_dir == "profiles/douyin-test"
    assert controller.started_request.chrome_executable_path == r"C:\Chrome\chrome.exe"
    assert controller.started_request.account_uid is None
    assert controller.started_request.auto_confirm is False
    assert "监测中" in window.monitor_status_label.text()
    assert "帧数=3" in window.monitor_metrics_label.text()
    assert "候选=1" in window.monitor_metrics_label.text()
    assert "payload" not in window.monitor_metrics_label.text().lower()
    assert window.start_button.isEnabled() is False
    assert window.start_button.isChecked() is True
    assert window.stop_button.isEnabled() is True


def test_main_window_binds_selected_account_to_auto_confirm_monitoring(qtbot: QtBot) -> None:
    controller = RecordingMonitorController()
    store = FakeAccountStore()
    store.save_tencent_session(
        _tencent_session("10001"),
        authorized=True,
    )
    window = MainWindow(account_store=store, monitor_controller=controller)
    qtbot.addWidget(window)
    window._refresh_account_table_row("10001")
    window.auto_confirm_checkbox.setChecked(True)
    window.room_input.setText("5373751")

    window.start_button.click()

    assert controller.started_request is not None
    assert controller.started_request.account_uid == "10001"
    assert controller.started_request.auto_confirm is True
    assert window.selected_account_label.text() == "绑定账号：10001"


def test_main_window_requires_bound_account_for_auto_confirm(qtbot: QtBot) -> None:
    controller = RecordingMonitorController()
    window = MainWindow(monitor_controller=controller)
    qtbot.addWidget(window)
    window.auto_confirm_checkbox.setEnabled(True)
    window.auto_confirm_checkbox.setChecked(True)
    window.room_input.setText("5373751")

    window.start_button.click()

    assert controller.started_request is None
    assert "请选择已保存登录态的账号" in window.monitor_status_label.text()
    assert window.start_button.isChecked() is False


def test_main_window_stops_decode_only_monitoring(qtbot: QtBot) -> None:
    controller = RecordingMonitorController()
    window = MainWindow(monitor_controller=controller)
    qtbot.addWidget(window)
    window.room_input.setText("5373751")

    window.start_button.click()
    window.stop_button.click()

    assert controller.stop_calls == 1
    assert "已停止" in window.monitor_status_label.text()
    assert window.start_button.isEnabled() is True
    assert window.start_button.isChecked() is False
    assert window.stop_button.isEnabled() is False


def test_main_window_requires_room_before_monitoring(qtbot: QtBot) -> None:
    controller = RecordingMonitorController()
    window = MainWindow(monitor_controller=controller)
    qtbot.addWidget(window)

    window.start_button.click()

    assert controller.started_request is None
    assert "请输入直播间" in window.monitor_status_label.text()
    assert window.start_button.isChecked() is False


def test_main_window_clears_local_authorization(qtbot: QtBot) -> None:
    store = FakeAccountStore()
    store.save_tencent_session(
        _tencent_session("10001"),
        authorized=True,
    )
    window = MainWindow(account_store=store)
    qtbot.addWidget(window)

    game_index = window.game_combo.findData(GameID.HONOR_OF_KINGS.value)
    window.game_combo.setCurrentIndex(game_index)
    window._refresh_account_table_row("10001")

    status_item = window.account_table.item(0, 1)
    assert status_item is not None
    assert status_item.text() == "已保存"

    window._clear_selected_account()

    assert store.get_tencent_session("10001") is None
    assert store.is_tencent_authorized("10001") is False
    assert window.account_table.rowCount() == 0
    assert window.auto_confirm_checkbox.isEnabled() is False
    assert "本地账号已删除" in window.statusBar().currentMessage()


def test_main_window_add_account_opens_qr_dialog_and_refreshes_account_table(
    qtbot: QtBot, monkeypatch: MonkeyPatch
) -> None:
    store = FakeAccountStore()
    opened: list[str] = []

    class FakeTencentAccountDialog:
        def __init__(self, **kwargs: object) -> None:
            assert "parent" in kwargs
            opened.append("tencent")
            self._uid = ""

        def exec(self) -> int:
            store.save_tencent_session(
                _tencent_session("10001"),
                authorized=True,
            )
            self._uid = "10001"
            return int(QDialog.DialogCode.Accepted)

        def uid(self) -> str:
            return self._uid

    monkeypatch.setattr(main_window_module, "TencentAccountDialog", FakeTencentAccountDialog)
    window = MainWindow(account_store=store)
    qtbot.addWidget(window)
    game_index = window.game_combo.findData(GameID.HONOR_OF_KINGS.value)
    window.game_combo.setCurrentIndex(game_index)

    window._show_add_account_dialog()

    assert store.get_token(GameID.HONOR_OF_KINGS, "10001") is None
    assert opened == ["tencent"]
    assert store.get_tencent_session("10001") is not None
    uid_item = window.account_table.item(0, 0)
    status_item = window.account_table.item(0, 1)
    assert uid_item is not None
    assert status_item is not None
    assert uid_item.text() == "10001"
    assert status_item.text() == "已保存"
    assert window.auto_confirm_checkbox.isEnabled() is True


def test_main_window_account_table_updates_rows_by_uid(qtbot: QtBot) -> None:
    store = FakeAccountStore()
    store.save_tencent_session(
        _tencent_session("10001", "first-token"),
        authorized=True,
    )
    store.save_tencent_session(
        _tencent_session("10002", "second-token"),
        authorized=True,
    )
    window = MainWindow(account_store=store)
    qtbot.addWidget(window)

    window._refresh_account_table_row("10001")
    window._refresh_account_table_row("10002")
    window._refresh_account_table_row("10001")

    assert window.account_table.rowCount() == 2
    first_uid_item = window.account_table.item(0, 0)
    second_uid_item = window.account_table.item(1, 0)
    assert first_uid_item is not None
    assert second_uid_item is not None
    assert first_uid_item.text() == "10001"
    assert second_uid_item.text() == "10002"


def test_main_window_can_set_default_account(qtbot: QtBot) -> None:
    store = FakeAccountStore()
    store.save_tencent_session(
        _tencent_session("10001", "first-token"),
        authorized=True,
    )
    store.save_tencent_session(
        _tencent_session("10002", "second-token"),
        authorized=True,
    )
    window = MainWindow(account_store=store)
    qtbot.addWidget(window)
    window._refresh_account_table_row("10001")
    window._refresh_account_table_row("10002")

    window.account_table.selectRow(1)
    window._set_selected_account_as_default()

    first_default_item = window.account_table.item(0, 2)
    second_default_item = window.account_table.item(1, 2)
    assert first_default_item is not None
    assert second_default_item is not None
    assert first_default_item.text() == ""
    assert second_default_item.text() == "是"
    assert window.selected_account_label.text() == "绑定账号：10002"


def test_main_window_persists_and_restores_gui_state(qtbot: QtBot, tmp_path: Path) -> None:
    state_path = tmp_path / "gui-state.json"
    store = FakeAccountStore()
    store.save_tencent_session(
        _tencent_session("10001"),
        authorized=True,
    )
    window = MainWindow(account_store=store, state_path=state_path)
    qtbot.addWidget(window)
    window.platform_combo.setCurrentText("抖音")
    window.game_combo.setCurrentIndex(window.game_combo.findData(GameID.HONOR_OF_KINGS.value))
    window.room_input.setText("https://live.douyin.com/12345")
    window.browser_profile_input.setText("profiles/douyin-test")
    window.chrome_path_input.setText("")
    window.roi_editor.set_roi(ROIConfig(x=0.1, y=0.2, width=0.3, height=0.4))
    window.auto_exit_checkbox.setChecked(True)
    window._refresh_account_table_row("10001")
    window._set_selected_account_as_default()
    window._save_state()

    restored = MainWindow(account_store=store, state_path=state_path)
    qtbot.addWidget(restored)

    assert restored._selected_platform() == "douyin"
    assert restored._selected_game_id() is GameID.HONOR_OF_KINGS
    assert restored.room_input.text() == "https://live.douyin.com/12345"
    assert restored.browser_profile_input.text() == "profiles/douyin-test"
    assert restored.chrome_path_input.text() == ""
    assert restored.roi_editor.roi() == ROIConfig(x=0.1, y=0.2, width=0.3, height=0.4)
    assert restored.auto_exit_checkbox.isChecked() is True
    assert restored.account_table.rowCount() == 1
    restored_uid_item = restored.account_table.item(0, 0)
    restored_default_item = restored.account_table.item(0, 2)
    assert restored_uid_item is not None
    assert restored_default_item is not None
    assert restored_uid_item.text() == "10001"
    assert restored_default_item.text() == "是"


def test_main_window_redacts_account_store_errors(qtbot: QtBot) -> None:
    window = MainWindow(account_store=FailingAccountStore())
    qtbot.addWidget(window)

    window._refresh_account_table_row("10001")

    assert window.statusBar().currentMessage() == "账号管理：请检查本机凭证存储配置"
    assert "SECRET_TOKEN_VALUE" not in window.statusBar().currentMessage()
    assert "token" not in window.statusBar().currentMessage().lower()
    assert "cookie" not in window.statusBar().currentMessage().lower()
