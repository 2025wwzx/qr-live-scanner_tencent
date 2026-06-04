from __future__ import annotations

from dataclasses import dataclass, field

from qr_live_scanner_tencent.interfaces import ROIConfig


def default_roi_levels() -> tuple[tuple[int, ROIConfig], ...]:
    """返回固定二维码位置的激进 ROI 级联配置。"""

    return (
        (0, ROIConfig(x=0.375, y=0.375, width=0.25, height=0.25)),
        (3, ROIConfig(x=0.35, y=0.35, width=0.30, height=0.30)),
        (10, ROIConfig(x=0.30, y=0.30, width=0.40, height=0.40)),
    )


@dataclass(slots=True)
class ROIFallbackStrategy:
    """按连续未命中次数选择 ROI，命中后回到主 ROI。"""

    levels: tuple[tuple[int, ROIConfig], ...] = field(default_factory=default_roi_levels)
    consecutive_misses: int = 0

    def current_roi(self) -> ROIConfig:
        selected = self.levels[0][1]
        for miss_threshold, roi in self.levels:
            if self.consecutive_misses >= miss_threshold:
                selected = roi
        return selected

    def record_miss(self) -> None:
        self.consecutive_misses += 1

    def record_hit(self) -> None:
        self.consecutive_misses = 0
