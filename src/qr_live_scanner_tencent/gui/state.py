from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from qr_live_scanner_tencent.interfaces import (
    DEFAULT_AGGRESSIVE_ROI,
    GameID,
    ROIConfig,
    TencentLoginProvider,
)
from qr_live_scanner_tencent.monitor import DEFAULT_BROWSER_PROFILE_DIR

DEFAULT_GUI_STATE_PATH = Path("profiles/gui-state.json")


class GuiAccountEntry(BaseModel):
    """保存 GUI 账号索引中的非敏感账号元数据。"""

    model_config = ConfigDict(extra="forbid")

    uid: str
    display_name: str = ""

    @field_validator("uid", "display_name")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        return str(value).strip()

    @field_validator("uid")
    @classmethod
    def _require_uid(cls, value: str) -> str:
        if not value:
            msg = "uid is required"
            raise ValueError(msg)
        return value


class GuiState(BaseModel):
    """保存 GUI 可恢复状态，不包含 Cookie、token 或二维码 payload。"""

    model_config = ConfigDict(extra="forbid")

    platform: str = "bilibili"
    game_id: GameID = GameID.HONOR_OF_KINGS
    provider: TencentLoginProvider = TencentLoginProvider.QQ
    room_id: str = ""
    browser_user_data_dir: str = DEFAULT_BROWSER_PROFILE_DIR
    chrome_executable_path: str | None = None
    roi: ROIConfig = DEFAULT_AGGRESSIVE_ROI
    auto_confirm: bool = False
    auto_exit: bool = False
    demo_mode: bool = False
    default_uid: str = ""
    accounts: list[GuiAccountEntry] = Field(default_factory=list)

    @field_validator("platform")
    @classmethod
    def _validate_platform(cls, value: str) -> str:
        normalized = str(value).strip().lower()
        if normalized not in {"bilibili", "douyin"}:
            return "bilibili"
        return normalized

    @field_validator(
        "room_id",
        "browser_user_data_dir",
        "chrome_executable_path",
        "default_uid",
        mode="before",
    )
    @classmethod
    def _strip_optional_text(cls, value: object) -> object:
        if value is None:
            return None
        return str(value).strip()

    @classmethod
    def from_path(cls, path: str | Path) -> GuiState:
        return load_gui_state(path)


def load_gui_state(path: str | Path = DEFAULT_GUI_STATE_PATH) -> GuiState:
    state_path = Path(path)
    if not state_path.exists():
        return GuiState()
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        return GuiState.model_validate(data)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return GuiState()


def save_gui_state(path: str | Path, state: GuiState) -> None:
    state_path = Path(path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        state.model_dump_json(indent=2, exclude_none=False),
        encoding="utf-8",
    )
