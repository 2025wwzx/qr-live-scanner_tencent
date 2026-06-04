from __future__ import annotations

from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFormLayout,
    QWidget,
)

from qr_live_scanner_tencent.interfaces import DEFAULT_AGGRESSIVE_ROI, ROIConfig

ROI_DECIMALS = 4
ROI_STEP = 0.01


class ROIEditorWidget(QWidget):
    """编辑 0 到 1 归一化 ROI 坐标的轻量控件。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.x_spin = self._create_spin_box()
        self.y_spin = self._create_spin_box()
        self.width_spin = self._create_spin_box()
        self.height_spin = self._create_spin_box()
        self.width_spin.setMinimum(ROI_STEP)
        self.height_spin.setMinimum(ROI_STEP)

        layout = QFormLayout(self)
        layout.addRow("X", self.x_spin)
        layout.addRow("Y", self.y_spin)
        layout.addRow("Width", self.width_spin)
        layout.addRow("Height", self.height_spin)

        self.set_roi(DEFAULT_AGGRESSIVE_ROI)

    def set_roi(self, roi: ROIConfig) -> None:
        self.x_spin.setValue(roi.x)
        self.y_spin.setValue(roi.y)
        self.width_spin.setValue(roi.width)
        self.height_spin.setValue(roi.height)

    def roi(self) -> ROIConfig:
        x = self.x_spin.value()
        y = self.y_spin.value()
        width = min(self.width_spin.value(), 1.0 - x)
        height = min(self.height_spin.value(), 1.0 - y)
        return ROIConfig(
            x=x,
            y=y,
            width=width,
            height=height,
        )

    @staticmethod
    def _create_spin_box() -> QDoubleSpinBox:
        spin_box = QDoubleSpinBox()
        spin_box.setDecimals(ROI_DECIMALS)
        spin_box.setRange(0.0, 1.0)
        spin_box.setSingleStep(ROI_STEP)
        return spin_box
