from pathlib import Path

from qr_live_scanner_tencent.gui.state import (
    GuiAccountEntry,
    GuiState,
    load_gui_state,
    save_gui_state,
)
from qr_live_scanner_tencent.interfaces import GameID, ROIConfig, TencentLoginProvider


def test_gui_state_round_trips_non_sensitive_monitor_and_account_index(tmp_path: Path) -> None:
    path = tmp_path / "gui-state.json"
    state = GuiState(
        platform="douyin",
        game_id=GameID.HONOR_OF_KINGS,
        room_id="https://live.douyin.com/12345",
        browser_user_data_dir="profiles/douyin-test",
        chrome_executable_path=None,
        roi=ROIConfig(x=0.1, y=0.2, width=0.3, height=0.4),
        auto_confirm=True,
        auto_exit=True,
        demo_mode=True,
        default_uid="10001",
        accounts=[
            GuiAccountEntry(uid="10001", display_name="main"),
            GuiAccountEntry(uid="10002"),
        ],
    )

    save_gui_state(path, state)
    loaded = load_gui_state(path)

    assert loaded == state
    text = path.read_text(encoding="utf-8")
    assert "10001" in text
    assert "profiles/douyin-test" in text
    assert "cookie" not in text.lower()
    assert "token" not in text.lower()
    assert "stoken" not in text.lower()


def test_gui_state_round_trips_provider_scoped_account_index(tmp_path: Path) -> None:
    path = tmp_path / "gui-state.json"
    state = GuiState(
        provider=TencentLoginProvider.WECHAT,
        default_uid="same-uid",
        default_provider=TencentLoginProvider.WECHAT,
        accounts=[
            GuiAccountEntry(uid="same-uid", provider=TencentLoginProvider.QQ),
            GuiAccountEntry(
                uid="same-uid",
                provider=TencentLoginProvider.WECHAT,
                display_name="wechat account",
            ),
        ],
    )

    save_gui_state(path, state)
    loaded = load_gui_state(path)

    assert loaded == state
    assert loaded.accounts[0].provider is TencentLoginProvider.QQ
    assert loaded.accounts[1].provider is TencentLoginProvider.WECHAT
    assert loaded.default_provider is TencentLoginProvider.WECHAT
    text = path.read_text(encoding="utf-8")
    assert "provider" in text
    assert "same-uid" in text
    assert "cookie" not in text.lower()
    assert "token" not in text.lower()


def test_gui_state_loads_default_when_file_is_missing(tmp_path: Path) -> None:
    state = load_gui_state(tmp_path / "missing.json")

    assert state.platform == "bilibili"
    assert state.game_id is GameID.HONOR_OF_KINGS
    assert state.accounts == []


def test_gui_state_ignores_invalid_file_without_leaking_contents(tmp_path: Path) -> None:
    path = tmp_path / "gui-state.json"
    path.write_text('{"accounts": [{"uid": ""}], "secret": "SECRET_STOKEN"}', encoding="utf-8")

    state = load_gui_state(path)

    assert state == GuiState()
