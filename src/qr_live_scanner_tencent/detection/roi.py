from __future__ import annotations

import numpy as np

from qr_live_scanner_tencent.interfaces import ROIConfig


def crop_roi(frame: np.ndarray, roi: ROIConfig) -> np.ndarray:
    height, width = frame.shape[:2]
    x, y, roi_width, roi_height = roi.to_pixels(frame_width=width, frame_height=height)
    x = min(width - 1, max(0, x))
    y = min(height - 1, max(0, y))
    roi_width = max(1, roi_width)
    roi_height = max(1, roi_height)
    return frame[y : min(height, y + roi_height), x : min(width, x + roi_width)]
