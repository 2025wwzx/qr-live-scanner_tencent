from qr_live_scanner_tencent.detection.roi_fallback import ROIFallbackStrategy
from qr_live_scanner_tencent.interfaces import ROIConfig


def test_roi_fallback_uses_aggressive_primary_roi_by_default() -> None:
    strategy = ROIFallbackStrategy()

    roi = strategy.current_roi()

    assert roi == ROIConfig(x=0.375, y=0.375, width=0.25, height=0.25)


def test_roi_fallback_expands_after_consecutive_misses() -> None:
    strategy = ROIFallbackStrategy()

    assert strategy.current_roi() == ROIConfig(x=0.375, y=0.375, width=0.25, height=0.25)

    strategy.record_miss()
    strategy.record_miss()

    assert strategy.current_roi() == ROIConfig(x=0.375, y=0.375, width=0.25, height=0.25)

    strategy.record_miss()

    assert strategy.current_roi() == ROIConfig(x=0.35, y=0.35, width=0.30, height=0.30)

    for _ in range(7):
        strategy.record_miss()

    assert strategy.current_roi() == ROIConfig(x=0.30, y=0.30, width=0.40, height=0.40)


def test_roi_fallback_resets_to_primary_after_hit() -> None:
    strategy = ROIFallbackStrategy()
    for _ in range(10):
        strategy.record_miss()

    assert strategy.current_roi() == ROIConfig(x=0.30, y=0.30, width=0.40, height=0.40)

    strategy.record_hit()

    assert strategy.current_roi() == ROIConfig(x=0.375, y=0.375, width=0.25, height=0.25)
