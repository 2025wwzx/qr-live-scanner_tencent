from __future__ import annotations

import os
from pathlib import Path
from typing import cast

from PySide6.QtCore import QSize
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication, QWidget

from qr_live_scanner_tencent.accounts import FakeAccountStore, TencentSession
from qr_live_scanner_tencent.gui.main_window import MainWindow, TencentAccountDialog
from qr_live_scanner_tencent.interfaces import TencentLoginProvider

SNAPSHOT_FONT_FILES = (
    Path("C:/Windows/Fonts/NotoSansSC-VF.ttf"),
    Path("C:/Windows/Fonts/msyh.ttc"),
    Path("C:/Windows/Fonts/simhei.ttf"),
    Path("C:/Windows/Fonts/simsun.ttc"),
)


def write_gui_snapshots(
    output_dir: str | Path,
    *,
    provider: TencentLoginProvider = TencentLoginProvider.QQ,
    mock_uid: str = "",
) -> list[Path]:
    """离屏渲染 GUI 快照，用于本地检查主窗口和账号登录弹窗。

    该函数只构造 GUI 控件并保存 PNG，不启动直播监测、不访问 keyring，也不会向
    腾讯 QQ/微信端点发送 HTTP 请求。`provider` 用于切换账号弹窗中的登录渠道显示；
    `mock_uid` 非空时会先写入本地 `FakeAccountStore` 并刷新账号表，只用于展示
    “账号已保存”的视觉状态。返回值为写入完成的 PNG 路径列表，调用方可以用于
    CLI 输出或测试校验。
    """

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app_instance = QApplication.instance()
    app = QApplication([]) if app_instance is None else cast(QApplication, app_instance)
    _configure_snapshot_font(app)

    normalized_provider = TencentLoginProvider(str(provider))
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    account_store = FakeAccountStore()
    main_window = MainWindow(account_store=account_store)
    provider_index = main_window.provider_combo.findData(normalized_provider.value)
    if provider_index >= 0:
        main_window.provider_combo.setCurrentIndex(provider_index)
    normalized_mock_uid = str(mock_uid).strip()
    if normalized_mock_uid:
        account_store.save_tencent_session(
            TencentSession(
                uid=normalized_mock_uid,
                provider=normalized_provider,
                credentials={"mock_session": "local-mock-only"},
            ),
            authorized=True,
        )
        main_window._refresh_account_table_row(normalized_mock_uid)
    main_window.resize(720, 820)

    account_dialog = TencentAccountDialog(
        provider=normalized_provider,
        account_store=account_store,
        qr_output_path=target_dir / f"tencent-account-dialog-{normalized_provider.value}-qr.png",
    )
    if normalized_mock_uid:
        account_dialog.mock_uid_input.setText(normalized_mock_uid)
        account_dialog._mock_confirm_local_session()
    account_dialog.resize(360, 460)

    paths = [
        target_dir / "main-window.png",
        target_dir / f"tencent-account-dialog-{normalized_provider.value}.png",
    ]
    _save_widget_png(main_window, paths[0], minimum_size=QSize(720, 820))
    _save_widget_png(account_dialog, paths[1], minimum_size=QSize(360, 460))
    return paths


def _save_widget_png(widget: QWidget, output_path: Path, *, minimum_size: QSize) -> None:
    widget.resize(widget.sizeHint().expandedTo(minimum_size))
    widget.show()
    app = QApplication.instance()
    if app is not None:
        app.processEvents()
    pixmap = widget.grab()
    if pixmap.isNull():
        msg = "GUI snapshot rendering failed"
        raise RuntimeError(msg)
    if not pixmap.save(str(output_path), "PNG"):
        msg = "GUI snapshot writing failed"
        raise RuntimeError(msg)
    widget.close()


def _configure_snapshot_font(app: QApplication) -> None:
    for font_path in SNAPSHOT_FONT_FILES:
        if not font_path.exists():
            continue
        font_id = QFontDatabase.addApplicationFont(str(font_path))
        if font_id < 0:
            continue
        families = QFontDatabase.applicationFontFamilies(font_id)
        if not families:
            continue
        app.setFont(QFont(families[0], 9))
        return
