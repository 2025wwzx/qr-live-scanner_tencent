from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator

from qr_live_scanner_tencent.interfaces import (
    DEFAULT_AGGRESSIVE_ROI,
    GameID,
    ROIConfig,
    TencentLoginProvider,
)

DEFAULT_ROI = DEFAULT_AGGRESSIVE_ROI


class PipelineConfig(BaseModel):
    processing_latency_p95_target_ms: int = Field(default=1000, gt=0)
    synthetic_pipeline_latency_target_ms: int = Field(default=200, gt=0)
    dedup_ttl_seconds: float = Field(default=10.0, gt=0.0)
    decoder_backend: str = "auto"


class StreamConfig(BaseModel):
    max_retries: int = Field(default=3, ge=0)
    retry_delay_seconds: float = Field(default=0.5, gt=0.0)
    retry_backoff_factor: float = Field(default=2.0, ge=1.0)
    preferred_bilibili_format: str = "flv"
    fallback_bilibili_format: str = "hls"


class AccountConfig(BaseModel):
    keyring_service: str = "qr-live-scanner-tencent"

    @staticmethod
    def keyring_username(game_id: GameID, uid: str) -> str:
        return f"{game_id.value}:{uid}"

    @staticmethod
    def tencent_keyring_username(
        uid: str,
        provider: TencentLoginProvider = TencentLoginProvider.QQ,
    ) -> str:
        return f"tencent:{provider.value}:{uid}"


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)
    stream: StreamConfig = Field(default_factory=StreamConfig)
    accounts: AccountConfig = Field(default_factory=AccountConfig)
    roi_templates: dict[GameID, ROIConfig] = Field(
        default_factory=lambda: dict.fromkeys(GameID, DEFAULT_ROI)
    )

    @field_validator("roi_templates", mode="before")
    @classmethod
    def _coerce_game_ids(cls, value: object) -> object:
        if value is None:
            return value
        if not isinstance(value, dict):
            return value
        coerced: dict[GameID, object] = {}
        for raw_key, raw_value in value.items():
            try:
                key = raw_key if isinstance(raw_key, GameID) else GameID(str(raw_key))
            except ValueError as exc:
                msg = f"unknown game ROI template: {raw_key}"
                raise ValueError(msg) from exc
            coerced[key] = raw_value
        return coerced

    @classmethod
    def from_toml(cls, path: str | Path) -> Self:
        with Path(path).open("rb") as handle:
            data = tomllib.load(handle)
        return cls.model_validate(data)
