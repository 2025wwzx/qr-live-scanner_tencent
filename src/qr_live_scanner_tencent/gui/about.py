from __future__ import annotations

from PySide6.QtWidgets import QMessageBox, QWidget

NON_COMMERCIAL_WARNING = "本项目禁止任何形式的商业用途/贩卖，违者必究。"
LICENSE_NOTICE = "CC BY-NC 4.0"


class AboutDialog(QMessageBox):
    """显示项目许可与非商业使用声明。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("About qr-live-scanner-tencent")
        self.setIcon(QMessageBox.Icon.Information)
        self.setText(f"{NON_COMMERCIAL_WARNING}\n{LICENSE_NOTICE}")
        self.setInformativeText("低延迟直播流二维码识别与授权登录外壳。")
