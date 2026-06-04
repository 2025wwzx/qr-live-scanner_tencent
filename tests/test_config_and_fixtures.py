from pathlib import Path

import pytest

from qr_live_scanner_tencent.config import AppConfig
from qr_live_scanner_tencent.interfaces import GameID, ROIConfig


def test_default_config_documents_latency_and_reconnect_policy() -> None:
    config = AppConfig()

    assert config.pipeline.processing_latency_p95_target_ms == 1000
    assert config.pipeline.dedup_ttl_seconds == 10.0
    assert config.stream.max_retries == 3
    assert config.stream.preferred_bilibili_format == "flv"


def test_default_roi_templates_cover_locked_game_scope() -> None:
    config = AppConfig()

    assert set(config.roi_templates) == set(GameID)


def test_default_roi_templates_use_aggressive_primary_roi() -> None:
    config = AppConfig()
    expected_roi = ROIConfig(x=0.375, y=0.375, width=0.25, height=0.25)

    assert set(config.roi_templates.values()) == {expected_roi}


def test_default_config_can_be_loaded_from_toml(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
        [pipeline]
        dedup_ttl_seconds = 5.0

        [stream]
        max_retries = 2
        """,
        encoding="utf-8",
    )

    config = AppConfig.from_toml(config_path)

    assert config.pipeline.dedup_ttl_seconds == 5.0
    assert config.stream.max_retries == 2


def test_config_rejects_unknown_game_roi_template() -> None:
    with pytest.raises(ValueError):
        AppConfig.model_validate(
            {"roi_templates": {"unknown": {"x": 0, "y": 0, "width": 1, "height": 1}}}
        )


@pytest.mark.parametrize(
    "data",
    [
        {"pipeline": {"processing_latency_p95_target_ms": 0}},
        {"pipeline": {"synthetic_pipeline_latency_target_ms": 0}},
        {"pipeline": {"dedup_ttl_seconds": 0.0}},
        {"stream": {"max_retries": -1}},
        {"stream": {"retry_delay_seconds": 0.0}},
        {"stream": {"retry_backoff_factor": 0.5}},
    ],
)
def test_config_rejects_invalid_numeric_bounds(data: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        AppConfig.model_validate(data)
